"""Where did the LLM choose differently from the rule, and did it help? (a recorded live run)

    python -m tools.diagnostics.llm_choices runs/seed42-agents-010 --limit 10

Replays the run exactly, noting for every LLM call what the rule would have chosen (the
`default` of a pick, the rule report for Incident Assessment, the rule ranking and first
shortfall option for Dispatch). Then, for each differing call, replays again with only that
call forced to the rule (later calls replay where they still match, otherwise fall back to
rules) and compares criticals on time and the delivery of the victims involved.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from coordination import arbiter, base, dispatch, hospital, incident_assessment
from coordination.dispatch import rule_ranking
from coordination.llm import LLMClient, LLMResult
from coordination.schemas import IncidentReport
from policies.registry import make_llm
from sim.config import SimConfig, default_config
from sim.runner import run

PICK_USERS = (arbiter, dispatch, hospital, incident_assessment)


@contextmanager
def instrumented(force: int | None = None):
    """Record each LLM call's rule answer; with `force`, that call (1-based) falls back to the rule."""
    notes: list[dict] = []
    original_pick, original_ask = base.pick, LLMClient.ask
    current: dict = {}

    def pick(llm, agent, purpose, instructions, payload, options, default, default_reason):
        current["default"] = default
        try:
            return original_pick(llm, agent, purpose, instructions, payload, options, default, default_reason)
        finally:
            current.pop("default", None)

    def ask(self, agent, purpose, instructions, payload, schema, tools=None):
        notes.append({"agent": agent, "purpose": purpose, "default": current.get("default"), "payload": payload})
        if force is not None and len(notes) == force:
            return LLMResult(None, "forced_rule")
        result = original_ask(self, agent, purpose, instructions, payload, schema, tools)
        notes[-1]["output"] = result.output.model_dump() if result.output is not None else None
        return result

    for mod in PICK_USERS:
        if hasattr(mod, "pick"):
            mod.pick = pick
    LLMClient.ask = ask
    try:
        yield notes
    finally:
        for mod in PICK_USERS:
            if hasattr(mod, "pick"):
                mod.pick = original_pick
        LLMClient.ask = original_ask


def differs(note: dict) -> str | None:
    """A one-line description of how the LLM's answer differs from the rule's, or None."""
    out = note.get("output")
    if out is None:
        return None
    if note["purpose"] == "assess":
        if out["extra_als"] or out["extra_bls"]:
            return f"extra units ALS +{out['extra_als']}, BLS +{out['extra_bls']} ({', '.join(out['flags'])})"
        return None
    if note["purpose"] == "plan_units":
        reports = [IncidentReport.model_validate(r) for r in note["payload"]["reports"]]
        rule = rule_ranking(reports)
        firsts = {s["incident"]: s["options"][0]["id"] for s in note["payload"]["shortfalls"]}
        picks = {p["incident"]: p["option_id"] for p in out["shortfall_plans"] if p["incident"] in firsts}
        changed = [f"{i}: {p} instead of {firsts[i]}" for i, p in picks.items() if p != firsts[i]]
        if out["ranking"] != rule:
            changed.insert(0, f"ranking {out['ranking']} instead of {rule}")
        return "; ".join(changed) or None
    if note["default"] is not None and out["option_id"] != note["default"]:
        return f"{out['option_id']} instead of {note['default']}: {out['reason']}"
    return None


def victims_of(payload: dict) -> list[str]:
    req = payload.get("request") or payload.get("escalation") or {}
    if "victims" in req:
        return [x["id"] for x in req["victims"]]
    if "victim" in req:
        return [req["victim"]]
    if isinstance(payload.get("incident"), dict):
        return [v["id"] for v in payload["incident"]["victims"]]
    return []


def spread(diff: list, limit: int) -> list:
    """Up to `limit` differing calls, taking each kind of decision in turn, earliest first."""
    by_kind: dict[str, list] = {}
    for item in diff:
        by_kind.setdefault(item[1]["purpose"], []).append(item)
    out = []
    while len(out) < limit and any(by_kind.values()):
        for kind in list(by_kind):
            if by_kind[kind] and len(out) < limit:
                out.append(by_kind[kind].pop(0))
    return sorted(out, key=lambda x: x[0])


def replay(src: Path, cfg: SimConfig, force: int | None, out_dir: Path):
    config = json.loads((src / "config.json").read_text())
    with instrumented(force) as notes:
        out = run(config["seed"], config["policy"], runs_dir=out_dir, cfg=cfg, llm=make_llm("replay", src),
                  run_id=f"force-{force or 0}")
    victims = {v["id"]: v for v in json.loads((out / "victims.json").read_text())}
    return notes, json.loads((out / "metrics.json").read_text()), victims


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="llm_choices")
    ap.add_argument("run", type=Path, help="a recorded live run folder")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args(argv)
    sim = json.loads((args.run / "config.json").read_text())["sim"]
    cfg = replace(default_config(), n_incidents=sim["n_incidents"])
    with tempfile.TemporaryDirectory() as tmp:
        notes, base_m, base_v = replay(args.run, cfg, None, Path(tmp))
        diff = [(i, n, d) for i, n in enumerate(notes, start=1) if (d := differs(n))]
        print(f"{len(notes)} LLM calls, {len(diff)} chose differently from the rule; "
              f"criticals on time {base_m['criticals_on_time']}/{base_m['criticals_total']}")
        kinds = {}
        for _, n, _ in diff:
            kinds[n["purpose"]] = kinds.get(n["purpose"], 0) + 1
        print("differing calls by kind:", kinds)
        for i, n, d in spread(diff, args.limit):
            _, m, v = replay(args.run, cfg, i, Path(tmp))
            involved = victims_of(n["payload"])
            change = base_m["criticals_on_time"] - m["criticals_on_time"]
            later = [f"{x}: {base_v[x]['delivered_at']} vs {v[x]['delivered_at']} with the rule"
                     for x in involved if x in base_v and base_v[x]["delivered_at"] != v[x]["delivered_at"]]
            verdict = "helped" if change > 0 else "hurt" if change < 0 else "no change in criticals on time"
            print(f"\n#{i} {n['agent']} {n['purpose']} (t={n['payload'].get('t', '?')}): {d}")
            print(f"   {verdict}: criticals on time {base_m['criticals_on_time']} with the LLM, "
                  f"{m['criticals_on_time']} with the rule here")
            if later:
                print("   delivery (LLM vs rule): " + "; ".join(later))


if __name__ == "__main__":
    main()
