"""Streamlit dashboard: run, reopen and watch simulations. Start with the launcher."""
from __future__ import annotations

import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from dashboard import agent_views, evaluation_page, map_view, panels, run_store  # noqa: E402
from sim import metrics  # noqa: E402
from sim.config import default_config, load_area  # noqa: E402
from coordination.llm import load_llm_config  # noqa: E402
from sim.runner import POLICIES, load_roads  # noqa: E402

POLICY_LABELS = {"greedy": "greedy baseline", "agents": "agents", "agents-nonego": "agents, no negotiation",
                 "agents-noarb": "agents, no Arbiter", "agents-rulehosp": "agents, rule hospitals"}

SPEEDS = {"1x": 1, "5x": 5, "20x": 20}
FRAME_SECONDS = 0.25

st.set_page_config(page_title="Emergency response simulator", page_icon="🚑", layout="wide")


@st.cache_resource(show_spinner="Loading road network ...")
def roads():
    return load_roads(default_config())


@st.cache_data(max_entries=8, show_spinner=False)
def load(run_id: str) -> dict:
    return run_store.load_run(run_id)


@st.cache_data(max_entries=4000, show_spinner=False)
def kpis_at(run_id: str, minute: int) -> dict:
    run = load(run_id)
    return metrics.at_minute(run["victims"], run["timeline"], run["events"], minute)


@st.cache_resource(max_entries=8, show_spinner=False)
def threads(run_id: str) -> dict:
    return agent_views.thread_index(load(run_id)["events"])


@st.cache_data(max_entries=2000, show_spinner=False)
def cards_at(run_id: str, minute: int) -> list[dict]:
    run = load(run_id)
    return agent_views.agent_cards(run["events"], run["timeline"][minute], minute)


def is_agents(run_id: str) -> bool:
    return load(run_id)["config"]["policy"] != "greedy"


def init_state() -> None:
    defaults = {"run_id": None, "play_t": 0.0, "playing": False, "last_tick": time.monotonic(),
                "minute_slider": 0}
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)
    if st.session_state.run_id is None:
        runs = run_store.list_runs()
        st.session_state.run_id = runs[0] if runs else None


def open_run(run_id: str) -> None:
    st.session_state.run_id = run_id
    st.session_state.play_t = 0.0
    st.session_state.minute_slider = 0
    st.session_state.playing = False


def start_run(seed: int, policy: str, llm_mode: str | None = None, replay_from: str | None = None) -> None:
    bar = st.sidebar.progress(0.0, text=f"Simulating seed {seed} ...")

    def progress(done: int, total: int) -> None:
        if done % 10 == 0 or done == total:
            bar.progress(done / total, text=f"Simulating seed {seed}: minute {done}/{total}")

    try:
        run_id = run_store.run_and_save(seed, policy, progress, roads=roads(), llm_mode=llm_mode,
                                        replay_from=replay_from)
    except ValueError as exc:
        bar.empty()
        st.sidebar.error(str(exc))
        return
    bar.empty()
    open_run(run_id)
    st.session_state.playing = True
    st.session_state.last_tick = time.monotonic()


def sidebar() -> None:
    sb = st.sidebar
    sb.header("🚑 Emergency response")
    seed = sb.number_input("Seed", min_value=0, max_value=1_000_000, value=42, step=1)
    policy = sb.selectbox("Policy", POLICIES, format_func=lambda p: POLICY_LABELS.get(p, p))
    llm_mode, replay_from = (None, None) if policy == "greedy" else llm_controls(sb, policy)
    if llm_mode == "replay" and not replay_from:
        sb.caption("No recorded runs of this policy to replay yet.")
    if sb.button("Run simulation", type="primary", width="stretch",
                 disabled=llm_mode == "replay" and not replay_from):
        if replay_from:
            seed = run_store.run_config(replay_from)["seed"]
        start_run(int(seed), policy, llm_mode, replay_from)
    if sb.button("New scenario", width="stretch", help="Random seed, then run it", disabled=llm_mode == "replay"):
        start_run(secrets.randbelow(100_000), policy, llm_mode)

    runs = run_store.list_runs()
    if runs:
        current = st.session_state.run_id if st.session_state.run_id in runs else runs[0]
        choice = sb.selectbox("Past runs", runs, index=runs.index(current))
        if choice != st.session_state.run_id:
            open_run(choice)

    compare_controls(sb)

    sb.divider()
    sb.subheader("Playback")
    label = "⏸ Pause" if st.session_state.playing else "▶ Play"
    if sb.button(label, width="stretch", disabled=st.session_state.run_id is None):
        st.session_state.playing = not st.session_state.playing
        st.session_state.last_tick = time.monotonic()
        st.rerun()
    sb.radio("Speed (simulated minutes per second)", list(SPEEDS), horizontal=True, key="speed")
    sb.caption("Drag the time slider above the map to jump to any minute.")


