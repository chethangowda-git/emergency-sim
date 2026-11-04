"""Helpers over the recorded negotiations (DESIGN 13.2): where each hospital stands in a thread, the
recorded LLM call behind each AI decision, and what the rule would have chosen. Wording comes from
dashboard/wording.py; nothing here calls an LLM."""
from __future__ import annotations

from collections import defaultdict

from coordination.choices import rule_default, rule_ranking_of
from dashboard import wording as W

KIND_PURPOSE = {"admission_offer": "admission", "award": "award", "plan_units": "plan_units", "assess": "assess",
                "resolve": "resolve", "transfer_response": "transfer", "staff_offer": "staff"}


# ----- negotiations -----

def question(th: dict, severity: dict[str, str]) -> str:
    first = th["messages"][0]
    iid, vids, p = first.get("incident_id"), first.get("victim_ids") or [], first.get("proposed_action") or {}
    who = W.victims_phrase(vids, severity)
    return {"Admission": lambda: f"Where should {who} go?",
            "Unit request": lambda: f"Which units go to {iid}?",
            "Transfer": lambda: f"Who takes {who} from {first['sender']}?",
            "Staff loan": lambda: f"Can another hospital lend {first['sender']} a {p.get('kind', 'staff member')}?",
            "Mutual aid": lambda: f"Can a hospital lend a vehicle for {iid}?",
            "Swap": lambda: f"Can a hospital vehicle take over a job so an ambulance is free for {iid}?",
            "Reroute": lambda: f"{first['sender']} lost the slot for {who}: where now?",
            "Pre-alert": lambda: f"Hold a slot for {who}?",
            }.get(th["protocol"], lambda: f"{th['protocol']} for {iid or who}")()


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
        return f"rules alone: {W.units_text(rule)}", bool(extra)
    rule = rule_default(call["agent"], call["purpose"], inp)
    return f"rule: {W.OPTIONS.get(rule, 'its usual choice')}", out.get("option_id") != rule


