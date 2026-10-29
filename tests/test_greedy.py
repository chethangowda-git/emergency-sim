from __future__ import annotations

from policies.greedy import GreedyPolicy
from sim.actions import AssignTeam, SendToScene, SetDestination
from sim.models import UnitSpec
from tests.helpers import hospital, incident, make_world, of_type, walk_in

ALS_A, ALS_E = UnitSpec("ALS1", "ALS", "A", "A"), UnitSpec("ALS2", "ALS", "E", "E")
BLS_A = UnitSpec("BLS1", "BLS", "A", "A")
FIRE_D, FIRE_A = UnitSpec("FIRE1", "FIRE", "D", "D"), UnitSpec("FIRE2", "FIRE", "A", "A")


def decide(world):
    return GreedyPolicy().decide(world.snapshot(), [])


def run_greedy(world, until):
    policy = GreedyPolicy()
    events = world.start()
    while world.t < until:
        events = world.apply(policy.decide(world.snapshot(), events))
        events += world.step()
    return world.events


def test_nearest_free_als_goes_to_critical():
    w = make_world([incident("I01", "C", 0, [("critical", "trauma")])], units=[ALS_A, ALS_E, BLS_A])
    w.start()
    assert decide(w) == [SendToScene("ALS2", "I01")]


def test_bls_for_non_criticals_and_one_unit_per_two():
    w = make_world([incident("I01", "C", 0, [("minor", "general"), ("serious", "trauma")])],
                   units=[ALS_A, BLS_A])
    w.start()
    assert decide(w) == [SendToScene("BLS1", "I01")]


def test_als_used_for_non_critical_when_no_bls_free():
    w = make_world([incident("I01", "C", 0, [("minor", "general")])], units=[ALS_A])
    w.start()
    assert decide(w) == [SendToScene("ALS1", "I01")]


def test_critical_waits_when_no_als_free():
    w = make_world([incident("I01", "C", 0, [("critical", "trauma")])], units=[BLS_A])
    w.start()
    assert decide(w) == []


def test_most_severe_incident_first():
    w = make_world([incident("I01", "C", 0, [("minor", "general")]),
                    incident("I02", "B", 0, [("critical", "trauma")])], units=[ALS_A])
    w.start()
    assert decide(w) == [SendToScene("ALS1", "I02")]


def test_oldest_first_among_equal_severity():
    w = make_world([incident("I01", "C", 0, [("serious", "trauma")]),
                    incident("I02", "B", 1, [("serious", "trauma")])], units=[BLS_A])
    w.start()
    w.step()
    assert decide(w) == [SendToScene("BLS1", "I01")]


def test_does_not_send_more_units_than_needed():
    w = make_world([incident("I01", "C", 0, [("minor", "general"), ("minor", "general")])],
                   units=[ALS_A, BLS_A])
    w.start()
    w.apply(decide(w))
    w.step()
    assert decide(w) == []


def test_nearest_capable_team():
    w = make_world([incident("I01", "B", 0, [("serious", "burns")], kind="building_fire")],
                   units=[FIRE_D, FIRE_A, ALS_A])
    w.start()
    acts = decide(w)
    assert AssignTeam("FIRE2", "I01") in acts and SendToScene("ALS1", "I01") in acts


def test_hospital_chosen_on_cleared_scene_nearest_with_space():
    w = make_world([incident("I01", "B", 0, [("serious", "general")])],
                   hospitals=[hospital("H1", "C"), hospital("H2", "E", caps=("general",))], units=[ALS_A])
    w.start()
    w.apply(decide(w))
    assert w.units["ALS1"].hospital is None          # not on scene yet
    w.step(); w.step()
    assert decide(w) == [SetDestination("ALS1", "H1", ("I01-V1",))]


def test_skips_full_hospital_and_respects_capability():
    w = make_world([incident("I01", "B", 0, [("serious", "general"), ("serious", "burns")])],
                   hospitals=[hospital("H1", "C", slots=1), hospital("H2", "E", caps=("general",))],
                   units=[ALS_A], walk_ins=[walk_in("W01", "H1", 0)])
    w.start()
    w.apply(decide(w))
    w.step(); w.step()
    assert decide(w) == [SetDestination("ALS1", "H2", ("I01-V1",))]   # burns victim has nowhere to go


def test_waits_when_no_capable_hospital_has_space():
    w = make_world([incident("I01", "B", 0, [("serious", "burns")])],
                   hospitals=[hospital("H1", "C", slots=1), hospital("H2", "E", caps=("general",))],
                   units=[ALS_A], walk_ins=[walk_in("W01", "H1", 0)])
    w.start()
    w.apply(decide(w))
    w.step(); w.step()
    assert decide(w) == []


def test_pairs_two_non_criticals_when_hospital_has_two_slots():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma"), ("minor", "general")])], units=[BLS_A])
    w.start()
    w.apply(decide(w))
    w.step(); w.step()
    assert decide(w) == [SetDestination("BLS1", "H1", ("I01-V1", "I01-V2"))]


def test_after_arrived_no_slot_goes_to_next_hospital():
    w = make_world([incident("I01", "B", 0, [("serious", "general")])],
                   hospitals=[hospital("H1", "C", slots=1), hospital("H2", "E", caps=("general",))],
                   units=[ALS_A], walk_ins=[walk_in("W01", "H1", 4)])
    evs = run_greedy(w, 12)
    assert of_type(evs, "reservation_broken")
    no_slot = of_type(evs, "arrived_no_slot")[0]
    delivered = of_type(evs, "patient_delivered")[0]
    assert no_slot["hospital"] == "H1" and delivered["hospital"] == "H2" and delivered["t"] == no_slot["t"] + 1


def test_only_phase1_actions_and_no_rejections_on_busy_scenario():
    incs = [incident("I01", "B", 0, [("critical", "trauma"), ("serious", "trauma"), ("minor", "general")]),
            incident("I02", "D", 3, [("serious", "burns"), ("minor", "general")], kind="building_fire"),
            incident("I03", "E", 5, [("critical", "trauma")], kind="structural_collapse")]
    units = [ALS_A, ALS_E, BLS_A, FIRE_D, UnitSpec("RESCUE1", "RESCUE", "D", "D")]
    w = make_world(incs, units=units, hospitals=[hospital("H1", "C", slots=6, doctors=("trauma", "burns")),
                                                 hospital("H2", "E", caps=("general",))])
    evs = run_greedy(w, 200)
    assert not of_type(evs, "action_rejected")
    applied = {e["action"] for e in of_type(evs, "action_applied")}
    assert applied <= {"send_to_scene", "set_destination", "assign_team"}
    delivered = {e["victim"] for e in of_type(evs, "patient_delivered")}
    assert delivered == {v for i, _ in incs for v in i.victim_ids}


def test_takes_next_victim_when_most_severe_has_no_hospital():
    w = make_world([incident("I01", "B", 0, [("serious", "burns"), ("minor", "general")])],
                   hospitals=[hospital("H1", "C", slots=1), hospital("H2", "E", caps=("general",))],
                   units=[BLS_A], walk_ins=[walk_in("W01", "H1", 0)])
    w.start()
    w.apply(decide(w))
    w.step(); w.step()
    assert decide(w) == [SetDestination("BLS1", "H2", ("I01-V2",))]
