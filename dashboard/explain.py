"""The "what's happening now" sentence and the tooltip glossary (DESIGN 13.2), from the logged run.

No LLM is called here; names and phrases come from dashboard/wording.py.
"""
from __future__ import annotations

from collections import Counter
from html import escape

from dashboard import wording as W

GLOSSARY = {
    "Criticals on time": "Critical victims who reached a capable hospital before their deadline, out of all "
                         "critical victims reported so far. The headline measure.",
    "Unserved criticals": "Critical victims not at a hospital yet.",
    "Avg to care": "Average minutes from an incident being reported to its victims reaching a hospital.",
    "Delivered": "Victims who have reached a hospital so far.",
    "ALS": "Paramedic ambulance: the only kind that can carry a critical victim (1 critical or 2 others).",
    "BLS": "Basic ambulance: carries up to 2 serious or minor victims, never a critical.",
    "Referee": "The Arbiter: steps in only when the agents cannot agree in time, and its rulings are final.",
    "on track": "Expected at a hospital with at least 15 minutes to spare before the deadline.",
    "tight": "Expected at a hospital with less than 15 minutes to spare.",
    "late": "Past the deadline, or expected to arrive after it.",
    "ER": "Emergency room slots in use out of the total; (+n) are reserved for ambulances on the way.",
    "Walk-ins": "Patients who came to the hospital on their own; they always get a slot.",
    "Guard": "Rules that check the AI's choices: 'after transfer' offers only when a transfer can start, "
             "conditional offers need 15 minutes of slack, and at most 1 extra paramedic ambulance per incident.",
    "AI judgment": "The language model chose among the options the rules built.",
    "Rule": "A rule decided: either only one option fitted, or the AI was not used.",
    "Fallback": "The AI's answer did not fit the options, so the rule decided.",
}
OPEN = ("waiting", "assigned", "in_transit", "at_door")


def tip(term: str, text: str | None = None) -> str:
    """The term with an ⓘ tooltip."""
    return f"{escape(text or term)} <span class='tip' title='{escape(GLOSSARY.get(term, term), quote=True)}'>ⓘ</span>"


# ----- what's happening now -----

def now_sentence(run: dict, minute: int) -> str:
    """One sentence for the minute, from its events and the hospitals' state."""
    state = run["timeline"][minute]
    names = {i["id"]: i for i in state["incidents"]}
    ev = [e for e in run["events"] if e["t"] == minute]
    parts = [f"{W.incident_name(names[e['incident']])} reported" for e in ev if e["type"] == "incident_reported"
             and e["incident"] in names]
    sent = Counter((e["incident"], W.kind_of(e["unit"])) for e in ev if e["type"] in ("unit_dispatched", "unit_redirected"))
    parts += [f"{W.count_units(k, n)} on the way to {iid}" for (iid, k), n in sorted(sent.items())]
    arrived = [e for e in ev if e["type"] == "patient_delivered"]
    if arrived:
        parts.append(f"{W.plural(len(arrived), 'patient')} reached a hospital")
    severity = {v["id"]: v["original_severity"] for v in run["victims"]}
    parts += [f"{W.victims_phrase([e['victim']], severity)} missed their deadline" for e in ev
              if e["type"] == "deadline_missed" and e["severity"] == "critical"]
    parts += [f"referee called in for {W.victims_phrase(e['victim_ids'][:1], severity)}" for e in ev
              if e["type"] == "message" and e["msg_type"] == "escalation" and e.get("victim_ids")]
    full = [h["id"] for h in state["hospitals"] if h["er_used"] >= h["er_slots"]]
    almost = [h["id"] for h in state["hospitals"] if h["id"] not in full and h["utilization"] >= 0.85]
    if full:
        parts.append("all hospitals full" if len(full) == len(state["hospitals"])
                     else f"{' and '.join(W.place(h) for h in full)} full")
    if almost:
        parts.append(f"{' and '.join(W.place(h) for h in almost)} almost full")
    if len(parts) == len([p for p in parts if p.endswith("full")]):
        parts.insert(0, quiet_line(state))
    return f"Minute {minute}: " + ", ".join(parts[:5]) + "."


def quiet_line(state: dict) -> str:
    open_incidents = [i for i in state["incidents"] if any(v["status"] in OPEN for v in i["victims"])]
    moving = sum(1 for u in state["units"] if u["trip"])
    if not open_incidents and not moving:
        return "all quiet, no incident open"
    return f"{W.plural(len(open_incidents), 'incident')} open, {W.plural(moving, 'unit')} on the road"
