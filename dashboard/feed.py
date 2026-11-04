"""The feed on the Simulation page (DESIGN 13.2): Incidents (each incident's conversation) and Log.

Everything is formatted from the run's logged data at the playback minute (dashboard/conversation.py and
dashboard/wording.py); results are cached per run and minute because the page redraws several times a second.
"""
from __future__ import annotations

import streamlit as st

from dashboard import agent_strip, agent_views, conversation, explain_agents, panels

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
def conversation_at(key: str, minute: int, incident: str, agent: str | None, _run: dict, _roads) -> str:
    ctx = context(key, minute, _run, _roads)
    ids = [i["id"] for i in _run["timeline"][minute]["incidents"]]
    lines = agent_strip.only(conversation.conversation(ctx, incident, ids), agent)
    if agent and not lines:
        return f"<div class='cv-n'>· {conversation.name(agent)} has not said or been asked anything here yet.</div>"
    return conversation.conversation_html(lines)


@st.cache_data(max_entries=600, show_spinner=False)
def strip_at(key: str, minute: int, _run: dict, _roads) -> list[dict]:
    return agent_strip.rows(context(key, minute, _run, _roads))


def pick_agent(agent: str) -> None:
    """Filter the conversations to one agent; clicking it again shows everyone."""
    st.session_state.agent_filter = None if st.session_state.get("agent_filter") == agent else agent
    st.session_state.feed = FEEDS[0]


def agents_strip(key: str, run: dict, minute: int, roads) -> None:
    """One line per agent (DESIGN 13.2): status, mood, pending requests, last message; click to filter."""
    picked = st.session_state.get("agent_filter")
    with st.container(key="agentstrip"):
        for row in strip_at(key, minute, run, roads):
            st.button(("▸ " if row["id"] == picked else "") + agent_strip.label(row), key=f"agent_{row['id']}",
                      type="tertiary", on_click=pick_agent, args=(row["id"],), width="stretch",
                      help=(row["last_text"] or "No message yet")
                      + "\n\nClick to show only this agent's messages in the conversations; click again for all.")


def select(incident: str) -> None:
    """Open an incident's conversation and highlight it on the map."""
    st.session_state.update(story=incident, focus=incident)


def render(key: str, run: dict, minute: int, agents: bool, height: int, roads=None) -> None:
    if agents:
        agents_strip(key, run, minute, roads)
    else:
        st.session_state.agent_filter = None
    if st.session_state.get("feed") not in FEEDS:
        st.session_state.feed = FEEDS[0]
    feed = st.segmented_control("Feed", FEEDS, key="feed", required=True, label_visibility="collapsed", help=HELP)
    with st.container(height=height, border=True, key="feedbox" if agents else "feedbox_tall"):
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
    agent = st.session_state.get("agent_filter")
    if agent:
        left, right = st.columns([3, 1], vertical_alignment="center")
        left.markdown(f"<div class='cv-n' style='margin-left:0'>Showing {conversation.name(agent)} only</div>",
                      unsafe_allow_html=True)
        right.button("Show all", key="agent_all", type="tertiary", on_click=pick_agent, args=(agent,))
    ids = [r["id"] for r in rows]
    chosen = st.session_state.get("story") or st.session_state.get("focus")
    if chosen not in ids:
        chosen = next((r["id"] for r in rows if r["active"]), None)
    for r in rows:
        st.button(f"{'▾ ' if r['id'] == chosen else '▸ '}{r['label']}", key=f"inc_{r['id']}", type="tertiary",
                  on_click=select, args=(r["id"],), help="Show what the agents said about this incident and highlight it on the map")
        if r["id"] == chosen:
            st.markdown(conversation_at(key, minute, r["id"], agent, run, roads), unsafe_allow_html=True)
