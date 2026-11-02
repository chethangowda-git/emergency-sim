"""Streamlit dashboard: run simulations live, reopen and watch saved runs. Start with the launcher."""
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
from sim.generator import SPAWN_MAX_VICTIMS, SPAWN_TYPES  # noqa: E402
from sim.live import MINUTES_PER_SECOND_1X  # noqa: E402
from sim.runner import load_roads  # noqa: E402

POLICY_LABELS = {"greedy": "greedy baseline", "agents": "agents", "agents-nonego": "agents, no negotiation",
                 "agents-noarb": "agents, no Arbiter", "agents-rulehosp": "agents, rule hospitals"}
PLAYBACK_POLICIES = ("greedy", "agents")  # the ablations live on the Evaluation page

SPEEDS = {"0.5x": 0.5, "1x": 1.0, "2x": 2.0, "4x": 4.0}
INCIDENT_CHOICES = (3, 4, 6, 12)
DEFAULT_INCIDENTS = 4
EVALUATED_INCIDENTS = 12  # DESIGN 3.11; the Evaluation page always uses it
FRAME_SECONDS = 0.25

st.set_page_config(page_title="Emergency response simulator", page_icon="🚑", layout="wide")


@st.cache_resource(show_spinner="Loading road network ...")
def roads():
    return load_roads(default_config())


@st.cache_resource(show_spinner=False)
def spawn_points() -> list[dict]:
    r = roads()
    return [{"node": n, "lat": lat, "lon": lon, "label": "Click to place the incident here"}
            for n in r.sorted_nodes() for lat, lon in [r.latlon(n)]]


@st.cache_data(max_entries=8, show_spinner=False)
def load(run_id: str) -> dict:
    return run_store.load_run(run_id)


# Saved runs are keyed by run id; a live run by its key, and its minutes never change once published.

@st.cache_data(max_entries=4000, show_spinner=False)
def kpis_at(key: str, minute: int, _run: dict) -> dict:
    return metrics.at_minute(_run["victims"], _run["timeline"], _run["events"], minute)


@st.cache_resource(max_entries=8, show_spinner=False)
def threads(key: str, n_events: int, _events: list[dict]) -> dict:
    return agent_views.thread_index(_events)


@st.cache_data(max_entries=2000, show_spinner=False)
def cards_at(key: str, minute: int, _run: dict) -> list[dict]:
    return agent_views.agent_cards(_run["events"], _run["timeline"][minute], minute)


def is_agents(run: dict) -> bool:
    return run["config"]["policy"] != "greedy"


def rate() -> float:
    return SPEEDS[st.session_state.speed] * MINUTES_PER_SECOND_1X


# ----- state: a live run, or a saved run being replayed -----

def init_state() -> None:
    defaults = {"run_id": None, "live": None, "play_t": 0.0, "playing": False, "last_tick": time.monotonic(),
                "speed": "1x", "seed": 42, "spawn_mode": False, "notice": None}
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)
    if st.session_state.run_id is None and st.session_state.live is None:
        runs = run_store.list_runs(policies=PLAYBACK_POLICIES)
        st.session_state.run_id = runs[0] if runs else None


def live():
    return st.session_state.live


def stop_live() -> None:
    lr = st.session_state.live
    if lr is not None and not lr.done:
        lr.stop()
    st.session_state.live = None
    st.session_state.spawn_mode = False


def open_run(run_id: str) -> None:
    """A saved run replays from minute 0, paused until Resume."""
    stop_live()
    st.session_state.update(run_id=run_id, play_t=0.0, playing=False)


def start_live(seed: int, policy: str, llm_mode: str | None, replay_from: str | None, n_incidents: int,
               paused: bool) -> None:
    stop_live()
    try:
        lr = run_store.start_live(seed, policy, paused=paused, rate=rate(), roads=roads(), llm_mode=llm_mode,
                                  replay_from=replay_from, n_incidents=n_incidents)
    except ValueError as exc:
        st.session_state.notice = ("error", str(exc))
        return
    st.session_state.update(live=lr, run_id=None, playing=False)


def finish_live() -> None:
    """The live run has been saved: keep showing its last minute, now as a saved run."""
    lr = st.session_state.live
    st.session_state.update(live=None, run_id=lr.run_id, play_t=float(lr.prep.world.cfg.horizon - 1),
                            playing=False, spawn_mode=False)
    st.session_state.notice = ("success", f"Run finished and saved as `{lr.run_id}`.")


