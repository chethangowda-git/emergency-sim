"""Negotiations and decisions in plain language (DESIGN 13.2), from the recorded messages, decisions,
guard events and LLM calls. The agents' own reasons are shown word for word; no LLM is called."""
from __future__ import annotations

from collections import Counter, defaultdict
from html import escape

from coordination.choices import rule_default, rule_ranking_of
from dashboard.agent_views import outcome
from dashboard.explain import UNIT_SHORT, agent_name, badge, plural

KIND_PURPOSE = {"admission_offer": "admission", "award": "award", "plan_units": "plan_units", "assess": "assess",
                "resolve": "resolve", "transfer_response": "transfer", "staff_offer": "staff"}
OPTION_TEXT = {"accept_now": "accept whoever fits now", "release_then_accept": "release a patient who is ready to go, "
               "then accept", "after_transfer": "accept after transferring a patient out",
               "protect_criticals": "keep the last slots for criticals", "earliest": "earliest arrival for each victim",
               "spare_specialist": "spare the specialist hospital", "one_hospital": "send everyone to one hospital",
               "accept": "accept", "decline": "decline", "send_now": "send staff now", "send_later": "send staff later"}
ARBITER_OPTIONS = {1: "another hospital in time", 2: "release patients ready to go", 3: "transfer a patient out to make room",
                   4: "override a held slot", 5: "reroute another patient", 6: "an urgent staff loan",
                   7: "the least-bad hospital"}
TRIGGERS = {"no_offer_in_time": "no hospital offer arrives in time", "rounds_exhausted": "3 rounds without agreement",
            "award_declined": "the hospital declined the award", "no_time": "too little time to negotiate",
            "reroute_needed": "a second reroute is needed"}


def plan_text(plan: str | None) -> str:
    """A shortfall plan id such as 'wait:ALS1' or 'redirect:ALS2' in words."""
    kind, _, unit = (plan or "queue").partition(":")
    return {"wait": f"wait for {unit} to free up", "redirect": f"redirect {unit}", "swap": f"free {unit} with a swap",
            "mutual_aid": "ask hospitals for a vehicle", "queue": "wait for the next free unit"}.get(kind, plan or "")


def units_text(needed: dict) -> str:
    return ", ".join(f"{n} {UNIT_SHORT[k][0 if n == 1 else 1]}" for k, n in needed.items() if n) or "no units"


def victims_text(ids: list[str], severity: dict[str, str]) -> str:
    counts = Counter(severity.get(v, "") for v in ids)
    groups = [f"{n} {s}" for s in ("critical", "serious", "minor") if (n := counts.get(s))]
    return " and ".join(groups) + (" victim" if sum(counts.values()) == 1 else " victims")


# ----- negotiations -----

def victims_phrase(ids: list[str], severity: dict[str, str]) -> str:
    """'I02's critical victim', 'I03's 2 minor victims'; walk-ins and transfers keep their patient ID."""
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
        parts.append(f"{iid}'s {' and '.join(kinds)} victim{'s' if len(vs) > 1 else ''}")
    return " and ".join(parts) or "a patient"


def minute_tag(t: int) -> str:
    return f"min {t}"


def question(th: dict, severity: dict[str, str]) -> str:
    first = th["messages"][0]
    iid, vids, p = first.get("incident_id"), first.get("victim_ids") or [], first.get("proposed_action") or {}
    who = victims_phrase(vids, severity)
    return {"Admission": lambda: f"Where should {who} go?",
            "Unit request": lambda: f"Which units go to {iid}?",
            "Transfer": lambda: f"Who takes {who} from {first['sender']}?",
            "Staff loan": lambda: f"Can another hospital lend {first['sender']} a {p.get('kind', 'staff member')}?",
            "Mutual aid": lambda: f"Can a hospital lend a vehicle for {iid}?",
            "Swap": lambda: f"Can a hospital vehicle take over a job so an ambulance is free for {iid}?",
            "Reroute": lambda: f"{first['sender']} lost the slot for {who}: where now?",
            "Pre-alert": lambda: f"Hold a slot for {who}?",
            }.get(th["protocol"], lambda: f"{th['protocol']} for {iid or who}")()


