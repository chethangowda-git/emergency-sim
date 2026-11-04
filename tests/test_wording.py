"""Every code the simulator and agents can log has plain-English wording (DESIGN 13.2).

The codes are read from the source itself, so a new event, message, reason or decision that has
no entry in dashboard/wording.py fails here instead of reaching the screen raw.
"""
from __future__ import annotations

import ast
from pathlib import Path

from dashboard import wording as W
from sim.generator import DESCRIPTIONS, SURGE_DESCRIPTIONS

ROOT = Path(__file__).resolve().parent.parent


def trees(folder: str):
    for path in sorted((ROOT / folder).glob("*.py")):
        yield path.name, ast.parse(path.read_text())


def const(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def consts(node) -> set[str]:
    """A string literal, or both branches of `"a" if ... else "b"`."""
    if isinstance(node, ast.IfExp):
        return consts(node.body) | consts(node.orelse)
    return {c} if (c := const(node)) else set()


def calls(folder: str, name: str):
    for _, tree in trees(folder):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if (isinstance(f, ast.Attribute) and f.attr == name) or (isinstance(f, ast.Name) and f.id == name):
                    yield node


def positional(folder: str, name: str, index: int) -> set[str]:
    return {c for node in calls(folder, name) if len(node.args) > index and (c := const(node.args[index]))}


def keyword(folder: str, name: str, key: str) -> set[str]:
    return {c for node in calls(folder, name) for kw in node.keywords if kw.arg == key and (c := const(kw.value))}


def missing(codes: set[str], known) -> list[str]:
    return sorted(c for c in codes if c not in known)


def test_every_event_type_has_wording():
    events = positional("sim", "emit", 0) | positional("sim", "_end_reservation", 2)
    for _, tree in trees("coordination"):          # what the agents add to the event log
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if const(k) == "type" and const(v):
                        events.add(const(v))
    assert len(events) > 30
    assert missing(events, W.EVENT_TYPES) == []
    sample = {"incident": "I01", "unit": "ALS1", "team": "FIRE1", "hospital": "H1", "from_hospital": "H2",
              "to_hospital": "H3", "victim": "I01-V1", "eta": 9, "incident_type": "bus_fire", "description": "d",
              "victims": [], "from_incident": "I02", "action": "reserve", "reason": "er_full", "minutes": 15,
              "until": 90, "severity": "minor", "outcome": "ward", "on_time": True}
    for k in events - {"message", "agent_decision", "guard"}:
        text = W.event_sentence({"type": k, **sample}, {"I01-V1": "critical"})
        assert text and "_" not in text, k


def test_every_message_type_has_wording():
    sent = set()
    for node in calls("coordination", "send"):
        via_bus = isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "bus"
        index = 3 if via_bus else 2
        if len(node.args) > index:
            sent |= consts(node.args[index])
    for _, tree in trees("coordination"):          # message types chosen into a variable first
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "kind" for t in node.targets) \
                    and isinstance(node.value, ast.IfExp):
                sent |= {c for c in consts(node.value) if "_" in c}          # unit kinds (ALS, ...) are not messages
    sent |= positional("coordination", "_vehicle_request", 1)
    assert {"admission_cfp", "admission_offer", "escalation", "transfer_request"} <= sent
    assert missing(sent, W.MESSAGE_TYPES) == []


def test_every_reason_code_has_wording():
    rejections = set()
    for _, tree in trees("sim"):
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and (fn.name.startswith("_check") or "rejection" in fn.name
                                                     or fn.name == "_can_transport"):
                rejections |= {c for r in ast.walk(fn) if isinstance(r, ast.Return) and (c := const(r.value))}
    assert {"er_full", "unit_busy", "victim_unavailable"} <= rejections
    assert missing(rejections, W.REJECTIONS) == []
    assert missing(positional("coordination", "_escalate", 1), W.TRIGGERS) == []
    assert missing(keyword("sim", "_end_reservation", "reason"), W.RELEASE_REASONS) == []


def test_every_decision_action_state_and_option_has_wording():
    assert missing(positional("coordination", "decision", 1), W.DECISIONS) == []
    actions, states, statuses = set(), set(), set()
    for _, tree in trees("sim"):
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "name" and const(node.value):
                actions.add(const(node.value))
            if isinstance(node, ast.Assign) and const(node.value):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
                        (states if (t.value.id, t.attr) == ("u", "state") else
                         statuses if (t.value.id, t.attr) == ("v", "status") else set()).add(const(node.value))
    assert missing(actions, W.ACTIONS) == [] and missing(states, W.UNIT_STATES) == []
    assert missing(statuses, W.VICTIM_STATUSES) == []
    assert missing(keyword("coordination", "ShortfallOption", "plan"), W.PLANS) == []
    options = set()
    for _, tree in trees("coordination"):
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and getattr(node.value, "id", "") in ("opts", "plans") and const(node.slice):
                options.add(const(node.slice))
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in ("opts", "plans") for t in node.targets) \
                    and isinstance(node.value, ast.Dict):
                options |= {c for k in node.value.keys if (c := const(k))}
    assert {"accept_now", "after_transfer", "earliest"} <= options and missing(options, W.OPTIONS) == []
    from coordination.arbiter import OPTION_NAMES
    assert set(OPTION_NAMES) == set(W.ARBITER_OPTIONS)
    assert missing(set(DESCRIPTIONS) | set(SURGE_DESCRIPTIONS), W.INCIDENT_TYPES) == []


def test_every_literal_rule_reason_reads_as_plain_words():
    reasons = set()
    for name in ("reason",):
        for folder in ("coordination",):
            reasons |= keyword(folder, "Decline", name) | keyword(folder, "send", name) | keyword(folder, "LoanResponse", name)
            reasons |= keyword(folder, "TransferResponse", name) | keyword(folder, "_offer", name)
    reasons |= positional("coordination", "reply", 1) | positional("coordination", "_retry", 1)
    reasons |= positional("coordination", "_transfer_failed", 2)
    assert len(reasons) > 8
    raw = [r for r in sorted(reasons) if r not in W.PHRASES and W.reason_phrase(r) == r]
    assert raw == []


def test_because_quotes_the_ai_and_translates_rules():
    assert W.because("We can treat the patient.", "llm") == "AI's words: “We can treat the patient.”"
    assert W.because("protective: decline (no free capacity now)", "rule") == \
        "because there is no free room right now; it is nearly full"
    assert W.because("set_destination rejected: victim_unavailable") == \
        "because the hospital choice was refused: the patient is no longer there"
    assert W.plan_text("wait:ALS3") == "wait for ALS3 to free up" and W.minute_tag(97) == "min 97"
    assert W.victims_phrase(["I02-V2"], {"I02-V2": "critical"}) == "I02's critical victim"