def compare_controls(sb) -> None:
    """Compare with baseline (DESIGN 13.1): the greedy run of the same seed and scenario, side by side."""
    run_id = st.session_state.run_id
    if run_id is None or not is_agents(run_id):
        st.session_state.baseline_id = None
        return
    on = sb.toggle("Compare with baseline", key="compare", help="Greedy baseline on the same seed, side by side")
    base = run_store.baseline_for(run_id) if on else None
    if on and base is None:
        seed = load(run_id)["config"]["seed"]
        sb.caption(f"No baseline run for seed {seed} yet.")
        if sb.button(f"Run baseline for seed {seed}", width="stretch"):
            with st.spinner("Running the greedy baseline ..."):
                run_store.run_and_save(seed, "greedy", roads=roads())
            st.rerun()
    st.session_state.baseline_id = base


def llm_controls(sb, policy: str) -> tuple[str, str | None]:
    """Live or replay (DESIGN 13.1): rules only, live model calls (recorded), or replay a recording."""
    cfg = load_llm_config()
    live_ok = bool(cfg.get("model")) and bool(os.environ.get("OPENAI_API_KEY"))
    modes = {"off": "Rules only (no LLM)", "live": "Live LLM (recorded)", "replay": "Replay a recorded run"}
    mode = sb.radio("Agents' LLM", list(modes), format_func=modes.get, key="llm_mode")
    if mode == "live" and not live_ok:
        sb.warning("Live needs a pinned `model` in config/llm.json and OPENAI_API_KEY. Using rules only.")
        return "off", None
    if mode == "replay":
        runs = run_store.replayable_runs(policy)
        return "replay", sb.selectbox("Recorded run", runs) if runs else None
    return mode, None


def on_slider() -> None:
    st.session_state.play_t = float(st.session_state.minute_slider)


def advance(horizon: int) -> None:
    now = time.monotonic()
    if st.session_state.playing:
        dt = min(1.0, now - st.session_state.last_tick)
        st.session_state.play_t = min(horizon - 1.0, st.session_state.play_t + SPEEDS[st.session_state.speed] * dt)
        st.session_state.minute_slider = int(st.session_state.play_t)
    st.session_state.last_tick = now


def view_state(run: dict, zoom: float = 12.6) -> dict:
    """Centred on the hospitals, zoomed so all three and their labels fit the map column."""
    hs = run["timeline"][0]["hospitals"]
    lats, lons = [h["lat"] for h in hs], [h["lon"] for h in hs]
    return {"latitude": (min(lats) + max(lats)) / 2, "longitude": (min(lons) + max(lons)) / 2,
            "zoom": zoom, "pitch": 0}


VIEWS = ("Resources & events", "Agent activity", "Negotiation history")


