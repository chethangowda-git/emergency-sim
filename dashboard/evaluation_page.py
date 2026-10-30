"""The Evaluation page (DESIGN 13.1 "Run evaluation"): settings, one button, results table and charts."""
from __future__ import annotations

import os

import streamlit as st

from coordination.llm import load_llm_config
from dashboard import eval_views
from evaluation import runner as ev
from evaluation.summary import PRIMARY, claim
from policies.registry import POLICY_NAMES

AGENT_POLICIES = [p for p in POLICY_NAMES if p != "greedy"]
CALLS_PER_RUN = 100  # DESIGN 12: roughly 60 to 150 LLM calls per agent run


@st.cache_data(max_entries=16, show_spinner=False)
def load(eval_id: str) -> dict:
    return ev.load_evaluation(eval_id)


def theme() -> str:
    return "light" if getattr(st.context.theme, "type", "dark") == "light" else "dark"


def settings_sidebar(roads) -> None:
    sb = st.sidebar
    sb.header("📊 Evaluation")
    c1, c2 = sb.columns(2)
    first = c1.number_input("First seed", 0, 1_000_000, 1, key="eval_first")
    last = c2.number_input("Last seed", 0, 1_000_000, 20, key="eval_last")
    policies = sb.multiselect("Agent variants", AGENT_POLICIES, default=AGENT_POLICIES, key="eval_policies",
                              format_func=eval_views.label, help="The greedy baseline always runs")
    modes = {"off": "Rules only (no LLM)", "live": "Live LLM (recorded)", "replay": "Replay a recorded evaluation"}
    mode = sb.radio("Agents' LLM", list(modes), format_func=modes.get, key="eval_llm")
    repeats, replay_from = 1, None
    if mode == "live":
        cfg = load_llm_config()
        if not (cfg.get("model") and os.environ.get("OPENAI_API_KEY")):
            sb.warning("Live needs a pinned `model` in config/llm.json and OPENAI_API_KEY.")
            mode = None
        repeats = sb.number_input("Repeats per seed", 1, 5, ev.LIVE_REPEATS, key="eval_repeats")
    elif mode == "replay":
        recorded = [e for e in ev.list_evaluations() if load(e)["settings"]["llm_mode"] in ("live", "replay")]
        replay_from = sb.selectbox("Recorded evaluation", recorded) if recorded else None
        if replay_from:
            s = load(replay_from)["settings"]
            first, last, policies, repeats = s["seeds"][0], s["seeds"][-1], s["policies"][1:], s["repeats"]
            sb.caption(f"Replays seeds {first}–{last}, {repeats} repeats, exactly as recorded.")
        else:
            sb.caption("No live evaluation has been recorded yet.")
            mode = None

    ready = mode is not None and last >= first and policies
    settings = None
    if ready:
        settings = ev.EvalSettings(seeds=tuple(range(int(first), int(last) + 1)), policies=tuple(policies),
                                   llm_mode=mode, repeats=int(repeats), replay_from=replay_from)
        plan = settings.plan()
        agent_runs = sum(1 for _, p, _ in plan if p != "greedy")
        calls = f", about {agent_runs * CALLS_PER_RUN:,} LLM calls" if mode == "live" else ""
        sb.caption(f"{len(plan)} runs{calls}. Rules-only runs are deterministic, so each seed runs once.")
    if sb.button("Run evaluation", type="primary", width="stretch", disabled=not ready):
        start(settings, roads)

    done = ev.list_evaluations()
    if done:
        current = st.session_state.get("eval_id") if st.session_state.get("eval_id") in done else done[0]
        st.session_state.eval_id = sb.selectbox("Past evaluations", done, index=done.index(current))


def start(settings: ev.EvalSettings, roads) -> None:
    bar = st.sidebar.progress(0.0, text="Starting the evaluation ...")
    out = ev.run_evaluation(settings, lambda d, n, what: bar.progress(d / n, text=f"Run {d}/{n}: {what}"),
                            roads=roads)
    bar.empty()
    st.session_state.eval_id = out.name