def current() -> tuple[str, dict, float] | None:
    """(cache key, run, minute to draw) for whatever is on screen."""
    lr = live()
    if lr is not None:
        view = lr.view()
        return None if view is None else (f"{lr.key}:{len(view['timeline'])}", view, lr.clock())
    if st.session_state.run_id is None:
        return None
    return st.session_state.run_id, load(st.session_state.run_id), st.session_state.play_t


def ticking() -> bool:
    lr = live()
    return lr.ticking() if lr is not None else st.session_state.playing


# ----- sidebar -----

def sidebar() -> None:
    sb = st.sidebar
    sb.header("🚑 Emergency response")
    sb.number_input("Seed", min_value=0, max_value=1_000_000, step=1, key="seed")
    st.session_state.setdefault("n_incidents", DEFAULT_INCIDENTS)
    n_incidents = sb.selectbox("Incidents per scenario", INCIDENT_CHOICES, key="n_incidents",
                               help=f"{EVALUATED_INCIDENTS} is the evaluated setting (DESIGN 3.11). Fewer incidents "
                                    "are easier to follow; one of them is always the surge (6 to 10 victims).")
    policy = sb.selectbox("Policy", PLAYBACK_POLICIES, format_func=lambda p: POLICY_LABELS[p].capitalize(),
                          key="policy")
    llm_mode, replay_from = (None, None) if policy == "greedy" else llm_controls(sb, policy)
    if llm_mode == "replay" and not replay_from:
        sb.caption("No recorded runs of this policy to replay yet.")
    sb.button("▶ Run simulation", type="primary", width="stretch", on_click=run_clicked,
              args=(policy, llm_mode, replay_from, n_incidents), disabled=llm_mode == "replay" and not replay_from,
              help="Starts a live run of this seed from minute 0")
    sb.button("🎲 Restart with new seed", width="stretch", on_click=restart_clicked,
              args=(policy, llm_mode, n_incidents), disabled=llm_mode == "replay",
              help="Picks a random seed and waits at minute 0; press Resume to start")

    past_runs(sb)
    compare_controls(sb)
    sb.divider()
    clock_controls(sb)


def run_clicked(policy: str, llm_mode: str | None, replay_from: str | None, n_incidents: int) -> None:
    if replay_from:
        st.session_state.seed = run_store.run_config(replay_from)["seed"]
    start_live(int(st.session_state.seed), policy, llm_mode, replay_from, n_incidents, paused=False)


def restart_clicked(policy: str, llm_mode: str | None, n_incidents: int) -> None:
    seed = secrets.randbelow(100_000)
    st.session_state.seed = seed
    start_live(seed, policy, llm_mode, None, n_incidents, paused=True)
    st.session_state.notice = ("info", f"New seed {seed}: press ▶ Resume to start.")


def past_runs(sb) -> None:
    runs = run_store.list_runs(policies=PLAYBACK_POLICIES)
    run_id = st.session_state.run_id
    if run_id is not None and run_id not in runs:  # an ablation opened from the Evaluation page
        runs = [run_id, *runs]
    if not runs:
        return
    st.session_state.past_run = None if live() is not None else run_id
    sb.selectbox("Past runs", runs, key="past_run", format_func=run_label, on_change=past_run_changed,
                 placeholder="Live run in progress", help="Replays a saved run from minute 0")


def past_run_changed() -> None:
    if st.session_state.past_run is not None:
        open_run(st.session_state.past_run)


def run_label(run_id: str) -> str:
    n = run_store.incidents_of(run_id)
    spawned = len(run_store.spawns_of(run_id))
    tail = f" · {n} incidents" if n != EVALUATED_INCIDENTS else ""
    return run_id + tail + (f" (+{spawned} placed)" if spawned else "")


def compare_controls(sb) -> None:
    """Compare with baseline (DESIGN 13.1): the greedy run of the same seed and scenario, side by side."""
    run_id = st.session_state.run_id
    if live() is not None or run_id is None or not is_agents(load(run_id)):
        st.session_state.baseline_id = None
        if live() is not None and live().prep.policy.name != "greedy":
            sb.caption("Compare with baseline is available once this run finishes.")
        return
    on = sb.toggle("Compare with baseline", key="compare", help="Greedy baseline on the same seed, side by side")
    base = run_store.baseline_for(run_id) if on else None
    if on and base is None:
        seed = load(run_id)["config"]["seed"]
        sb.caption(f"No baseline run for seed {seed} with these incidents yet.")
        if sb.button(f"Run baseline for seed {seed}", width="stretch"):
            with st.spinner("Running the greedy baseline ..."):
                run_store.run_and_save(seed, "greedy", roads=roads(), n_incidents=run_store.incidents_of(run_id),
                                       spawns=run_store.spawns_of(run_id))
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


