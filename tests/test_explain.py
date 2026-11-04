from __future__ import annotations

import json

from coordination.dispatch import DispatchAgent
from coordination.llm import LLMClient
from coordination.orchestrator import AgentPolicy
from coordination.schemas import AdmissionOffer, Conditional
from coordination.tools import DispatchScope
from dashboard import explain, explain_agents, run_store
from sim import metrics
from sim.models import UnitSpec
from sim.runner import run
from tests.helpers import hospital, incident, make_world, walk_in
from tests.test_agents import award_setup, fake
from tests.test_generator_runner import PLACES, SMALL, roads
from tests.test_orchestrator import TRAUMA_GENERAL, step_agents


# ----- guard events (DESIGN 12.8): logged, never acted on -----

def guards(events):
    return [e for e in events if e["type"] == "guard"]


def test_the_extra_ambulance_cap_is_logged_with_the_ais_suggestion():
    llm = fake({"flags": ["children likely"], "extra_als": 2, "extra_bls": 0, "note": "school bus"})
    w = make_world([incident("I01", "B", 0, [("critical", "trauma")])], units=[UnitSpec("ALS1", "ALS", "A", "A")])
    step_agents(w, AgentPolicy(llm, guard=True), w.start(), 3)
    g = guards(w.events)
    assert [(e["rule"], e["agent"], e["incident"], e["suggested"], e["allowed"], e["ai_reason"]) for e in g] == [
        ("G2", "ia", "I01", "2 extra ALS", "1 extra ALS", "school bus")]
    w2 = make_world([incident("I01", "B", 0, [("critical", "trauma")])], units=[UnitSpec("ALS1", "ALS", "A", "A")])
    step_agents(w2, AgentPolicy(llm, guard=False), w2.start(), 3)
    assert guards(w2.events) == []


def test_a_withheld_after_transfer_offer_is_logged_with_why():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])],
                   hospitals=[hospital("H1", "C", slots=1, doctors=("trauma", "trauma"), nurses=2),
                              hospital("H2", "E", caps=TRAUMA_GENERAL, slots=4)],
                   units=[], walk_ins=[walk_in("W01", "H1", 0, "critical", "trauma")])
    p = AgentPolicy(LLMClient("off"), guard=True)
    events = step_agents(w, p, w.start(), 21)                   # W01 transferable, H1 full, vehicle idle
    p.cooldown[("transfer", "H1")] = p.t + 5
    th = p.open_thread("admission", unit="X1")
    req = {"incident": "I01", "origin_node": "B", "ready_at": 25, "unit": "X1",
           "victims": [{"id": "I01-V1", "severity": "serious", "care_need": "trauma", "deadline": 200}]}
    p._h_admission_admission_cfp(p.bus.send(p.t, "dispatch", "H1", "admission_cfp", th, proposed_action=req))
    g = guards(p.log)
    assert [(e["rule"], e["agent"], e["withheld"], e["thread"]) for e in g] == [("G1", "H1", "after transfer", th)]
    assert "10 minutes" in g[0]["why"]


def test_a_conditional_offer_counted_late_is_noted_for_the_log():
    w, req, offers = award_setup()                              # I01-V1 critical, deadline 60
    late = [AdmissionOffer(hospital="H1", mood="protective", accept=[], decided_by="rule",
                           conditional=[Conditional(victims=["I01-V1"], after="transfer", after_minute=50)]), offers[1]]
    d = DispatchAgent(LLMClient("off"), guard=True)
    d.award(DispatchScope(w.snapshot()), req, late)
    assert [(b["victim"], b["hospital"]) for b in d.blocked] == [("I01-V1", "H1")] and d.blocked[0]["slack"] < 15
    plain = DispatchAgent(LLMClient("off"))
    plain.award(DispatchScope(w.snapshot()), req, late)
    assert plain.blocked == []


def test_metrics_ignore_guard_events(tmp_path):
    out = run(5, "agents", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES, llm_mode="off")
    data = run_store.load_run(out.name, tmp_path)
    plain = metrics.compute(data["victims"], data["timeline"], data["events"])
    extra = data["events"] + [{"t": 3, "type": "guard", "rule": "G1", "agent": "H1", "why": "x", "seq": 10**6}]
    assert metrics.compute(data["victims"], data["timeline"], extra) == plain


