"""Streamlit dashboard: run simulations live, reopen and watch saved runs. Start with the launcher.

One screen (DESIGN 13.1): a thin control bar on top, the map on the left, KPIs, units, hospitals and a
tabbed feed on the right. Speed and skipping quiet time only pace the clock; they never change a run.
"""
from __future__ import annotations

import os
import re
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
from dashboard import versions as V  # noqa: E402
from dashboard import wording as W  # noqa: E402
from sim import metrics  # noqa: E402
from sim.config import default_config, load_area  # noqa: E402
from coordination.llm import load_llm_config  # noqa: E402
from sim.generator import SPAWN_MAX_VICTIMS, SPAWN_TYPES  # noqa: E402
from sim.live import MINUTES_PER_SECOND_1X, QUIET_RATE, is_quiet  # noqa: E402
from sim.runner import load_roads  # noqa: E402

PLAYBACK_POLICIES = ("greedy", "agents")  # the ablations live on the Evaluation page
SOURCES = ("Replay", "Live")   # for the two LLM versions: a free replay of a recording, or live model calls

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
    """Opens on the intro (nothing playing), with the demo recording selected."""
    featured = run_store.featured_replay()
    seed = run_store.run_config(featured)["seed"] if featured else 42
    defaults = {"run_id": None, "live": None, "play_t": 0.0, "playing": False, "last_tick": time.monotonic(),
                "speed": "1x", "seed": seed, "spawn_mode": False, "notice": None, "skip_quiet": True,
                "n_incidents": DEFAULT_INCIDENTS, "version": "llm-guard" if featured else "rules",
                "source": SOURCES[0], "recording": featured, "compare": False, "feed": feed.FEEDS[0],
                "focus": None, "loaded": None}
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


def start_live(version: str, seed: int, llm_mode: str | None, recording: str | None, n_incidents: int,
               paused: bool) -> None:
    stop_live()
    try:
        lr = run_store.start_live(seed, V.policy(version), paused=paused, rate=rate(), roads=roads(),
                                  llm_mode=llm_mode, replay_from=recording, n_incidents=n_incidents,
                                  skip_quiet=st.session_state.skip_quiet, llm_guard=V.guard(version))
    except ValueError as exc:
        st.session_state.notice = ("error", str(exc))
        return
    st.session_state.update(live=lr, run_id=None, playing=False, compare=False,
                            loaded={"version": version, "recording": recording})


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
    with st.container(key="controlbar"):
        controls()


def controls() -> None:
    c = st.columns([0.42, 1.22, 0.78, 1.0, 0.5, 0.7, 0.6, 0.5, 1.02, 0.62, 0.74, 0.56, 0.84],
                   vertical_alignment="bottom")
    version, llm_mode, recording = version_controls(c[0], c[1], c[2], c[3])
    c[4].button("▶ Run", type="primary", width="stretch", on_click=run_clicked, args=(version, llm_mode, recording),
                disabled=not runnable(version, llm_mode, recording),
                help="Starts the chosen version from minute 0; it plays live and is saved at the end")
    clock_buttons(c[5], c[6])
    c[7].selectbox("Speed", list(SPEEDS), key="speed", on_change=speed_changed,
                   help="1x = one simulated minute per second. Speed never changes what happens.")
    c[8].toggle("Skip quiet", key="skip_quiet", on_change=skip_changed,
                help="While no unit is moving and no incident is open, fast-forward; "
                     "back to the chosen speed when something happens")
    lr = live()
    c[9].button("🚨 Spawn", width="stretch", on_click=begin_spawn,
                disabled=lr is None or st.session_state.spawn_mode or lr.view() is None,
                help="Place an incident: pauses, then click a street spot on the map")
    with c[10].popover("⚙ Settings", help="Incidents per scenario, new seed, past runs, compare with the baseline"):
        settings_popover(version, llm_mode)
    with c[11].popover("❓ Help", help="How to read this page"):
        st.markdown(HOW_TO_READ)
    with c[12].popover("⭐ Highlights", help="Negotiation highlights: the moments with the most real negotiation"):
        highlights_list("pop")


@st.cache_data(ttl=30, show_spinner=False)
def recordings_for(version: str, seed: int) -> dict[str, str]:
    return V.recordings(version, seed)


def recording_options(version: str, seed: int) -> dict[str, str]:
    """This version's recordings for the seed; a saved run opened from elsewhere is listed too."""
    opts = dict(recordings_for(version, seed))
    mine = st.session_state.get("recording")
    loaded = st.session_state.get("loaded") or {}
    if mine and mine not in opts and loaded.get("recording") == mine and loaded.get("version") == version \
            and run_store.run_config(mine)["seed"] == seed:
        opts[mine] = replay_label(mine)
    return opts


