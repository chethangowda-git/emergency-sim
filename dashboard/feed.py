"""The feed on the Simulation page (DESIGN 13.2): Incidents (each incident's story) and Log.

Everything is formatted from the run's logged data at the playback minute (dashboard/story.py and
dashboard/wording.py); results are cached per run and minute because the page redraws several times a second.
"""
from __future__ import annotations

import streamlit as st

from dashboard import agent_views, explain_agents, panels, story

FEEDS = ("Incidents", "Log")
HELP = "Incidents: each incident's story in plain sentences, active ones first. Log: the raw event list, for debugging."


@st.cache_resource(max_entries=8, show_spinner=False)
def thread_index(key: str, n_events: int, _events: list[dict]) -> dict:
    return agent_views.thread_index(_events)


@st.cache_resource(max_entries=8, show_spinner=False)
def calls_of(key: str, n_events: int, _run: dict) -> dict[int, dict]:
    return explain_agents.match_calls(_run["events"], _run.get("llm_calls", []))


@st.cache_data(max_entries=600, show_spinner=False)
def entries_at(key: str, minute: int, _run: dict) -> list[dict]:
    return story.entries(_run, minute)


@st.cache_data(max_entries=1200, show_spinner=False)
def story_at(key: str, minute: int, incident: str, _run: dict) -> str:
    ctx = story.Context(_run, minute, thread_index(key, len(_run["events"]), _run["events"]),
                        calls_of(key, len(_run["events"]), _run))
    return story.story_html(story.story(ctx, incident), story.now_line(ctx, incident))


def select(incident: str) -> None:
    """Open an incident's story and highlight it on the map."""
    st.session_state.update(story=incident, focus=incident)


def render(key: str, run: dict, minute: int, agents: bool, height: int) -> None:
    if st.session_state.get("feed") not in FEEDS:
        st.session_state.feed = FEEDS[0]
    feed = st.segmented_control("Feed", FEEDS, key="feed", required=True, label_visibility="collapsed", help=HELP)
    with st.container(height=height, border=True, key="feedbox"):
        if feed == "Log":
            st.markdown(panels.events_html(panels.event_feed(run["events"], minute, n=80, agents=True), minute),
                        unsafe_allow_html=True)
        else:
            incidents_tab(key, run, minute)


def incidents_tab(key: str, run: dict, minute: int) -> None:
    rows = entries_at(key, minute, run)
    if not rows:
        st.caption("No incident reported yet.")
        return
    ids = [r["id"] for r in rows]
    chosen = st.session_state.get("story") or st.session_state.get("focus")
    if chosen not in ids:
        chosen = next((r["id"] for r in rows if r["active"]), None)
    for r in rows:
        st.button(f"{'▾ ' if r['id'] == chosen else '▸ '}{r['label']}", key=f"inc_{r['id']}", type="tertiary",
                  on_click=select, args=(r["id"],), help="Show this incident's story and highlight it on the map")
        if r["id"] == chosen:
            st.markdown(story_at(key, minute, r["id"], run), unsafe_allow_html=True)
