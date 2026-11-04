"""Streamlit dashboard: run simulations live, reopen and watch saved runs. Start with the launcher.

One screen (DESIGN 13.1): a thin control bar on top, the map on the left, KPIs, units, hospitals and a
tabbed feed on the right. Speed and skipping quiet time only pace the clock; they never change a run.
"""
from __future__ import annotations

import os
import secrets
import sys
import time
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from dashboard import evaluation_page, explain, feed, map_view, panels, run_store, style  # noqa: E402
from dashboard import wording as W  # noqa: E402
from sim import metrics  # noqa: E402
from sim.config import default_config, load_area  # noqa: E402
from coordination.llm import load_llm_config  # noqa: E402
from sim.generator import SPAWN_MAX_VICTIMS, SPAWN_TYPES  # noqa: E402
from sim.live import MINUTES_PER_SECOND_1X, QUIET_RATE, is_quiet  # noqa: E402
from sim.runner import load_roads  # noqa: E402

POLICY_LABELS = {"greedy": "greedy baseline", "agents": "agents", "agents-nonego": "agents, no negotiation",
                 "agents-noarb": "agents, no Arbiter", "agents-rulehosp": "agents, rule hospitals"}
PLAYBACK_POLICIES = ("greedy", "agents")  # the ablations live on the Evaluation page
LLM_MODES = {"off": "Rules only", "live": "Live LLM", "replay": "Replay"}

SPEEDS = {"0.5x": 0.5, "1x": 1.0, "2x": 2.0, "4x": 4.0, "10x": 10.0}
INCIDENT_CHOICES = (3, 4, 6, 12)
DEFAULT_INCIDENTS = 4
EVALUATED_INCIDENTS = 12  # DESIGN 3.11; the Evaluation page always uses it
FRAME_SECONDS = 0.125     # 8 frames a second: units glide along their routes
MAP_HEIGHT = 588
FEED_HEIGHT = 342

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


def is_agents(run: dict) -> bool:
    return run["config"]["policy"] != "greedy"


def rate() -> float:
    return SPEEDS[st.session_state.speed] * MINUTES_PER_SECOND_1X


# ----- state: a live run, or a saved run being replayed -----

def init_state() -> None:
    """Opens on the intro (nothing playing): seed 42, agents, replaying a recorded final-evaluation run."""
    featured = run_store.featured_replay()
    defaults = {"run_id": None, "live": None, "play_t": 0.0, "playing": False, "last_tick": time.monotonic(),
                "speed": "1x", "seed": 42, "spawn_mode": False, "notice": None, "skip_quiet": True,
                "n_incidents": DEFAULT_INCIDENTS, "policy": "agents", "llm_mode": "replay" if featured else "off",
                "replay_from": featured, "compare": False, "feed": feed.FEEDS[0], "focus": None}
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


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
                                  replay_from=replay_from, n_incidents=n_incidents,
                                  skip_quiet=st.session_state.skip_quiet)
    except ValueError as exc:
        st.session_state.notice = ("error", str(exc))
        return
    st.session_state.update(live=lr, run_id=None, playing=False, compare=False)


def finish_live() -> None:
    """The live run has been saved: keep showing its last minute, now as a saved run."""
    lr = st.session_state.live
    st.session_state.update(live=None, run_id=lr.run_id, play_t=float(lr.prep.world.cfg.horizon - 1),
                            playing=False, spawn_mode=False)
    st.session_state.notice = ("success", f"Run finished and saved as {lr.run_id}.")


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


def find_baseline() -> str | None:
    """Compare with baseline (DESIGN 13.1): the greedy run of the same seed and scenario, once saved."""
    run_id = st.session_state.run_id
    if not st.session_state.compare or live() is not None or run_id is None or not is_agents(load(run_id)):
        return None
    return run_store.baseline_for(run_id)


def baseline_id() -> str | None:
    """Looked up once per full rerun (it reads every saved scenario), not on every frame."""
    return st.session_state.get("baseline_id")


# ----- the control bar -----

