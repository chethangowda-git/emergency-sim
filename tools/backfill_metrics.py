"""Add metrics introduced after a run was saved, without changing any existing number (no API calls).

    python -m tools.backfill_metrics            # the final evaluations, the demo and every saved full run

An evaluated run keeps only its config, scenario, metrics and LLM calls, so new metrics need the run
again: rules-only and baseline runs are re-simulated, LLM runs replay their recorded calls. Every
metric already saved must come out identical, or the run is left untouched and reported; then only the
new keys are added. Each evaluation's results.jsonl and summary.json are rebuilt from the metrics.
Saved full runs under runs/ have their events, so their metrics are recomputed directly.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from coordination.llm import LLMClient
from evaluation.runner import EVALS_DIR, run_id_for
from evaluation.summary import flatten, summarize
from policies.registry import make_llm
from sim import metrics
from sim.config import default_config, load_places
from sim.runner import RUNS_DIR, incident_victims, load_roads, prepare, simulate, write_json, write_jsonl

FINAL = ("eval-010", "eval-011", "eval-012", "eval-013")   # run with the current simulator and agents
DEMO = RUNS_DIR / "demo" / "demo-seed376"


def replayed_metrics(src: Path, roads, places) -> dict:
    config = json.loads((src / "config.json").read_text())
    mode = (config.get("llm") or {}).get("mode", "off")
    llm = None if config["policy"] == "greedy" else \
        make_llm("replay", src) if mode in ("live", "replay", "fake") else LLMClient("off")
    cfg = replace(default_config(), n_incidents=config["sim"]["n_incidents"])
    prep = prepare(config["seed"], config["policy"], cfg=cfg, roads=roads, places=places, llm=llm,
                   spawns=config.get("spawns", []))
    simulate(prep.world, prep.policy)
    w = prep.world
    return metrics.compute(incident_victims(w), w.timeline, w.events)


def merge(path: Path, new: dict) -> str:
    """'added', 'same' or 'mismatch: <keys>' (left untouched). Written in the order a new run writes, so a
    later rebuild of the run is byte-identical to it."""
    old = json.loads(path.read_text())
    differ = sorted(k for k in old if old[k] != new.get(k))
    if differ:
        return f"mismatch: {', '.join(differ)}"
    before = path.read_text()
    write_json(path, new)
    return "same" if path.read_text() == before else "added"


def backfill_evaluation(eval_id: str, roads, places, evals_dir: Path = EVALS_DIR) -> list[str]:
    d = evals_dir / eval_id
    rows = [json.loads(line) for line in (d / "results.jsonl").read_text().splitlines() if line]
    problems = []
    for r in rows:
        src = d / "runs" / run_id_for(r["seed"], r["policy"], r["repeat"])
        result = merge(src / "metrics.json", replayed_metrics(src, roads, places))
        if result.startswith("mismatch"):
            problems.append(f"{eval_id}/{src.name}: {result}")
    if problems:
        return problems
    refreshed = [{**{k: r[k] for k in ("seed", "policy", "repeat", "run_id")},
                  **flatten(json.loads((d / "runs" / r["run_id"] / "metrics.json").read_text()))} for r in rows]
    for old, new in zip(rows, refreshed):
        if any(old[k] != new[k] for k in old):
            return [f"{eval_id}/{old['run_id']}: a result row would change"]
    summary = json.loads((d / "summary.json").read_text())
    write_jsonl(d / "results.jsonl", refreshed)
    write_json(d / "summary.json", {**summarize(refreshed), "failed_runs": summary.get("failed_runs", [])})
    return []


def backfill_saved(runs_dir: Path = RUNS_DIR) -> list[str]:
    problems = []
    for d in sorted(p for p in runs_dir.iterdir() if (p / "events.jsonl").exists() and (p / "victims.json").exists()):
        read = lambda name: [json.loads(x) for x in (d / name).read_text().splitlines() if x]
        new = metrics.compute(json.loads((d / "victims.json").read_text()), read("timeline.jsonl"), read("events.jsonl"))
        result = merge(d / "metrics.json", new)
        if result.startswith("mismatch"):
            problems.append(f"{d.name}: {result}")
    return problems


def main(evals: tuple[str, ...] = FINAL) -> None:
    roads, places = load_roads(default_config()), load_places()
    problems = []
    for eval_id in evals:
        problems += backfill_evaluation(eval_id, roads, places)
        print(eval_id, "done", flush=True)
    if DEMO.exists():
        result = merge(DEMO / "metrics.json", replayed_metrics(DEMO, roads, places))
        problems += [f"demo: {result}"] if result.startswith("mismatch") else []
    problems += backfill_saved()
    print("\n".join(problems) if problems else "All runs matched; new metrics added.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main(tuple(sys.argv[1:]) or FINAL)
