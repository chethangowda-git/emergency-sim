"""Every code the run logs, in plain English (DESIGN 13.2). One place for all visible wording.

Short sentences, everyday words, friendly names first ("paramedic ambulance", "referee"), times as
"min 97" and reasons as a "because" clause. Code identifiers (send_now, set_destination, TH024,
I02-V2) never appear in visible text; callers put them in tooltips. The AI's own reasons are quoted
word for word and labelled as the AI's words. tests/test_wording.py fails if a new event, message,
reason or decision code has no entry here.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from dashboard.names import hospital_name

# ----- names -----

UNIT_KINDS = {"ALS": ("paramedic ambulance", "paramedic ambulances"), "BLS": ("basic ambulance", "basic ambulances"),
              "FIRE": ("fire team", "fire teams"), "RESCUE": ("rescue team", "rescue teams"),
              "HV": ("hospital vehicle", "hospital vehicles")}
AGENTS = {"ia": "Incident Assessment", "dispatch": "Dispatch", "arbiter": "the referee",
          "all_hospitals": "all hospitals"}
INCIDENT_TYPES = {"traffic_accident": "traffic accident", "medical_emergency": "medical emergency",
                  "building_fire": "building fire", "structural_collapse": "building collapse", "bus_fire": "bus fire"}


def cap(text: str) -> str:
    """Upper-case the first letter only (str.capitalize would lower-case unit IDs such as ALS1)."""
    return text[:1].upper() + text[1:]


def minute_tag(t) -> str:
    return f"min {t}"


def kind_of(unit_id: str) -> str:
    if "-TV" in unit_id:
        return "HV"
    return next((k for k in ("ALS", "BLS", "FIRE", "RESCUE") if unit_id.startswith(k)), "HV")


def unit_name(unit_id: str) -> str:
    return f"{UNIT_KINDS[kind_of(unit_id)][0]} {unit_id}"


def count_units(kind: str, n: int) -> str:
    one, many = UNIT_KINDS[kind]
    return f"{n} {one if n == 1 else many}"


def units_text(needed: dict) -> str:
    return ", ".join(count_units(k, n) for k, n in needed.items() if n) or "no units"


def place(agent: str) -> str:
    """An agent or hospital, friendly name first."""
    if agent in AGENTS:
        return AGENTS[agent]
    return hospital_name(agent) if agent.startswith("H") else agent


def incident_name(inc: dict) -> str:
    return f"{inc['id']} {INCIDENT_TYPES.get(inc['type'], inc['type'].replace('_', ' '))}"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def victims_phrase(ids, severity: dict[str, str]) -> str:
    """'I02's critical victim', 'I03's 2 minor victims'; walk-ins keep their patient ID."""
    by_incident: dict[str, list[str]] = defaultdict(list)
    for v in ids:
        by_incident[v.split("-V")[0] if "-V" in v else v].append(v)
    parts = []
    for iid, vs in by_incident.items():
        if "-V" not in vs[0]:
            parts.append(f"patient {iid}")
            continue
        counts = Counter(severity.get(v, "") for v in vs)
        kinds = [f"{n} {s}" if n > 1 else s for s in ("critical", "serious", "minor") if (n := counts.get(s))]
        parts.append(f"{iid}'s {' and '.join(kinds) or 'injured'} victim{'s' if len(vs) > 1 else ''}")
    return " and ".join(parts) or "a patient"


# ----- codes -----

REJECTIONS = {  # why the world refused an action (DESIGN 3.10)
    "already_covered": "another team already covers it", "already_reserved": "a slot is already reserved",
    "capability_mismatch": "that unit or hospital can't handle this case", "deadline_miss": "it would miss the deadline",
    "destination_already_set": "the unit already has a destination", "er_full": "the emergency room is full",
    "general": "it is not a specialist case", "hold_limit": "the hospital already holds a slot",
    "incident_resolved": "everyone has already left the scene", "invalid_hold": "a held slot is for one patient only",
    "invalid_kind": "that kind of reservation does not exist", "invalid_time": "the time is not valid",
    "no_critical": "no critical victim is waiting there", "no_reservation": "no slot was reserved",
    "no_victims": "nobody to carry", "not_lent": "the vehicle is not on loan", "not_permitted": "that agent may not do this",
    "not_ready": "the patient is not ready yet", "not_required": "no team is needed there",
    "not_transferable": "the patient can't be moved yet", "over_capacity": "the vehicle is full",
    "redirect_limit": "the ambulance was already redirected on this trip",
    "reroute_limit": "the ambulance was already rerouted on this trip", "same_hospital": "it is the same hospital",
    "same_incident": "it is the same incident", "staff_busy": "the staff member is busy",
    "unit_busy": "the unit is busy", "unknown_hold": "that held slot no longer exists",
    "unknown_hospital": "that hospital is unknown", "unknown_incident": "that incident is unknown",
    "unknown_staff": "that staff member is unknown", "unknown_unit": "that unit is unknown",
    "unknown_vehicle": "that vehicle is unknown", "unknown_victim": "that patient is unknown",
    "vehicle_busy": "the vehicle is busy", "victim_unavailable": "the patient is no longer there",
    "wrong_hospital": "it is the wrong hospital", "wrong_incident": "the patient is at another incident",
}
TRIGGERS = {"no_offer_in_time": "no hospital offer arrives in time", "rounds_exhausted": "3 rounds passed without a deal",
            "award_declined": "the hospital took back its offer", "no_time": "there was no time to negotiate",
            "reroute_needed": "a second change of hospital was needed"}
PLANS = {"wait": "wait for {unit} to free up", "redirect": "redirect {unit} from a less urgent job",
         "swap": "free {unit} by handing its job to a hospital vehicle", "mutual_aid": "ask hospitals for a vehicle",
         "queue": "wait for the next free unit"}
OPTIONS = {"accept_now": "accept whoever fits now", "release_then_accept": "release a patient who is ready to go, "
           "then accept", "after_transfer": "accept after transferring a patient out",
           "protect_criticals": "keep the last slots for critical patients", "earliest": "the earliest arrival for each victim",
           "spare_specialist": "spare the specialist hospital", "one_hospital": "send everyone to one hospital",
           "accept": "accept", "decline": "decline", "send_now": "send staff now", "send_later": "send staff later"}
ARBITER_OPTIONS = {1: "another hospital that can take them in time", 2: "release patients who are ready to go",
                   3: "move a stable patient out to make room", 4: "use a slot held for someone else",
                   5: "send another patient elsewhere", 6: "borrow staff urgently", 7: "the least-bad hospital"}
ACTIONS = {"assign_team": "send a team", "lend_staff": "lend staff", "lend_vehicle": "lend a vehicle",
           "redirect": "redirect an ambulance", "release_hold": "release a held slot",
           "release_patient": "release a patient", "reroute": "change hospital on the way", "reserve": "reserve a slot",
           "send_to_scene": "send a unit", "set_destination": "choose the hospital", "transfer_patient": "transfer a patient"}
UNIT_STATES = {"idle": "at base", "to_scene": "on the way to the scene", "on_scene": "at the scene",
               "clearing": "clearing the scene", "transporting": "driving to hospital",
               "at_hospital_no_slot": "waiting at a hospital door", "handover": "handing over the patient",
               "returning": "returning to base", "transfer_prep": "preparing a transfer"}
VICTIM_STATUSES = {"waiting": "waiting at the scene", "assigned": "being loaded", "in_transit": "on the way to hospital",
                   "at_door": "at a hospital door", "in_er": "in the emergency room", "in_treatment": "being treated",
                   "transferable": "stable", "ready_for_release": "ready to leave", "ward": "on a ward",
                   "boarding": "waiting for a ward bed", "discharged": "discharged"}
RELEASE_REASONS = {"released": "it was given up", "transfer_cancelled": "the transfer was cancelled",
                   "reroute": "the ambulance went elsewhere", "went_elsewhere": "the patient went elsewhere",
                   "replaced": "a new slot replaced it"}
OUTCOMES = {"discharged": "went home", "ward": "moved to a ward", "boarding": "waits for a ward bed"}
DECISIONS = {"assess": "assessed the incident", "plan_units": "planned the units", "admission_offer": "answered a request",
             "award": "chose the hospitals", "hold": "held a slot", "release": "released patients to make room",
             "resolve": "ruled on an escalation", "retry": "will try again", "staff_offer": "answered a staff request",
             "transfer": "transferred a patient", "transfer_response": "answered a transfer request",
             "mutual_aid_offer": "answered a vehicle request", "swap_response": "answered a swap request"}
MOODS = {"eager": "it has room to spare", "normal": "it is moderately busy", "protective": "it is nearly full"}
GUARD_RULES = {"G1": "conditional offers", "G2": "extra ambulances"}


def plan_text(plan: str | None) -> str:
    kind, _, unit = (plan or "queue").partition(":")
    return PLANS.get(kind, "wait for the next free unit").format(unit=unit or "a unit")


# ----- reasons -----

RULE_REASONS = [  # (pattern of a rule's own reason, plain "because" clause)
    (r"^(eager|normal|protective): decline \((.+)\)$", lambda m: f"{reason_phrase(m[2])}; {MOODS[m[1]]}"),
    (r"^(eager|normal|protective): (accept now|release then accept|after transfer|protect criticals) \((\d+) of (\d+) now\)$",
     lambda m: f"it can take {m[3]} of {m[4]} now; {MOODS[m[1]]}"),
    (r"^(eager|normal|protective): (send now|send later|decline)$", lambda m: MOODS[m[1]]),
    (r"^nearest free unit by road, by urgency$", lambda m: "the nearest free units go to the most urgent incidents"),
    (r"^earliest capable arrival for each victim$", lambda m: "each victim goes where they can be treated soonest"),
    (r"^(.+): earliest arrival$", lambda m: "it was the earliest arrival among the referee's options"),
    (r"^rules$", lambda m: "the rules size the response from the victims reported"),
]
PHRASES = {"no free capacity now": "there is no free room right now", "no free capacity": "there is no free room",
           "cannot treat this care need": "it can't treat this kind of injury",
           "keeping my last slots for criticals": "it keeps its last slots for critical patients",
           "capacity taken since my offer": "its room was taken since it offered",
           "transfer did not happen": "the transfer it counted on did not happen",
           "transfer not started in time": "the transfer did not start in time",
           "no hospital accepted after 3 rounds": "no hospital accepted after 3 rounds",
           "no staff to spare": "it has no staff to spare", "my vehicle is busy": "its vehicle is busy",
           "I may need my vehicle for a transfer": "it may need its vehicle for a transfer",
           "vehicle idle": "its vehicle is free", "firm reservation placed": "the slot is reserved",
           "you take the transfer": "it was the nearest hospital that said yes",
           "eager: idle capacity": "it has room to spare",
           "lateral transfer; keeping capacity for criticals": "it keeps room for critical patients",
           "normal load: lateral transfer accepted": "it has room",
           "specialty the sender lacks and I have capacity (EMTALA)": "it has the specialty and the room (the law requires it)",
           "no hospital can treat this care need": "no hospital can treat this injury",
           "critical waiting for a unit": "a critical victim is waiting for an ambulance",
           "offer accepted": "its offer was the best fit", "rule hospital does not lend": "simple rule hospitals never lend",
           "rule hospital: vehicle idle": "its vehicle is free", "vehicle busy": "its vehicle is busy",
           "rule hospital: accept if real capacity is free": "it accepts whenever it has room",
           "real capacity free": "it has room"}


def reason_phrase(text: str) -> str:
    """A rule's reason or reason code as plain words (without 'because')."""
    if text in PHRASES:
        return PHRASES[text]
    if text in REJECTIONS:
        return REJECTIONS[text]
    if text in TRIGGERS:
        return TRIGGERS[text]
    for pattern, render in RULE_REASONS:
        m = re.match(pattern, text)
        if m:
            return render(m)
    m = re.match(r"^(set_destination|reroute) rejected: (\w+)$", text)
    if m:
        return f"the hospital choice was refused: {REJECTIONS.get(m[2], 'the world said no')}"
    m = re.match(r"^release (.+) to make room$", text)
    if m:
        return f"it sent home {plural(len(m[1].split(', ')), 'patient')} who were ready to leave, to make room"
    m = re.match(r"^transfer (\S+) to (H\d+)$", text)
    if m:
        return f"it moved a stable patient to {place(m[2])} to make room"
    m = re.match(r"^walk-in (\w+) took the slot reserved for (\S+)$", text)
    if m:
        return "a walk-in patient took the reserved slot"
    m = re.match(r"^(.*) \(LLM fallback: .*\)$", text)
    if m:
        return reason_phrase(m[1]) + " (the AI's answer did not fit, so the rule decided)"
    return text


