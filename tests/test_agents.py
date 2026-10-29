from __future__ import annotations

from coordination.arbiter import ArbiterAgent
from coordination.dispatch import DispatchAgent
from coordination.hospital import HospitalAgent
from coordination.incident_assessment import IncidentAssessmentAgent
from coordination.llm import LLMClient
from coordination.schemas import (AdmissionOffer, AdmissionRequest, Conditional, Escalation, RequestedVictim,
                                  StaffRequest, TransferRequest, VehicleRequest)
from coordination.tools import ArbiterScope, DispatchScope, HospitalScope, IncidentScope, mood_for
from sim.actions import Reserve, SendToScene, SetDestination
from sim.models import UnitSpec
from tests.helpers import hospital, incident, make_world, of_type, run_until, walk_in

OFF = LLMClient("off")
TRAUMA_GENERAL = ("trauma", "general")


def fake(answer):
    return LLMClient("fake", fake=lambda agent, purpose, payload: answer)


def apply_ok(world, proposed) -> list[dict]:
    """Apply proposed actions now (ignoring not_before) and return any rejections."""
    evs = world.apply([p.to_action() for p in proposed])
    return of_type(evs, "action_rejected")


# ----- Incident Assessment -----

def fire_world():
    return make_world([incident("I01", "B", 0, [("critical", "burns"), ("critical", "burns"), ("serious", "burns"),
                                                ("minor", "general"), ("minor", "general")], kind="building_fire")])


def test_ia_rule_report():
    w = fire_world()
    w.start()
    r = IncidentAssessmentAgent(OFF).assess(IncidentScope(w.snapshot()), "I01")
    assert r.response_types == ["critical_care", "rescue_first"]
    assert r.units_needed.model_dump() == {"ALS": 2, "BLS": 2, "FIRE": 1, "RESCUE": 0}
    assert (r.minutes_left, r.at_risk) == (60, 2)
    assert r.care_counts == {"burns": {"critical": 2, "serious": 1}, "general": {"minor": 2}}
    assert r.decided_by == "rule"


def test_ia_llm_adds_context_and_units():
    w = fire_world()
    w.start()
    llm = fake({"flags": ["more burns likely"], "extra_als": 1, "extra_bls": 0, "note": "heavy smoke"})
    r = IncidentAssessmentAgent(llm).assess(IncidentScope(w.snapshot()), "I01")
    assert r.decided_by == "llm" and r.units_needed.ALS == 3 and r.flags == ["more burns likely"]


def test_ia_invalid_llm_falls_back_to_rules():
    w = fire_world()
    w.start()
    r = IncidentAssessmentAgent(fake({"extra_als": 9})).assess(IncidentScope(w.snapshot()), "I01")
    assert r.decided_by == "fallback" and r.units_needed.ALS == 2 and "invalid_output" in r.note


# ----- Dispatch: unit plans -----

def reports(world, llm=OFF):
    ia = IncidentAssessmentAgent(llm)
    scope = IncidentScope(world.snapshot())
    return [ia.assess(scope, i) for i in world.reported]


def test_dispatch_fast_path_assigns_nearest_units():
    w = fire_world()
    w.start()
    units = [UnitSpec("ALS1", "ALS", "A", "A"), UnitSpec("ALS2", "ALS", "E", "E"), UnitSpec("BLS1", "BLS", "A", "A"),
             UnitSpec("BLS2", "BLS", "A", "A"), UnitSpec("FIRE1", "FIRE", "D", "D")]
    w = make_world([incident("I01", "B", 0, [("critical", "burns"), ("critical", "burns"), ("serious", "burns"),
                                             ("minor", "general"), ("minor", "general")], kind="building_fire")],
                   units=units)
    w.start()
    plan = DispatchAgent(OFF).plan_units(DispatchScope(w.snapshot()), reports(w))
    assert plan.decided_by == "rule" and not plan.shortfalls
    assert {a.unit for a in plan.assignments} == {"ALS1", "ALS2", "BLS1", "BLS2", "FIRE1"}
    assert apply_ok(w, plan.proposed_actions) == []


def competing_world():
    return make_world([incident("I01", "C", 0, [("critical", "trauma")]), incident("I02", "B", 0, [("critical", "trauma")])],
                      units=[UnitSpec("ALS1", "ALS", "A", "A")])


def test_dispatch_shortfall_rule_ranks_by_minutes_left():
    w = competing_world()
    w.start()
    plan = DispatchAgent(OFF).plan_units(DispatchScope(w.snapshot()), reports(w))
    assert plan.ranking == ["I01", "I02"] and plan.assignments[0].incident == "I01"
    assert plan.shortfalls[0].incident == "I02" and plan.shortfalls[0].chosen == "queue"


