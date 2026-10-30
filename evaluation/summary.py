"""DESIGN 11.2: mean and spread per policy, improvement over the baseline, win rate on the primary metric.

Pure functions over result rows (one per run). Repeats of a seed are averaged first, so every
seed counts once; spread is the standard deviation across seeds, and for live repeats also the
average spread across the repeats of a seed.
"""
from __future__ import annotations

from statistics import mean, stdev

PRIMARY = "critical_on_time_pct"
BASELINE = "greedy"

# key: (label, +1 when higher is better, -1 when lower is better, 0 when neither)
METRICS = {
    "critical_on_time_pct": ("Criticals on time (%)", 1),
    "unserved_criticals": ("Unserved criticals", -1),
    "avg_report_to_care_min": ("Avg report → care (min)", -1),
    "er_load_balance_pct": ("ER load balance (pts)", -1),
    "idle_minutes": ("Hospital idle minutes (all hospitals)", -1),
    "overload_minutes": ("Hospital overload minutes (all hospitals)", -1),
    "wasted_hold_minutes": ("Wasted hold minutes", -1),
    "victims_delivered": ("Victims delivered", 1),
    "deadlines_missed": ("Deadlines missed", -1),
    "avg_response_time_min": ("Avg response time (min)", -1),
    "unit_utilization_pct": ("Unit utilization (%)", 0),
    "transfers": ("Transfers between hospitals", 0),
    "reroutes": ("Reroutes", 0),
    "escalations": ("Escalations", 0),
    "rejected_actions": ("Rejected actions", -1),
    "threads": ("Negotiation threads", 0),
    "deals_without_arbiter": ("Deals without the Arbiter", 0),
    "arbiter_decisions": ("Arbiter decisions", 0),
    "llm_share_pct": ("Decisions by LLM (%)", 0),
}


def flatten(m: dict) -> dict:
    """The metrics the evaluation compares, from one run's metrics.json."""
    neg = m.get("negotiation") or {}
    return {
        **{k: m.get(k) for k in ("critical_on_time_pct", "unserved_criticals", "avg_report_to_care_min",
                                 "er_load_balance_pct", "wasted_hold_minutes", "victims_delivered",
                                 "deadlines_missed", "avg_response_time_min", "transfers", "reroutes",
                                 "rejected_actions")},
        "idle_minutes": sum(m["idle_minutes"].values()),
        "overload_minutes": sum(m["overload_minutes"].values()),
        "unit_utilization_pct": m["unit_utilization_pct"].get("overall"),
        "escalations": neg.get("escalations", 0),
        "threads": neg.get("threads", 0),
        "deals_without_arbiter": neg.get("deals_without_arbiter", 0),
        "arbiter_decisions": neg.get("arbiter_decisions", 0),
        "llm_share_pct": (neg.get("decisions_by") or {}).get("llm", 0.0),
    }


def stats(values: list[float]) -> dict:
    if not values:
        return {"mean": None, "sd": None, "min": None, "max": None, "n": 0}
    return {"mean": round(mean(values), 2), "sd": round(stdev(values), 2) if len(values) > 1 else 0.0,
            "min": min(values), "max": max(values), "n": len(values)}


def per_seed(rows: list[dict], policy: str, key: str) -> dict[int, float]:
    """Each seed's value, averaged over its repeats; seeds where the metric is undefined are left out."""
    by: dict[int, list[float]] = {}
    for r in rows:
        if r["policy"] == policy and r[key] is not None:
            by.setdefault(r["seed"], []).append(r[key])
    return {seed: round(mean(v), 2) for seed, v in sorted(by.items())}


def repeat_spread(rows: list[dict], policy: str, key: str) -> float | None:
    """Average standard deviation across the repeats of a seed (live runs); None with one repeat."""
    by: dict[int, list[float]] = {}
    for r in rows:
        if r["policy"] == policy and r[key] is not None:
            by.setdefault(r["seed"], []).append(r[key])
    sds = [stdev(v) for v in by.values() if len(v) > 1]
    return round(mean(sds), 2) if sds else None


def versus(rows: list[dict], policy: str, baseline: str = BASELINE) -> dict:
    """Improvement (agents - baseline) / baseline on mean values, and wins on the primary metric."""
    out = {}
    for key, (_, better) in METRICS.items():
        a, b = stats(list(per_seed(rows, policy, key).values()))["mean"], \
            stats(list(per_seed(rows, baseline, key).values()))["mean"]
        diff = None if a is None or b is None else round(a - b, 2)
        out[key] = {"diff": diff, "improvement_pct": round(100 * diff / b, 1) if diff is not None and b else None,
                    "better": None if diff in (None, 0) or better == 0 else diff * better > 0}
    mine, base = per_seed(rows, policy, PRIMARY), per_seed(rows, baseline, PRIMARY)
    both = sorted(set(mine) & set(base))
    wins = [s for s in both if mine[s] > base[s]]
    ties = [s for s in both if mine[s] == base[s]]
    out["wins"] = {"wins": len(wins), "ties": len(ties), "losses": len(both) - len(wins) - len(ties),
                   "seeds": len(both), "win_rate_pct": round(100 * len(wins) / len(both), 1) if both else None,
                   "won_seeds": wins}
    return out


def summarize(rows: list[dict], baseline: str = BASELINE) -> dict:
    policies = list(dict.fromkeys(r["policy"] for r in rows))
    return {
        "baseline": baseline,
        "policies": policies,
        "seeds": sorted({r["seed"] for r in rows}),
        "metrics": {p: {k: {**stats(list(per_seed(rows, p, k).values())), "repeat_sd": repeat_spread(rows, p, k)}
                        for k in METRICS} for p in policies},
        "versus_baseline": {p: versus(rows, p, baseline) for p in policies if p != baseline},
        "per_seed": {p: per_seed(rows, p, PRIMARY) for p in policies},
    }


def claim(summary: dict, policy: str = "agents") -> str | None:
    """DESIGN section 1: the sentence the evaluation exists to fill in."""
    if policy not in summary["versus_baseline"]:
        return None
    a = summary["metrics"][policy][PRIMARY]["mean"]
    b = summary["metrics"][summary["baseline"]][PRIMARY]["mean"]
    w = summary["versus_baseline"][policy]["wins"]
    return (f"With the same seeds, incidents, walk-ins and resources, the multi-agent system got {a}% of "
            f"critical victims to care on time versus {b}% for the greedy baseline, winning on "
            f"{w['wins']} of {w['seeds']} seeds.")