def verdict(text: str) -> str:
    t = text.lower()
    if "arbiter" in t or "referee" in t or "escalated" in t:
        return "escalated to referee"
    if "in progress" in t:
        return "in progress"
    if any(w in t for w in ("declined", "no award", "no reply", "decline", "no unit sent")):
        return "no deal"
    if t == "notified":
        return "noted"
    return "agreed"


def sentence(m: dict, severity: dict[str, str]) -> str:
    """One plain sentence for a message, from its structured payload."""
    p, k, vids = m.get("proposed_action") or {}, m["msg_type"], m.get("victim_ids") or []
    who = victims_phrase(vids, severity)
    if k in ("unit_request", "incident_update") and p:
        return (f"{'Update: ' if k == 'incident_update' else ''}I need {units_text(p['units_needed'])} at "
                f"{m['incident_id']}; {p['at_risk']} at risk, {p['minutes_left']} min left.")
    if k == "unit_response":
        sent = f"Sending {', '.join(p['sent'])}." if p.get("sent") else "No unit is free right now."
        return sent + (f" Still short of {units_text(p['shortfall'])}; plan: {plan_text(p['plan'])}."
                       if p.get("shortfall") else "")
    if k in ("admission_cfp", "admission_counter"):
        return f"Can you take {who}?"
    if k == "admission_offer":
        cond = [v for c in p.get("conditional", []) for v in c["victims"]]
        parts = ([f"I can take {victims_phrase(p['accept'], severity)} now."] if p.get("accept") else []) + \
                ([f"I can take {victims_phrase(cond, severity)} after a transfer."] if cond else [])
        return " ".join(parts) or "I can't take anyone right now."
    if k == "arbiter_decision":
        miss = "" if p.get("meets_deadline") else ", which misses the deadline"
        return (f"Decision for {victims_phrase([p['victim']], severity)}: {p['option_name'].replace('_', ' ')} at "
                f"{p['hospital']}, arriving {minute_tag(p['arrival'])}{miss}.")
    if k in ("staff_offer", "mutual_aid_offer", "swap_response"):
        return {"send_now": "Yes, sending now.", "send_later": f"Later, at {minute_tag(p.get('at_minute'))}.",
                "decline": "No, I can't."}.get(p.get("answer"), p.get("answer", ""))
    return {
        "pre_alert": f"Heads-up: {who} may come to you.",
        "admission_award": f"Please take {who}" + (" once your transfer is done." if p.get("conditional") else "."),
        "ack": "Done: the slot is reserved." if m["recipient"] == "dispatch" else "Agreed.",
        "decline": "I can't keep this after all.",
        "escalation": f"Referee, please decide for {who}: {TRIGGERS.get(m.get('reason'), m.get('reason') or '')}.",
        "transfer_request": f"Can you take {who} ({p.get('care_need', '')})?",
        "transfer_response": "Yes, I'll take them." if p.get("accept") else "No.",
        "capacity_alert": f"A walk-in took the slot I had reserved for {who}.",
        "staff_request": f"Can you lend me a {p.get('kind')} ({p.get('skill')}) for {p.get('minutes')} min?",
        "mutual_aid_request": f"Can you lend a vehicle at {p.get('incident')} until {minute_tag(p.get('until'))}?",
    }.get(k, k.replace("_", " "))


