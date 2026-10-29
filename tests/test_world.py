from __future__ import annotations

from sim.actions import AssignTeam, SendToScene, SetDestination
from sim.models import UnitSpec
from sim.roads import position_at
from tests.helpers import hospital, incident, make_world, of_type, run_until, walk_in


def rejected(world, action) -> str | None:
    evs = world.apply([action])
    return evs[0]["reason"] if evs[0]["type"] == "action_rejected" else None


# ----- send_to_scene -----

def test_send_to_scene_drives_by_road_and_arrives():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])])
    evs = run_until(w, 2, {0: [SendToScene("ALS1", "I01")]})
    assert of_type(evs, "unit_dispatched")[0]["eta"] == 2
    on_scene = of_type(evs, "unit_on_scene")
    assert on_scene == [{"seq": on_scene[0]["seq"], "t": 2, "type": "unit_on_scene", "unit": "ALS1", "incident": "I01"}]
    assert w.trips[0].route.nodes == ("A", "B")


def test_send_to_scene_rejections():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")]), incident("I02", "B", 50, [("minor", "general")])])
    w.start()
    assert rejected(w, SendToScene("ALS9", "I01")) == "unknown_unit"
    assert rejected(w, SendToScene("ALS1", "I02")) == "unknown_incident"
    assert rejected(w, SendToScene("FIRE1", "I01")) == "capability_mismatch"
    assert rejected(w, SendToScene("ALS1", "I01")) is None
    assert rejected(w, SendToScene("ALS1", "I01")) == "unit_busy"
    w.apply([SetDestination("ALS1", "H1", ("I01-V1",))])
    assert rejected(w, SendToScene("BLS1", "I01")) == "incident_resolved"


def test_rejections_are_logged_with_reason():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])])
    w.start()
    w.apply([SendToScene("FIRE1", "I01")])
    ev = of_type(w.events, "action_rejected")[0]
    assert ev["reason"] == "capability_mismatch" and ev["action"] == "send_to_scene" and ev["unit"] == "FIRE1"


# ----- full trip, timing, handover, return -----

def test_full_trip_timing():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])])
    evs = run_until(w, 20, {0: [SendToScene("ALS1", "I01")], 2: [SetDestination("ALS1", "H1", ("I01-V1",))]})
    assert of_type(evs, "transport_started")[0]["t"] == 7          # on scene 2 + 5
    delivered = of_type(evs, "patient_delivered")[0]
    assert delivered["t"] == 8 and delivered["on_time"] is True     # B -> C is 1 min
    assert of_type(evs, "treatment_started")[0]["t"] == 8
    returning = of_type(evs, "unit_returning")[0]
    assert returning["t"] == 13 and returning["eta"] == 16          # 5 min handover, C -> A 3 min
    assert of_type(evs, "unit_at_base")[0]["t"] == 16
    assert w.units["ALS1"].state == "idle"


def test_destination_set_before_arrival_departs_after_on_scene_time():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])])
    evs = run_until(w, 10, {0: [SendToScene("ALS1", "I01"), SetDestination("ALS1", "H1", ("I01-V1",))]})
    assert of_type(evs, "transport_started")[0]["t"] == 7
    assert w.reservations["R001"].created_at == 0


def test_returning_unit_can_be_reassigned_and_route_continues():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")]), incident("I02", "B", 14, [("minor", "general")])])
    run_until(w, 14, {0: [SendToScene("ALS1", "I01")], 2: [SetDestination("ALS1", "H1", ("I01-V1",))],
                      14: [SendToScene("ALS1", "I02")]})
    old, new = w.trips[-2], w.trips[-1]
    assert old.purpose == "to_base" and old.ended_at == 14
    assert new.purpose == "to_scene" and new.depart_t == 14
    assert new.route.points[0] == position_at(old.route, old.depart_t, 14)


def test_idle_ambulance_on_scene_returns_to_base():
    w = make_world([incident("I01", "B", 0, [("minor", "general")])])
    evs = run_until(w, 3, {0: [SendToScene("ALS1", "I01"), SendToScene("BLS1", "I01"),
                               SetDestination("BLS1", "H1", ("I01-V1",))]})
    assert [e["unit"] for e in of_type(evs, "unit_returning")] == ["ALS1"]


