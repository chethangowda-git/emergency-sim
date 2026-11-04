"""Plain-language explanations of a run (DESIGN 13.2), filled into templates from the logged data.

No LLM is called here. Friendly names come first ("Paramedic ambulance (ALS)"); every term has a
tooltip in GLOSSARY. Incident cards, the "what's happening now" sentence and the hospital view all
read the timeline and the event log at the playback minute.
"""
from __future__ import annotations

from collections import Counter
from html import escape

from coordination.tools import mood_for

UNIT_NAMES = {"ALS": "Paramedic ambulance (ALS)", "BLS": "Basic ambulance (BLS)", "FIRE": "Fire team",
              "RESCUE": "Rescue team", "HV": "Hospital transport vehicle"}
UNIT_SHORT = {"ALS": ("paramedic ambulance", "paramedic ambulances"), "BLS": ("basic ambulance", "basic ambulances"),
              "FIRE": ("fire team", "fire teams"), "RESCUE": ("rescue team", "rescue teams"),
              "HV": ("hospital vehicle", "hospital vehicles")}
AGENT_NAMES = {"ia": "Incident Assessment", "dispatch": "Dispatch", "arbiter": "Referee agent (Arbiter)"}
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
TIGHT_MINUTES = 15
STEPS = ("reported", "units sent", "scene cleared", "transporting", "all at hospital")


def tip(term: str, text: str | None = None) -> str:
    """The term with an ⓘ tooltip."""
    return f"{escape(text or term)} <span class='tip' title='{escape(GLOSSARY.get(term, term), quote=True)}'>ⓘ</span>"


def kind_of(unit_id: str) -> str:
    return "HV" if "-TV" in unit_id else next((k for k in ("ALS", "BLS", "FIRE", "RESCUE") if unit_id.startswith(k)),
                                              "HV")


def unit_name(unit_id: str) -> str:
    return f"{UNIT_SHORT[kind_of(unit_id)][0]} {unit_id}"


def count_units(kind: str, n: int) -> str:
    one, many = UNIT_SHORT[kind]
    return f"{n} {one if n == 1 else many}"


def agent_name(agent: str) -> str:
    return AGENT_NAMES.get(agent, f"Hospital {agent}")


def incident_title(inc: dict) -> str:
    return f"{inc['id']} {inc['type'].replace('_', ' ')}"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ----- what's happening now -----

def now_sentence(run: dict, minute: int) -> str:
    """One sentence for the minute, from its events and the hospitals' state."""
    state = run["timeline"][minute]
    names = {i["id"]: i for i in state["incidents"]}
    ev = [e for e in run["events"] if e["t"] == minute]
    parts = [f"{incident_title(names[e['incident']])} reported" for e in ev if e["type"] == "incident_reported"
             and e["incident"] in names]
    sent = Counter((e["incident"], kind_of(e["unit"])) for e in ev if e["type"] in ("unit_dispatched", "unit_redirected"))
    parts += [f"{count_units(k, n)} on the way to {iid}" for (iid, k), n in sorted(sent.items())]
    arrived = [e for e in ev if e["type"] == "patient_delivered"]
    if arrived:
        parts.append(f"{plural(len(arrived), 'victim')} reached a hospital")
    parts += [f"critical {e['victim']} missed its deadline" for e in ev
              if e["type"] == "deadline_missed" and e["severity"] == "critical"]
    parts += [f"referee called in for {e['victim_ids'][0]}" for e in ev
              if e["type"] == "message" and e["msg_type"] == "escalation" and e.get("victim_ids")]
    full = [h["id"] for h in state["hospitals"] if h["er_used"] >= h["er_slots"]]
    almost = [h["id"] for h in state["hospitals"] if h["id"] not in full and h["utilization"] >= 0.85]
    if full:
        parts.append("all hospitals full" if len(full) == len(state["hospitals"]) else f"{' and '.join(full)} full")
    if almost:
        parts.append(f"{' and '.join(almost)} almost full")
    if len(parts) == len([p for p in parts if p.endswith("full")]):
        parts.insert(0, quiet_line(state))
    return f"Minute {minute}: " + ", ".join(parts[:5]) + "."


