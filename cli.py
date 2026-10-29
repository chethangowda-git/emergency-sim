"""Headless runs for tests and debugging: python -m cli run --policy greedy --seed 42"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sim.runner import POLICIES, run


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cli")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run one simulation and save it under runs/")
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--policy", choices=POLICIES, default="greedy")
    r.add_argument("--llm", choices=("off", "live", "replay"), default=None,
                   help="agents only: rules only (off), live model calls, or replay a recorded run")
    r.add_argument("--from", dest="replay_from", type=Path, default=None, help="run folder to replay")
    args = parser.parse_args(argv)

    out = run(args.seed, args.policy, llm_mode=args.llm, replay_from=args.replay_from)
    m = json.loads((out / "metrics.json").read_text())
    print(f"Saved {out}")
    for key in ("critical_on_time_pct", "unserved_criticals", "avg_report_to_care_min",
                "er_load_balance_pct", "avg_response_time_min", "rejected_actions"):
        print(f"  {key}: {m[key]}")


if __name__ == "__main__":
    main()