def test_dispatch_llm_can_reorder_competing_incidents():
    w = competing_world()
    w.start()
    llm = fake({"ranking": ["I02", "I01"], "shortfall_plans": [{"incident": "I01", "option_id": "queue"}],
                "reason": "I02 is closer"})
    plan = DispatchAgent(llm).plan_units(DispatchScope(w.snapshot()), reports(w))
    assert plan.decided_by == "llm" and plan.assignments[0].incident == "I02"
    assert plan.shortfalls[0].chosen == "queue"


def test_dispatch_bad_ranking_falls_back():
    w = competing_world()
    w.start()
    llm = fake({"ranking": ["I09"], "reason": "?"})
    plan = DispatchAgent(llm).plan_units(DispatchScope(w.snapshot()), reports(w))
    assert plan.decided_by == "fallback" and plan.ranking == ["I01", "I02"]


def test_dispatch_offers_redirect_from_job_without_critical():
    w = make_world([incident("I01", "D", 0, [("serious", "trauma")]), incident("I02", "B", 1, [("critical", "trauma")])],
                   units=[UnitSpec("ALS1", "ALS", "A", "A")])
    w.start()
    w.apply([SendToScene("ALS1", "I01")])
    w.step()
    rs = [r for r in reports(w) if r.incident == "I02"] + [r for r in reports(w) if r.incident == "I01"]
    plan = DispatchAgent(OFF).plan_units(DispatchScope(w.snapshot()), rs)
    sf = next(s for s in plan.shortfalls if s.incident == "I02")
    assert sf.chosen == "redirect:ALS1"
    redirect = next(p for p in plan.proposed_actions if p.action == "redirect")
    assert apply_ok(w, [redirect]) == [] and w.units["ALS1"].incident == "I02"


# ----- Dispatch: awards -----

def award_setup():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma"), ("serious", "trauma")])],
                   hospitals=[hospital("H1", "C", slots=4), hospital("H2", "E", caps=TRAUMA_GENERAL, slots=4)])
    w.start()
    req = AdmissionRequest(incident="I01", origin_node="B", ready_at=5, victims=[
        RequestedVictim(id="I01-V1", severity="critical", care_need="trauma", deadline=60),
        RequestedVictim(id="I01-V2", severity="serious", care_need="trauma", deadline=120)])
    offers = [AdmissionOffer(hospital="H1", mood="eager", accept=["I01-V1", "I01-V2"], decided_by="rule"),
              AdmissionOffer(hospital="H2", mood="eager", accept=["I01-V2"], decided_by="rule")]
    return w, req, offers


def test_award_earliest_capable_arrival():
    w, req, offers = award_setup()
    d = DispatchAgent(OFF).award(DispatchScope(w.snapshot()), req, offers)
    assert d.decided_by == "rule" and [(a.hospital, a.victims) for a in d.awards] == [("H1", ["I01-V1", "I01-V2"])]
    assert d.escalate == [] and apply_ok(w, d.proposed_actions) == []


def test_award_llm_can_spare_the_specialist_hospital():
    w, req, offers = award_setup()
    d = DispatchAgent(fake({"option_id": "spare_specialist", "reason": "keep H1 for criticals"})).award(
        DispatchScope(w.snapshot()), req, offers)
    assert d.decided_by == "llm"
    assert [(a.hospital, a.victims) for a in d.awards] == [("H1", ["I01-V1"]), ("H2", ["I01-V2"])]


def test_award_escalates_when_offers_arrive_too_late():
    w, req, offers = award_setup()
    late = AdmissionOffer(hospital="H1", mood="protective", accept=[], decided_by="rule",
                          conditional=[Conditional(victims=["I01-V1"], after="transfer", after_minute=70)])
    d = DispatchAgent(OFF).award(DispatchScope(w.snapshot()), req, [late, offers[1]])
    assert d.escalate == ["I01-V1"]


def test_award_leaves_victims_nobody_offered_for_the_next_round():
    w, req, offers = award_setup()
    d = DispatchAgent(OFF).award(DispatchScope(w.snapshot()), req, [offers[1]])
    assert d.escalate == [] and [a.victims for a in d.awards] == [["I01-V2"]]


# ----- Hospital -----

def test_mood_thresholds():
    assert [mood_for(u) for u in (0.0, 0.59, 0.6, 0.85, 0.86)] == ["eager", "eager", "normal", "normal", "protective"]


def request(*victims):
    return AdmissionRequest(incident="I01", origin_node="B", ready_at=5,
                            victims=[RequestedVictim(id=v, severity=s, care_need=c, deadline=d) for v, s, c, d in victims])


def test_hospital_eager_accepts_and_declines_untreatable():
    w = make_world([], hospitals=[hospital("H1", "C", caps=TRAUMA_GENERAL, slots=4, doctors=("trauma", "trauma"))])
    w.start()
    o = HospitalAgent("H1", OFF).respond_admission(HospitalScope(w.snapshot(), "H1"), request(
        ("V1", "critical", "trauma", 60), ("V2", "serious", "burns", 120)))
    assert o.mood == "eager" and o.accept == ["V1"] and o.decline[0].victims == ["V2"] and o.decided_by == "rule"


