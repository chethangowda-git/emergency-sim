"""The tabbed feed on the Simulation page (DESIGN 13.2): Incidents, Negotiations, Decisions, Hospitals, Log.

Everything is formatted from the run's logged data at the playback minute (dashboard/explain*.py);
results are cached per run and minute because the page redraws several times a second.
"""
from __future__ import annotations

import streamlit as st

from dashboard import agent_views, explain, explain_agents, panels, timeline

FEEDS = ("Incidents", "Negotiations", "Decisions", "Hospitals", "Log")
BASELINE_FEEDS = ("Incidents", "Hospitals", "Log")
HELP = {"Incidents": "One card per incident, least time left first: its progress and where each victim is.",
        "Negotiations": "Each negotiation as a question, its outcome, and who said what (reasons in grey).",
        "Decisions": "What each agent saw, decided and why; by default only where the AI differed from the rule, "
                     "the guard stepped in, or the referee ruled.",
        "Hospitals": "Each hospital's patients, the offers it made and its walk-ins.",
        "Log": "The raw event timeline, for debugging."}


# ----- cached views (a saved run's key is its run id; a live run's changes every minute) -----

@st.cache_resource(max_entries=8, show_spinner=False)
def thread_index(key: str, n_events: int, _events: list[dict]) -> dict:
    return agent_views.thread_index(_events)


def severities(run: dict) -> dict[str, str]:
    return {v["id"]: v["original_severity"] for v in run["victims"]}


@st.cache_data(max_entries=600, show_spinner=False)
def threads_at(key: str, minute: int, _run: dict) -> list[dict]:
    return explain_agents.thread_views(thread_index(key, len(_run["events"]), _run["events"]), minute, severities(_run))


@st.cache_data(max_entries=600, show_spinner=False)
def incidents_at(key: str, minute: int, _run: dict) -> list[dict]:
    return explain.incident_cards(_run, minute, threads_at(key, minute, _run))


@st.cache_resource(max_entries=8, show_spinner=False)
def calls_of(key: str, n_events: int, _run: dict) -> dict[int, dict]:
    return explain_agents.match_calls(_run["events"], _run.get("llm_calls", []))


@st.cache_data(max_entries=600, show_spinner=False)
def decisions_at(key: str, minute: int, show_all: bool, _run: dict) -> list[dict]:
    incidents = {i["id"]: i for i in _run["timeline"][minute]["incidents"]}
    return explain_agents.decision_cards(_run["events"], minute, incidents, calls_of(key, len(_run["events"]), _run),
                                         show_all, severities(_run))


@st.cache_data(max_entries=600, show_spinner=False)
def hospitals_at(key: str, minute: int, _run: dict) -> list[dict]:
    return explain.hospital_cards(_run, minute)


# ----- the feed -----

def focus_on(incident: str | None) -> None:
    st.session_state.focus = incident


def render(key: str, run: dict, minute: int, agents: bool, height: int) -> None:
    feeds = FEEDS if agents else BASELINE_FEEDS
    if st.session_state.get("feed") not in feeds:
        st.session_state.feed = feeds[0]
    tabs, filt = st.columns([6, 1], vertical_alignment="center")
    feed = tabs.segmented_control("Feed", feeds, key="feed", required=True, label_visibility="collapsed",
                                  help=" · ".join(f"{f}: {HELP[f]}" for f in feeds))
    incident, agent = filters(filt, run, minute, agents)
    if feed == "Negotiations":
        negotiations_tab(key, run, minute, incident, agent, height)
        return
    with st.container(height=height, border=True, key="feedbox"):
        {"Incidents": incidents_tab, "Decisions": decisions_tab, "Hospitals": hospitals_tab,
         "Log": log_tab}[feed](key, run, minute, incident, agent)


def filters(col, run: dict, minute: int, agents: bool) -> tuple[str | None, str | None]:
    incidents = ["All", *[i["id"] for i in run["timeline"][minute]["incidents"]]]
    agent_ids = ["All", *(agent_views.agent_ids(run["timeline"][minute]) if agents
                          else [h["id"] for h in run["timeline"][minute]["hospitals"]])]
    for k, opts in (("f_incident", incidents), ("f_agent", agent_ids)):
        if st.session_state.get(k) not in opts:
            st.session_state[k] = "All"
    active = st.session_state.f_incident != "All" or st.session_state.f_agent != "All"
    with col.popover("🔍" + (" •" if active else ""), help="Filter the feed by incident or agent"):
        st.selectbox("Incident", incidents, key="f_incident")
        st.selectbox("Agent", agent_ids, key="f_agent", format_func=lambda a: a if a == "All" else explain.agent_name(a))
        if st.session_state.get("focus"):
            st.button("Clear the map highlight", on_click=focus_on, args=(None,), width="stretch")
    pick = lambda k: None if st.session_state[k] == "All" else st.session_state[k]
    return pick("f_incident"), pick("f_agent")