# ----- scene clearance and teams -----

def test_fire_scene_needs_clearance_before_transport():
    w = make_world([incident("I01", "B", 0, [("serious", "burns")], kind="building_fire")])
    evs = run_until(w, 30, {0: [SendToScene("ALS1", "I01"), AssignTeam("FIRE1", "I01"),
                                SetDestination("ALS1", "H1", ("I01-V1",))]})
    team_on_scene = [e for e in of_type(evs, "unit_on_scene") if e["unit"] == "FIRE1"][0]
    assert team_on_scene["t"] == 6                                     # D -> B
    assert of_type(evs, "scene_cleared")[0]["t"] == 16                 # 10 min after team arrives
    assert of_type(evs, "transport_started")[0]["t"] == 21             # cleared + 5
    assert any(e["unit"] == "FIRE1" for e in of_type(evs, "unit_returning"))


def test_assign_team_rejections():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")]),
                    incident("I02", "B", 0, [("serious", "trauma")], kind="structural_collapse"),
                    incident("I03", "B", 0, [("serious", "burns")], kind="building_fire")])
    w.start()
    assert rejected(w, AssignTeam("FIRE9", "I03")) == "unknown_unit"
    assert rejected(w, AssignTeam("FIRE1", "I09")) == "unknown_incident"
    assert rejected(w, AssignTeam("FIRE1", "I01")) == "not_required"
    assert rejected(w, AssignTeam("FIRE1", "I02")) == "capability_mismatch"
    assert rejected(w, AssignTeam("ALS1", "I03")) == "capability_mismatch"
    assert rejected(w, AssignTeam("RESCUE1", "I02")) is None
    assert rejected(w, AssignTeam("RESCUE1", "I02")) == "already_covered"
    w2 = make_world([incident("I02", "B", 0, [("serious", "trauma")], kind="structural_collapse"),
                     incident("I04", "B", 0, [("serious", "trauma")], kind="structural_collapse")])
    w2.start()
    w2.apply([AssignTeam("RESCUE1", "I02")])
    assert rejected(w2, AssignTeam("RESCUE1", "I04")) == "unit_busy"


# ----- set_destination and loading rules -----

def test_unit_loading_rules():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma"), ("serious", "trauma"),
                                             ("minor", "general"), ("minor", "general")])],
                   hospitals=[hospital("H1", "C", slots=6)])
    w.start()
    w.apply([SendToScene("ALS1", "I01"), SendToScene("BLS1", "I01")])
    assert rejected(w, SetDestination("BLS1", "H1", ("I01-V1",))) == "capability_mismatch"
    assert rejected(w, SetDestination("ALS1", "H1", ("I01-V1", "I01-V2"))) == "over_capacity"
    assert rejected(w, SetDestination("BLS1", "H1", ("I01-V2", "I01-V3", "I01-V4"))) == "over_capacity"
    assert rejected(w, SetDestination("BLS1", "H1", ("I01-V2", "I01-V3"))) is None
    assert rejected(w, SetDestination("ALS1", "H1", ("I01-V1",))) is None


def test_als_may_carry_two_non_criticals():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma"), ("minor", "general")])])
    w.start()
    w.apply([SendToScene("ALS1", "I01")])
    assert rejected(w, SetDestination("ALS1", "H1", ("I01-V1", "I01-V2"))) is None