def control_bar() -> None:
    replaying = st.session_state.get("policy", "greedy") != "greedy" and st.session_state.llm_mode == "replay"
    widths = ([0.5, 0.9, 0.8, 1.45, 0.55, 0.8, 0.65, 0.5, 1.0, 0.4, 0.42, 0.42, 0.42] if replaying
              else [0.65, 1.1, 0.95, 0.75, 0.9, 0.75, 0.6, 1.15, 0.45, 0.42, 0.42, 0.42])
    c = st.columns(widths, vertical_alignment="bottom")
    if not replaying:
        c.insert(3, st.empty())   # the recorded-run picker only takes room while replaying
    c[0].number_input("Seed", min_value=0, max_value=1_000_000, step=1, key="seed", disabled=replaying,
                      help="Picks the scenario: incidents, victims and walk-ins. A replay uses its recorded seed.")
    policy = c[1].selectbox("Policy", PLAYBACK_POLICIES, format_func=lambda p: POLICY_LABELS[p].capitalize(),
                            key="policy", help="Greedy baseline: nearest unit, nearest hospital, no negotiation. "
                                               "Agents: assessment, dispatch, hospital and referee agents negotiate.")
    llm_mode, replay_from = llm_controls(c[2], c[3], policy)
    c[4].button("▶ Run", type="primary", width="stretch", on_click=run_clicked,
                args=(policy, llm_mode, replay_from), disabled=llm_mode == "replay" and not replay_from,
                help="Starts the run from minute 0; it plays live and is saved at the end")
    clock_buttons(c[5], c[6])
    c[7].selectbox("Speed", list(SPEEDS), key="speed", on_change=speed_changed,
                   help="1x = one simulated minute per second. Speed never changes what happens.")
    c[8].toggle("Skip quiet time", key="skip_quiet", on_change=skip_changed,
                help="While no unit is moving and no incident is open, fast-forward; "
                     "back to the chosen speed when something happens")
    lr = live()
    c[9].button("🚨", width="stretch", on_click=begin_spawn,
                disabled=lr is None or st.session_state.spawn_mode or lr.view() is None,
                help="Place an incident: pauses, then click a street spot on the map")
    with c[10].popover("⚙", help="More settings: incidents per scenario, new seed, past runs, compare"):
        settings_popover(policy, llm_mode)
    with c[11].popover("❓", help="How to read this page"):
        st.markdown(HOW_TO_READ)
    with c[12].popover("⭐", help="Negotiation highlights: the moments with the most real negotiation"):
        highlights_list("pop")


HOW_TO_READ = """**How to read this**

1. **The map.** Ambulances drive real Boston streets: purple = paramedic ambulances (ALS, the only ones that
   carry critical victims), blue = basic ambulances (BLS). Red circles are incidents with critical victims,
   amber without, grey once everyone is at a hospital. Hover any marker; click one to open its conversation.
2. **The conversations** (right). Click an incident to read what the agents said to each other about it, in
   time order: "Dispatch → H1, H2: Can you take the critical patient?" and each hospital's answer with its
   reason. Grey lines starting with "·" say what happened in the world. **(AI)** after a line means that
   agent's AI decided it; hover it for the AI's own words. Hover a hospital (H1, H2, H3) for its name.
3. **The results** (top right): critical victims who reached care in time, those not yet served, average time
   to care and victims delivered. The **Evaluation** page compares the policies over 20 scenarios.
"""


def llm_controls(mode_col, run_col, policy: str) -> tuple[str | None, str | None]:
    """Live or replay (DESIGN 13.1): rules only, live model calls (recorded), or replay a recording."""
    if policy == "greedy":
        mode_col.selectbox("LLM", ["–"], disabled=True, key="llm_greedy", help="The baseline uses no AI")
        return None, None
    mode = mode_col.selectbox("LLM", list(LLM_MODES), format_func=LLM_MODES.get, key="llm_mode",
                              help="Rules only: no AI. Live: the AI makes the judgment calls (recorded). "
                                   "Replay: rerun a recorded run with the AI's recorded answers, exactly.")
    if mode == "live":
        cfg = load_llm_config()
        if not (cfg.get("model") and os.environ.get("OPENAI_API_KEY")):
            run_col.caption("Live needs a pinned model and OPENAI_API_KEY; using rules only.")
            return "off", None
    if mode == "replay":
        runs = [p["path"] for p in run_store.replay_presets()] + run_store.replayable_runs(policy)
        if not runs:
            run_col.caption("No recorded runs to replay yet.")
            return "replay", None
        if st.session_state.get("replay_from") not in runs:
            st.session_state.replay_from = runs[0]
        return "replay", run_col.selectbox("Recorded run", runs, key="replay_from", format_func=replay_label,
                                           help="A run recorded with the AI; the first is from the final evaluation")
    return mode, None


