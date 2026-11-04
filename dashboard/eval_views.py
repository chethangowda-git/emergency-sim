"""Evaluation results (DESIGN 11.2, 13.1): tables and Plotly charts from a saved summary. No simulation.

Charts use the emphasis form: the full agent system in the accent blue, the baseline and the
ablations in a muted gray, with policy names on the axis and values labeled directly.
"""
from __future__ import annotations

from html import escape

import pandas as pd
import plotly.graph_objects as go

from evaluation.summary import METRICS, PRIMARY

POLICY_LABELS = {"greedy": "Greedy baseline", "agents": "Agents", "agents-nonego": "Agents, no negotiation",
                 "agents-noarb": "Agents, no Arbiter", "agents-rulehosp": "Agents, rule hospitals"}
ACCENT = {"light": "#2a78d6", "dark": "#3987e5"}
MUTED = "#898781"
SURFACE = {"light": "#ffffff", "dark": "#0e1117"}
INK = {"light": "#0b0b0b", "dark": "#f0efec"}
GRID = {"light": "#e1e0d9", "dark": "#2c2c2a"}


def label(policy: str) -> str:
    return POLICY_LABELS.get(policy, policy)


def d1(value) -> str:
    """One decimal everywhere (39.6), '–' when there is no value."""
    return "–" if value is None else f"{value:.1f}"


def r1(value):
    return None if value is None else round(value, 1)


def mean_sd(s: dict) -> str:
    if s["mean"] is None:
        return "–"
    spread = f" ± {d1(s['sd'])}" if s["n"] > 1 else ""
    repeats = f" (repeats ± {d1(s['repeat_sd'])})" if s.get("repeat_sd") is not None else ""
    return f"{d1(s['mean'])}{spread}{repeats}"


def policy_table(summary: dict) -> pd.DataFrame:
    """Every metric for every policy: mean ± standard deviation across seeds."""
    rows = [{"Metric": name, **{label(p): mean_sd(summary["metrics"][p][key]) for p in summary["policies"]}}
            for key, (name, _) in METRICS.items()]
    return pd.DataFrame(rows).set_index("Metric")


def verdict(v: dict) -> str:
    return {True: "✅ better", False: "🔻 worse"}.get(v["better"], "")


def versus_table(summary: dict, policy: str) -> pd.DataFrame:
    """One variant against the baseline: means, change, improvement (agents - baseline) / baseline."""
    base, mine, vs = (summary["metrics"][summary["baseline"]], summary["metrics"][policy],
                      summary["versus_baseline"][policy])
    rows = []
    for key, (name, _) in METRICS.items():
        v = vs[key]
        rows.append({"Metric": name, "Baseline": d1(base[key]["mean"]), label(policy): d1(mine[key]["mean"]),
                     "Change": "–" if v["diff"] is None else f"{v['diff']:+.1f}",
                     "Improvement": "–" if v["improvement_pct"] is None else f"{v['improvement_pct']:+.1f}%",
                     "": verdict(v)})
    return pd.DataFrame(rows).set_index("Metric")


def wins_table(summary: dict) -> pd.DataFrame:
    rows = []
    for p, vs in summary["versus_baseline"].items():
        w = vs["wins"]
        rows.append({"Policy": label(p), "Wins": w["wins"], "Ties": w["ties"], "Losses": w["losses"],
                     "Seeds": w["seeds"], "Win rate": "–" if w["win_rate_pct"] is None else f"{d1(w['win_rate_pct'])}%"})
    return pd.DataFrame(rows).set_index("Policy")


def per_seed_table(summary: dict) -> pd.DataFrame:
    """The primary metric per seed and policy: the table view of the per-seed chart."""
    rows = [{"Seed": seed, **{label(p): d1(summary["per_seed"][p].get(str(seed), summary["per_seed"][p].get(seed)))
                              for p in summary["policies"]}} for seed in summary["seeds"]]
    return pd.DataFrame(rows).set_index("Seed")