def bubbles_of(ms: list[dict], minute: int, severity: dict[str, str]) -> list[dict]:
    """One bubble per message, merging the same message sent to several recipients; a heads-up goes into
    the request it comes with."""
    out: list[dict] = []
    for m in ms:
        kind = "admission_cfp" if m["msg_type"] == "pre_alert" else m["msg_type"]
        key = (m["t"], m["sender"], m["round"], kind, tuple(m.get("victim_ids") or []))
        same = next((b for b in out[-8:] if b["key"] == key), None)
        if same is None:
            same = {"key": key, "t": m["t"], "round": m["round"], "who": agent_name(m["sender"]), "to": [],
                    "text": sentence(m, severity), "reason": m.get("reason") or "", "by": m["decided_by"],
                    "heads_up": False, "ids": list(m.get("victim_ids") or []), "new": m["t"] >= minute - 1}
            out.append(same)
        if m["msg_type"] == "pre_alert":
            same["heads_up"] = True
        else:
            same["text"] = sentence(m, severity)
        if m["recipient"] not in same["to"]:
            same["to"].append(m["recipient"])
    return out


CHIP_TEXT = {"waiting": "waiting", "offered": "offered", "offered later": "offers after a transfer",
             "awarded": "awarded", "accepted": "accepted", "declined": "declined", "cant": "can't treat",
             "referee": "chosen by referee", "later": "later"}


def hospital_chips(ms: list[dict]) -> list[dict]:
    """Where each hospital stands in the thread: waiting, offered, accepted, declined (reason on hover)."""
    state: dict[str, tuple[str, str]] = {}
    latest = max(m["round"] for m in ms)
    for m in ms:
        k, p = m["msg_type"], m.get("proposed_action") or {}
        if k in ("admission_cfp", "admission_counter", "transfer_request", "staff_request", "mutual_aid_request") \
                and m["recipient"].startswith("H") and m["round"] == latest:
            state[m["recipient"]] = ("waiting", "")
        elif k == "admission_offer" and m["round"] == latest:
            reasons = "; ".join(d["reason"] for d in p.get("decline", []))
            if p.get("accept"):
                state[m["sender"]] = ("offered", m.get("reason") or "")
            elif p.get("conditional"):
                state[m["sender"]] = ("offered later", m.get("reason") or "")
            else:
                state[m["sender"]] = ("cant" if "cannot treat" in reasons else "declined", m.get("reason") or reasons)
        elif k == "admission_award":
            state[m["recipient"]] = ("awarded", m.get("reason") or "")
        elif k == "ack" and m["sender"].startswith("H") and m["recipient"] == "dispatch":
            state[m["sender"]] = ("accepted", m.get("reason") or "")
        elif k == "decline" and m["sender"].startswith("H"):
            state[m["sender"]] = ("declined", m.get("reason") or "")
        elif k == "transfer_response":
            state[m["sender"]] = ("accepted" if p.get("accept") else "declined", m.get("reason") or "")
        elif k in ("staff_offer", "mutual_aid_offer", "swap_response") and m["sender"].startswith("H"):
            state[m["sender"]] = ({"send_now": "offered", "send_later": "later"}.get(p.get("answer"), "declined"),
                                  m.get("reason") or "")
        elif k == "arbiter_decision" and p.get("hospital"):
            state[p["hospital"]] = ("referee", m.get("reason") or "")
    return [{"hospital": h, "status": st, "text": CHIP_TEXT[st], "reason": why} for h, (st, why) in sorted(state.items())]


def thread_view(th: dict, minute: int, severity: dict[str, str]) -> dict | None:
    ms = [m for m in th["messages"] if m["t"] <= minute]
    if not ms:
        return None
    result = outcome(ms, minute).replace("the Arbiter", "the referee").replace("Arbiter:", "Referee:")
    rounds = max(m["round"] for m in ms)
    iid = next((m["incident_id"] for m in ms if m.get("incident_id")), None)
    title = question({**th, "messages": ms}, severity)
    v = verdict(result)
    if th["protocol"] == "Unit request":
        v, result = unit_outcome(ms)
    return {"id": th["id"], "protocol": th["protocol"], "incident": iid, "title": title, "verdict": v,
            "active": v in ("in progress", "short of units"), "outcome": result, "rounds": rounds, "last": ms[-1]["t"],
            "bubbles": bubbles_of(ms, minute, severity), "chips": hospital_chips(ms),
            "link": f"{plural(rounds, 'round')}, {result}", "new": ms[-1]["t"] >= minute - 1, "start": ms[0]["t"],
            "ids": sorted({v for m in ms for v in m.get("victim_ids") or []}),
            "agents": {m["sender"] for m in ms} | {m["recipient"] for m in ms}}