# ----- plain-language templates -----

def test_victim_status_against_the_deadline():
    v = {"deadline": 60}
    assert explain.victim_status(v, 30, 40, None) == "on track"          # 20 minutes to spare
    assert explain.victim_status(v, 30, 50, None) == "tight"             # 10 minutes
    assert explain.victim_status(v, 30, 65, None) == "late"
    assert explain.victim_status(v, 61, None, None) == "late"
    assert explain.victim_status(v, 70, None, (55, "H1")) == "on time"
    assert explain.victim_status({"deadline": None}, 10, None, None) == "on track"


def saved(tmp_path, policy="agents"):
    out = run(5, policy, runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES, llm_mode="off")
    return run_store.load_run(out.name, tmp_path)


def test_incident_cards_follow_the_run(tmp_path):
    data = saved(tmp_path)
    first = data["timeline"][1]["incidents"][0]["id"]
    cards = explain.incident_cards(data, 1, [])
    card = next(c for c in cards if c["id"] == first)
    assert card["open"] and card["steps"][0] and card["title"].startswith(first)
    assert all(r["status"] in ("on track", "tight", "late") for r in card["victims"])
    end = explain.incident_cards(data, len(data["timeline"]) - 1, [])
    done = [c for c in end if not c["open"]]
    assert done and all(c["steps"] == [True] * 5 for c in done)
    assert [c["open"] for c in end] == sorted((c["open"] for c in end), reverse=True)   # finished ones last
    html = explain.incident_card_html(card, focus=True)
    assert "set_destination" not in html and "class='icard" in html and "focus" in html


def test_now_sentence_names_what_happened(tmp_path):
    data = saved(tmp_path)
    t = next(e["t"] for e in data["events"] if e["type"] == "incident_reported")
    s = explain.now_sentence(data, t)
    assert s.startswith(f"Minute {t}: ") and "reported" in s and s.endswith(".")
    quiet = {"units": [{"trip": None}], "incidents": [], "hospitals": []}
    assert explain.quiet_line(quiet) == "all quiet, no incident open"


def test_negotiations_read_as_questions_with_outcomes(tmp_path):
    from dashboard.agent_views import thread_index

    data = saved(tmp_path)
    severity = {v["id"]: v["original_severity"] for v in data["victims"]}
    views = explain_agents.thread_views(thread_index(data["events"]), len(data["timeline"]) - 1, severity)
    admission = next(v for v in views if v["protocol"] == "Admission")
    assert admission["title"].startswith("Where should ") and admission["title"].endswith(" go?")
    assert admission["verdict"] in ("agreed", "escalated to referee", "no deal", "in progress")
    assert all("_" not in b["text"] for b in admission["bubbles"])
    assert explain_agents.verdict("Referee: least bad at H1") == "escalated to referee"
    assert explain_agents.verdict("award declined by H2") == "no deal"
    assert explain_agents.verdict("awarded to H1, reserved") == "agreed"
    assert explain_agents.plan_text("wait:ALS1") == "wait for ALS1 to free up"