def _layout(fig: go.Figure, mode: str, height: int, x_title: str) -> go.Figure:
    fig.update_layout(height=height, margin={"l": 10, "r": 10, "t": 10, "b": 40}, plot_bgcolor=SURFACE[mode],
                      paper_bgcolor=SURFACE[mode], font={"color": INK[mode]}, bargap=0.35,
                      legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0})
    fig.update_xaxes(title=x_title, range=[-3, 103], tickvals=[0, 20, 40, 60, 80, 100], gridcolor=GRID[mode],
                     zeroline=False, ticksuffix="%")
    fig.update_yaxes(gridcolor=GRID[mode], autorange="reversed", automargin=True, ticklabelstandoff=8)
    return fig


def primary_bars(summary: dict, mode: str = "dark", focus: str = "agents") -> go.Figure:
    """Mean criticals on time per policy, with ±1 standard deviation across seeds."""
    policies = summary["policies"]
    stats = [summary["metrics"][p][PRIMARY] for p in policies]
    fig = go.Figure(go.Bar(
        y=[label(p) for p in policies], x=[s["mean"] for s in stats], orientation="h",
        marker={"color": [ACCENT[mode] if p == focus else MUTED for p in policies], "cornerradius": 4},
        error_x={"type": "data", "array": [s["sd"] for s in stats], "color": INK[mode], "thickness": 1, "width": 4},
        text=[f"{d1(s['mean'])}%" for s in stats], textposition="inside", insidetextanchor="start",
        textfont={"color": "#ffffff"},
        customdata=[[s["sd"], s["n"]] for s in stats],
        hovertemplate="%{y}<br>%{x:.1f}% on time (mean)<br>varies by scenario: ±%{customdata[0]:.1f} over "
                      "%{customdata[1]} seeds<extra></extra>"))
    return _layout(fig, mode, 60 + 46 * len(policies), "Criticals on time, % (mean of seeds)")


def per_seed_dumbbell(summary: dict, policy: str = "agents", mode: str = "dark") -> go.Figure:
    """Baseline and one variant per seed on the primary metric; the gap between them is the effect."""
    base, mine = summary["per_seed"][summary["baseline"]], summary["per_seed"][policy]
    get = lambda d, s: d.get(str(s), d.get(s))
    seeds = [s for s in summary["seeds"] if get(base, s) is not None and get(mine, s) is not None]
    names = [f"Seed {s}" for s in seeds]
    xs, ys = [], []
    for name, s in zip(names, seeds):
        xs += [get(base, s), get(mine, s), None]
        ys += [name, name, None]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line={"color": GRID[mode], "width": 2},
                             hoverinfo="skip", showlegend=False))
    for d, name, color in ((base, label(summary["baseline"]), MUTED), (mine, label(policy), ACCENT[mode])):
        fig.add_trace(go.Scatter(
            x=[get(d, s) for s in seeds], y=names, mode="markers", name=name,
            marker={"color": color, "size": 10, "line": {"color": SURFACE[mode], "width": 2}},
            hovertemplate="%{y} · " + name + ": %{x:.1f}%<extra></extra>"))
    return _layout(fig, mode, 80 + 24 * len(seeds), "Criticals on time, %")


def per_seed_bars(summary: dict, policy: str, mode: str = "dark") -> go.Figure:
    """One policy's criticals on time per seed, with its mean as a reference line (single series, one hue)."""
    values = summary["per_seed"][policy]
    get = lambda s: values.get(str(s), values.get(s))
    seeds = [s for s in summary["seeds"] if get(s) is not None]
    avg = summary["metrics"][policy][PRIMARY]["mean"]
    fig = go.Figure(go.Bar(
        y=[f"Seed {s}" for s in seeds], x=[get(s) for s in seeds], orientation="h",
        marker={"color": ACCENT[mode], "cornerradius": 4},
        text=[f"{d1(get(s))}%" for s in seeds], textposition="outside", cliponaxis=False,
        textfont={"color": INK[mode]}, hovertemplate="%{y}: %{x:.1f}% on time<extra></extra>"))
    fig.add_vline(x=avg, line={"color": INK[mode], "width": 1})
    fig.add_annotation(x=avg, y=1, yref="paper", yanchor="bottom", text=f"mean {d1(avg)}%", showarrow=False,
                       font={"color": INK[mode]})
    fig = _layout(fig, mode, 90 + 26 * len(seeds), "Criticals on time, %")
    fig.update_layout(margin={"t": 30})
    return fig