def unit_outcome(ms: list[dict]) -> tuple[str, str]:
    """A unit request: every unit sent so far, and what it is still short of, in words."""
    responses = [m["proposed_action"] for m in ms if m["msg_type"] == "unit_response"]
    if not responses:
        return "in progress", "waiting for Dispatch"
    sent = [u for p in responses for u in p.get("sent", [])]
    last = responses[-1]
    done = f"sent {', '.join(sent)}" if sent else "no unit sent yet"
    if last.get("shortfall") and any(last["shortfall"].values()):
        return "short of units", f"{done}; still short of {units_text(last['shortfall'])}: {plan_text(last['plan'])}"
    return "units sent", done


def thread_views(index: dict, minute: int, severity: dict[str, str]) -> list[dict]:
    views = [v for th in index.values() if th["protocol"] != "Pre-alert" and (v := thread_view(th, minute, severity))]
    return sorted(views, key=lambda v: (v["last"], v["id"]), reverse=True)


def default_thread(views: list[dict]) -> str | None:
    """The newest active thread, else the newest one."""
    return next((v["id"] for v in views if v["active"]), views[0]["id"] if views else None)


def chips_html(v: dict) -> str:
    return " ".join(f"<span class='hchip c-{c['status'].split()[0]}' title='{escape(c['reason'] or c['text'], quote=True)}'>"
                    f"{c['hospital']} {c['text']}</span>" for c in v["chips"])


def verdict_html(v: dict) -> str:
    return f"<span class='verdict v-{v['verdict'].split()[0]}'>{v['verdict']}</span>"


def thread_entry_html(v: dict) -> str:
    """The list entry under the thread's title: status, and the hospitals' chips while it is open."""
    tail = chips_html(v) if v["active"] else f"<span class='agent-d'>{escape(v['outcome'])}</span>"
    return (f"<div class='tentry{' new' if v['new'] else ''}' title='{v['id']} · {', '.join(v['ids'])}'>"
            f"{verdict_html(v)} <span class='agent-d'>from {minute_tag(v['start'])}</span> {tail}</div>")


def thread_detail_html(v: dict) -> str:
    out = [f"<div class='tdetail' title='{v['id']}'><div><b>{escape(v['title'])}</b></div>"
           f"<div>{verdict_html(v)} <span class='agent-d'>{escape(v['outcome'])} · {plural(v['rounds'], 'round')}</span></div>"
           f"<div class='chips'>{chips_html(v)}</div>"]
    last_round = None
    for b in v["bubbles"]:
        if b["round"] != last_round:
            out.append(f"<div class='round'>round {b['round']}</div>")
            last_round = b["round"]
        to = ", ".join(r if r.startswith("H") else agent_name(r) for r in b["to"])
        text = ("Heads-up and request: " + b["text"][0].lower() + b["text"][1:]) if b["heads_up"] else b["text"]
        reason = f"<div class='why'>{'AI: ' if b['by'] == 'llm' else ''}{escape(b['reason'])}</div>" if b["reason"] else ""
        out.append(f"<div class='bubble{' new' if b['new'] else ''}' title='{escape(', '.join(b['ids']), quote=True)}'>"
                   f"<span class='ev-t'>{minute_tag(b['t'])}</span> <b>{escape(b['who'])}</b> → {escape(to)}: "
                   f"{escape(text)}{reason}</div>")
    return "".join(out) + "</div>"


# ----- decisions -----