def test_ai_decisions_show_the_rules_alternative_and_guard_cards_merge():
    call = {"agent": "H1", "purpose": "admission", "input": {"t": 40, "mood": "normal", "options": ["accept_now",
            "after_transfer"], "request": {"victims": [{"id": "I04-V3"}]},
            "option_details": {"after_transfer": {"conditional": [{"victims": ["I04-V3"]}]}}},
            "output": {"option_id": "after_transfer", "reason": "keeps capacity"}}
    events = [{"seq": 1, "t": 40, "type": "agent_decision", "agent": "H1", "kind": "admission_offer",
               "decided_by": "llm", "reason": "keeps capacity", "accept": [], "thread": "TH1"},
              {"seq": 2, "t": 41, "type": "agent_decision", "agent": "H1", "kind": "admission_offer",
               "decided_by": "rule", "reason": "normal: accept now", "accept": ["I05-V1"], "thread": "TH2"}]
    events += [{"seq": 3 + k, "t": 42 + k, "type": "guard", "rule": "G1", "agent": "H2", "why": "cooldown",
                "thread": "TH3", "incident": "I06", "withheld": "after transfer"} for k in range(3)]
    calls = explain_agents.match_calls(events, [call])
    assert calls == {1: call}
    cards = explain_agents.decision_cards(events, 60, {}, calls, show_all=False)
    assert [c["badge"] for c in cards] == ["guard-option", "llm"]                 # the rule's own decision hidden
    guard, ai = cards
    assert guard["times"] == 3 and guard["t"] == 44
    assert ai["why"] == "keeps capacity" and ai["other"] == "rule: accept whoever fits now"
    assert "once a patient is transferred out" in ai["decided"]
    assert len(explain_agents.decision_cards(events, 60, {}, calls, show_all=True)) == 3
    html = explain_agents.decision_html(ai)
    assert ">AI<" in html and "Rule would have chosen: accept whoever fits now" in html and "AI&#x27;s words" in html


def test_hospital_cards_list_patients_offers_and_walk_ins(tmp_path):
    data = saved(tmp_path)
    cards = explain.hospital_cards(data, len(data["timeline"]) - 1)
    assert [c["id"] for c in cards] == [h["id"] for h in data["timeline"][0]["hospitals"]]
    assert all(c["mood"] in ("eager", "normal", "protective") for c in cards)
    assert sum(c["walk_ins"] for c in cards) == sum(1 for e in data["events"] if e["type"] == "walk_in")
    assert "<b>" in explain.hospital_card_html(cards[0])


def test_every_tooltip_term_has_text():
    assert all(explain.GLOSSARY[t] for t in ("Criticals on time", "Unserved criticals", "Avg to care", "Delivered",
                                             "on track", "tight", "late", "Guard", "Referee"))
    assert "title='" in explain.tip("tight") and explain.unit_name("ALS2") == "paramedic ambulance ALS2"
    assert explain.count_units("BLS", 2) == "2 basic ambulances"


# ----- map labels, display names, negotiation list and chips -----

def test_hospitals_get_neutral_display_names_and_unknown_ids_keep_theirs():
    from dashboard.names import hospital_label, hospital_name
    assert hospital_label({"id": "H2", "name": "Northside General"}) == "H2 Riverside Medical"
    assert hospital_name("H9", "Recorded") == "Recorded"


def test_unit_badges_never_overlap_labels_or_each_other():
    from dashboard import map_view as mv
    spot = {"lat": 42.35, "lon": -71.06}
    hospitals = [{**spot, "name": "H1 South End General", "er": "ER 6/6 (+2)"}]
    incidents = [{"lat": 42.3502, "lon": -71.0601, "radius": 200}]
    units = [{"id": f"ALS{k}", "kind": "ALS", "state": "on_scene", **spot} for k in range(1, 6)] + \
            [{"id": "FIRE1", "kind": "FIRE", "state": "idle", "lat": 42.3501, "lon": -71.0600}]
    zoom = 12.6
    offsets = mv.badge_offsets(units, hospitals, incidents, zoom)
    boxes = []
    for u in units:
        x, y = mv.to_px(u["lat"], u["lon"], zoom)
        dx, dy = offsets[u["id"]]
        w = mv.badge_width(mv.unit_badge_text(u))
        boxes.append((x + dx - w / 2, y + dy - mv.BADGE_H / 2, x + dx + w / 2, y + dy + mv.BADGE_H / 2))
    fixed = mv.fixed_boxes(hospitals, incidents, zoom)
    assert not any(mv.overlaps(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:])
    assert not any(mv.overlaps(a, b) for a in boxes for b in fixed)