def tight_world():
    """H1: 2 slots, one taken by a minor ready for release at minute 30."""
    w = make_world([], hospitals=[hospital("H1", "C", slots=2, doctors=("trauma", "trauma"), nurses=2)],
                   walk_ins=[walk_in("W01", "H1", 0, "minor", "general"), walk_in("W02", "H1", 0, "serious", "trauma")])
    run_until(w, 31)
    return w


def test_hospital_release_ladder_default_and_llm_choice():
    w = tight_world()
    scope = HospitalScope(w.snapshot(), "H1")
    req = request(("V1", "critical", "trauma", 60))
    o = HospitalAgent("H1", OFF).respond_admission(scope, req)
    assert o.accept == ["V1"] and o.proposed_actions[0].action == "release_patient"
    o2 = HospitalAgent("H1", fake({"option_id": "accept_now", "reason": "no"})).respond_admission(scope, req)
    assert o2.decided_by == "llm" and o2.accept == []
    assert apply_ok(w, o.proposed_actions) == []


def test_hospital_protective_keeps_slots_for_criticals():
    # 14 slots, 12 taken: 2 free of 14 is 85.7% utilization (over 85%), with room for both requests.
    w = make_world([], hospitals=[hospital("H1", "C", slots=14, doctors=("trauma",) * 7, nurses=5)],
                   walk_ins=[walk_in(f"W{k:02d}", "H1", 0, "minor", "general") for k in range(1, 13)])
    run_until(w, 1)
    scope = HospitalScope(w.snapshot(), "H1")
    assert scope.mood() == "protective"
    o = HospitalAgent("H1", OFF).respond_admission(scope, request(("V1", "critical", "trauma", 60),
                                                                  ("V2", "minor", "general", 240)))
    assert o.accept == ["V1"] and o.decline[0].reason == "keeping my last slots for criticals"


def test_hospital_transfer_emtala_and_mood():
    w = make_world([], hospitals=[hospital("H1", "C", caps=("burns", "general"), doctors=("burns",)),
                                  hospital("H2", "E", caps=TRAUMA_GENERAL, slots=4)])
    w.start()
    scope = HospitalScope(w.snapshot(), "H2")
    need_specialty = TransferRequest(victim="W01", care_need="trauma", from_hospital="H1",
                                     sender_capabilities=["burns", "general"])
    r = HospitalAgent("H2", OFF).respond_transfer(scope, need_specialty)
    assert r.accept and "EMTALA" in r.reason and r.proposed_actions[0].action == "reserve"
    lateral = need_specialty.model_copy(update={"care_need": "general", "sender_capabilities": ["general"]})
    assert HospitalAgent("H2", OFF).respond_transfer(scope, lateral).accept          # eager
    no_cap = need_specialty.model_copy(update={"care_need": "burns"})
    assert not HospitalAgent("H2", OFF).respond_transfer(scope, no_cap).accept


def test_hospital_staff_and_vehicle_loans():
    w = make_world([], hospitals=[hospital("H1", "C", doctors=("trauma", "trauma")), hospital("H2", "E")])
    w.start()
    scope = HospitalScope(w.snapshot(), "H1")
    s = HospitalAgent("H1", OFF).respond_staff(scope, StaffRequest(from_hospital="H2", kind="doctor", skill="trauma"))
    assert s.answer == "send_now" and s.proposed_actions[0].action == "lend_staff"
    v = HospitalAgent("H1", OFF).respond_vehicle(scope, VehicleRequest(incident="I01", until=100))
    assert v.answer == "send_now" and v.resource == "H1-TV1"
    assert apply_ok(w, s.proposed_actions + v.proposed_actions) == []


# ----- Arbiter -----

def esc(vid, origin="B", ready_at=None, unit=None, t=0):
    return Escalation(victim=vid, trigger="no_offer_in_time", unit=unit, origin_node=origin,
                      ready_at=ready_at if ready_at is not None else t)


def test_arbiter_option1_and_llm_choice_between_hospitals():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma")])],
                   hospitals=[hospital("H1", "C"), hospital("H2", "E", caps=TRAUMA_GENERAL)])
    w.start()
    scope = ArbiterScope(w.snapshot())
    d = ArbiterAgent(OFF).resolve(scope, esc("I01-V1"))
    assert d.option == 1 and d.hospital == "H1" and d.meets_deadline and d.decided_by == "rule"
    d2 = ArbiterAgent(fake({"option_id": "H2", "reason": "spread load"})).resolve(scope, esc("I01-V1"))
    assert d2.hospital == "H2" and d2.decided_by == "llm"
    assert apply_ok(w, d.proposed_actions) == []