def version_controls(seed_col, version_col, source_col, rec_col) -> tuple[str, str | None, str | None]:
    """Seed, Version and, for the two LLM versions, Replay or Live and the recording (DESIGN 13.1)."""
    choices = list(V.VERSIONS)
    if st.session_state.version not in choices:       # an ablation opened from the Evaluation page
        choices.append(st.session_state.version)
    seed_col.number_input("Seed", min_value=0, max_value=1_000_000, step=1, key="seed", on_change=selection_changed,
                          help="Picks the scenario: incidents, victims and walk-ins")
    version = version_col.selectbox("Version", choices, format_func=V.label, key="version",
                                    on_change=selection_changed,
                                    help="The four systems the Evaluation page compares. The guard setting "
                                         "comes from the version.")
    if not V.uses_llm(version):
        return version, (None if version == "greedy" else "off"), None
    if st.session_state.get("source") not in SOURCES:   # Streamlit forgets a widget's value while it is hidden
        st.session_state.source = SOURCES[0]
    source = source_col.segmented_control("LLM", SOURCES, key="source", required=True, on_change=selection_changed,
                                          help="Replay (free): the AI's recorded answers, exactly. "
                                               "Live (costs credit): new model calls, recorded.")
    if source == "Live":
        if not live_available():
            rec_col.caption("Live needs a pinned model and OPENAI_API_KEY.")
        else:
            rec_col.caption("Live: calls the model; costs API credit.")
        return version, "live", None
    opts = recording_options(version, int(st.session_state.seed))
    if not opts:
        rec_col.caption(f"No recording for this seed; recorded: {V.seeds_text(V.recorded_seeds(version)) or 'none'}")
        return version, "replay", None
    if st.session_state.get("recording") not in opts:
        st.session_state.recording = next(iter(opts))
    recording = rec_col.selectbox("Recording", list(opts), format_func=opts.get, key="recording",
                                  on_change=selection_changed, help="The recorded run to replay for this seed")
    return version, "replay", recording


def live_available() -> bool:
    cfg = load_llm_config()
    return bool(cfg.get("model") and os.environ.get("OPENAI_API_KEY"))


def runnable(version: str, llm_mode: str | None, recording: str | None) -> bool:
    if llm_mode == "replay":
        return recording is not None
    return llm_mode != "live" or live_available()


def current_selection() -> tuple[str, str | None, str | None]:
    """(version, llm mode, recording) as the widgets stand, before they are drawn."""
    version = st.session_state.version
    if not V.uses_llm(version):
        return version, (None if version == "greedy" else "off"), None
    if st.session_state.get("source", SOURCES[0]) == "Live":
        return version, "live", None
    opts = recording_options(version, int(st.session_state.seed))
    if st.session_state.get("recording") not in opts:
        st.session_state.recording = next(iter(opts), None)
    return version, "replay", st.session_state.recording


def selection_changed() -> None:
    """Never show a run other than the one selected: a free choice loads at once, paused at minute 0;
    a live LLM run waits for Run (it costs credit)."""
    if live() is None and st.session_state.run_id is None:
        return                                          # the intro: Run says what it will load
    version, llm_mode, recording = current_selection()
    if llm_mode == "live" or (llm_mode == "replay" and recording is None):
        stop_live()
        st.session_state.update(run_id=None, playing=False, compare=False, loaded=None)
        return
    start_live(version, int(st.session_state.seed), llm_mode, recording, st.session_state.n_incidents, paused=True)


def sync_selection(run_id: str) -> None:
    """A saved run opened from Past runs, a highlight or the Evaluation page: the controls follow it."""
    cfg = run_store.run_config(run_id)
    version = V.version_of(cfg)
    opened_from = st.session_state.get("opened_from", {}).get(run_id)   # set when a highlight or evaluation opened it
    recording = (opened_from or V.source_of(run_store.RUNS_DIR / run_id)) if V.uses_llm(version) else None
    st.session_state.update(version=version, seed=cfg["seed"], loaded={"version": version, "recording": recording},
                            synced=run_id)
    if V.uses_llm(version):
        st.session_state.update(source=SOURCES[0], recording=recording)
    if cfg["sim"]["n_incidents"] in INCIDENT_CHOICES and not cfg.get("spawns"):
        st.session_state.n_incidents = cfg["sim"]["n_incidents"]


def replay_label(path: str) -> str:
    preset = V.presets().get(path)
    if preset:
        return preset
    m = re.match(r"evals/(eval-\d+)/runs/seed(\d+)-agents-r(\d+)$", path)
    return f"final evaluation, seed {m[2]}, recording {m[3]}" if m else run_label(path)


def run_clicked(version: str, llm_mode: str | None, recording: str | None) -> None:
    if recording:
        st.session_state.seed = run_store.run_config(recording)["seed"]
    start_live(version, int(st.session_state.seed), llm_mode, recording, st.session_state.n_incidents, paused=False)


def restart_clicked(version: str, llm_mode: str | None) -> None:
    seed = secrets.randbelow(100_000)
    st.session_state.seed = seed
    start_live(version, seed, llm_mode, None, st.session_state.n_incidents, paused=True)
    st.session_state.notice = ("info", f"New seed {seed}: press ▶ Resume to start.")


def clock_buttons(pause_col, step_col) -> None:
    lr = live()
    nothing = lr is None and st.session_state.run_id is None
    paused = lr.paused if lr is not None else not st.session_state.playing
    pause_col.button("▶ Resume" if paused else "⏸ Pause", width="stretch", on_click=toggle_pause, disabled=nothing,
                     help="Stops or restarts the clock; every panel holds at the current minute")
    step_col.button("⏭ 1 min", width="stretch", on_click=step_minute, disabled=nothing or not paused,
                    help="Advance one minute, then stay paused")


