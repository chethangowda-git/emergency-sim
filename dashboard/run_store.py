"""List, load and save runs. The dashboard only reads run folders (plus starting a run)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from sim.config import ROOT
from sim.roads import Route

RUNS_DIR = ROOT / "runs"
REQUIRED = ("config.json", "events.jsonl", "routes.json", "timeline.jsonl", "metrics.json", "victims.json")


def list_runs(runs_dir: Path = RUNS_DIR) -> list[str]:
    """Complete runs, newest first."""
    if not runs_dir.exists():
        return []
    done = [p for p in runs_dir.iterdir() if p.is_dir() and all((p / f).exists() for f in REQUIRED)]
    return [p.name for p in sorted(done, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def route_of(trip: dict) -> Route:
    return Route(tuple(trip["nodes"]), tuple(tuple(p) for p in trip["points"]), tuple(trip["cum"]),
                 (), 0.0, trip["minutes"])


def load_run(run_id: str, runs_dir: Path = RUNS_DIR) -> dict:
    d = runs_dir / run_id
    trips = json.loads((d / "routes.json").read_text())
    by_unit: dict[str, list[dict]] = {}
    for trip in trips:
        trip["end_t"] = trip["ended_at"] if trip["ended_at"] is not None else trip["arrive_t"]
        trip["route"] = route_of(trip)
        by_unit.setdefault(trip["unit"], []).append(trip)
    return {
        "run_id": run_id,
        "config": json.loads((d / "config.json").read_text()),
        "events": _jsonl(d / "events.jsonl"),
        "trips_by_unit": by_unit,
        "timeline": _jsonl(d / "timeline.jsonl"),
        "metrics": json.loads((d / "metrics.json").read_text()),
        "victims": json.loads((d / "victims.json").read_text()),
    }


def run_and_save(seed: int, policy: str, progress_cb: Callable[[int, int], None] | None = None,
                 roads=None, llm_mode: str | None = None, replay_from: str | None = None) -> str:
    from sim.runner import run

    src = RUNS_DIR / replay_from if replay_from else None
    return run(seed, policy, progress_cb, roads=roads, llm_mode=llm_mode, replay_from=src).name


def replayable_runs(policy: str, runs_dir: Path = RUNS_DIR) -> list[str]:
    """Saved runs of this agent policy that recorded at least one LLM call."""
    out = []
    for run_id in list_runs(runs_dir):
        d = runs_dir / run_id
        cfg = json.loads((d / "config.json").read_text())
        calls = d / "llm_calls.jsonl"
        if cfg.get("policy") == policy and calls.exists() and calls.stat().st_size > 0:
            out.append(run_id)
    return out


def run_config(run_id: str, runs_dir: Path = RUNS_DIR) -> dict:
    return json.loads((runs_dir / run_id / "config.json").read_text())