def results(eval_id: str, playback_page) -> None:
    data = load(eval_id)
    summary, s = data["summary"], data["settings"]
    mode = {"off": "rules only", "live": "live LLM", "replay": "replayed LLM"}[s["llm_mode"]]
    st.markdown(f"### Evaluation `{eval_id}` · seeds {s['seeds'][0]}–{s['seeds'][-1]} · {mode}")
    sentence = claim(summary)
    if sentence:
        st.success(sentence)
    variants = list(summary["versus_baseline"])
    if not variants:
        st.info("Only the baseline ran in this evaluation.")
        return
    focus = "agents" if "agents" in variants else variants[0]
    headline(summary, focus)

    st.markdown("##### Criticals on time by policy")
    st.caption("Mean over seeds; the whisker is ±1 standard deviation across seeds.")
    st.plotly_chart(eval_views.primary_bars(summary, theme(), focus), theme=None, width="stretch")
    st.dataframe(eval_views.wins_table(summary), width="stretch")

    st.markdown("##### Against the baseline")
    pick = st.selectbox("Variant", variants, index=variants.index(focus), format_func=eval_views.label,
                        key="eval_variant")
    left, right = st.columns([3, 2])
    with left:
        st.dataframe(eval_views.versus_table(summary, pick), width="stretch", height=670)
    with right:
        st.caption("Criticals on time per seed")
        st.plotly_chart(eval_views.per_seed_dumbbell(summary, pick, theme()), theme=None, width="stretch")

    with st.expander("All policies, every metric (mean ± sd across seeds)"):
        st.dataframe(eval_views.policy_table(summary), width="stretch")
    with st.expander("Criticals on time per seed (table)"):
        st.dataframe(eval_views.per_seed_table(summary), width="stretch")
    open_in_playback(data, playback_page)


def headline(summary: dict, focus: str) -> None:
    """KPI row: the variant against the baseline on the numbers the claim rests on."""
    base, mine = summary["metrics"][summary["baseline"]], summary["metrics"][focus]
    wins = summary["versus_baseline"][focus]["wins"]
    cols = st.columns(4)
    for col, (name, key, suffix, inverse) in zip(cols[:3], (
            ("Criticals on time", PRIMARY, "%", False), ("Avg report → care", "avg_report_to_care_min", " min", True),
            ("Victims delivered", "victims_delivered", "", False))):
        a, b = mine[key]["mean"], base[key]["mean"]
        col.metric(f"{name} ({eval_views.label(focus).lower()})", f"{a}{suffix}",
                   delta=None if a is None or b is None
                   else f"{a - b:+.2f}{' pts' if suffix == '%' else suffix} vs baseline",
                   delta_color="inverse" if inverse else "normal")
    cols[3].metric("Seeds won", f"{wins['wins']} of {wins['seeds']}",
                   delta=f"{plural(wins['ties'], 'tie')}, {plural(wins['losses'], 'loss')}", delta_color="off")


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "es" if word.endswith("s") else "s")


def open_in_playback(data: dict, playback_page) -> None:
    """Rebuild one evaluated run in full (re-simulated, or replayed for LLM runs) and play it."""
    st.markdown("##### Watch one seed")
    s = data["settings"]
    c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
    seed = c1.selectbox("Seed", s["seeds"], key="eval_open_seed")
    policy = c2.selectbox("Policy", s["policies"], format_func=eval_views.label, key="eval_open_policy")
    reps = 1 if policy == "greedy" or s["llm_mode"] == "off" else s["repeats"]
    repeat = c3.selectbox("Repeat", list(range(1, reps + 1)), key="eval_open_repeat")
    c4.markdown("&nbsp;")
    if c4.button("Open in playback", width="stretch"):
        with st.spinner("Rebuilding the run ..."):
            out = ev.rebuild_run(data["eval_id"], int(seed), policy, int(repeat))
        st.session_state.update(run_id=out.name, play_t=0.0, minute_slider=0, playing=False)
        st.switch_page(playback_page)


def render(roads, playback_page) -> None:
    settings_sidebar(roads)
    eval_id = st.session_state.get("eval_id")
    if eval_id is None:
        st.title("Evaluation")
        st.info("Seeds 1 to 20 with the baseline, the agents and the three ablations (DESIGN 11.2, 11.3). "
                "Press **Run evaluation** in the sidebar.")
        return
    results(eval_id, playback_page)
