"""Agent activity and negotiation history (DESIGN 13.2), read from a recorded run's event stream.

Everything here formats recorded messages and decisions as they stood at a playback minute;
nothing decides or simulates. Each round costs one minute (DESIGN 7), so a request is pending
from when it is sent until it is delivered, unless its recipient has already answered.
"""
from __future__ import annotations

from collections import defaultdict

from coordination.tools import mood_for

AGENT_NAMES = {"ia": "Incident Assessment", "dispatch": "Dispatch", "arbiter": "Arbiter"}
REQUESTS = ("unit_request", "incident_update", "admission_cfp", "admission_counter", "admission_award",
            "mutual_aid_request", "swap_request", "transfer_request", "staff_request", "escalation")
PROTOCOLS = (  # the first type a thread contains names its protocol
    ("admission_cfp", "Admission"), ("transfer_request", "Transfer"), ("staff_request", "Staff loan"),
    ("mutual_aid_request", "Mutual aid"), ("swap_request", "Swap"), ("unit_request", "Unit request"),
    ("incident_update", "Unit request"), ("unit_response", "Unit request"), ("capacity_alert", "Reroute"),
    ("pre_alert", "Pre-alert"), ("escalation", "Escalation"),
)


def agent_ids(minute: dict) -> list[str]:
    return ["ia", "dispatch", *[h["id"] for h in minute["hospitals"]], "arbiter"]


def agent_name(agent: str, minute: dict | None = None) -> str:
    if agent in AGENT_NAMES:
        return AGENT_NAMES[agent]
    names = {h["id"]: h["name"] for h in minute["hospitals"]} if minute else {}
    return f"{agent} {names[agent]}" if agent in names else agent


def messages(events: list[dict]) -> list[dict]:
    return [e for e in events if e["type"] == "message"]


def addressed_to(m: dict, agent: str) -> bool:
    return m["recipient"] == agent or (m["recipient"] == "all_hospitals" and agent.startswith("H"))


def involves(m: dict, agent: str) -> bool:
    return m["sender"] == agent or addressed_to(m, agent)


# ----- message text -----

def _units(needed: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in needed.items() if v) or "none"


def _victims(vs: list) -> str:
    return ", ".join(v["id"] if isinstance(v, dict) else v for v in vs) or "–"


def _sent_so_far(ms: list[dict]) -> str:
    """Unit-request threads: every unit sent so far, then the latest shortfall."""
    responses = [m["proposed_action"] for m in ms if m["msg_type"] == "unit_response"]
    sent = [u for p in responses for u in p.get("sent", [])]
    last = responses[-1]
    short = f"; short {_units(last['shortfall'])} (plan: {last['plan']})" if last.get("shortfall") else ""
    return (f"sent {', '.join(sent)}" if sent else "no unit sent") + short


def summary(m: dict) -> str:
    """One line of what a message proposes, from its structured payload."""
    p, k = m.get("proposed_action") or {}, m["msg_type"]
    if k in ("unit_request", "incident_update") and p:
        return f"needs {_units(p['units_needed'])}; {p['at_risk']} at risk, {p['minutes_left']} min left"
    if k == "unit_response":
        sent = f"sent {', '.join(p['sent'])}" if p.get("sent") else "no unit sent"
        short = f"; short {_units(p['shortfall'])} (plan: {p['plan']})" if p.get("shortfall") else ""
        return sent + short
    if k in ("admission_cfp", "admission_counter") and p:
        who = ", ".join(f"{v['id']} ({v['severity']} {v['care_need']}, by {v['deadline']})" for v in p["victims"])
        return f"{who} on {p['unit']}, ready at {p['ready_at']}"
    if k == "admission_offer":
        parts = [f"accept {_victims(p['accept'])}"] if p.get("accept") else []
        parts += [f"{_victims(c['victims'])} after {c['after']}"
                  + (f" (min {c['after_minute']})" if c.get("after_minute") is not None else "")
                  for c in p.get("conditional", [])]
        parts += [f"decline {_victims(d['victims'])} ({d['reason']})" for d in p.get("decline", [])]
        return f"{p.get('mood', '')}: " + ("; ".join(parts) or "nothing")
    if k == "admission_award":
        cond = f"; conditional {_victims(p['conditional'])}" if p.get("conditional") else ""
        return f"award {_victims(p.get('victims', []))}{cond}"
    if k == "transfer_request":
        return f"take {p['victim']} ({p['care_need']}) from {p['from_hospital']}"
    if k == "transfer_response":
        return "accept" if p.get("accept") else "decline"
    if k == "staff_request":
        return f"{p['kind']} ({p['skill']}) for {p['minutes']} min"
    if k == "mutual_aid_request":
        return f"vehicle for {p['incident']} until minute {p['until']}"
    if k in ("staff_offer", "mutual_aid_offer", "swap_response"):
        res = f" {p['resource']}" if p.get("resource") else ""
        when = f" at {p['at_minute']}" if p.get("at_minute") is not None else ""
        return f"{p.get('answer', '')}{res}{when}"
    if k == "arbiter_decision":
        verdict = "meets deadline" if p.get("meets_deadline") else "misses deadline"
        return (f"option {p['option']} {p['option_name'].replace('_', ' ')} for {p['victim']}: "
                f"{p['hospital']} by {p['arrival']}, {verdict}")
    return _victims(m.get("victim_ids", [])) if m.get("victim_ids") else ""