def clock_controls(sb) -> None:
    sb.subheader("Clock")
    lr = live()
    nothing = lr is None and st.session_state.run_id is None
    paused = lr.paused if lr is not None else not st.session_state.playing
    c1, c2 = sb.columns(2)
    c1.button("▶ Resume" if paused else "⏸ Pause", width="stretch", on_click=toggle_pause, disabled=nothing)
    c2.button("⏭ Step 1 min", width="stretch", on_click=step_minute, disabled=nothing or not paused,
              help="Advance one minute, then stay paused")
    sb.radio("Speed", list(SPEEDS), horizontal=True, key="speed", on_change=speed_changed,
             help="1x plays the whole 360-minute run in about 50 seconds")
    if lr is not None:
        sb.button("🚨 Spawn incident", width="stretch", on_click=begin_spawn,
                  disabled=st.session_state.spawn_mode or lr.view() is None,
                  help="Pauses the run; click a street spot on the map to place an incident")
    else:
        sb.caption("Saved runs replay what was recorded; start a run to place incidents.")


def toggle_pause() -> None:
    lr = live()
    if lr is not None:
        if lr.paused:
            lr.resume()
        else:
            lr.pause()
        st.session_state.spawn_mode = False
        return
    if not st.session_state.playing and st.session_state.play_t >= horizon_of_saved() - 1:
        st.session_state.play_t = 0.0  # Resume at the end replays from the start
    st.session_state.playing = not st.session_state.playing
    st.session_state.last_tick = time.monotonic()


def step_minute() -> None:
    lr = live()
    if lr is not None:
        lr.step()
        return
    st.session_state.play_t = min(horizon_of_saved() - 1.0, int(st.session_state.play_t) + 1.0)


def speed_changed() -> None:
    if live() is not None:
        live().set_rate(rate())


def begin_spawn() -> None:
    live().pause()
    st.session_state.update(spawn_mode=True, spawn_pick=None, spawn_round=st.session_state.get("spawn_round", 0) + 1)


def horizon_of_saved() -> int:
    run = load(st.session_state.run_id)
    base_id = st.session_state.get("baseline_id")
    return len(run["timeline"]) if base_id is None else min(len(run["timeline"]), len(load(base_id)["timeline"]))


def advance_saved() -> None:
    now = time.monotonic()
    if st.session_state.playing:
        dt = min(1.0, now - st.session_state.last_tick)
        st.session_state.play_t = min(horizon_of_saved() - 1.0, st.session_state.play_t + rate() * dt)
    st.session_state.last_tick = now


# ----- main area -----

def view_state(run: dict, zoom: float = 12.6) -> dict:
    """Centred on the hospitals, zoomed so all three and their labels fit the map column."""
    hs = run["timeline"][0]["hospitals"]
    lats, lons = [h["lat"] for h in hs], [h["lon"] for h in hs]
    return {"latitude": (min(lats) + max(lats)) / 2, "longitude": (min(lons) + max(lons)) / 2,
            "zoom": zoom, "pitch": 0}


VIEWS = ("Resources & events", "Agent activity", "Negotiation history")


def playback_body() -> None:
    if st.session_state.ticking_mode and not ticking():
        st.rerun(scope="app")  # stopped changing: redraw once without the timer
    lr = live()
    if lr is not None and lr.done:
        finish_live()
        st.rerun(scope="app")
    if lr is None:
        advance_saved()
    now = current()
    if now is None:
        st.info("Starting the run ...")
        return
    key, run, t = now
    minute = int(t)
    base_id = st.session_state.get("baseline_id")
    if base_id is None:
        single_map(key, run, t, minute)
    else:
        side_by_side(key, run, base_id, t, minute)

    views = VIEWS if is_agents(run) else VIEWS[:1]
    view = st.segmented_control("View", views, default=views[0], key="view", required=True,
                                label_visibility="collapsed") if len(views) > 1 else views[0]
    if view == "Agent activity":
        agent_activity(key, run, minute)
    elif view == "Negotiation history":
        negotiation_history(key, run, minute)
    elif base_id is None:
        resources(key, run, minute, "main")
    else:
        left, right = st.columns(2)
        with left:
            st.markdown(f"#### Greedy baseline · `{base_id}`")
            resources(base_id, load(base_id), minute, "base", stacked=True)
        with right:
            st.markdown(f"#### {POLICY_LABELS.get(run['config']['policy'], 'agents')} · `{st.session_state.run_id}`")
            resources(key, run, minute, "agents", stacked=True)

    if lr is None and st.session_state.playing and st.session_state.play_t >= horizon_of_saved() - 1:
        st.session_state.playing = False
        st.rerun(scope="app")


