"""Greedy vs rules-only agents vs live LLM agents, from two saved evaluations.

    python -m tools.diagnostics.live_vs_rules --live eval-010 --rules eval-007

Criticals on time (mean over seeds, spread across repeats), unserved criticals, wins against
greedy and against rules-only agents, decision shares, failed runs, tokens and estimated cost.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from evaluation.runner import EVALS_DIR, load_evaluation

PRICE_PER_M = {"input": 0.15, "output": 0.60}   # gpt-4o-mini, USD per million tokens (OpenAI model page)


def per_seed(rows: list[dict], policy: str, key: str = "critical_on_time_pct") -> dict[int, float]:
    by: dict[int, list[float]] = {}
    for r in rows:
        if r["policy"] == policy and r[key] is not None:
            by.setdefault(r["seed"], []).append(r[key])
    return {s: statistics.mean(v) for s, v in by.items()}


def wtl(mine: dict[int, float], other: dict[int, float]) -> tuple[int, int, int]:
    seeds = sorted(set(mine) & set(other))
    return (sum(mine[s] > other[s] for s in seeds), sum(mine[s] == other[s] for s in seeds),
            sum(mine[s] < other[s] for s in seeds))


def shares(eval_dir: Path, rows: list[dict], policy: str) -> dict[str, float]:
    tot = {"rule": 0.0, "llm": 0.0, "fallback": 0.0}
    runs = [r for r in rows if r["policy"] == policy]
    for r in runs:
        m = json.loads((eval_dir / "runs" / r["run_id"] / "metrics.json").read_text())
        for k, v in (m["negotiation"].get("decisions_by") or {}).items():
            tot[k] += v
    return {k: round(v / len(runs), 2) for k, v in tot.items()} if runs else tot


def usage(eval_dir: Path) -> dict:
    calls = inp = out = 0
    for f in sorted((eval_dir / "runs").glob("**/llm_calls.jsonl")):
        for line in f.read_text().splitlines():
            c = json.loads(line)
            calls += 1
            u = c.get("usage") or {}
            inp += u.get("input_tokens", 0)
            out += u.get("output_tokens", 0)
    cost = inp / 1e6 * PRICE_PER_M["input"] + out / 1e6 * PRICE_PER_M["output"]
    return {"calls": calls, "input_tokens": inp, "output_tokens": out, "cost_usd": round(cost, 3)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="live_vs_rules")
    ap.add_argument("--live", required=True)
    ap.add_argument("--rules", required=True)
    args = ap.parse_args(argv)
    live, rules = load_evaluation(args.live), load_evaluation(args.rules)
    lm = live["summary"]["metrics"]
    g, r, a = per_seed(live["rows"], "greedy"), per_seed(rules["rows"], "agents"), per_seed(live["rows"], "agents")
    unserved = {"greedy": per_seed(live["rows"], "greedy", "unserved_criticals"),
                "rules": per_seed(rules["rows"], "agents", "unserved_criticals"),
                "live": per_seed(live["rows"], "agents", "unserved_criticals")}
    out = {
        "criticals_on_time_pct": {"greedy": lm["greedy"]["critical_on_time_pct"],
                                  "rules_only_agents": rules["summary"]["metrics"]["agents"]["critical_on_time_pct"],
                                  "live_llm_agents": lm["agents"]["critical_on_time_pct"]},
        "unserved_criticals_mean": {k: round(statistics.mean(v.values()), 2) for k, v in unserved.items()},
        "wins_ties_losses": {"live_vs_greedy": wtl(a, g), "live_vs_rules_only": wtl(a, r),
                             "rules_only_vs_greedy": wtl(r, g)},
        "decision_shares_pct": {"rules_only_agents": shares(EVALS_DIR / args.rules, rules["rows"], "agents"),
                                "live_llm_agents": shares(EVALS_DIR / args.live, live["rows"], "agents")},
        "failed_runs": live["summary"].get("failed_runs", []),
        "usage": usage(EVALS_DIR / args.live),
        "per_seed": {s: {"greedy": g.get(s), "rules": r.get(s), "live": round(a[s], 2) if s in a else None}
                     for s in sorted(g)},
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
