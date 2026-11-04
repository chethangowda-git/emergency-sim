"""The tabs under the KPI cards on the Simulation page (DESIGN 13.2): Conversations, Agents, Resources, Log.

Everything is formatted from the run's logged data at the playback minute (dashboard/conversation.py,
dashboard/agents_tab.py and dashboard/wording.py); results are cached per run and minute because the
page redraws several times a second.
"""
from __future__ import annotations

import streamlit as st

from dashboard import agent_views, agents_tab, conversation, explain_agents, panels

FEEDS = ("Conversations", "Agents", "Resources", "Log")
INTROS = {"Conversations": "Messages between agents, grouped by incident.",
          "Agents": "What each agent is doing right now. Click one to see its messages.",
          "Resources": "Ambulances, teams and hospital capacity.",
          "Log": "Every event, in time order, for debugging."}
GREEDY = {"Conversations": "The greedy baseline doesn't negotiate; its decisions are in the Log.",
          "Agents": "The greedy baseline has no agents."}


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


@st.cache_data(max_entries=600, show_spinner=False)
def cards_at(key: str, minute: int, _run: dict, _roads) -> list[dict]:
    return agents_tab.cards(context(key, minute, _run, _roads))


@st.cache_data(max_entries=600, show_spinner=False)
def agent_lines_at(key: str, minute: int, agent: str, _run: dict, _roads) -> list[dict]:
    return agents_tab.agent_lines(context(key, minute, _run, _roads), agent)


def select(incident: str) -> None:
    """Open an incident's conversation and highlight it on the map."""
    st.session_state.update(story=incident, focus=incident)


def toggle_agent(agent: str) -> None:
    """Open one agent's messages under its card; clicking it again closes it."""
    opened = st.session_state.get("agent_open")
    st.session_state.agent_open = None if opened == agent else agent
    st.session_state.agent_shown = agents_tab.PAGE


def show_earlier() -> None:
    st.session_state.agent_shown = st.session_state.get("agent_shown", agents_tab.PAGE) + agents_tab.PAGE


def render(key: str, run: dict, minute: int, agents: bool, height: int, roads=None, m: dict | None = None) -> None:
    if not agents:
        st.session_state.agent_open = None
    if st.session_state.get("feed") not in FEEDS:
        st.session_state.feed = FEEDS[0]
    feed = st.segmented_control("Feed", FEEDS, key="feed", required=True, label_visibility="collapsed",
                                help="Conversations: what the agents said, by incident. Agents: what each one is doing. "
                                     "Resources: units and hospital capacity. Log: the raw events.")
    with st.container(height=height, border=True, key="feedbox"):
        st.markdown(f"<div class='tab-intro'>{INTROS[feed]}</div>", unsafe_allow_html=True)
        if not agents and feed in GREEDY:
            st.caption(GREEDY[feed])
        elif feed == "Log":
            st.markdown(panels.events_html(panels.event_feed(run["events"], minute, n=80, agents=True), minute),
                        unsafe_allow_html=True)
        elif feed == "Resources":
            state = run["timeline"][minute]
            st.markdown(panels.units_html(state, m) + panels.hospitals_html(state, m), unsafe_allow_html=True)
        elif feed == "Agents":
            agents_list(key, run, minute, roads)
        else:
            incidents_tab(key, run, minute, roads)


def agents_list(key: str, run: dict, minute: int, roads) -> None:
    """One card per agent; clicking one opens its messages right below it (one agent at a time)."""
    opened = st.session_state.get("agent_open")
    with st.container(key="agentlist"):
        for card in cards_at(key, minute, run, roads):
            is_open = card["id"] == opened             # the key carries the state, for the open card's highlight
            st.button(("▾ " if is_open else "▸ ") + agents_tab.card_label(card),
                      key=f"agent_{'open_' if is_open else ''}{card['id']}", width="stretch",
                      on_click=toggle_agent, args=(card["id"],),
                      help="Click to show this agent's messages below; click again to close.")
            if card["id"] == opened:
                agent_panel(key, run, minute, roads, card["id"])


def agent_panel(key: str, run: dict, minute: int, roads, agent: str) -> None:
    lines = agent_lines_at(key, minute, agent, run, roads)
    shown = st.session_state.get("agent_shown", agents_tab.PAGE)
    earlier = len(lines) - shown
    if earlier > 0:
        st.button(f"Show earlier ({earlier} more)", key=f"earlier_{agent}", type="tertiary", on_click=show_earlier)
    st.markdown(agents_tab.lines_html(lines[-shown:]), unsafe_allow_html=True)


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