def because(reason: str | None, decided_by: str | None = None) -> str:
    """'because …' for a rule's reason; the AI's words in quotes, labelled."""
    if not reason:
        return ""
    if decided_by == "llm":
        return f"AI's words: “{reason}”"
    return f"because {reason_phrase(reason)}"


# ----- world events -----

def event_sentence(e: dict, severity: dict[str, str]) -> str:
    """Any logged world event as one short sentence (agent messages and decisions have their own)."""
    who = lambda key="victim": victims_phrase([e[key]], severity) if e.get(key) else "a patient"
    hosp = lambda key="hospital": place(e[key]) if e.get(key) else "the hospital"
    k = e["type"]
    texts = {
        "incident_reported": lambda: f"{e['incident']} {INCIDENT_TYPES.get(e['incident_type'], '')} reported: "
                                     f"“{e['description']}”, {plural(len(e['victims']), 'victim')}",
        "unit_dispatched": lambda: f"{cap(unit_name(e['unit']))} sent to {e['incident']}, arriving {minute_tag(e['eta'])}",
        "unit_redirected": lambda: f"{cap(unit_name(e['unit']))} redirected from {e['from_incident']} to {e['incident']}",
        "unit_on_scene": lambda: f"{cap(unit_name(e['unit']))} reached {e['incident']}",
        "scene_cleared": lambda: f"{e['incident']} made safe by {unit_name(e['team'])}",
        "transport_started": lambda: f"{cap(unit_name(e['unit']))} left for {hosp()}, arriving {minute_tag(e['eta'])}",
        "patient_delivered": lambda: f"{cap(who())} reached {hosp()}"
                                     + (" in time" if e.get("on_time") else ""),
        "arrived_no_slot": lambda: f"{cap(unit_name(e['unit']))} arrived at {hosp()} but there was no slot",
        "reservation_broken": lambda: f"A walk-in patient took the slot {hosp()} had reserved for {who()}",
        "reservation_placed": lambda: f"{hosp()} reserved a slot for {who()}",
        "reservation_released": lambda: f"{hosp()} let go of a slot because {RELEASE_REASONS.get(e.get('reason'), 'it was not needed')}",
        "hold_placed": lambda: f"{hosp()} is holding a slot for {who()}",
        "hold_released": lambda: f"{hosp()} let go of a held slot because {RELEASE_REASONS.get(e.get('reason'), 'it was not needed')}",
        "hold_expired": lambda: f"{hosp()}'s held slot went unused for {e.get('minutes')} min and expired",
        "walk_in": lambda: f"A {e.get('severity', '')} walk-in patient arrived at {hosp()}",
        "treatment_started": lambda: f"{cap(who())} started treatment at {hosp()}",
        "stabilized": lambda: f"{cap(who())} is stable at {hosp()} and could be moved",
        "ready_for_release": lambda: f"{cap(who())} is ready to leave {hosp()}",
        "patient_released": lambda: f"{cap(who())} {OUTCOMES.get(e.get('outcome'), 'left')} from {hosp()}",
        "moved_to_ward": lambda: f"{cap(who())} moved to a ward at {hosp()}",
        "deadline_missed": lambda: f"{cap(who())} missed their deadline",
        "unit_returning": lambda: f"{cap(unit_name(e['unit']))} is returning to base",
        "unit_at_base": lambda: f"{cap(unit_name(e['unit']))} is back at base",
        "unit_rerouted": lambda: f"{cap(unit_name(e['unit']))} changed hospital from {hosp('from_hospital')} to {hosp()}",
        "transfer_prep": lambda: f"{hosp('from_hospital')} is preparing {who()} to move to {hosp('to_hospital')}",
        "transfer_started": lambda: f"{cap(who())} is moving from {hosp('from_hospital')} to {hosp('to_hospital')}",
        "transfer_completed": lambda: f"{cap(who())} arrived at {hosp()} after a transfer",
        "transfer_cancelled": lambda: f"The transfer of {who()} was cancelled",
        "vehicle_lent": lambda: f"{hosp()} lent its vehicle to Dispatch until {minute_tag(e.get('until'))}",
        "vehicle_loan_ended": lambda: f"{hosp()}'s vehicle is back",
        "staff_lent": lambda: f"{hosp('from_hospital')} lent a staff member to {hosp('to_hospital')}",
        "staff_arrived": lambda: f"A borrowed staff member arrived at {hosp()}",
        "staff_returning": lambda: f"A borrowed staff member is heading back from {hosp('from_hospital')}",
        "staff_returned": lambda: f"A borrowed staff member is back at {hosp()}",
        "action_applied": lambda: f"Done: {ACTIONS.get(e['action'], 'an action')}",
        "action_rejected": lambda: f"Could not {ACTIONS.get(e['action'], 'act')} because {REJECTIONS.get(e['reason'], 'it was refused')}",
    }
    return texts[k]() + "." if k in texts else ""


