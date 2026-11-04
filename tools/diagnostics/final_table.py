"""The final comparison (README): greedy, rules-only agents, live LLM agents guarded and unguarded.

    python -m tools.diagnostics.final_table --rules eval-010 --guarded eval-011 --unguarded eval-012

Criticals on time (mean over seeds of the per-seed mean over repeats; spread across repeats for
evaluations with repeats), unserved criticals, wins/ties/losses against greedy and against rules-only
agents, report-to-care time, victims delivered, decision shares, failed runs, tokens and cost.
"""
from __future__ import annotations

import argparse
import json
import statistics

from evaluation.runner import EVALS_DIR, load_evaluation
from tools.diagnostics.live_vs_rules import per_seed, shares, usage, wtl


def column(eval_id: str, policy: str, greedy: dict[int, float], rules: dict[int, float] | None) -> dict:
    data = load_evaluation(eval_id)
    rows, summary = data["rows"], data["summary"]
    pct = per_seed(rows, policy)
    mean = lambda key: round(statistics.mean(per_seed(rows, policy, key).values()), 2)
    out = {"evaluation": eval_id, "criticals_on_time_pct": round(statistics.mean(pct.values()), 2),
           "sd_across_seeds": round(statistics.stdev(pct.values()), 2),
           "repeat_spread": summary["metrics"][policy]["critical_on_time_pct"].get("repeat_sd"),
           "unserved_criticals": mean("unserved_criticals"), "report_to_care_min": mean("avg_report_to_care_min"),
           "victims_delivered": mean("victims_delivered"), "failed_runs": len(summary.get("failed_runs", []))}
    if policy != "greedy":
        out["wtl_vs_greedy"] = wtl(pct, greedy)
        out["decision_shares_pct"] = shares(EVALS_DIR / eval_id, rows, policy)
    if rules is not None:
        out["wtl_vs_rules_only"] = wtl(pct, rules)
    if (EVALS_DIR / eval_id / "settings.json").exists():
        settings = json.loads((EVALS_DIR / eval_id / "settings.json").read_text())
        if settings["llm_mode"] == "live":
            out["usage"] = usage(EVALS_DIR / eval_id)
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="final_table")
    ap.add_argument("--rules", required=True)
    ap.add_argument("--guarded", required=True)
    ap.add_argument("--unguarded", required=True)
    args = ap.parse_args(argv)
    rows = load_evaluation(args.rules)["rows"]
    greedy, rules = per_seed(rows, "greedy"), per_seed(rows, "agents")
    print(json.dumps({"greedy": column(args.rules, "greedy", greedy, None),
                      "rules_only_agents": column(args.rules, "agents", greedy, None),
                      "llm_guarded": column(args.guarded, "agents", greedy, rules),
                      "llm_unguarded": column(args.unguarded, "agents", greedy, rules)}, indent=1))


if __name__ == "__main__":
    main()