def playback_body(run_id: str, base_id: str | None) -> None:
    run = load(run_id)
    horizon = len(run["timeline"]) if base_id is None else min(len(run["timeline"]), len(load(base_id)["timeline"]))
    advance(horizon)
    st.slider("Minute", 0, horizon - 1, key="minute_slider", on_change=on_slider)
    t = st.session_state.play_t
    minute = int(t)

    if base_id is None:
        single_map(run_id, run, t, minute, horizon)
    else:
        side_by_side(run_id, base_id, t, minute, horizon)

    views = VIEWS if is_agents(run_id) else VIEWS[:1]
    view = st.segmented_control("View", views, default=views[0], key="view", required=True,
                                label_visibility="collapsed") if len(views) > 1 else views[0]
    if view == "Agent activity":
        agent_activity(run_id, minute)
    elif view == "Negotiation history":
        negotiation_history(run_id, minute)
    elif base_id is None:
        resources(run_id, minute, "main")
    else:
        left, right = st.columns(2)
        with left:
            st.markdown(f"#### Greedy baseline · `{base_id}`")
            resources(base_id, minute, "base", stacked=True)
        with right:
            st.markdown(f"#### {POLICY_LABELS.get(run['config']['policy'], 'agents')} · `{run_id}`")
            resources(run_id, minute, "agents", stacked=True)

    if st.session_state.playing and st.session_state.play_t >= horizon - 1:
        st.session_state.playing = False
        st.rerun(scope="app")


def single_map(run_id: str, run: dict, t: float, minute: int, horizon: int) -> None:
    left, right = st.columns([4, 1])
    with left:
        st.pydeck_chart(map_view.build_deck(run, t, view_state(run)), height=560, key="map")
        st.markdown(map_view.legend_html(), unsafe_allow_html=True)
    with right:
        st.markdown(f"#### Minute {minute} of {horizon - 1}")
        for name, value in panels.kpis(kpis_at(run_id, minute)):
            st.metric(name, value)


def side_by_side(run_id: str, base_id: str, t: float, minute: int, horizon: int) -> None:
    run, base = load(run_id), load(base_id)
    left, right = st.columns(2)
    with left:
        st.markdown(f"##### Greedy baseline · minute {minute} of {horizon - 1}")
        st.pydeck_chart(map_view.build_deck(base, t, view_state(base, 11.9)), height=460, key="map_base")
    with right:
        st.markdown(f"##### {POLICY_LABELS.get(run['config']['policy'], 'agents').capitalize()} · same seed")
        st.pydeck_chart(map_view.build_deck(run, t, view_state(run, 11.9)), height=460, key="map_agents")
    st.markdown(map_view.legend_html(), unsafe_allow_html=True)
    st.markdown("##### Baseline vs agents at this minute")
    st.dataframe(panels.kpi_comparison(kpis_at(base_id, minute), kpis_at(run_id, minute)), width="stretch")


def resources(run_id: str, minute: int, key: str, stacked: bool = False) -> None:
    run = load(run_id)
    state = run["timeline"][minute]
    if stacked:
        st.dataframe(panels.unit_table(state), width="stretch")
        st.dataframe(panels.hospital_table(state), width="stretch")
        st.dataframe(panels.per_hospital_kpis(kpis_at(run_id, minute)), width="stretch")
    else:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.markdown("##### Units")
            st.dataframe(panels.unit_table(state), width="stretch")
            st.dataframe(panels.unit_states(state), width="stretch", height=250)
        with c2:
            st.markdown("##### Hospitals")
            st.dataframe(panels.hospital_table(state), width="stretch")
            st.dataframe(panels.per_hospital_kpis(kpis_at(run_id, minute)), width="stretch")
    head, toggle = st.columns([3, 1])
    head.markdown("##### Live events")
    show_agents = is_agents(run_id) and toggle.toggle("Agent messages", value=False, key=f"show_agent_events_{key}")
    feed = panels.event_feed(run["events"], minute, agents=show_agents)
    st.markdown("\n".join(f"- **{et:>3}** {'🟢 ' if et == minute else ''}{text}" for et, text in feed)
                or "_No events yet._")


def agent_activity(run_id: str, minute: int) -> None:
    """DESIGN 13.2: each agent's status, recent messages, pending requests and last decision."""
    cards = cards_at(run_id, minute)
    for row in range(0, len(cards), 2):
        for col, card in zip(st.columns(2), cards[row:row + 2]):
            with col.container(border=True):
                st.markdown(f"**{card['name']}**  \n{agent_status_line(card)}")
                st.caption(f"Last decision: {agent_views.decision_text(card['decision'])}")
                if card["pending"]:
                    st.markdown("Pending requests")
                    st.dataframe(card["pending"], width="stretch", hide_index=True)
                if card["recent"]:
                    st.dataframe(card["recent"], width="stretch", hide_index=True, height=213,
                                 column_order=("Min", "With", "Message", "Details", "Reason", "By"))
                else:
                    st.caption("No messages yet.")