def match_calls(events: list[dict], calls: list[dict]) -> dict[int, dict]:
    """The recorded LLM call behind each AI or fallback decision, by agent, purpose and minute (or incident)."""
    queues: dict[tuple, list[dict]] = defaultdict(list)
    for c in calls:
        inp = c["input"]
        key = inp["incident"]["id"] if c["purpose"] == "assess" else inp.get("t")
        queues[(c["agent"], c["purpose"], key)].append(c)
    out = {}
    for e in events:
        if e["type"] != "agent_decision" or e["decided_by"] == "rule" or e["kind"] not in KIND_PURPOSE:
            continue
        key = e.get("incident") if e["kind"] == "assess" else e["t"]
        q = queues.get((e["agent"], KIND_PURPOSE[e["kind"]], key))
        if q:
            out[e["seq"]] = q.pop(0)
    return out


def rule_choice(e: dict, call: dict | None) -> tuple[str | None, bool]:
    """(what the rule would have done, whether the AI chose differently)."""
    if call is None or call.get("output") is None:
        return None, False
    inp, out = call["input"], call["output"]
    if e["kind"] == "plan_units":
        rule = rule_ranking_of(inp)
        return f"rule order: {', '.join(rule)}", out.get("ranking") != rule
    if e["kind"] == "assess":
        rule = inp["rule_report"]["units_needed"]
        extra = out.get("extra_als") or out.get("extra_bls")
        return f"rules alone: {units_text(rule)}", bool(extra)
    rule = rule_default(call["agent"], call["purpose"], inp)
    return f"rule: {OPTION_TEXT.get(rule, rule)}", out.get("option_id") != rule


def saw_decided(e: dict, incidents: dict[str, dict], call: dict | None,
                severity: dict[str, str] | None = None) -> tuple[str, str]:
    """What the agent saw and what it decided, one plain line each."""
    k = e["kind"]
    if k == "assess":
        inc = incidents.get(e.get("incident"), {})
        flags = f" (flags: {', '.join(e['flags'])})" if e.get("flags") else ""
        return f"{e.get('incident')} reported: “{inc.get('description', '')}”", f"asked for {units_text(e['units_needed'])}{flags}"
    if k == "plan_units":
        sent = ", ".join(f"{a['unit']} → {a['incident']} ({a['eta']} min)" for a in e.get("assignments", []))
        short = "; ".join(f"{s['incident']}: {plan_text(s['chosen'])}" for s in e.get("shortfalls", []))
        return (f"{plural(len(e.get('ranking', [])), 'incident')} waiting for units, order {', '.join(e.get('ranking', []))}",
                (f"sent {sent}" if sent else "no unit free") + (f"; short: {short}" if short else ""))
    if k == "admission_offer":
        inp = (call or {}).get("input", {})
        who = victims_phrase([v["id"] for v in inp.get("request", {}).get("victims", [])], severity or {})
        chosen = (call or {}).get("output", {}).get("option_id")
        detail = inp.get("option_details", {}).get(chosen, {})
        later = [v for c in detail.get("conditional", []) for v in c["victims"]]
        now = f"take {victims_phrase(e['accept'], severity or {})} now" if e.get("accept") else "take nobody now"
        then = f", and {victims_phrase(later, severity or {})} once a patient is transferred out" if later else ""
        return f"a request to take {who}", now + then + (f" ({OPTION_TEXT[chosen]})" if chosen in OPTION_TEXT and not later
                                                         else "")
    if k == "award":
        awards = "; ".join(f"{a['hospital']} takes {victims_phrase(a['victims'], severity or {})} (arrives "
                           f"{minute_tag(a['arrival'])})" for a in e.get("awards", []))
        esc = f"; escalate {victims_phrase(e['escalate'], severity or {})} to the referee" if e.get("escalate") else ""
        return "the hospitals' offers", (awards or "no award yet") + esc
    if k == "resolve":
        loss = " (a loss: it misses the deadline)" if e.get("loss") else ""
        return (f"an escalation for {victims_phrase([e.get('victim')], severity or {})}",
                f"{ARBITER_OPTIONS.get(e.get('option'), 'option ' + str(e.get('option')))} at {e.get('hospital')}{loss}")
    if k == "transfer_response":
        return f"a request to take {e.get('victim')}", "accept" if e.get("accept") else "decline"
    if k == "retry":
        return f"no hospital took {', '.join(e.get('victims', []))}", "try again in 5 minutes"
    return k.replace("_", " "), e.get("reason", "")