def quiet_line(state: dict) -> str:
    open_incidents = [i for i in state["incidents"] if any(v["status"] in OPEN for v in i["victims"])]
    moving = sum(1 for u in state["units"] if u["trip"])
    if not open_incidents and not moving:
        return "all quiet, no incident open"
    return f"{plural(len(open_incidents), 'incident')} open, {plural(moving, 'unit')} on the road"


# ----- incident cards -----

def delivered_at(run: dict, minute: int) -> dict[str, tuple[int, str]]:
    return {e["victim"]: (e["t"], e["hospital"]) for e in run["events"]
            if e["type"] == "patient_delivered" and e["t"] <= minute}


def victim_status(v: dict, minute: int, eta: int | None, delivered: tuple[int, str] | None) -> str:
    if v["deadline"] is None:
        return "on track"
    if delivered:
        return "on time" if delivered[0] <= v["deadline"] else "late"
    expected = eta if eta is not None else minute
    slack = v["deadline"] - expected
    return "late" if slack < 0 or minute > v["deadline"] else "tight" if slack < TIGHT_MINUTES else "on track"


def victim_row(v: dict, units: dict[str, dict], minute: int, delivered: tuple[int, str] | None) -> dict:
    u = units.get(v["unit"]) if v.get("unit") else None
    eta = u["eta"] if u and v["status"] == "in_transit" else None
    if delivered:
        where = f"at {delivered[1]} since minute {delivered[0]}"
    elif v["status"] == "waiting":
        where = "waiting at the scene"
    elif v["status"] == "assigned":
        where = f"being loaded into {unit_name(v['unit'])}" + (f", going to {u['hospital']}" if u and u["hospital"] else "")
    elif v["status"] == "in_transit":
        where = f"in {unit_name(v['unit'])} to {u['hospital'] if u else v['hospital']}" + (f", ETA {eta}" if eta else "")
    elif v["status"] == "at_door":
        where = f"at {u['hospital'] if u else 'the hospital'}'s door, waiting for a slot"
    else:
        where = f"at {v['hospital']}"
    return {"id": v["id"], "severity": v["original_severity"], "care": v["care_need"], "where": where,
            "deadline": v["deadline"], "status": victim_status(v, minute, eta, delivered)}


def steps_done(inc: dict, run: dict, minute: int) -> list[bool]:
    sent = any(e["type"] in ("unit_dispatched", "unit_redirected") and e.get("incident") == inc["id"]
               and e["t"] <= minute for e in run["events"])
    moving = any(v["status"] not in ("waiting", "assigned") for v in inc["victims"])
    done = not any(v["status"] in OPEN for v in inc["victims"])
    return [True, sent, inc["cleared"] or not inc["requires"], moving, done]


def incident_cards(run: dict, minute: int, threads: list[dict]) -> list[dict]:
    """One card per reported incident: open ones by least time left, finished ones last."""
    state = run["timeline"][minute]
    units = {u["id"]: u for u in state["units"]}
    delivered = delivered_at(run, minute)
    recent = {e.get("incident") for e in run["events"] if minute - 1 <= e["t"] <= minute}
    cards = []
    for inc in state["incidents"]:
        rows = [victim_row(v, units, minute, delivered.get(v["id"])) for v in inc["victims"]]
        open_rows = [r for r, v in zip(rows, inc["victims"]) if v["status"] in OPEN]
        left = min((r["deadline"] - minute for r in open_rows if r["deadline"] is not None), default=None)
        crit = sum(1 for r in open_rows if r["severity"] == "critical")
        cards.append({"id": inc["id"], "title": incident_title(inc), "description": inc["description"],
                      "open": bool(open_rows), "criticals": crit, "left": left, "steps": steps_done(inc, run, minute),
                      "victims": rows, "changed": inc["id"] in recent,
                      "negotiations": [t["link"] for t in threads if t["incident"] == inc["id"]
                                       and t["protocol"] == "Admission"][:2]})
    return sorted(cards, key=lambda c: (not c["open"], c["left"] if c["left"] is not None else 10**6, c["id"]))