def label(kind: str) -> str:
    return kind.replace("_", " ")


# ----- agent activity -----

def answered(m: dict, thread_msgs: list[dict], agent: str, t: int) -> bool:
    return any(r["sender"] == agent and m["seq"] < r["seq"] and r["t"] <= t for r in thread_msgs)


def pending(msgs: list[dict], by_thread: dict[str, list[dict]], agent: str, t: int, incoming: bool) -> list[dict]:
    """Requests in their round at minute t, to this agent (incoming) or from it, still unanswered."""
    out = []
    for m in msgs:
        if m["msg_type"] not in REQUESTS or not m["sent_at"] <= t <= m["deliver_at"]:
            continue
        mine = addressed_to(m, agent) if incoming else m["sender"] == agent
        replier = agent if incoming else m["recipient"]
        if mine and not answered(m, by_thread[m["thread_id"]], replier, t):
            out.append(m)
    return out


def last_decision(events: list[dict], agent: str, t: int) -> dict | None:
    return next((e for e in reversed(events) if e["t"] <= t and e["type"] == "agent_decision"
                 and e["agent"] == agent), None)


def status(waiting: list[dict], to_answer: list[dict], acted: bool) -> str:
    parts = []
    if to_answer:
        parts.append(f"{len(to_answer)} request{'s' * (len(to_answer) > 1)} to answer")
    if waiting:
        parts.append(f"waiting for {len(waiting)} repl{'ies' if len(waiting) > 1 else 'y'}")
    return "; ".join(parts) or ("active" if acted else "idle")


def message_row(m: dict, agent: str) -> dict:
    other = f"→ {m['recipient']}" if m["sender"] == agent else f"← {m['sender']}"
    return {"Min": m["t"], "With": other, "Message": label(m["msg_type"]), "Details": summary(m),
            "Reason": m.get("reason") or "", "By": m["decided_by"]}


def agent_cards(events: list[dict], minute: dict, t: int, recent: int = 5) -> list[dict]:
    """Per agent at minute t: status, pending requests, recent messages, last decision (DESIGN 13.2)."""
    msgs = [m for m in messages(events) if m["t"] <= t]
    by_thread = threads_of(msgs)
    hospitals = {h["id"]: h for h in minute["hospitals"]}
    cards = []
    for agent in agent_ids(minute):
        mine = [m for m in msgs if involves(m, agent)]
        waiting = pending(msgs, by_thread, agent, t, incoming=False)
        to_answer = pending(msgs, by_thread, agent, t, incoming=True)
        last = last_decision(events, agent, t)
        acted = any(m["t"] == t and m["sender"] == agent for m in mine) or (last is not None and last["t"] == t)
        card = {"id": agent, "name": agent_name(agent, minute), "status": status(waiting, to_answer, acted),
                "pending": [message_row(m, agent) for m in to_answer + waiting],
                "recent": [message_row(m, agent) for m in reversed(mine[-recent:])],
                "decision": last, "sent": sum(1 for m in mine if m["sender"] == agent),
                "received": sum(1 for m in mine if m["sender"] != agent)}
        if agent in hospitals:
            h = hospitals[agent]
            card["mood"] = mood_for(h["utilization"])
            card["er"] = f"{h['er_used']}/{h['er_slots']}"
            card["holds"] = len(h["holds"])
        cards.append(card)
    return cards


def decision_details(e: dict) -> str:
    if e["kind"] == "award":
        awards = [f"{a['hospital']}: {_victims(a['victims'])} (arrives {a['arrival']})" for a in e.get("awards", [])]
        esc = f"; escalate {_victims(e['escalate'])}" if e.get("escalate") else ""
        return ("; ".join(awards) or "no award") + esc
    if e["kind"] == "admission_offer":
        return f"accept {_victims(e.get('accept', []))}"
    return ""


def decision_text(e: dict | None) -> str:
    if e is None:
        return "no decision yet"
    by = {"rule": "rule", "llm": "LLM", "fallback": "fallback rule"}.get(e["decided_by"], e["decided_by"])
    return f"min {e['t']} · {label(e['kind'])} ({by}): {e['reason']}"


# ----- negotiation history -----

