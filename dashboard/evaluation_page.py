"""The Evaluation page (DESIGN 13.1 "Run evaluation"): settings, one button, results table and charts."""
from __future__ import annotations

import os

import streamlit as st

from coordination.llm import load_llm_config
from dashboard import eval_catalog, eval_views
from evaluation import runner as ev
from evaluation.summary import PRIMARY, claim
from policies.registry import POLICY_NAMES

AGENT_POLICIES = [p for p in POLICY_NAMES if p != "greedy"]
CALLS_PER_RUN = 250  # DESIGN 12.5: measured 150 to 250 LLM calls per 12-incident agent run; the upper end


@st.cache_data(max_entries=16, show_spinner=False)
def load(eval_id: str) -> dict:
    return ev.load_evaluation(eval_id)


def theme() -> str:
    return "light" if getattr(st.context.theme, "type", "dark") == "light" else "dark"


def run_section(roads) -> None:
    """Run a new evaluation (DESIGN 13.1), collapsed at the bottom of the page."""
    with st.expander("Run a new evaluation"):
        c1, c2, c3, c4 = st.columns([1, 1, 3, 2])
        first = c1.number_input("First seed", 0, 1_000_000, 1, key="eval_first")
        last = c2.number_input("Last seed", 0, 1_000_000, 20, key="eval_last")
        policies = c3.multiselect("Agent variants", AGENT_POLICIES, default=AGENT_POLICIES, key="eval_policies",
                                  format_func=eval_views.label, help="The greedy baseline always runs")
        modes = {"off": "Rules only (no LLM)", "live": "Live LLM (recorded)", "replay": "Replay a recorded evaluation"}
        mode = c4.radio("Agents' LLM", list(modes), format_func=modes.get, key="eval_llm", horizontal=True)
        repeats, replay_from = 1, None
        if mode == "live":
            cfg = load_llm_config()
            if not (cfg.get("model") and os.environ.get("OPENAI_API_KEY")):
                st.warning("Live needs a pinned `model` in config/llm.json and OPENAI_API_KEY.")
                mode = None
            repeats = st.number_input("Repeats per seed", 1, 5, ev.LIVE_REPEATS, key="eval_repeats")
        elif mode == "replay":
            recorded = [e for e in ev.list_evaluations() if load(e)["settings"]["llm_mode"] in ("live", "replay")]
            replay_from = st.selectbox("Recorded evaluation", recorded, format_func=name_of) if recorded else None
            if replay_from:
                s = load(replay_from)["settings"]
                first, last, policies, repeats = s["seeds"][0], s["seeds"][-1], s["policies"][1:], s["repeats"]
                st.caption(f"Replays seeds {first}–{last}, {repeats} repeats, exactly as recorded.")
            else:
                st.caption("No live evaluation has been recorded yet.")
                mode = None
        ready = mode is not None and last >= first and policies
        settings = None
        if ready:
            settings = ev.EvalSettings(seeds=tuple(range(int(first), int(last) + 1)), policies=tuple(policies),
                                       llm_mode=mode, repeats=int(repeats), replay_from=replay_from)
            plan = settings.plan()
            agent_runs = sum(1 for _, p, _ in plan if p != "greedy")
            calls = f", about {agent_runs * CALLS_PER_RUN:,} LLM calls" if mode == "live" else ""
            st.caption(f"{len(plan)} runs{calls}. Rules-only runs are deterministic, so each seed runs once.")
        if st.button("Run evaluation", type="primary", disabled=not ready):
            start(settings, roads)
            st.rerun()


def name_of(eval_id: str) -> str:
    return eval_catalog.label(eval_id, load(eval_id)["settings"], eval_catalog.load_catalog())


def start(settings: ev.EvalSettings, roads) -> None:
    bar = st.progress(0.0, text="Starting the evaluation ...")
    out = ev.run_evaluation(settings, lambda d, n, what: bar.progress(d / n, text=f"Run {d}/{n}: {what}"),
                            roads=roads, workers=4 if settings.llm_mode == "live" else 1)
    bar.empty()
    st.session_state.eval_pick_next = out.name   # applied before the picker is drawn on the next run


def results(eval_id: str, playback_page) -> None:
    data = load(eval_id)
    summary = data["summary"]
    st.markdown(f"#### {name_of(eval_id)} · `{eval_id}`")
    sentence = claim(summary)
    if sentence:
        st.success(sentence)
    failed = summary.get("failed_runs") or []
    if failed:
        st.warning(f"{len(failed)} run(s) kept hitting API errors and are left out: "
                   + ", ".join(f["run_id"] for f in failed))
    variants = list(summary["versus_baseline"])
    if not variants:
        baseline_only(data, playback_page)
        return
    focus = "agents" if "agents" in variants else variants[0]
    headline(summary, focus)

    st.markdown("##### Criticals on time by policy")
    st.caption("Mean over seeds; the whisker shows how much it varies by scenario (±1 standard deviation).")
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

    with st.expander("All policies, every metric (mean ± how much it varies by scenario)"):
        st.dataframe(eval_views.policy_table(summary), width="stretch")
    with st.expander("Criticals on time per seed (table)"):
        st.dataframe(eval_views.per_seed_table(summary), width="stretch")
    open_in_playback(data, playback_page)