def test_set_destination_rejections():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma"), ("serious", "trauma"), ("serious", "trauma")]),
                    incident("I02", "B", 0, [("serious", "trauma")])],
                   units=[UnitSpec("ALS1", "ALS", "A", "A"), UnitSpec("BLS1", "BLS", "A", "A"),
                          UnitSpec("BLS2", "BLS", "A", "A"), UnitSpec("FIRE1", "FIRE", "D", "D")])
    w.start()
    assert rejected(w, SetDestination("ALS1", "H1", ("I01-V1",))) == "unit_busy"   # not sent anywhere
    w.apply([SendToScene("ALS1", "I01"), SendToScene("BLS1", "I01"), SendToScene("BLS2", "I01")])
    assert rejected(w, SetDestination("ALS9", "H1", ("I01-V1",))) == "unknown_unit"
    assert rejected(w, SetDestination("ALS1", "H9", ("I01-V1",))) == "unknown_hospital"
    assert rejected(w, SetDestination("FIRE1", "H1", ("I01-V1",))) == "capability_mismatch"
    assert rejected(w, SetDestination("ALS1", "H1", ())) == "no_victims"
    assert rejected(w, SetDestination("ALS1", "H1", ("I01-V9",))) == "unknown_victim"
    assert rejected(w, SetDestination("ALS1", "H1", ("I02-V1",))) == "wrong_incident"
    assert rejected(w, SetDestination("ALS1", "H2", ("I01-V1",))) == "capability_mismatch"   # H2 general only
    assert rejected(w, SetDestination("ALS1", "H1", ("I01-V1", "I01-V1"))) == "victim_unavailable"
    assert rejected(w, SetDestination("ALS1", "H1", ("I01-V1",))) is None
    assert rejected(w, SetDestination("ALS1", "H1", ("I01-V2",))) == "destination_already_set"
    assert rejected(w, SetDestination("BLS1", "H1", ("I01-V1",))) == "victim_unavailable"
    assert rejected(w, SetDestination("BLS1", "H1", ("I01-V2",))) is None
    assert rejected(w, SetDestination("BLS2", "H1", ("I01-V3",))) == "er_full"            # 2 slots, both reserved


def test_firm_reservation_holds_a_slot():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])])
    w.start()
    w.apply([SendToScene("ALS1", "I01"), SetDestination("ALS1", "H1", ("I01-V1",))])
    h1 = w.snapshot().data["hospitals"][0]
    assert h1["reserved"] == 1 and h1["free_slots"] == 1
    run_until(w, 8)
    assert w.reservations["R001"].status == "used"
    h1 = w.snapshot().data["hospitals"][0]
    assert h1["reserved"] == 0 and h1["er_used"] == 1


# ----- walk-ins -----

def test_walk_in_takes_free_slot_without_breaking():
    w = make_world([], hospitals=[hospital("H1", "C")], walk_ins=[walk_in("W01", "H1", 3)])
    evs = run_until(w, 3)
    assert of_type(evs, "walk_in")[0]["over_capacity"] is False
    assert not of_type(evs, "reservation_broken")


def test_walk_in_breaks_reservation_and_unit_learns_on_arrival():
    w = make_world([incident("I01", "B", 0, [("serious", "general")])],
                   hospitals=[hospital("H1", "C", slots=1), hospital("H2", "E", caps=("general",))],
                   walk_ins=[walk_in("W01", "H1", 4)])
    evs = run_until(w, 8, {0: [SendToScene("ALS1", "I01")], 2: [SetDestination("ALS1", "H1", ("I01-V1",))]})
    broken = of_type(evs, "reservation_broken")[0]
    assert broken["t"] == 4 and broken["unit"] == "ALS1" and broken["walk_in"] == "W01"
    assert of_type(evs, "arrived_no_slot")[0]["t"] == 8
    assert not of_type(evs, "patient_delivered")
    assert w.units["ALS1"].state == "at_hospital_no_slot"
    evs = run_until(w, 9, {8: [SetDestination("ALS1", "H2", ("I01-V1",))]})
    assert of_type(evs, "transport_started")[-1]["t"] == 8
    assert of_type(evs, "patient_delivered")[0]["hospital"] == "H2"
    assert of_type(evs, "patient_delivered")[0]["t"] == 9            # C -> E 1 min


def test_walk_in_breaks_reservation_expected_latest():
    w = make_world([incident("I01", "B", 0, [("serious", "general"), ("serious", "general")])],
                   hospitals=[hospital("H1", "C", slots=2)],
                   walk_ins=[walk_in("W01", "H1", 7)])
    run_until(w, 7, {0: [SendToScene("ALS1", "I01")],
                     3: [SendToScene("BLS1", "I01"), SetDestination("BLS1", "H1", ("I01-V2",))],
                     4: [SetDestination("ALS1", "H1", ("I01-V1",))]})
    assert w.reservations["R001"].unit == "BLS1" and w.reservations["R002"].unit == "ALS1"
    assert w.reservations["R002"].status == "active"   # ALS1 left at 7, expected at 8
    assert w.reservations["R001"].status == "broken"   # BLS1 has not left yet, though reserved first