def settings_popover(version: str, llm_mode: str | None) -> None:
    st.selectbox("Incidents per scenario", INCIDENT_CHOICES, key="n_incidents", on_change=selection_changed,
                 help=f"{EVALUATED_INCIDENTS} is the evaluated setting (DESIGN 3.11). Fewer incidents are easier "
                      "to follow; one of them is always the surge (6 to 10 victims). A replay uses its recording's.")
    st.button("🎲 Restart with new seed", width="stretch", on_click=restart_clicked, args=(version, llm_mode),
              disabled=llm_mode in ("replay", "live"),
              help="Picks a random seed and waits at minute 0; press Resume to start")
    past_runs()
    compare_controls()


HOW_TO_READ = """**How to read this**

1. **The map.** Ambulances drive real Boston streets: purple = paramedic ambulances (ALS, the only ones that
   carry critical victims), blue = basic ambulances (BLS). Red circles are incidents with critical victims,
   amber without, grey once everyone is at a hospital. Hover any marker; click one to open its conversation.
2. **Conversations** (the first tab, right). Click an incident to read what the agents said to each other about it, in
   time order: "Dispatch → H1, H2: Can you take the critical patient?" and each hospital's answer with its
   reason. Grey lines starting with "·" say what happened in the world. **(AI)** after a line means that
   agent's AI decided it; hover it for the AI's own words. Hover a hospital (H1, H2, H3) for its name.
3. **The results** (top right): criticals on time, unserved critical victims, response time (report to first
   ambulance), average time to care, victims delivered and allocation conflicts so far. Below them, each kind
   of unit free now and how busy it has been so far, and each hospital's ER, free doctors and nurses and how
   much of it has been used so far (the **Resources** tab). The **Agents** tab has one card per agent: what it
   is doing, a hospital's mood and its last message; click one to see its messages, sent and received.
   The **Evaluation** page compares the four versions over 20 scenarios.
"""


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
    loaded = st.session_state.get("loaded") or {}
    if loaded.get("recording"):
        where += f" · replaying {replay_label(loaded['recording'])}"
    cfg = run["config"]
    placed = len(cfg.get("spawns") or [])
    version = loaded.get("version") or V.version_of(cfg)
    scenario = (f"seed {cfg['seed']} · {V.label(version)} · "
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
            st.session_state.update(focus=picked, story=picked, feed="Conversations")


def side_panel(key: str, run: dict, minute: int) -> None:
    m = kpis_at(key, minute, run)
    st.markdown(panels.kpi_cards_html(m), unsafe_allow_html=True)
    if st.session_state.spawn_mode and live() is not None:
        spawn_form()
        return
    feed.render(key, run, minute, is_agents(run), FEED_HEIGHT, roads(), m)


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
    right.caption(f"{V.label(V.version_of(run['config']))} · same seed")
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
    rid = st.session_state.run_id
    if rid is not None and st.session_state.get("synced") != rid:
        sync_selection(rid)
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
    version, llm_mode, recording = current_selection()
    seed = int(st.session_state.seed)
    if llm_mode == "replay" and recording:
        head = f"Press ▶ Run to load: {escape(replay_label(recording))}"
        what = "It replays the run with the AI's recorded answers, exactly as it happened, at no cost."
    elif llm_mode == "replay":
        head = f"No recording for seed {seed} with {escape(V.label(version))}"
        what = (f"Recorded seeds: {V.seeds_text(V.recorded_seeds(version)) or 'none'}. Pick one of them, or switch "
                "the LLM to Live (costs credit).")
    elif llm_mode == "live":
        head = f"Press ▶ Run to start a live run: {escape(V.label(version))}, seed {seed}"
        what = "The AI is called live and its answers are recorded; this costs API credit."
    else:
        head = f"Press ▶ Run to start: {escape(V.label(version))}, seed {seed}"
        what = f"It runs live from minute 0 with {st.session_state.n_incidents} incidents, at no cost."
    st.markdown(f"<div class='intro'><h3>{head}</h3><p>{what} Emergency calls come in across central "
                f"Boston ({escape(load_area()['name'])}); ambulances are sent and three hospitals take the patients, "
                "in about 6 minutes at 1x with quiet time skipped.</p></div>", unsafe_allow_html=True)
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
    st.session_state.setdefault("opened_from", {})[built[key]] = \
        run_store.source_of(h["run"]).relative_to(run_store.RUNS_DIR).as_posix()
    open_run(built[key])
    st.session_state.update(play_t=float(h["minute"]), focus=h["incident"], story=h["incident"], feed="Conversations")


def evaluation() -> None:
    style.apply()
    evaluation_page.render(roads(), PLAYBACK)


PLAYBACK = st.Page(playback, title="Simulation", icon="🗺️", default=True)
st.navigation([PLAYBACK, st.Page(evaluation, title="Evaluation", icon="📊", url_path="evaluation")],
              position="top").run()