def baseline_only(data: dict, playback_page) -> None:
    """An evaluation of the baseline alone: its numbers per seed, ready for agents to be compared against."""
    summary = data["summary"]
    base = summary["baseline"]
    m = summary["metrics"][base]
    st.caption("Only the greedy baseline ran in this evaluation; add agent variants to compare against it.")
    cols = st.columns(4)
    for col, (name, key, suffix) in zip(cols, (("Criticals on time", PRIMARY, "%"),
                                               ("Avg report → care", "avg_report_to_care_min", " min"),
                                               ("Deadlines missed", "deadlines_missed", ""),
                                               ("Victims delivered", "victims_delivered", ""))):
        col.metric(f"{name} (mean)", f"{eval_views.d1(m[key]['mean'])}{suffix}",
                   delta=f"range {m[key]['min']}–{m[key]['max']}{suffix}", delta_color="off", delta_arrow="off")
    left, right = st.columns([3, 2])
    with left:
        st.markdown("##### Criticals on time per seed")
        st.plotly_chart(eval_views.per_seed_bars(summary, base, theme()), theme=None, width="stretch")
    with right:
        st.markdown("##### Every metric (mean ± how much it varies by scenario)")
        st.dataframe(eval_views.policy_table(summary), width="stretch", height=670)
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
        col.metric(f"{name} ({eval_views.label(focus).lower()})", f"{eval_views.d1(a)}{suffix}",
                   delta=None if a is None or b is None
                   else f"{a - b:+.1f}{' pts' if suffix == '%' else suffix} vs baseline",
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
        try:
            with st.spinner("Rebuilding the run ..."):
                out = ev.rebuild_run(data["eval_id"], int(seed), policy, int(repeat))
        except FileNotFoundError:
            st.error("This evaluation's run folders are not on this machine (only its summary is committed). "
                     "Run it again below; rules-only runs are deterministic.")
            return
        live = st.session_state.get("live")
        if live is not None and not live.done:
            live.stop()
        st.session_state.update(live=None, spawn_mode=False, run_id=out.name, play_t=0.0, playing=False)
        st.session_state.setdefault("opened_from", {})[out.name] = \
            f"evals/{data['eval_id']}/runs/{ev.run_id_for(int(seed), policy, int(repeat))}"
        st.switch_page(playback_page)


OVERVIEW = "overview"


def overview() -> None:
    """The final results on one screen: headline numbers, chart, contributions, how we got here."""
    catalog = eval_catalog.load_catalog()
    items = eval_catalog.headline(catalog, load)
    if not items:
        st.info("The final evaluations are not on this machine yet. Run one below, or turn on "
                "**Show all evaluations**.")
        return
    d1 = eval_views.d1
    cards = "".join(
        f"<div class='kpi{' focus' if i.get('focus') else ''}'><div class='kpi-t'>{i['label']}</div>"
        f"<div class='kpi-v'>{d1(i['mean'])}%</div><div class='kpi-d'>criticals on time · varies by scenario: "
        f"±{d1(i['sd'])}" + (f" · between repeats: ±{d1(i['repeat_sd'])}" if i.get("repeat_sd") is not None else "")
        + "</div></div>" for i in items)
    st.markdown(f"<div class='headline'>{cards}</div>", unsafe_allow_html=True)
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown("##### Criticals on time (seeds 1-20, 12 incidents)")
        st.plotly_chart(eval_views.headline_bars(items, theme()), theme=None, width="stretch")
    with right:
        st.markdown("##### What each part contributes")
        rows = eval_catalog.contributions(catalog, load)
        st.dataframe(eval_views.contributions_table([r for r in rows if not r["side"]]), width="stretch")
        st.markdown("**Taking one part away**", help="Each against the full system it was taken from")
        st.markdown(eval_views.removals_html([r for r in rows if r["side"]]), unsafe_allow_html=True)
    st.markdown("##### Resource use and conflicts")
    st.caption("Mean per run over seeds 1-20; (±) is how much it varies by scenario. Allocation conflicts: times a "
               "planned unit or bed could not be given as planned (refused actions, ambulances arriving to no free "
               "slot, reservations taken by walk-ins, awards a hospital declined), counted the same way for every "
               "version. Escalations happen only with agents, so they are shown apart.")
    st.dataframe(eval_views.resource_table(eval_catalog.resources(catalog, load)), width="stretch")
    st.markdown("##### How we got here")
    steps = "".join(f"<div class='step'><b>{s['title']}</b> · <span class='num'>"
                    f"{'–' if s['mean'] is None else d1(s['mean']) + '%'}</span><br>{s['text']}</div>"
                    for s in eval_catalog.timeline(catalog, load))
    st.markdown(f"<div class='steps'>{steps}</div>", unsafe_allow_html=True)


def picker() -> str:
    catalog = eval_catalog.load_catalog()
    if "eval_pick_next" in st.session_state:
        st.session_state.update(eval_pick=st.session_state.pop("eval_pick_next"), eval_show_all=True)
    head, pick, toggle = st.columns([2.2, 2.6, 1], vertical_alignment="bottom")
    head.markdown("### 📊 Evaluation")
    show_all = toggle.toggle("Show all evaluations", key="eval_show_all",
                             help="Also list development, replayed and outdated evaluations")
    options = [OVERVIEW, *eval_catalog.visible(ev.list_evaluations(), catalog, show_all)]
    if st.session_state.get("eval_pick") not in options:
        st.session_state.eval_pick = OVERVIEW
    return pick.selectbox("Evaluation", options, key="eval_pick",
                          format_func=lambda e: "Final results (overview)" if e == OVERVIEW else name_of(e))


def render(roads, playback_page) -> None:
    choice = picker()
    if choice == OVERVIEW:
        overview()
    else:
        results(choice, playback_page)
    run_section(roads)