def clock_line(run: dict, minute: int) -> str:
    horizon = run["config"]["sim"]["horizon"]
    lr = live()
    if lr is None:
        state = "▶ replaying" if st.session_state.playing else "⏸ paused"
        return f"#### Minute {minute} of {horizon - 1}\n{state} · saved run"
    if lr.error:
        state = "⚠️ stopped by an error"
    elif lr.deciding:
        state = "⏳ pausing after this minute …" if lr.paused else "🧠 agents deciding …"
    else:
        state = "⏸ paused" if lr.paused else "🔴 live"
    return f"#### Minute {minute} of {horizon - 1}\n{state}"


def single_map(key: str, run: dict, t: float, minute: int) -> None:
    left, right = st.columns([4, 1])
    with left:
        if st.session_state.spawn_mode and live() is not None:
            spawn_map(run, t)
        else:
            st.pydeck_chart(map_view.build_deck(run, t, view_state(run)), height=560, key="map")
        st.markdown(map_view.legend_html(), unsafe_allow_html=True)
    with right:
        st.markdown(clock_line(run, minute))
        if live() is not None and live().error:
            st.error(live().error)
        for name, value in panels.kpis(kpis_at(key, minute, run)):
            st.metric(name, value)


def spawn_map(run: dict, t: float) -> None:
    """Place an incident: click a dot (a street intersection), choose what happened, confirm."""
    picked = st.session_state.get("spawn_pick")
    deck = map_view.build_deck(run, t, view_state(run), extra=map_view.spawn_layers(spawn_points(), picked))
    event = st.pydeck_chart(deck, height=560, key=f"map_spawn_{st.session_state.spawn_round}",
                            on_select="rerun", selection_mode="single-object")
    chosen = (event.selection.get("objects") or {}).get("spawn-points") if event else None
    if chosen:
        picked = st.session_state.spawn_pick = chosen[0]
    with st.container(border=True):
        st.markdown("**🚨 Spawn incident** · "
                    + (f"spot chosen ({picked['lat']:.4f}, {picked['lon']:.4f})" if picked
                       else "click a red dot on the map to choose the spot"))
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        kind = c1.selectbox("What happened", ["random", *SPAWN_TYPES], key="spawn_kind",
                            format_func=lambda k: "Let the seed decide" if k == "random" else k.replace("_", " "))
        n = c2.selectbox("Victims", ["random", *range(1, SPAWN_MAX_VICTIMS + 1)], key="spawn_victims",
                         format_func=lambda v: "Seed decides" if v == "random" else str(v))
        c3.markdown("&nbsp;")
        c4.markdown("&nbsp;")
        if c3.button("Place incident", type="primary", width="stretch", disabled=picked is None):
            place_incident(picked["node"], None if kind == "random" else kind, None if n == "random" else int(n))
        if c4.button("Cancel", width="stretch"):
            st.session_state.spawn_mode = False
            st.rerun(scope="app")


def place_incident(node: str, kind: str | None, n_victims: int | None) -> None:
    lr = live()
    try:
        raw = lr.spawn(node, kind, n_victims)
    except ValueError as exc:
        st.error(f"Could not place the incident: {exc}")
        return
    st.session_state.notice = ("success", f"Placed a {raw['type'].replace('_', ' ')} with "
                                          f"{len(raw['severities'])} victims; it is reported at minute {raw['t']}.")
    st.session_state.spawn_mode = False
    lr.resume()
    st.rerun(scope="app")


def side_by_side(key: str, run: dict, base_id: str, t: float, minute: int) -> None:
    base = load(base_id)
    horizon = horizon_of_saved()
    left, right = st.columns(2)
    with left:
        st.markdown(f"##### Greedy baseline · minute {minute} of {horizon - 1}")
        st.pydeck_chart(map_view.build_deck(base, t, view_state(base, 11.9)), height=460, key="map_base")
    with right:
        st.markdown(f"##### {POLICY_LABELS.get(run['config']['policy'], 'agents').capitalize()} · same seed")
        st.pydeck_chart(map_view.build_deck(run, t, view_state(run, 11.9)), height=460, key="map_agents")
    st.markdown(map_view.legend_html(), unsafe_allow_html=True)
    st.markdown("##### Baseline vs agents at this minute")
    st.dataframe(panels.kpi_comparison(kpis_at(base_id, minute, base), kpis_at(key, minute, run)), width="stretch")