def agent_status_line(card: dict) -> str:
    dot = "🟢" if card["status"] == "idle" else "🟠"
    line = f"{dot} {card['status']} · {card['sent']} sent, {card['received']} received"
    if "mood" in card:
        line += f"  \nmood **{card['mood']}** · ER {card['er']} · holds {card['holds']}"
    return line


def negotiation_history(run_id: str, minute: int) -> None:
    """DESIGN 13.2: threads with rounds, offers, counters, awards, declines and Arbiter decisions."""
    index = threads(run_id)
    neg = kpis_at(run_id, minute)["negotiation"]
    stats = st.columns(6)
    for col, (name, key) in zip(stats, (("Threads", "threads"), ("Rounds", "rounds"), ("Counters", "counters"),
                                        ("Deals without Arbiter", "deals_without_arbiter"),
                                        ("Escalations", "escalations"), ("Arbiter decisions", "arbiter_decisions"))):
        col.metric(name, neg.get(key, 0))
    shares = neg.get("decisions_by") or {}
    if shares:
        st.caption(f"Agent decisions by rule {shares.get('rule', 0)}%, LLM {shares.get('llm', 0)}%, "
                   f"fallback {shares.get('fallback', 0)}%")

    protocols = sorted({th["protocol"] for th in index.values()})
    incidents = sorted({m["incident_id"] for th in index.values() for m in th["messages"] if m.get("incident_id")})
    f1, f2 = st.columns([3, 1])
    chosen = f1.multiselect("Protocols", protocols, default=[p for p in protocols if p != "Pre-alert"],
                            key="neg_protocols")
    incident = f2.selectbox("Incident", ["All", *incidents], key="neg_incident")
    rows = agent_views.thread_rows(index, minute, chosen or None, None if incident == "All" else incident)
    st.dataframe(rows, width="stretch", hide_index=True, height=280)

    order = sorted(index.values(), key=lambda th: (th["messages"][0]["t"], th["id"]))
    options = ["latest", *[th["id"] for th in order]]
    names = {th["id"]: f"{th['id']} · {th['protocol']} · min {th['messages'][0]['t']}"
             + (f" · {th['messages'][0]['incident_id']}" if th["messages"][0].get("incident_id") else "")
             for th in order}
    pick = st.selectbox("Thread", options, key="neg_thread",
                        format_func=lambda o: "Most recently active (follows playback)" if o == "latest" else names[o])
    th_id = (rows[0]["Thread"] if rows else None) if pick == "latest" else pick
    if th_id is None:
        st.caption("No negotiation yet at this minute.")
        return
    detail = agent_views.thread_detail(index[th_id], minute)
    if not detail:
        st.info(f"{th_id} starts at minute {index[th_id]['messages'][0]['t']}.")
        return
    st.markdown(f"**{names[th_id]}** · {agent_views.thread_row(index[th_id], minute)['Outcome']}")
    st.dataframe(detail, width="stretch", hide_index=True)


def playback() -> None:
    init_state()
    st.session_state.setdefault("speed", "5x")
    sidebar()
    area = load_area()["name"]
    run_id = st.session_state.run_id
    if run_id is None:
        st.title("Emergency response simulator")
        st.info(f"Area: {area}. Pick a seed and press **Run simulation** in the sidebar.")
        return
    cfg = load(run_id)["config"]
    st.markdown(f"### {area} · seed {cfg['seed']} · {cfg['policy']} · `{run_id}`")
    fragment = st.fragment(run_every=FRAME_SECONDS if st.session_state.playing else None)(playback_body)
    fragment(run_id, st.session_state.get("baseline_id"))


def evaluation() -> None:
    evaluation_page.render(roads(), PLAYBACK)


PLAYBACK = st.Page(playback, title="Playback", icon="🗺️", default=True)
st.navigation([PLAYBACK, st.Page(evaluation, title="Evaluation", icon="📊", url_path="evaluation")]).run()