def test_arbiter_option2_release_ready_patient():
    w = make_world([incident("I01", "B", 31, [("critical", "trauma")])],
                   hospitals=[hospital("H1", "C", slots=1), hospital("H2", "E", caps=("general",))],
                   walk_ins=[walk_in("W01", "H1", 0, "minor", "general")])
    run_until(w, 31)
    d = ArbiterAgent(OFF).resolve(ArbiterScope(w.snapshot()), esc("I01-V1", t=31))
    assert d.option == 2 and [a.action for a in d.proposed_actions] == ["release_patient", "reserve"]
    assert apply_ok(w, d.proposed_actions) == []


def test_arbiter_option3_stabilize_and_transfer_swap():
    w = make_world([incident("I01", "B", 25, [("critical", "burns")])],
                   hospitals=[hospital("H1", "C", slots=1, doctors=("trauma", "burns")),
                              hospital("H2", "E", caps=TRAUMA_GENERAL)],
                   walk_ins=[walk_in("W01", "H1", 0, "critical", "trauma")])
    run_until(w, 25)
    d = ArbiterAgent(OFF).resolve(ArbiterScope(w.snapshot()), esc("I01-V1", t=25))
    assert d.option == 3 and d.hospital == "H1" and d.arrival == 30
    assert [a.action for a in d.proposed_actions] == ["reserve", "transfer_patient", "reserve"]
    assert d.proposed_actions[-1].not_before == 30
    assert apply_ok(w, d.proposed_actions[:2]) == []


def test_arbiter_option4_override_hold():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma")])],
                   hospitals=[hospital("H1", "C", slots=1), hospital("H2", "E", caps=("general",))])
    w.start()
    w.apply([Reserve("H1", (), "hold", by="H1")])
    d = ArbiterAgent(OFF).resolve(ArbiterScope(w.snapshot()), esc("I01-V1"))
    assert d.option == 4 and d.proposed_actions[0].action == "release_hold"
    assert apply_ok(w, d.proposed_actions) == []


def test_arbiter_option5_reroutes_another_patient():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")]), incident("I02", "A", 7, [("critical", "burns")])],
                   hospitals=[hospital("H1", "C", slots=1, doctors=("trauma", "burns")),
                              hospital("H2", "E", caps=TRAUMA_GENERAL)])
    w.start()
    w.apply([SendToScene("BLS1", "I01"), SetDestination("BLS1", "H1", ("I01-V1",))])
    run_until(w, 7)
    d = ArbiterAgent(OFF).resolve(ArbiterScope(w.snapshot()), esc("I02-V1", origin="A", t=7))
    assert d.option == 5 and [a.action for a in d.proposed_actions] == ["reserve", "reroute", "reserve"]
    assert apply_ok(w, d.proposed_actions) == []


def test_arbiter_option6_urgent_staff_loan():
    w = make_world([incident("I01", "B", 0, [("critical", "burns")])],
                   hospitals=[hospital("H1", "C", doctors=("trauma",)), hospital("H2", "E", caps=("general",),
                                                                                doctors=("burns",))])
    w.start()
    d = ArbiterAgent(OFF).resolve(ArbiterScope(w.snapshot()), esc("I01-V1"))
    assert d.option == 6 and d.proposed_actions[0].action == "lend_staff" and d.proposed_actions[0].staff == "H2-D1"
    assert apply_ok(w, d.proposed_actions) == []


def test_arbiter_option7_least_bad_is_a_loss():
    w = make_world([incident("I01", "D", 0, [("critical", "trauma")])])
    w.victims["I01-V1"].deadline = 2
    w.start()
    d = ArbiterAgent(OFF).resolve(ArbiterScope(w.snapshot()), esc("I01-V1", origin="D"))
    assert d.option == 7 and d.loss and not d.meets_deadline and d.hospital == "H1"


def test_every_agent_output_round_trips_through_json():
    w = fire_world()
    w.start()
    r = IncidentAssessmentAgent(OFF).assess(IncidentScope(w.snapshot()), "I01")
    assert type(r).model_validate_json(r.model_dump_json()) == r


def test_agent_decision_replays_exactly(tmp_path):
    w = tight_world()
    scope = HospitalScope(w.snapshot(), "H1")
    req = request(("V1", "critical", "trauma", 60))
    live = fake({"option_id": "accept_now", "reason": "keep the bed"})
    first = HospitalAgent("H1", live).respond_admission(scope, req)
    live.save(tmp_path / "llm_calls.jsonl")
    replayed = HospitalAgent("H1", LLMClient("replay", replay_path=tmp_path / "llm_calls.jsonl")).respond_admission(
        scope, req)
    assert replayed == first and replayed.decided_by == "llm"
