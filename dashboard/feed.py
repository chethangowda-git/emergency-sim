"""The feed on the Simulation page (DESIGN 13.2): Incidents (each incident's conversation) and Log.

Everything is formatted from the run's logged data at the playback minute (dashboard/conversation.py and
dashboard/wording.py); results are cached per run and minute because the page redraws several times a second.
"""
from __future__ import annotations

import streamlit as st

from dashboard import agent_views, conversation, explain_agents, panels

FEEDS = ("Incidents", "Log")
HELP = ("Incidents: what the agents said to each other about each incident, active ones first. "
        "Log: the raw event list, for debugging.")


@st.cache_resource(max_entries=8, show_spinner=False)
def thread_index(key: str, n_events: int, _events: list[dict]) -> dict:
    return agent_views.thread_index(_events)


@st.cache_resource(max_entries=8, show_spinner=False)
def calls_of(key: str, n_events: int, _run: dict) -> dict[int, dict]:
    return explain_agents.match_calls(_run["events"], _run.get("llm_calls", []))


def context(key: str, minute: int, run: dict, roads) -> conversation.Context:
    n = len(run["events"])
    return conversation.Context(run, minute, thread_index(key, n, run["events"]), calls_of(key, n, run), roads)


@st.cache_data(max_entries=600, show_spinner=False)
def entries_at(key: str, minute: int, _run: dict, _roads) -> list[dict]:
    ctx = context(key, minute, _run, _roads)
    ids = [i["id"] for i in _run["timeline"][minute]["incidents"]]
    return conversation.entries(_run, minute, bool(conversation.conversation(ctx, conversation.BETWEEN, ids)))


@st.cache_data(max_entries=1200, show_spinner=False)
def conversation_at(key: str, minute: int, incident: str, _run: dict, _roads) -> str:
    ctx = context(key, minute, _run, _roads)
    ids = [i["id"] for i in _run["timeline"][minute]["incidents"]]
    return conversation.conversation_html(conversation.conversation(ctx, incident, ids))


def select(incident: str) -> None:
    """Open an incident's conversation and highlight it on the map."""
    st.session_state.update(story=incident, focus=incident)


def render(key: str, run: dict, minute: int, agents: bool, height: int, roads=None) -> None:
    if st.session_state.get("feed") not in FEEDS:
        st.session_state.feed = FEEDS[0]
    feed = st.segmented_control("Feed", FEEDS, key="feed", required=True, label_visibility="collapsed", help=HELP)
    with st.container(height=height, border=True, key="feedbox"):
        if feed == "Log":
            st.markdown(panels.events_html(panels.event_feed(run["events"], minute, n=80, agents=True), minute),
                        unsafe_allow_html=True)
        else:
            incidents_tab(key, run, minute, roads)


def incidents_tab(key: str, run: dict, minute: int, roads) -> None:
    rows = entries_at(key, minute, run, roads)
    if not rows:
        st.caption("No incident reported yet.")
        return
    ids = [r["id"] for r in rows]
    chosen = st.session_state.get("story") or st.session_state.get("focus")
    if chosen not in ids:
        chosen = next((r["id"] for r in rows if r["active"]), None)
    for r in rows:
        st.button(f"{'▾ ' if r['id'] == chosen else '▸ '}{r['label']}", key=f"inc_{r['id']}", type="tertiary",
                  on_click=select, args=(r["id"],), help="Show what the agents said about this incident and highlight it on the map")
        if r["id"] == chosen:
            st.markdown(conversation_at(key, minute, r["id"], run, roads), unsafe_allow_html=True)