def headline_bars(items: list[dict], mode: str = "dark") -> go.Figure:
    """The overview chart: criticals on time for the headline policies, the focus one in the accent."""
    fig = go.Figure(go.Bar(
        y=[i["label"] for i in items], x=[i["mean"] for i in items], orientation="h",
        marker={"color": [ACCENT[mode] if i.get("focus") else MUTED for i in items], "cornerradius": 4},
        error_x={"type": "data", "array": [i["sd"] for i in items], "color": INK[mode], "thickness": 1, "width": 4},
        text=[f"{d1(i['mean'])}%" for i in items], textposition="inside", insidetextanchor="start",
        textfont={"color": "#ffffff"}, customdata=[[i["sd"], i["n"]] for i in items],
        hovertemplate="%{y}<br>%{x:.1f}% on time (mean)<br>varies by scenario: ±%{customdata[0]:.1f} over "
                      "%{customdata[1]} seeds<extra></extra>"))
    return _layout(fig, mode, 50 + 46 * len(items), "Criticals on time, % (mean of seeds 1-20; whisker: varies by scenario)")


def contributions_table(rows: list[dict]) -> pd.DataFrame:
    """What each part contributes: the main chain, step by step."""
    out = [{"Step": r["label"], "Criticals on time": f"{d1(r['mean'])}%",
            "Change": "" if r["change"] is None else f"{r['change']:+.1f} pts"} for r in rows]
    return pd.DataFrame(out).set_index("Step")


def effect(change: float | None, part: str, plural: bool = False) -> str:
    """'+4.1 pts (the referee currently costs points)': what taking a part away does, read from the sign."""
    if change is None:
        return "–"
    if change == 0:
        return f"{change:+.1f} pts (no difference)"
    verb = ("currently cost" if plural else "currently costs") if change > 0 else ("add" if plural else "adds")
    return f"{change:+.1f} pts ({part} {verb} points)"


def removals_html(rows: list[dict]) -> str:
    """The ablation rows as plain sentences: 'Removing the referee: +4.1 pts (the referee currently costs points)'."""
    items = "".join(f"<li><b>{escape(r['label'])}</b>: {escape(effect(r['change'], r.get('part', 'this part'), r.get('plural', False)))}"
                    f" · {d1(r['mean'])}% on time</li>" for r in rows)
    return f"<ul class='removals'>{items}</ul>"


RESOURCE_ROWS = (  # metric key, label, unit
    ("avg_response_time_min", "Response time (report → first ambulance)", " min"),
    ("als_utilization_pct", "Paramedic ambulances (ALS) busy", "%"),
    ("bls_utilization_pct", "Basic ambulances (BLS) busy", "%"),
    ("fire_utilization_pct", "Fire teams busy", "%"),
    ("idle_minutes", "Hospital idle minutes (all hospitals)", ""),
    ("overload_minutes", "Hospital overload minutes (all hospitals)", ""),
    ("er_load_balance_pct", "ER load balance (peak gap, pts)", ""),
    ("wasted_hold_minutes", "Wasted hold minutes", ""),
    ("allocation_conflicts", "Allocation conflicts", ""),
    ("escalations", "Escalations to the referee (shown apart)", ""),
)


def resource_table(items: list[dict]) -> pd.DataFrame:
    """Resource use and conflicts for the headline versions: mean over seeds, and how much it varies by scenario.
    items: [{"label", "metrics": that policy's summary metrics}]."""
    rows = []
    for key, name, unit in RESOURCE_ROWS:
        row = {"Measure": name}
        for i in items:
            s = i["metrics"].get(key)
            row[i["label"]] = "–" if not s or s["mean"] is None else \
                f"{d1(s['mean'])}{unit}" + (f" (±{d1(s['sd'])})" if s["n"] > 1 else "")
        rows.append(row)
    return pd.DataFrame(rows).set_index("Measure")