def replay_label(path: str) -> str:
    preset = next((p for p in run_store.replay_presets() if p["path"] == path), None)
    return preset["label"] if preset else run_label(path)


def run_clicked(policy: str, llm_mode: str | None, replay_from: str | None) -> None:
    if replay_from:
        st.session_state.seed = run_store.run_config(replay_from)["seed"]
    start_live(int(st.session_state.seed), policy, llm_mode, replay_from, st.session_state.n_incidents, paused=False)


def restart_clicked(policy: str, llm_mode: str | None) -> None:
    seed = secrets.randbelow(100_000)
    st.session_state.seed = seed
    start_live(seed, policy, llm_mode, None, st.session_state.n_incidents, paused=True)
    st.session_state.notice = ("info", f"New seed {seed}: press ▶ Resume to start.")


def clock_buttons(pause_col, step_col) -> None:
    lr = live()
    nothing = lr is None and st.session_state.run_id is None
    paused = lr.paused if lr is not None else not st.session_state.playing
    pause_col.button("▶ Resume" if paused else "⏸ Pause", width="stretch", on_click=toggle_pause, disabled=nothing,
                     help="Stops or restarts the clock; every panel holds at the current minute")
    step_col.button("⏭ 1 min", width="stretch", on_click=step_minute, disabled=nothing or not paused,
                    help="Advance one minute, then stay paused")


def settings_popover(policy: str, llm_mode: str | None) -> None:
    st.selectbox("Incidents per scenario", INCIDENT_CHOICES, key="n_incidents",
                 help=f"{EVALUATED_INCIDENTS} is the evaluated setting (DESIGN 3.11). Fewer incidents are easier "
                      "to follow; one of them is always the surge (6 to 10 victims).")
    st.button("🎲 Restart with new seed", width="stretch", on_click=restart_clicked, args=(policy, llm_mode),
              disabled=llm_mode == "replay", help="Picks a random seed and waits at minute 0; press Resume to start")
    past_runs()
    compare_controls()


def past_runs() -> None:
    runs = run_store.list_runs(policies=PLAYBACK_POLICIES)
    run_id = st.session_state.run_id
    if run_id is not None and run_id not in runs:  # an ablation opened from the Evaluation page
        runs = [run_id, *runs]
    if not runs:
        return
    st.session_state.past_run = None if live() is not None else run_id
    st.selectbox("Past runs", runs, key="past_run", format_func=run_label, on_change=past_run_changed,
                 placeholder="Live run in progress", help="Replays a saved run from minute 0")


def past_run_changed() -> None:
    if st.session_state.past_run is not None:
        open_run(st.session_state.past_run)


def run_label(run_id: str) -> str:
    n = run_store.incidents_of(run_id)
    spawned = len(run_store.spawns_of(run_id))
    tail = f" · {n} incidents" if n != EVALUATED_INCIDENTS else ""
    return run_id + tail + (f" (+{spawned} placed)" if spawned else "")


def compare_controls() -> None:
    run_id = st.session_state.run_id
    if live() is not None or run_id is None or not is_agents(load(run_id)):
        st.caption("Compare with baseline: open a saved agents run (it is available once a live run finishes).")
        st.session_state.compare = False
        return
    on = st.toggle("Compare with baseline", key="compare", help="Greedy baseline on the same seed, side by side")
    if on and find_baseline() is None:
        seed = load(run_id)["config"]["seed"]
        st.caption(f"No baseline run for seed {seed} with these incidents yet.")
        if st.button(f"Run baseline for seed {seed}", width="stretch"):
            with st.spinner("Running the greedy baseline ..."):
                run_store.run_and_save(seed, "greedy", roads=roads(), n_incidents=run_store.incidents_of(run_id),
                                       spawns=run_store.spawns_of(run_id))
            st.rerun()