def decision_cards(events: list[dict], minute: int, incidents: dict[str, dict], calls: dict[int, dict],
                   show_all: bool, severity: dict[str, str] | None = None) -> list[dict]:
    cards = []
    for e in events:
        if e["t"] > minute:
            break
        if e["type"] == "guard":
            cards.append(guard_card(e))
        elif e["type"] == "agent_decision":
            call = calls.get(e["seq"])
            other, differs = rule_choice(e, call)
            interesting = (e["decided_by"] == "llm" and differs) or e["decided_by"] == "fallback" or e["kind"] == "resolve"
            if not (show_all or interesting):
                continue
            saw, decided = saw_decided(e, incidents, call, severity)
            cards.append({"t": e["t"], "agent": e["agent"], "badge": e["decided_by"], "saw": saw, "decided": decided,
                          "why": why_of(e), "other": other if differs or e["decided_by"] != "llm" else None,
                          "incident": e.get("incident"), "key": ("d", e["seq"])})
    return merge_guards(cards)[::-1]


def why_of(e: dict) -> str:
    """The recorded reason, word for word; an AI assessment may give only flags and no written note."""
    if e["kind"] == "assess" and e["decided_by"] == "llm" and e["reason"] == "rules":
        return "no written reason recorded" + (f"; the AI flagged: {', '.join(e['flags'])}" if e.get("flags") else "")
    return e["reason"]


def guard_card(e: dict) -> dict:
    if "suggested" in e:
        return {"t": e["t"], "agent": e["agent"], "badge": "guard", "saw": f"{e['incident']} assessed",
                "decided": f"AI suggested {e['suggested']}; the guard allowed {e['allowed']}",
                "why": e["why"], "ai_reason": e.get("ai_reason"), "other": None, "incident": e.get("incident"),
                "key": ("g", e["t"], e["agent"], e.get("incident"))}
    if "withheld" in e:
        return {"t": e["t"], "agent": e["agent"], "badge": "guard-option", "saw": f"a request for {e.get('incident')}",
                "decided": "the “after transfer” offer was not available to the AI", "why": e["why"], "other": None,
                "incident": e.get("incident"), "key": ("g", e["agent"], e.get("incident"))}
    return {"t": e["t"], "agent": e["agent"], "badge": "guard-option", "saw": f"an offer from {e['hospital']} for {e['victim']}",
            "decided": "the conditional offer counted as late, so it could lead to the referee", "why": e["why"],
            "other": None, "incident": e.get("incident"), "key": ("g", e.get("thread"), e["victim"])}


def merge_guards(cards: list[dict]) -> list[dict]:
    """Repeated guard steps in one negotiation become one card with a count."""
    out, seen = [], {}
    for c in cards:
        if c["key"][0] == "g" and c["key"] in seen:
            seen[c["key"]]["times"] += 1
            seen[c["key"]]["t"] = c["t"]
            continue
        c = {**c, "times": 1}
        out.append(c)
        if c["key"][0] == "g":
            seen[c["key"]] = c
    return sorted(out, key=lambda c: c["t"])


def decision_html(c: dict) -> str:
    times = f" · {c['times']}×" if c.get("times", 1) > 1 else ""
    ai = f"<div class='why'>AI's reason: {escape(c['ai_reason'])}</div>" if c.get("ai_reason") else ""
    other = f"<div class='agent-d'>Other option: {escape(c['other'])}</div>" if c.get("other") else ""
    return (f"<div class='dcard'><span class='ev-t'>{minute_tag(c['t'])}</span> {badge(c['badge'])} <b>{escape(agent_name(c['agent']))}</b>"
            f"{times}<div>Saw {escape(c['saw'])}.</div><div>Decided: <b>{escape(c['decided'])}</b>.</div>"
            f"<div class='why'>Why: {escape(c['why'])}</div>{ai}{other}</div>")