EVENT_TYPES = ("incident_reported", "unit_dispatched", "unit_redirected", "unit_on_scene", "scene_cleared",
               "transport_started", "patient_delivered", "arrived_no_slot", "reservation_broken", "reservation_placed",
               "reservation_released", "hold_placed", "hold_released", "hold_expired", "walk_in", "treatment_started",
               "stabilized", "ready_for_release", "patient_released", "moved_to_ward", "deadline_missed",
               "unit_returning", "unit_at_base", "unit_rerouted", "transfer_prep", "transfer_started",
               "transfer_completed", "transfer_cancelled", "vehicle_lent", "vehicle_loan_ended", "staff_lent",
               "staff_arrived", "staff_returning", "staff_returned", "action_applied", "action_rejected",
               "message", "agent_decision", "guard")


# ----- agent messages -----

def message_sentence(m: dict, severity: dict[str, str]) -> str:
    """One plain sentence for an agent message, from its structured payload."""
    p, k = m.get("proposed_action") or {}, m["msg_type"]
    who = victims_phrase(m.get("victim_ids") or [], severity)
    if k in ("unit_request", "incident_update") and p:
        return (f"{'Update: ' if k == 'incident_update' else ''}{m['incident_id']} needs {units_text(p['units_needed'])}; "
                f"{p['at_risk']} at risk, {p['minutes_left']} min left.")
    if k == "unit_response":
        sent = f"Sending {', '.join(unit_name(u) for u in p['sent'])}." if p.get("sent") else "No unit is free right now."
        return sent + (f" Still short of {units_text(p['shortfall'])}; plan: {plan_text(p['plan'])}."
                       if p.get("shortfall") and any(p["shortfall"].values()) else "")
    if k == "admission_offer":
        cond = [v for c in p.get("conditional", []) for v in c["victims"]]
        parts = ([f"I can take {victims_phrase(p['accept'], severity)} now."] if p.get("accept") else []) + \
                ([f"I can take {victims_phrase(cond, severity)} after moving a patient out."] if cond else [])
        return " ".join(parts) or "I can't take anyone right now."
    if k == "arbiter_decision":
        miss = "" if p.get("meets_deadline") else ", even though that misses the deadline"
        return (f"Ruling for {victims_phrase([p['victim']], severity)}: {ARBITER_OPTIONS.get(p.get('option'), 'a decision')}"
                f" at {place(p['hospital']) if p.get('hospital') else 'no hospital'}, arriving "
                f"{minute_tag(p.get('arrival'))}{miss}.")
    if k in ("staff_offer", "mutual_aid_offer", "swap_response"):
        return {"send_now": "Yes, sending now.", "send_later": f"Yes, later, at {minute_tag(p.get('at_minute'))}.",
                "decline": "No, I can't."}.get(p.get("answer"), "No answer.")
    return MESSAGES[k](m, p, who) if k in MESSAGES else ""