def test_walk_in_over_capacity_when_er_full_of_patients():
    w = make_world([], hospitals=[hospital("H1", "C", slots=1)],
                   walk_ins=[walk_in("W01", "H1", 1), walk_in("W02", "H1", 2)])
    evs = run_until(w, 2)
    assert [e["over_capacity"] for e in of_type(evs, "walk_in")] == [False, True]
    assert w.snapshot().data["hospitals"][0]["er_used"] == 2


# ----- patient flow -----

def test_critical_becomes_transferable_then_ready_then_ward():
    w = make_world([], hospitals=[hospital("H1", "C")], walk_ins=[walk_in("W01", "H1", 0, "critical", "trauma")])
    evs = run_until(w, 121)
    st = of_type(evs, "stabilized")[0]
    assert st["t"] == 20 and st["transfer_deadline"] == 140
    assert w.victims["W01"].severity == "serious"
    assert of_type(evs, "ready_for_release")[0]["t"] == 90
    rel = of_type(evs, "patient_released")[0]
    assert rel["t"] == 120 and rel["outcome"] == "ward"
    assert w.hospitals["H1"].er == [] and w.hospitals["H1"].ward == ["W01"]


def test_stabilizing_keeps_original_deadline():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma")])])
    run_until(w, 40, {0: [SendToScene("ALS1", "I01")], 2: [SetDestination("ALS1", "H1", ("I01-V1",))]})
    v = w.victims["I01-V1"]
    assert v.status == "transferable" and v.deadline == 60 and v.transfer_deadline == 28 + 120


def test_minor_is_discharged_and_frees_slot():
    w = make_world([], hospitals=[hospital("H1", "C")], walk_ins=[walk_in("W01", "H1", 0, "minor", "general")])
    evs = run_until(w, 60)
    rel = of_type(evs, "patient_released")[0]
    assert rel["t"] == 60 and rel["outcome"] == "discharged"
    assert w.hospitals["H1"].er == []


def test_boarding_when_ward_full():
    w = make_world([], hospitals=[hospital("H1", "C", beds=0)], walk_ins=[walk_in("W01", "H1", 0, "serious")])
    evs = run_until(w, 90)
    assert of_type(evs, "patient_released")[0]["outcome"] == "boarding"
    assert w.hospitals["H1"].er == ["W01"]


def test_patients_wait_for_staff():
    w = make_world([], hospitals=[hospital("H1", "C", slots=4, doctors=("trauma",), doctor_max=2)],
                   walk_ins=[walk_in(f"W0{k}", "H1", 0, "minor", "general") for k in (1, 2, 3)])
    run_until(w, 1)
    statuses = {vid: w.victims[vid].status for vid in ("W01", "W02", "W03")}
    assert statuses == {"W01": "in_treatment", "W02": "in_treatment", "W03": "in_er"}
    assert w.snapshot().data["hospitals"][0]["waiting_for_staff"] == 1
    run_until(w, 30)
    assert w.victims["W03"].treatment_started_at == 30


def test_doctor_needs_matching_skill():
    w = make_world([], hospitals=[hospital("H1", "C", doctors=("trauma",))],
                   walk_ins=[walk_in("W01", "H1", 0, "serious", "burns")])
    run_until(w, 5)
    assert w.victims["W01"].status == "in_er"


def test_deadline_missed_once():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma")])])
    evs = run_until(w, 70)
    missed = of_type(evs, "deadline_missed")
    assert len(missed) == 1 and missed[0]["t"] == 60 and missed[0]["victim"] == "I01-V1"


def test_same_inputs_same_events():
    def run():
        w = make_world([incident("I01", "B", 0, [("serious", "trauma"), ("minor", "general")])],
                       walk_ins=[walk_in("W01", "H1", 3)])
        return run_until(w, 30, {0: [SendToScene("ALS1", "I01"), SetDestination("ALS1", "H1", ("I01-V1", "I01-V2"))]})
    assert run() == run()


def test_bls_on_scene_with_only_criticals_left_returns():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma")])])
    evs = run_until(w, 3, {0: [SendToScene("BLS1", "I01")]})
    assert [e["unit"] for e in of_type(evs, "unit_returning")] == ["BLS1"]