def incidents_tab(key: str, run: dict, minute: int, incident: str | None, agent: str | None) -> None:
    cards = [c for c in incidents_at(key, minute, run) if incident in (None, c["id"])]
    if not cards:
        st.caption("No incident reported yet.")
        return
    focus = st.session_state.get("focus")
    cards.sort(key=lambda c: c["id"] != focus)          # a highlighted incident's card comes first
    for c in cards:
        if c["open"]:
            st.button(f"{'📍 ' if focus == c['id'] else ''}{c['title']}", key=f"card_{c['id']}", type="tertiary",
                      on_click=focus_on, args=(None if focus == c["id"] else c["id"],),
                      help="Highlight this incident, its units and their routes on the map")
        st.markdown(explain.incident_card_html(c, focus == c["id"]), unsafe_allow_html=True)


@st.cache_data(max_entries=1200, show_spinner=False)
def steps_at(key: str, minute: int, incident: str | None, _run: dict) -> list[dict]:
    ctx = timeline.Context(_run, minute, thread_index(key, len(_run["events"]), _run["events"]),
                           calls_of(key, len(_run["events"]), _run))
    return timeline.incident_steps(ctx, incident)


@st.cache_data(max_entries=600, show_spinner=False)
def incident_list_at(key: str, minute: int, _run: dict) -> list[dict]:
    return timeline.incident_list(_run, minute, lambda iid: steps_at(key, minute, iid, _run))


def pick_incident(iid: str | None) -> None:
    st.session_state.update(neg_incident=iid or "requests", focus=iid)


def negotiations_tab(key: str, run: dict, minute: int, incident: str | None, agent: str | None, height: int) -> None:
    """Left: one entry per incident (active first) and 'Hospital requests'. Right: the chosen one's timeline."""
    entries = incident_list_at(key, minute, run)
    if not entries:
        with st.container(height=height, border=True, key="feedbox"):
            st.caption("No incident reported yet.")
        return
    ids = [e["id"] or "requests" for e in entries]
    chosen = incident or st.session_state.get("neg_incident") or st.session_state.get("focus")
    if chosen not in ids:
        active = [e for e in entries if e["active"]]
        chosen = (active or entries)[0]["id"] or "requests"
    left, right = st.columns([2, 5], gap="small")
    with left.container(height=height, border=True, key="neglist"):
        for e in entries:
            eid = e["id"] or "requests"
            st.button(f"{'▸ ' if eid == chosen else ''}{e['name']}", key=f"inc_{eid}", type="tertiary",
                      on_click=pick_incident, args=(e["id"],), help="Show this incident's timeline")
            status = "active" if e["active"] else "finished" if e["id"] else "not tied to one incident"
            st.markdown(f"<div class='tentry'>{status} · {e['steps']} steps</div>", unsafe_allow_html=True)
    with right.container(height=height, border=True, key="negdetail"):
        steps = steps_at(key, minute, None if chosen == "requests" else chosen, run)
        if not steps:
            st.caption("Nothing yet.")
        for i, s in enumerate(steps):
            st.markdown(timeline.step_html(s), unsafe_allow_html=True)
            if s["messages"]:
                with st.expander(f"Messages ({len(s['messages'])})", key=f"msg_{chosen}_{i}_{s['t']}"):
                    st.markdown(explain_agents.bubbles_html(s["messages"]), unsafe_allow_html=True)


def decisions_tab(key: str, run: dict, minute: int, incident: str | None, agent: str | None) -> None:
    show_all = st.toggle("Show every decision", key="dec_all",
                         help="Off: only where the AI differed from the rule, the guard stepped in, a fallback was "
                              "needed, or the referee ruled")
    cards = [c for c in decisions_at(key, minute, show_all, run) if incident in (None, c.get("incident"))
             and agent in (None, c["agent"])]
    if not cards:
        st.caption("Nothing to show yet: every decision so far was the rule's own." if not show_all
                   else "No decision yet.")
        return
    st.markdown("".join(explain_agents.decision_html(c) for c in cards[:40]), unsafe_allow_html=True)


def hospitals_tab(key: str, run: dict, minute: int, incident: str | None, agent: str | None) -> None:
    cards = [h for h in hospitals_at(key, minute, run) if agent in (None, h["id"])]
    st.markdown("".join(explain.hospital_card_html(h) for h in cards) or "<i>No hospital matches.</i>",
                unsafe_allow_html=True)


def log_tab(key: str, run: dict, minute: int, incident: str | None, agent: str | None) -> None:
    events = [e for e in run["events"] if (incident is None or incident in str(e))
              and (agent is None or agent in (e.get("agent"), e.get("sender"), e.get("recipient"), e.get("hospital")))]
    st.markdown(panels.events_html(panels.event_feed(events, minute, n=80, agents=True), minute), unsafe_allow_html=True)