# ----- the clock -----

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


def skip_changed() -> None:
    if live() is not None:
        live().set_skip_quiet(st.session_state.skip_quiet)


def begin_spawn() -> None:
    live().pause()
    st.session_state.update(spawn_mode=True, spawn_pick=None, spawn_round=st.session_state.get("spawn_round", 0) + 1)


def horizon_of_saved() -> int:
    run = load(st.session_state.run_id)
    base = baseline_id()
    return len(run["timeline"]) if base is None else min(len(run["timeline"]), len(load(base)["timeline"]))


def saved_quiet(minute: int) -> bool:
    runs = [load(st.session_state.run_id)] + [load(b) for b in [baseline_id()] if b]
    return all(is_quiet(r["timeline"][min(minute, len(r["timeline"]) - 1)]) for r in runs)


def saved_skipping() -> bool:
    return st.session_state.skip_quiet and saved_quiet(int(st.session_state.play_t))


def advance_saved() -> None:
    now = time.monotonic()
    if st.session_state.playing:
        dt = min(1.0, now - st.session_state.last_tick)
        speed = max(rate(), QUIET_RATE) if saved_skipping() else rate()
        st.session_state.play_t = min(horizon_of_saved() - 1.0, st.session_state.play_t + speed * dt)
    st.session_state.last_tick = now


# ----- the screen -----

def view_state(run: dict, zoom: float = 12.6) -> dict:
    """Centred on the hospitals, zoomed so all three and their labels fit the map column."""
    hs = run["timeline"][0]["hospitals"]
    lats, lons = [h["lat"] for h in hs], [h["lon"] for h in hs]
    return {"latitude": (min(lats) + max(lats)) / 2, "longitude": (min(lons) + max(lons)) / 2,
            "zoom": zoom, "pitch": 0}


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
    st.markdown(clock_bar(run, minute) + f"<div class='now-line'>{escape(now_at(key, minute, run))}</div>",
                unsafe_allow_html=True)
    base = baseline_id()
    left, right = st.columns([3, 2], gap="medium")
    with left:
        if base is not None:
            two_maps(run, load(base), t)
        elif st.session_state.spawn_mode and lr is not None:
            spawn_map(run, t)
        else:
            main_map(run, t)
        st.markdown(map_view.legend_compact_html(), unsafe_allow_html=True)
    with right:
        if lr is not None and lr.error:
            st.error(lr.error)
        if base is not None:
            comparison_panel(key, run, base, minute)
        else:
            side_panel(key, run, minute)
    if lr is None and st.session_state.playing and st.session_state.play_t >= horizon_of_saved() - 1:
        st.session_state.playing = False
        st.rerun(scope="app")


def clock_bar(run: dict, minute: int) -> str:
    horizon = run["config"]["sim"]["horizon"]
    lr = live()
    if lr is None:
        state = ("⏩ skipping quiet time" if saved_skipping() else "▶ replaying") if st.session_state.playing \
            else "⏸ paused"
        where = f"saved run {st.session_state.run_id}"
    else:
        if lr.error:
            state = "⚠️ stopped by an error"
        elif lr.deciding:
            state = "⏳ pausing after this minute …" if lr.paused else "🧠 agents deciding …"
        elif lr.paused:
            state = "⏸ paused"
        else:
            state = "⏩ skipping quiet time" if lr.skipping else "🔴 live"
        where = "live run"
    cfg = run["config"]
    placed = len(cfg.get("spawns") or [])
    scenario = (f"seed {cfg['seed']} · {POLICY_LABELS.get(cfg['policy'], cfg['policy'])} · "
                f"{cfg['sim']['n_incidents']} incidents" + (f" (+{placed} placed)" if placed else ""))
    pct = round(100 * minute / max(1, horizon - 1), 1)
    return (f"<div class='clock'><span class='min'>Min {minute} / {horizon - 1}</span><span>{state}</span>"
            f"<span class='tag'>{scenario} · {where}</span></div>"
            f"<div class='prog'><div style='width:{pct}%'></div></div>")


