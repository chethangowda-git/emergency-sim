"""Streamlit dashboard: run, reopen and watch simulations. Start with the launcher."""
from __future__ import annotations

import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from dashboard import map_view, panels, run_store  # noqa: E402
from sim import metrics  # noqa: E402
from sim.config import default_config, load_area  # noqa: E402
from sim.runner import POLICIES, load_roads  # noqa: E402

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


def start_run(seed: int, policy: str) -> None:
    bar = st.sidebar.progress(0.0, text=f"Simulating seed {seed} ...")

    def progress(done: int, total: int) -> None:
        if done % 10 == 0 or done == total:
            bar.progress(done / total, text=f"Simulating seed {seed}: minute {done}/{total}")

    run_id = run_store.run_and_save(seed, policy, progress, roads=roads())
    bar.empty()
    open_run(run_id)
    st.session_state.playing = True
    st.session_state.last_tick = time.monotonic()


def sidebar() -> None:
    sb = st.sidebar
    sb.header("🚑 Emergency response")
    seed = sb.number_input("Seed", min_value=0, max_value=1_000_000, value=42, step=1)
    policy = sb.selectbox("Policy", sorted(POLICIES))
    if sb.button("Run simulation", type="primary", width="stretch"):
        start_run(int(seed), policy)
    if sb.button("New scenario", width="stretch", help="Random seed, then run it"):
        start_run(secrets.randbelow(100_000), policy)

    runs = run_store.list_runs()
    if runs:
        current = st.session_state.run_id if st.session_state.run_id in runs else runs[0]
        choice = sb.selectbox("Past runs", runs, index=runs.index(current))
        if choice != st.session_state.run_id:
            open_run(choice)

    sb.divider()
    sb.subheader("Playback")
    label = "⏸ Pause" if st.session_state.playing else "▶ Play"
    if sb.button(label, width="stretch", disabled=st.session_state.run_id is None):
        st.session_state.playing = not st.session_state.playing
        st.session_state.last_tick = time.monotonic()
        st.rerun()
    sb.radio("Speed (simulated minutes per second)", list(SPEEDS), horizontal=True, key="speed")
    sb.caption("Drag the time slider above the map to jump to any minute.")


def on_slider() -> None:
    st.session_state.play_t = float(st.session_state.minute_slider)


def advance(horizon: int) -> None:
    now = time.monotonic()
    if st.session_state.playing:
        dt = min(1.0, now - st.session_state.last_tick)
        st.session_state.play_t = min(horizon - 1.0, st.session_state.play_t + SPEEDS[st.session_state.speed] * dt)
        st.session_state.minute_slider = int(st.session_state.play_t)
    st.session_state.last_tick = now


def view_state() -> dict:
    b = load_area()["bbox"]
    return {"latitude": (b["south"] + b["north"]) / 2, "longitude": (b["west"] + b["east"]) / 2,
            "zoom": 13.2, "pitch": 0}


def playback_body(run_id: str) -> None:
    run = load(run_id)
    horizon = len(run["timeline"])
    advance(horizon)
    st.slider("Minute", 0, horizon - 1, key="minute_slider", on_change=on_slider)
    t = st.session_state.play_t
    minute = int(t)
    state = run["timeline"][minute]

    left, right = st.columns([3, 1])
    with left:
        st.pydeck_chart(map_view.build_deck(run, t, view_state()), height=560, key="map")
        st.markdown(map_view.LEGEND_HTML, unsafe_allow_html=True)
    with right:
        st.markdown(f"#### Minute {minute} of {horizon - 1}")
        for name, value in panels.kpis(kpis_at(run_id, minute)):
            st.metric(name, value)

    c1, c2 = st.columns([1, 2])
    with c1:
        st.markdown("##### Units")
        st.dataframe(panels.unit_table(state), width="stretch")
        st.dataframe(panels.unit_states(state), width="stretch", height=250)
    with c2:
        st.markdown("##### Hospitals")
        st.dataframe(panels.hospital_table(state), width="stretch")
        st.dataframe(panels.per_hospital_kpis(kpis_at(run_id, minute)), width="stretch")
    st.markdown("##### Live events")
    feed = panels.event_feed(run["events"], minute)
    st.markdown("\n".join(f"- **{et:>3}** {'🟢 ' if et == minute else ''}{text}" for et, text in feed)
                or "_No events yet._")

    if st.session_state.playing and st.session_state.play_t >= horizon - 1:
        st.session_state.playing = False
        st.rerun(scope="app")


def main() -> None:
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
    fragment(run_id)


main()