MESSAGES = {
    "pre_alert": lambda m, p, who: f"Heads-up: {who} may be coming to you.",
    "admission_cfp": lambda m, p, who: f"Can you take {who}?",
    "admission_counter": lambda m, p, who: f"Asking again: can you take {who}?",
    "admission_award": lambda m, p, who: f"Please take {who}" + (" once your transfer is done." if p.get("conditional")
                                                                 else "."),
    "ack": lambda m, p, who: "Confirmed: the slot is reserved." if m["recipient"] == "dispatch" else "Agreed.",
    "decline": lambda m, p, who: "Sorry, I can't keep this after all.",
    "escalation": lambda m, p, who: f"Referee, please decide for {who}.",
    "transfer_request": lambda m, p, who: f"Can you take {who} ({p.get('care_need', 'general')} care)?",
    "transfer_response": lambda m, p, who: "Yes, I'll take them." if p.get("accept") else "No, I can't take them.",
    "capacity_alert": lambda m, p, who: f"A walk-in took the slot I had reserved for {who}.",
    "staff_request": lambda m, p, who: f"Can you lend me a {p.get('kind', 'staff member')} for {p.get('minutes')} min?",
    "mutual_aid_request": lambda m, p, who: f"Can you lend a vehicle for {p.get('incident')} until {minute_tag(p.get('until'))}?",
    "swap_request": lambda m, p, who: f"Can your vehicle take over a job so an ambulance is free for {m.get('incident_id')}?",
}
MESSAGE_TYPES = (*MESSAGES, "unit_request", "incident_update", "unit_response", "admission_offer", "arbiter_decision",
                 "staff_offer", "mutual_aid_offer", "swap_response")


# ----- guard steps -----

def guard_sentence(e: dict, severity: dict[str, str]) -> str:
    if "suggested" in e:
        return (f"The AI asked for {e['suggested'].replace('ALS', 'paramedic ambulances')}; the guard allowed "
                f"{e['allowed'].replace('ALS', 'paramedic ambulance')} because {e['why'].replace('ALS', 'paramedic ambulance')}.")
    if "withheld" in e:
        return f"The guard kept “accept after a transfer” from {place(e['agent'])}'s AI because {e['why']}."
    return (f"The guard counted {place(e.get('hospital', ''))}'s conditional offer for "
            f"{victims_phrase([e['victim']], severity)} as too late because {e['why']}.")
