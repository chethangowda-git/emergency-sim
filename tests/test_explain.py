from __future__ import annotations


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


def saved(tmp_path, policy="agents"):
    out = run(5, policy, runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES, llm_mode="off")
    return run_store.load_run(out.name, tmp_path)


def test_now_sentence_names_what_happened(tmp_path):
    data = saved(tmp_path)
    t = next(e["t"] for e in data["events"] if e["type"] == "incident_reported")
    s = explain.now_sentence(data, t)
    assert s.startswith(f"Minute {t}: ") and "reported" in s and s.endswith(".")
    quiet = {"units": [{"trip": None}], "incidents": [], "hospitals": []}
    assert explain.quiet_line(quiet) == "all quiet, no incident open"


def test_every_tooltip_term_has_text():
    assert all(explain.GLOSSARY[t] for t in ("Criticals on time", "Unserved criticals", "Avg to care", "Delivered",
                                             "on track", "tight", "late", "Guard", "Referee"))
    from dashboard import wording as W
    assert "title='" in explain.tip("tight") and W.unit_name("ALS2") == "paramedic ambulance ALS2"
    assert W.count_units("BLS", 2) == "2 basic ambulances"


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


def test_hospital_chips_say_where_each_hospital_stands():
    ms = admission_messages()
    chips = {c["hospital"]: (c["text"], c["reason"]) for c in explain_agents.hospital_chips(ms)}
    assert chips == {"H1": ("offered", "normal: accept now"), "H2": ("can't treat", "no burns unit"),
                     "H3": ("waiting", "")}
    ms += [msg(7, 12, "admission_award", "dispatch", "H1"), msg(8, 13, "ack", "H1", "dispatch", reason="firm")]
    assert explain_agents.hospital_chips(ms)[0]["text"] == "accepted"


def test_victims_read_as_phrases_with_ids_kept_for_tooltips():
    from dashboard import wording as W
    sev = {"I02-V1": "critical", "I02-V2": "serious", "I03-V1": "minor", "I03-V2": "minor"}
    assert W.victims_phrase(["I02-V1"], sev) == "I02's critical victim"
    assert W.victims_phrase(["I03-V1", "I03-V2"], sev) == "I03's 2 minor victims"
    assert W.victims_phrase(["I02-V1", "I02-V2"], sev) == "I02's critical and serious victims"
    assert W.victims_phrase(["W03"], sev) == "patient W03"
