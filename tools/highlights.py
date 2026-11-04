"""Negotiation highlights for the Simulation page (DESIGN 13.1): the 5 moments with the most real negotiation.

    python -m tools.highlights            # writes config/highlights.json (no API calls)

Replays every recorded live run (the final live evaluations and the demo), scores each negotiation by
rounds, AI-decided offers, counters, guard blocks, escalations and AI choices that differ from the rule,
and keeps the top 5 (at most one per run, the demo always included). For each, it replays once more with
that one AI choice switched to the rule and records how the outcome for the victims involved changed.
After the switch the run diverges; later calls then take the model's answer to the most similar recorded
question (tools/diagnostics/llm_losses.py), so "what happened instead" is an estimate.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from dashboard import agent_views, explain_agents, run_store
from dashboard import wording as W
from evaluation.runner import EVALS_DIR, load_evaluation, run_id_for
from sim.config import ROOT, default_config, load_places
from sim.runner import RUNS_DIR, incident_victims, load_roads, prepare, simulate
from tools.diagnostics.llm_losses import Counterfactual

OUT = ROOT / "config" / "highlights.json"
SOURCES = ("eval-011", "eval-012")
DEMO = "demo/demo-seed376"
TOP = 5


class ForceOne(Counterfactual):
    """Replay exactly, except one recorded call (by its position in llm_calls.jsonl) answered by the rule."""

    def __init__(self, src: Path, forced: int) -> None:
        super().__init__(src)
        self.forced = forced

    def _answer(self, agent, purpose, key, instructions, payload, schema, tools):
        queue = self.exact[(agent, purpose, key)]
        i = next((i for i in queue if not self.used[i]), None)
        if i == self.forced:
            self.used[i] = True
            return None, "forced_rule"
        return super()._answer(agent, purpose, key, instructions, payload, schema, tools)


def replayed(src: Path, llm, cfg, roads, places) -> dict:
    config = json.loads((src / "config.json").read_text())
    prep = prepare(config["seed"], config["policy"], cfg=cfg, roads=roads, places=places, llm=llm,
                   spawns=config.get("spawns", []))
    simulate(prep.world, prep.policy)
    w = prep.world
    return {"events": w.events, "timeline": w.timeline, "victims": incident_victims(w), "llm_calls": llm.calls}


def score(th: dict, run: dict, calls: dict[int, dict]) -> dict:
    ms, decisions = th["messages"], th["decisions"]
    differs = [e for e in decisions if e["decided_by"] == "llm" and explain_agents.rule_choice(e, calls.get(e["seq"]))[1]]
    parts = {"rounds": max(m["round"] for m in ms),
             "AI-decided offers": sum(1 for e in decisions if e["decided_by"] == "llm" and e["kind"] == "admission_offer"),
             "counters": sum(1 for m in ms if m["msg_type"] == "admission_counter"),
             "guard blocks": min(3, sum(1 for e in run["events"] if e["type"] == "guard" and e.get("thread") == th["id"])),
             "escalations": sum(1 for m in ms if m["msg_type"] == "escalation"),
             "AI differs from rule": len(differs)}
    severity = {v["id"]: v["original_severity"] for v in run["victims"]}
    critical = any(severity.get(v) == "critical" for m in ms for v in m.get("victim_ids") or [])
    total = (parts["rounds"] + 2 * parts["AI-decided offers"] + parts["counters"] + parts["guard blocks"]
             + 3 * parts["escalations"] + 3 * min(5, parts["AI differs from rule"]) + 8 * critical)
    return {"total": total, "parts": parts, "differs": differs}


def best_thread(run: dict) -> dict | None:
    index = agent_views.thread_index(run["events"])
    calls = explain_agents.match_calls(run["events"], run["llm_calls"])
    scored = [(score(th, run, calls), th) for th in index.values() if th["protocol"] == "Admission"]
    scored = [(s, th) for s, th in scored if s["differs"]]
    if not scored:
        return None
    s, th = max(scored, key=lambda x: (x[0]["total"], x[1]["id"]))
    return {"score": s, "thread": th, "calls": calls}


def call_position(run: dict, call: dict) -> int:
    return next(i for i, c in enumerate(run["llm_calls"]) if c is call)


def instead(src: Path, run: dict, pick: dict, cfg, roads, places) -> str:
    """Replay with the thread's first AI-vs-rule choice switched to the rule; describe the difference."""
    e = pick["score"]["differs"][0]
    call = pick["calls"][e["seq"]]
    other = replayed(src, ForceOne(src, call_position(run, call)), cfg, roads, places)
    severity = {v["id"]: v["original_severity"] for v in run["victims"]}
    before = {v["id"]: v for v in run["victims"]}
    after = {v["id"]: v for v in other["victims"]}
    rule, _ = explain_agents.rule_choice(e, call)
    rule = (rule or "").replace("rule: ", "").replace("rule order: ", "ranking ").replace("rules alone: ", "")
    lines = []
    for vid in sorted({v for m in pick["thread"]["messages"] for v in m.get("victim_ids") or []}):
        a, b = before[vid], after[vid]
        who = W.cap(W.victims_phrase([vid], severity))
        lines.append(f"{who} would end the same ({care(a)})" if care(a) == care(b)
                     else f"{who} would be {care(b)} instead of {care(a)}")
    crit = lambda vs: (sum(1 for v in vs.values() if v["original_severity"] == "critical" and v["delivered_at"] is not None
                           and v["delivered_at"] <= v["deadline"]), sum(1 for v in vs.values() if v["original_severity"] == "critical"))
    (ca, n), (cb, _) = crit(before), crit(after)
    head = f"With the rule here ({W.place(e['agent'])} would {rule})" if e["kind"] == "admission_offer" else \
        f"With the rule here ({rule})"
    whole = f"unchanged at {ca}/{n}" if ca == cb else f"{cb}/{n} instead of {ca}/{n}"
    return f"{head}: " + "; ".join(lines) + f". Criticals on time in the whole run: {whole} (an estimate)."