def resources(key: str, run: dict, minute: int, widget_key: str, stacked: bool = False) -> None:
    state = run["timeline"][minute]
    if stacked:
        st.dataframe(panels.unit_table(state), width="stretch")
        st.dataframe(panels.hospital_table(state), width="stretch")
        st.dataframe(panels.per_hospital_kpis(kpis_at(key, minute, run)), width="stretch")
    else:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.markdown("##### Units")
            st.dataframe(panels.unit_table(state), width="stretch")
            st.dataframe(panels.unit_states(state), width="stretch", height=250)
        with c2:
            st.markdown("##### Hospitals")
            st.dataframe(panels.hospital_table(state), width="stretch")
            st.dataframe(panels.per_hospital_kpis(kpis_at(key, minute, run)), width="stretch")
    head, toggle = st.columns([3, 1])
    head.markdown("##### Live events")
    show_agents = is_agents(run) and toggle.toggle("Agent messages", value=False,
                                                   key=f"show_agent_events_{widget_key}")
    feed = panels.event_feed(run["events"], minute, agents=show_agents)
    st.markdown("\n".join(f"- **{et:>3}** {'🟢 ' if et == minute else ''}{text}" for et, text in feed)
                or "_No events yet._")


def agent_activity(key: str, run: dict, minute: int) -> None:
    """DESIGN 13.2: each agent's status, recent messages, pending requests and last decision."""
    cards = cards_at(key, minute, run)
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


def negotiation_history(key: str, run: dict, minute: int) -> None:
    """DESIGN 13.2: threads with rounds, offers, counters, awards, declines and Arbiter decisions."""
    index = threads(key, len(run["events"]), run["events"])
    neg = kpis_at(key, minute, run)["negotiation"]
    stats = st.columns(6)
    for col, (name, k) in zip(stats, (("Threads", "threads"), ("Rounds", "rounds"), ("Counters", "counters"),
                                      ("Deals without Arbiter", "deals_without_arbiter"),
                                      ("Escalations", "escalations"), ("Arbiter decisions", "arbiter_decisions"))):
        col.metric(name, neg.get(k, 0))
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
                        format_func=lambda o: "Most recently active (follows the clock)" if o == "latest" else names[o])
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


def header(area: str) -> None:
    lr = live()
    if lr is not None:
        run_id = None
        cfg = lr.view()["config"] if lr.view() else {"seed": lr.prep.seed, "policy": lr.prep.policy.name,
                                                     "sim": lr.prep.world.cfg.to_dict()}
    else:
        run_id = st.session_state.run_id
        cfg = {**load(run_id)["config"], "spawns": run_store.spawns_of(run_id)}
    placed = len(cfg.get("spawns") or [])
    tail = f" (+{placed} placed)" if placed else ""
    where = "🔴 live run" if run_id is None else f"`{run_id}`"
    st.markdown(f"### {area} · seed {cfg['seed']} · {cfg['policy']} · {cfg['sim']['n_incidents']} incidents{tail} "
                f"· {where}")


def show_notice() -> None:
    notice = st.session_state.notice
    if notice:
        kind, text = notice
        {"error": st.error, "success": st.success, "info": st.info}[kind](text)
        st.session_state.notice = None


def playback() -> None:
    init_state()
    sidebar()
    area = load_area()["name"]
    if live() is None and st.session_state.run_id is None:
        st.title("Emergency response simulator")
        show_notice()
        st.info(f"Area: {area}. Pick a seed and press **Run simulation** in the sidebar.")
        return
    header(area)
    show_notice()
    st.session_state.ticking_mode = ticking()
    fragment = st.fragment(run_every=FRAME_SECONDS if st.session_state.ticking_mode else None)(playback_body)
    fragment()


def evaluation() -> None:
    evaluation_page.render(roads(), PLAYBACK)


PLAYBACK = st.Page(playback, title="Simulation", icon="🗺️", default=True)
st.navigation([PLAYBACK, st.Page(evaluation, title="Evaluation", icon="📊", url_path="evaluation")]).run()