def test_parked_hospital_vehicles_move_into_the_hospital_tooltip():
    from dashboard import map_view as mv
    minute = {"hospitals": [{"id": "H1", "node": "C"}]}
    units = [{"id": "H1-TV1", "kind": "HV", "state": "idle", "owner": "H1", "node": "C"},
             {"id": "H1-TV2", "kind": "HV", "state": "transporting", "owner": "H1", "node": "C"},
             {"id": "ALS1", "kind": "ALS", "state": "idle", "owner": None, "node": "C"}]
    assert mv.parked_vehicles(units, minute) == {"H1": ["H1-TV1"]}


def msg(seq, t, kind, sender, recipient, vids=("I02-V2",), round_=1, reason="", **p):
    return {"seq": seq, "t": t, "msg_type": kind, "sender": sender, "recipient": recipient, "round": round_,
            "victim_ids": list(vids), "reason": reason, "decided_by": "rule", "proposed_action": p,
            "incident_id": "I02", "thread_id": "TH9", "deliver_at": t + 1}


def admission_messages():
    out = [msg(1, 10, "pre_alert", "dispatch", "H1")]
    out += [msg(2 + k, 10, "admission_cfp", "dispatch", h) for k, h in enumerate(("H1", "H2", "H3"))]
    out += [msg(5, 11, "admission_offer", "H1", "dispatch", accept=["I02-V2"], conditional=[], decline=[],
                reason="normal: accept now"),
            msg(6, 11, "admission_offer", "H2", "dispatch", accept=[], conditional=[],
                decline=[{"victims": ["I02-V2"], "reason": "cannot treat this care need"}], reason="no burns unit")]
    return out


def test_one_bubble_for_a_message_to_several_hospitals_with_the_heads_up_folded_in():
    severity = {"I02-V2": "critical"}
    bubbles = explain_agents.bubbles_of(admission_messages(), 11, severity)
    first = bubbles[0]
    assert first["to"] == ["H1", "H2", "H3"] and first["heads_up"]
    assert first["text"] == "Can you take I02's critical victim?" and first["ids"] == ["I02-V2"]
    assert len(bubbles) == 3                                     # the request, then each hospital's offer
    html = explain_agents.thread_detail_html({"id": "TH9", "title": "t", "verdict": "in progress",
                                              "outcome": "round 1 in progress", "rounds": 1, "chips": [],
                                              "bubbles": bubbles})
    assert "min 10" in html and "Heads-up and request" in html and "TH9" in html.split(">")[0]


def test_hospital_chips_say_where_each_hospital_stands():
    ms = admission_messages()
    chips = {c["hospital"]: (c["text"], c["reason"]) for c in explain_agents.hospital_chips(ms)}
    assert chips == {"H1": ("offered", "normal: accept now"), "H2": ("can't treat", "no burns unit"),
                     "H3": ("waiting", "")}
    ms += [msg(7, 12, "admission_award", "dispatch", "H1"), msg(8, 13, "ack", "H1", "dispatch", reason="firm")]
    assert explain_agents.hospital_chips(ms)[0]["text"] == "accepted"


def test_victims_read_as_phrases_with_ids_kept_for_tooltips():
    sev = {"I02-V1": "critical", "I02-V2": "serious", "I03-V1": "minor", "I03-V2": "minor"}
    assert explain_agents.victims_phrase(["I02-V1"], sev) == "I02's critical victim"
    assert explain_agents.victims_phrase(["I03-V1", "I03-V2"], sev) == "I03's 2 minor victims"
    assert explain_agents.victims_phrase(["I02-V1", "I02-V2"], sev) == "I02's critical and serious victims"
    assert explain_agents.victims_phrase(["W03"], sev) == "patient W03"


def test_unit_requests_say_what_was_sent_and_what_is_still_short():
    ms = [msg(1, 5, "unit_request", "ia", "dispatch", vids=()),
          msg(2, 6, "unit_response", "dispatch", "ia", vids=(), sent=["ALS1"], shortfall={"BLS": 1}, plan="wait:BLS3")]
    assert explain_agents.unit_outcome(ms) == ("short of units",
                                               "sent ALS1; still short of 1 basic ambulance: wait for BLS3 to free up")
    ms[1]["proposed_action"].update(shortfall=None)
    assert explain_agents.unit_outcome(ms) == ("units sent", "sent ALS1")