def threads_of(msgs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for m in msgs:
        out[m["thread_id"]].append(m)
    return out


def protocol(kinds: set[str]) -> str:
    return next((name for k, name in PROTOCOLS if k in kinds), "Other")


def thread_index(events: list[dict]) -> dict[str, dict]:
    """Every thread of the run with its messages and the agent decisions that name it."""
    index = {th: {"id": th, "messages": ms, "protocol": protocol({m["msg_type"] for m in ms}), "decisions": []}
             for th, ms in threads_of(messages(events)).items()}
    for e in events:
        if e["type"] == "agent_decision" and e.get("thread") in index:
            index[e["thread"]]["decisions"].append(e)
    return index


def outcome(ms: list[dict], t: int) -> str:
    """Where the thread stands at minute t, from the messages so far."""
    last_of = {m["msg_type"]: m for m in ms}
    if "arbiter_decision" in last_of:
        p = last_of["arbiter_decision"]["proposed_action"]
        return f"Arbiter: {label(p['option_name'])} at {p['hospital']}" + (" (loss)" if p.get("loss") else "")
    if "escalation" in last_of:
        return "escalated to the Arbiter"
    if "admission_award" in last_of:
        award = last_of["admission_award"]
        later = [m for m in ms if m["seq"] > award["seq"] and m["sender"] == award["recipient"]]
        if any(m["msg_type"] == "decline" for m in later):
            return f"award declined by {award['recipient']}"
        return f"awarded to {award['recipient']}" + (", reserved" if any(m["msg_type"] == "ack" for m in later) else "")
    if "transfer_response" in last_of:
        r = last_of["transfer_response"]
        return f"{'accepted' if r['proposed_action'].get('accept') else 'declined'} by {r['sender']}"
    for k in ("staff_offer", "mutual_aid_offer", "swap_response"):
        if k in last_of:
            return f"{last_of[k]['proposed_action'].get('answer', '')} from {last_of[k]['sender']}"
    if "unit_response" in last_of:
        return _sent_so_far(ms)
    rounds = max(m["round"] for m in ms)
    if any(m["deliver_at"] >= t for m in ms):
        return f"round {rounds} in progress" if "admission_cfp" in last_of else "in progress"
    if "admission_offer" in last_of:
        return f"no award after {rounds} round{'s' * (rounds > 1)}"
    return "no reply" if any(m["msg_type"] in REQUESTS for m in ms) else "notified"


def thread_row(th: dict, t: int) -> dict | None:
    ms = [m for m in th["messages"] if m["t"] <= t]
    if not ms:
        return None
    incidents = sorted({m["incident_id"] for m in ms if m.get("incident_id")})
    victims = sorted({v for m in ms for v in m.get("victim_ids", [])})
    return {"Thread": th["id"], "Protocol": th["protocol"], "Incident": ", ".join(incidents),
            "Victims": ", ".join(victims), "Started": ms[0]["t"], "Last": ms[-1]["t"],
            "Rounds": max(m["round"] for m in ms), "Messages": len(ms),
            "Counters": sum(1 for m in ms if m["msg_type"] == "admission_counter"),
            "Declines": sum(1 for m in ms if is_decline(m)), "Outcome": outcome(ms, t)}


def is_decline(m: dict) -> bool:
    p = m.get("proposed_action") or {}
    if m["msg_type"] == "admission_offer":
        return not p.get("accept") and not p.get("conditional")
    return m["msg_type"] == "decline" or p.get("answer") == "decline" or p.get("accept") is False


def thread_rows(index: dict[str, dict], t: int, protocols: list[str] | None = None,
                incident: str | None = None) -> list[dict]:
    """Threads with activity up to minute t, most recently active first."""
    rows = [r for th in index.values() if (r := thread_row(th, t))]
    rows = [r for r in rows if (not protocols or r["Protocol"] in protocols)
            and (not incident or incident in r["Incident"].split(", "))]
    return sorted(rows, key=lambda r: (r["Last"], r["Thread"]), reverse=True)


def thread_detail(th: dict, t: int) -> list[dict]:
    """The thread's messages and linked decisions up to minute t, in the order they happened."""
    items = [e for e in th["messages"] + th["decisions"] if e["t"] <= t]
    rows = []
    for e in sorted(items, key=lambda e: e["seq"]):
        if e["type"] == "message":
            rows.append({"Min": e["t"], "Round": e["round"], "From → to": f"{e['sender']} → {e['recipient']}",
                         "What": label(e["msg_type"]), "Details": summary(e), "Reason": e.get("reason") or "",
                         "By": e["decided_by"], "Arrives": e["deliver_at"]})
        else:
            rows.append({"Min": e["t"], "Round": None, "From → to": f"{e['agent']} decides",
                         "What": label(e["kind"]), "Details": decision_details(e), "Reason": e["reason"],
                         "By": e["decided_by"], "Arrives": None})
    return rows
