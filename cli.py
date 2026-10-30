"""Headless runs for tests and debugging:
python -m cli run --policy greedy --seed 42
python -m cli eval --seeds 1-20"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.runner import LIVE_REPEATS, EvalSettings, run_evaluation
from evaluation.summary import claim
from sim.runner import POLICIES, run


def seed_range(text: str) -> tuple[int, ...]:
    """`1-20` or `1,4,9` or a mix such as `1-5,42`."""
    seeds: list[int] = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        seeds += range(int(lo), int(hi or lo) + 1)
    return tuple(seeds)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cli")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run one simulation and save it under runs/")
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--policy", choices=POLICIES, default="greedy")
    r.add_argument("--llm", choices=("off", "live", "replay"), default=None,
                   help="agents only: rules only (off), live model calls, or replay a recorded run")
    r.add_argument("--from", dest="replay_from", type=Path, default=None, help="run folder to replay")
    e = sub.add_parser("eval", help="evaluate seeds x policies (DESIGN 11.2) under runs/evals/")
    e.add_argument("--seeds", type=seed_range, default=tuple(range(1, 21)), help="for example 1-20 or 1,2,42")
    e.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    e.add_argument("--llm", choices=("off", "live", "replay"), default="off")
    e.add_argument("--repeats", type=int, default=LIVE_REPEATS, help="agent runs per seed (live and replay only)")
    e.add_argument("--from", dest="replay_from", default=None, help="evaluation id to replay, such as eval-001")
    args = parser.parse_args(argv)
    if args.command == "eval":
        return evaluate(args)

    out = run(args.seed, args.policy, llm_mode=args.llm, replay_from=args.replay_from)
    m = json.loads((out / "metrics.json").read_text())
    print(f"Saved {out}")
    for key in ("critical_on_time_pct", "unserved_criticals", "avg_report_to_care_min",
                "er_load_balance_pct", "avg_response_time_min", "rejected_actions"):
        print(f"  {key}: {m[key]}")


def evaluate(args) -> None:
    settings = EvalSettings(seeds=args.seeds, policies=tuple(args.policies), llm_mode=args.llm,
                            repeats=args.repeats, replay_from=args.replay_from)
    out = run_evaluation(settings, lambda done, total, label: print(f"[{done}/{total}] {label}", flush=True))
    summary = json.loads((out / "summary.json").read_text())
    print(f"Saved {out}")
    for policy in summary["policies"]:
        m = summary["metrics"][policy]["critical_on_time_pct"]
        wins = summary["versus_baseline"].get(policy, {}).get("wins")
        tail = f", wins {wins['wins']}/{wins['seeds']}" if wins else ""
        print(f"  {policy}: criticals on time {m['mean']}% (sd {m['sd']}){tail}")
    if claim(summary):
        print(claim(summary))


if __name__ == "__main__":
    main()