def incident_card_html(card: dict, focus: bool = False) -> str:
    if not card["open"]:
        return (f"<div class='icard done'>✅ <b>{escape(card['title'])}</b> · everyone at a hospital"
                f" · {plural(len(card['victims']), 'victim')}</div>")
    left = "" if card["left"] is None else (f"{card['left']} min left" if card["left"] >= 0
                                            else f"{-card['left']} min past the deadline")
    chip = f"🔴 {card['criticals']} critical · {left}" if card["criticals"] else f"🟠 {left}"
    bar = "".join(f"<span class='pstep{' on' if done else ''}' title='{s}'></span>" for s, done in zip(STEPS, card["steps"]))
    here = next((s for s, done in zip(STEPS, card["steps"]) if not done), STEPS[-1])
    rows = "".join(f"<div class='vrow'><span class='sev {r['severity']}'>{r['severity']}</span> {r['id']} · "
                   f"{escape(r['where'])} <span class='st {r['status'].replace(' ', '-')}'>{tip(r['status'])}</span></div>"
                   for r in card["victims"])
    neg = "".join(f"<div class='neg-link'>🤝 {escape(n)}</div>" for n in card["negotiations"])
    cls = "icard" + (" changed" if card["changed"] else "") + (" focus" if focus else "")
    return (f"<div class='{cls}'><div class='chip'>{chip}</div><div class='desc'>“{escape(card['description'])}”</div>"
            f"<div class='steps-bar'>{bar}<span class='step-now'>next: {here}</span></div>{rows}{neg}</div>")


# ----- hospitals -----

def hospital_cards(run: dict, minute: int) -> list[dict]:
    state = run["timeline"][minute]
    offers = [e for e in run["events"] if e["type"] == "agent_decision" and e["kind"] == "admission_offer"
              and e["t"] <= minute]
    walk = [e for e in run["events"] if e["type"] == "walk_in" and e["t"] <= minute]
    out = []
    for h in state["hospitals"]:
        mine = [e for e in offers if e["agent"] == h["id"]]
        out.append({"id": h["id"], "name": h["name"], "mood": mood_for(h["utilization"]),
                    "er": f"{h['er_used']}/{h['er_slots']}" + (f" (+{h['reserved']})" if h["reserved"] else ""),
                    "patients": [{"id": p["id"], "severity": p["original_severity"], "status": p["status"].replace("_", " "),
                                  "walk_in": p["walk_in"]} for p in h["patients"]],
                    "offers": [(e["t"], e["decided_by"], e["reason"]) for e in mine[-4:]][::-1],
                    "offers_total": len(mine), "walk_ins": sum(1 for e in walk if e["hospital"] == h["id"])})
    return out


def hospital_card_html(h: dict) -> str:
    patients = ", ".join(f"{p['id']} ({p['severity']}{', walk-in' if p['walk_in'] else ''}: {p['status']})"
                         for p in h["patients"]) or "no patients"
    offers = "".join(f"<div class='ev'><span class='ev-t'>{t}</span> {badge(by)} {escape(reason)}</div>"
                     for t, by, reason in h["offers"])
    return (f"<div class='hcard'><b>{h['id']} {escape(h['name'])}</b> · mood {h['mood']} · {tip('ER', 'ER')} {h['er']}"
            f" · {tip('Walk-ins', 'walk-ins')} so far {h['walk_ins']}<div class='desc'>{escape(patients)}</div>"
            f"<div class='agent-d'>Offers made: {h['offers_total']} (latest first)</div>{offers}</div>")


BADGES = {"rule": ("Rule", "b-rule"), "llm": ("AI judgment", "b-ai"), "fallback": ("Fallback", "b-fb"),
          "guard": ("AI blocked by guard", "b-guard"), "guard-option": ("Guard", "b-guard")}


def badge(kind: str) -> str:
    text, cls = BADGES.get(kind, (kind, "b-rule"))
    term = {"llm": "AI judgment", "rule": "Rule", "fallback": "Fallback"}.get(kind, "Guard")
    return f"<span class='badge {cls}' title='{escape(GLOSSARY[term], quote=True)}'>{text}</span>"