def care(v: dict) -> str:
    if v["delivered_at"] is None:
        return "not at a hospital by the end"
    late = v["deadline"] is not None and v["delivered_at"] > v["deadline"]
    return f"at a hospital at {W.minute_tag(v['delivered_at'])}, {'late' if late else 'on time'}"


def sources() -> list[tuple[dict, Path]]:
    out = []
    for eval_id in SOURCES:
        if not (EVALS_DIR / eval_id).exists():
            continue
        for r in load_evaluation(eval_id)["rows"]:
            if r["policy"] != "greedy":
                ref = {"kind": "eval", "eval": eval_id, "seed": r["seed"], "policy": r["policy"], "repeat": r["repeat"]}
                out.append((ref, EVALS_DIR / eval_id / "runs" / run_id_for(r["seed"], r["policy"], r["repeat"])))
    if (RUNS_DIR / DEMO).exists():
        out.append(({"kind": "saved", "path": DEMO}, RUNS_DIR / DEMO))
    return out


def main() -> None:
    roads, places = load_roads(default_config()), load_places()
    found = []
    for ref, src in sources():
        sim = json.loads((src / "config.json").read_text())["sim"]
        cfg = replace(default_config(), n_incidents=sim["n_incidents"])
        run = replayed(src, Counterfactual(src), cfg, roads, places)
        pick = best_thread(run)
        if pick:
            found.append((pick["score"]["total"], ref, src, run, pick, cfg))
            print(f"{src.name}: {pick['score']['total']}", flush=True)
    found.sort(key=lambda f: (-f[0], json.dumps(f[1], sort_keys=True)))
    top, unguarded = [], 0
    for f in found:                                  # at most two picks from the unguarded evaluation
        if f[1].get("eval") == "eval-012":
            if unguarded == 2:
                continue
            unguarded += 1
        top.append(f)
        if len(top) == TOP:
            break
    demo = next((f for f in found if f[1]["kind"] == "saved"), None)
    if demo and demo not in top:
        top = top[:TOP - 1] + [demo]
    out = []
    for total, ref, src, run, pick, cfg in top:
        th = pick["thread"]
        severity = {v["id"]: v["original_severity"] for v in run["victims"]}
        first = th["messages"][0]
        out.append({"run": ref, "minute": first["t"], "incident": first.get("incident_id"),
                    "title": explain_agents.question(th, severity), "score": total, "why": pick["score"]["parts"],
                    "instead": instead(src, run, pick, cfg, roads, places),
                    "label": ("Demo" if ref["kind"] == "saved" else f"Final eval · seed {ref['seed']}, repeat {ref['repeat']}"
                              + (", no guard" if ref.get("eval") == "eval-012" else ""))})
    OUT.write_text(json.dumps({"note": "Written by python -m tools.highlights from recorded runs; no API calls.",
                               "highlights": out}, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