@st.cache_data(max_entries=2000, show_spinner=False)
def now_at(key: str, minute: int, _run: dict) -> str:
    return explain.now_sentence(_run, minute)


def main_map(run: dict, t: float) -> None:
    """The map; clicking an incident or a unit opens that incident's card and highlights it."""
    focus = st.session_state.get("focus")
    event = st.pydeck_chart(map_view.build_deck(run, t, view_state(run), focus=focus), height=MAP_HEIGHT, key="map",
                            on_select="rerun", selection_mode="single-object")
    objects = (event.selection.get("objects") or {}) if event else {}
    picked = ((objects.get("incidents") or [{}])[0].get("id")
              or (objects.get("units") or [{}])[0].get("incident"))
    marker = repr(objects)
    if objects and marker != st.session_state.get("map_pick"):
        st.session_state.map_pick = marker
        if picked:
            st.session_state.update(focus=picked, story=picked, feed="Incidents")


def side_panel(key: str, run: dict, minute: int) -> None:
    state = run["timeline"][minute]
    st.markdown(panels.kpi_cards_html(kpis_at(key, minute, run)) + panels.units_free_line(state)
                + panels.hospitals_html(state), unsafe_allow_html=True)
    if st.session_state.spawn_mode and live() is not None:
        spawn_form()
        return
    feed.render(key, run, minute, is_agents(run), FEED_HEIGHT, roads())


def comparison_panel(key: str, run: dict, base_id: str, minute: int) -> None:
    base = load(base_id)
    st.markdown(f"**Baseline vs agents at minute {minute}** · greedy `{base_id}` · agents "
                f"`{st.session_state.run_id}`")
    st.dataframe(panels.kpi_comparison(kpis_at(base_id, minute, base), kpis_at(key, minute, run)),
                 width="stretch", height=318)
    st.markdown(panels.compare_resources_html(base["timeline"][minute], run["timeline"][minute]),
                unsafe_allow_html=True)


def two_maps(run: dict, base: dict, t: float) -> None:
    left, right = st.columns(2, gap="small")
    left.caption("Greedy baseline")
    left.pydeck_chart(map_view.build_deck(base, t, view_state(base, 11.7)), height=MAP_HEIGHT - 28, key="map_base")
    right.caption(f"{POLICY_LABELS.get(run['config']['policy'], 'agents').capitalize()} · same seed")
    right.pydeck_chart(map_view.build_deck(run, t, view_state(run, 11.7)), height=MAP_HEIGHT - 28, key="map_agents")


def spawn_map(run: dict, t: float) -> None:
    """Place an incident: click a dot (a street intersection), choose what happened, confirm."""
    picked = st.session_state.get("spawn_pick")
    deck = map_view.build_deck(run, t, view_state(run), extra=map_view.spawn_layers(spawn_points(), picked))
    event = st.pydeck_chart(deck, height=MAP_HEIGHT, key=f"map_spawn_{st.session_state.spawn_round}",
                            on_select="rerun", selection_mode="single-object")
    chosen = (event.selection.get("objects") or {}).get("spawn-points") if event else None
    if chosen:
        st.session_state.spawn_pick = chosen[0]


def spawn_form() -> None:
    picked = st.session_state.get("spawn_pick")
    with st.container(border=True):
        st.markdown("**🚨 Spawn incident** · "
                    + (f"spot chosen ({picked['lat']:.4f}, {picked['lon']:.4f})" if picked
                       else "click a red dot on the map to choose the spot"))
        c1, c2 = st.columns([2, 1])
        kind = c1.selectbox("What happened", ["random", *SPAWN_TYPES], key="spawn_kind",
                            format_func=lambda k: "Let the seed decide" if k == "random" else k.replace("_", " "))
        n = c2.selectbox("Victims", ["random", *range(1, SPAWN_MAX_VICTIMS + 1)], key="spawn_victims",
                         format_func=lambda v: "Seed decides" if v == "random" else str(v))
        b1, b2 = st.columns(2)
        if b1.button("Place incident", type="primary", width="stretch", disabled=picked is None):
            place_incident(picked["node"], None if kind == "random" else kind, None if n == "random" else int(n))
        if b2.button("Cancel", width="stretch"):
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


def show_notice() -> None:
    notice = st.session_state.notice
    if notice:
        kind, text = notice
        st.toast(text, icon={"error": "⚠️", "success": "✅", "info": "ℹ️"}[kind])
        st.session_state.notice = None


def playback() -> None:
    style.apply()
    init_state()
    control_bar()
    show_notice()
    if live() is None and st.session_state.run_id is None:
        intro()
        return
    st.session_state.baseline_id = find_baseline()
    st.session_state.ticking_mode = ticking()
    fragment = st.fragment(run_every=FRAME_SECONDS if st.session_state.ticking_mode else None)(playback_body)
    fragment()


def intro() -> None:
    """Before a run (DESIGN 13.1): what pressing Run will show, and how to read the page."""
    featured = st.session_state.get("replay_from")
    what = (f"It replays <b>{escape(replay_label(featured))}</b> with the AI's recorded answers, exactly as it "
            "happened." if featured and st.session_state.llm_mode == "replay"
            else "It runs the chosen seed and policy live, from minute 0.")
    st.markdown(f"<div class='intro'><h3>Press ▶ Run to start</h3><p>{what} Emergency calls come in across central "
                f"Boston ({escape(load_area()['name'])}); agents send ambulances and negotiate with three "
                "hospitals, in about 6 minutes at 1x with quiet time skipped.</p></div>", unsafe_allow_html=True)
    left, right = st.columns([3, 2], gap="large")
    left.markdown(HOW_TO_READ)
    with right:
        st.markdown("**⭐ Negotiation highlights**: the moments in the recorded runs with the most real negotiation")
        highlights_list("intro")


def highlights_list(where: str) -> None:
    """The top negotiation moments; the popover also says what the rule would have changed."""
    items = run_store.highlights()
    if not items:
        st.caption("No highlights yet: run `python -m tools.highlights`.")
    for k, h in enumerate(items):
        why = ", ".join(f"{v} {name}" for name, v in h["why"].items() if v)
        text, go = st.columns([3, 2], vertical_alignment="center")
        extra = f"<div class='why'>{escape(h['instead'])}</div>" if where == "pop" else ""
        text.markdown(f"<div class='hl' title='{escape(why, quote=True)}'><b>{escape(h['title'])}</b><div class='agent-d'>"
                      f"{escape(h['label'])} · {escape(W.minute_tag(h['minute']))}</div>{extra}</div>",
                      unsafe_allow_html=True)
        go.button("Jump to this moment", key=f"hl_{where}_{k}", on_click=jump_to, args=(h,),
                  help="Opens that run, paused at this minute, with the incident's conversation open")


def jump_to(h: dict) -> None:
    """Open a highlight's run at its minute with its incident selected (the run is rebuilt once per session)."""
    built = st.session_state.setdefault("built_runs", {})
    key = repr(sorted(h["run"].items()))
    try:
        if key not in built or not (run_store.RUNS_DIR / built[key]).exists():
            built[key] = run_store.materialize(h["run"], roads=roads())
    except FileNotFoundError:
        st.session_state.notice = ("error", "That run's recording is not on this machine.")
        return
    open_run(built[key])
    st.session_state.update(play_t=float(h["minute"]), focus=h["incident"], story=h["incident"], feed="Incidents")


def evaluation() -> None:
    style.apply()
    evaluation_page.render(roads(), PLAYBACK)


PLAYBACK = st.Page(playback, title="Simulation", icon="🗺️", default=True)
st.navigation([PLAYBACK, st.Page(evaluation, title="Evaluation", icon="📊", url_path="evaluation")],
              position="top").run()
