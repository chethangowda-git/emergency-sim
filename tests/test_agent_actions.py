from __future__ import annotations

from sim import metrics
from sim.actions import (LendStaff, LendVehicle, Redirect, ReleaseHold, ReleasePatient, Reroute, Reserve,
                         SendToScene, SetDestination, TransferPatient)
from tests.helpers import hospital, incident, make_world, of_type, run_until, walk_in

TRAUMA_H2 = ("trauma", "general")


def rejected(world, action) -> str | None:
    ev = world.apply([action])[0]
    return ev["reason"] if ev["type"] == "action_rejected" else None


def two_hospitals(slots1=2, slots2=2, doctors2=("trauma",)):
    return [hospital("H1", "C", slots=slots1), hospital("H2", "E", caps=TRAUMA_H2, slots=slots2, doctors=doctors2)]


# ----- reserve (firm) -----

def test_firm_reserve_holds_slot_and_set_destination_reuses_it():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])], hospitals=two_hospitals())
    w.start()
    assert rejected(w, Reserve("H1", ("I01-V1",), "firm", by="H1")) is None
    assert w.snapshot().data["hospitals"][0]["free_slots"] == 1
    w.apply([SendToScene("ALS1", "I01"), SetDestination("ALS1", "H1", ("I01-V1",))])
    assert len(w.reservations) == 1 and w.reservations["R001"].unit == "ALS1"
    assert w.snapshot().data["hospitals"][0]["free_slots"] == 1


def test_firm_reserve_rejections():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma"), ("serious", "trauma"), ("serious", "trauma")])],
                   hospitals=[hospital("H1", "C", slots=2), hospital("H2", "E", caps=("general",))])
    w.start()
    assert rejected(w, Reserve("H9", ("I01-V1",), "firm", by="H9")) == "unknown_hospital"
    assert rejected(w, Reserve("H1", ("I01-V1",), "firm", by="H2")) == "not_permitted"
    assert rejected(w, Reserve("H1", ("I01-V9",), "firm", by="H1")) == "unknown_victim"
    assert rejected(w, Reserve("H1", (), "firm", by="H1")) == "no_victims"
    assert rejected(w, Reserve("H1", ("I01-V1",), "maybe", by="H1")) == "invalid_kind"
    assert rejected(w, Reserve("H2", ("I01-V1",), "firm", by="H2")) == "capability_mismatch"
    assert rejected(w, Reserve("H1", ("I01-V1", "I01-V2"), "firm", by="arbiter")) is None
    assert rejected(w, Reserve("H1", ("I01-V1",), "firm", by="H1")) == "already_reserved"
    assert rejected(w, Reserve("H1", ("I01-V3",), "firm", by="H1")) == "er_full"


def test_destination_elsewhere_releases_earlier_firm_reservation():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])], hospitals=two_hospitals())
    w.start()
    w.apply([Reserve("H1", ("I01-V1",), "firm", by="H1"), SendToScene("ALS1", "I01")])
    w.apply([SetDestination("ALS1", "H2", ("I01-V1",))])
    released = of_type(w.events, "reservation_released")[0]
    assert released["reservation"] == "R001" and released["reason"] == "replaced"
    assert w.reservations["R001"].status == "released"


# ----- holds -----

def test_targeted_hold_expires_and_counts_as_wasted():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma")])])
    w.start()
    assert rejected(w, Reserve("H1", ("I01-V1",), "hold", by="H1")) is None
    assert w.snapshot().data["hospitals"][0]["free_slots"] == 1
    evs = run_until(w, 15)
    exp = of_type(evs, "hold_expired")[0]
    assert exp["t"] == 15 and exp["minutes"] == 15
    assert metrics.wasted_hold_minutes(evs) == 15


def test_hold_converts_when_its_critical_is_reserved_here():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma")])], hospitals=[hospital("H1", "C", slots=1)])
    w.start()
    w.apply([Reserve("H1", ("I01-V1",), "hold", by="H1")])
    assert rejected(w, Reserve("H1", ("I01-V1",), "firm", by="H1")) is None   # uses the held slot
    assert w.reservations["R001"].status == "used"
    run_until(w, 20)
    assert not of_type(w.events, "hold_expired")


def test_hold_released_when_critical_goes_elsewhere():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma")])], hospitals=two_hospitals())
    w.start()
    w.apply([Reserve("H1", ("I01-V1",), "hold", by="H1"), Reserve("H2", ("I01-V1",), "firm", by="H2")])
    rel = of_type(w.events, "hold_released")[0]
    assert rel["reservation"] == "R001" and rel["reason"] == "went_elsewhere"


def test_hold_rejections():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma"), ("serious", "trauma"), ("critical", "trauma")])],
                   hospitals=[hospital("H1", "C", slots=2)])
    w.start()
    assert rejected(w, Reserve("H1", ("I01-V2",), "hold", by="H1")) == "invalid_hold"           # serious
    assert rejected(w, Reserve("H1", ("I01-V1", "I01-V3"), "hold", by="H1")) == "invalid_hold"
    assert rejected(w, Reserve("H1", (), "hold", by="H1")) is None                               # general
    assert rejected(w, Reserve("H1", (), "hold", by="H1")) == "hold_limit"
    assert rejected(w, Reserve("H1", ("I01-V1",), "hold", by="H1")) is None
    assert rejected(w, Reserve("H1", ("I01-V1",), "hold", by="H1")) == "already_reserved"
    assert rejected(w, Reserve("H1", ("I01-V3",), "hold", by="H1")) == "er_full"


def test_walk_in_takes_a_hold_before_a_firm_reservation():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma"), ("critical", "trauma")])],
                   hospitals=[hospital("H1", "C", slots=2)], walk_ins=[walk_in("W01", "H1", 3)])
    w.start()
    w.apply([Reserve("H1", ("I01-V1",), "firm", by="H1"), Reserve("H1", ("I01-V2",), "hold", by="H1")])
    evs = run_until(w, 3)
    broken = of_type(evs, "reservation_broken")[0]
    assert broken["reservation"] == "R002" and broken["kind"] == "hold"
    assert w.reservations["R001"].status == "active"


def test_release_hold():
    w = make_world([incident("I01", "B", 0, [("critical", "trauma")])])
    w.start()
    w.apply([Reserve("H1", ("I01-V1",), "hold", by="H1")])
    assert rejected(w, ReleaseHold("H1", "R009", by="H1")) == "unknown_hold"
    assert rejected(w, ReleaseHold("H1", "R001", by="H2")) == "not_permitted"
    assert rejected(w, ReleaseHold("H1", "R001", by="arbiter")) is None
    assert w.reservations["R001"].status == "released"
    assert rejected(w, ReleaseHold("H1", "R001", by="H1")) == "unknown_hold"


# ----- early release -----

def test_early_release_frees_the_slot_now():
    w = make_world([], hospitals=[hospital("H1", "C")], walk_ins=[walk_in("W01", "H1", 0, "minor", "general")])
    run_until(w, 10)
    assert rejected(w, ReleasePatient("W01", by="H1")) == "not_ready"
    run_until(w, 31)                                  # treatment ends at 30
    assert rejected(w, ReleasePatient("W01", by="H2")) == "not_permitted"
    assert rejected(w, ReleasePatient("W09", by="H1")) == "unknown_victim"
    assert rejected(w, ReleasePatient("W01", by="H1")) is None
    rel = of_type(w.events, "patient_released")[0]
    assert rel["t"] == 31 and rel["early"] is True and rel["outcome"] == "discharged"
    assert w.hospitals["H1"].er == []


# ----- transfers -----

def transfer_world():
    return make_world([], hospitals=two_hospitals(), walk_ins=[walk_in("W01", "H1", 0, "critical", "trauma")])


def test_transfer_moves_patient_and_remaining_treatment_continues():
    w = transfer_world()
    run_until(w, 25)
    w.apply([Reserve("H2", ("W01",), "firm", by="H2"), TransferPatient("W01", "H2", "H1-TV1", by="H1")])
    evs = run_until(w, 40)
    assert of_type(evs, "transfer_prep")[0]["departs_at"] == 30
    started = of_type(evs, "transfer_started")[0]
    assert started["t"] == 30 and started["eta"] == 31
    assert of_type(evs, "transfer_completed")[0]["t"] == 31
    assert w.victims["W01"].hospital == "H2" and "W01" not in w.hospitals["H1"].er
    assert w.victims["W01"].treatment_minutes == 60      # 90 minus 30 already done at H1
    assert w.units["H1-TV1"].state == "idle"             # handover 5, then back C <- E
    run_until(w, 91)
    assert w.victims["W01"].status == "ready_for_release" and w.victims["W01"].ready_at == 91


def test_transfer_rejections():
    w = transfer_world()
    run_until(w, 10)
    assert rejected(w, TransferPatient("W01", "H2", "H1-TV1", by="H1")) == "not_transferable"   # not yet stabilized
    run_until(w, 25)
    assert rejected(w, TransferPatient("W09", "H2", "H1-TV1", by="H1")) == "unknown_victim"
    assert rejected(w, TransferPatient("W01", "H2", "ALS1", by="H1")) == "unknown_vehicle"
    assert rejected(w, TransferPatient("W01", "H9", "H1-TV1", by="H1")) == "unknown_hospital"
    assert rejected(w, TransferPatient("W01", "H2", "H1-TV1", by="H2")) == "not_permitted"
    assert rejected(w, TransferPatient("W01", "H2", "H2-TV1", by="H1")) == "wrong_hospital"
    assert rejected(w, TransferPatient("W01", "H1", "H1-TV1", by="H1")) == "same_hospital"
    assert rejected(w, TransferPatient("W01", "H2", "H1-TV1", by="H1")) == "no_reservation"
    w.apply([Reserve("H2", ("W01",), "firm", by="H2"), LendVehicle("H1-TV1", 200, by="H1")])
    assert rejected(w, TransferPatient("W01", "H2", "H1-TV1", by="H1")) == "vehicle_busy"


# ----- vehicle loans -----

def test_lent_vehicle_carries_like_bls_then_returns():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma"), ("critical", "trauma")])],
                   hospitals=[hospital("H1", "C", slots=4)])
    w.start()
    assert rejected(w, SendToScene("H1-TV1", "I01")) == "not_lent"
    assert rejected(w, LendVehicle("H1-TV1", 30, by="H2")) == "not_permitted"
    assert rejected(w, LendVehicle("H1-TV1", 0, by="H1")) == "invalid_time"
    assert rejected(w, LendVehicle("H1-TV1", 30, by="H1")) is None
    assert rejected(w, LendVehicle("H1-TV1", 30, by="H1")) == "vehicle_busy"
    assert rejected(w, SendToScene("H1-TV1", "I01")) is None
    assert rejected(w, SetDestination("H1-TV1", "H1", ("I01-V2",))) == "capability_mismatch"   # a critical
    assert rejected(w, SetDestination("H1-TV1", "H1", ("I01-V1",))) is None
    evs = run_until(w, 40)
    assert [e["victim"] for e in of_type(evs, "patient_delivered")] == ["I01-V1"]
    assert of_type(evs, "vehicle_loan_ended")[0]["t"] == 30
    assert w.units["H1-TV1"].lent_until is None


# ----- staff loans -----

def test_staff_loan_travels_works_and_returns():
    w = make_world([], hospitals=[hospital("H1", "C", doctors=("trauma", "trauma")), hospital("H2", "E", doctors=())],
                   walk_ins=[walk_in("W01", "H2", 2, "minor", "general")])
    w.start()
    assert rejected(w, LendStaff("H1-D9", "H2", 50, by="H1")) == "unknown_staff"
    assert rejected(w, LendStaff("H1-D2", "H2", 50, by="H2")) == "not_permitted"
    assert rejected(w, LendStaff("H1-D2", "H1", 50, by="H1")) == "same_hospital"
    assert rejected(w, LendStaff("H1-D2", "H2", 50, by="H1")) is None
    assert rejected(w, LendStaff("H1-D2", "H2", 50, by="H1")) == "staff_busy"
    evs = run_until(w, 60)
    assert of_type(evs, "staff_arrived")[0]["t"] == 1                    # C -> E 1 min
    assert w.victims["W01"].doctor == "H1-D2"                           # H2 had no doctor
    assert of_type(evs, "staff_returning")[0]["t"] == 50
    assert of_type(evs, "staff_returned")[0]["t"] == 51
    assert [d.id for d in w.hospitals["H1"].doctors] == ["H1-D1", "H1-D2"]


def test_staff_with_patients_cannot_be_lent():
    w = make_world([], hospitals=[hospital("H1", "C"), hospital("H2", "E")],
                   walk_ins=[walk_in("W01", "H1", 0, "minor", "general")])
    run_until(w, 1)
    assert rejected(w, LendStaff("H1-D1", "H2", 50, by="H1")) == "staff_busy"


# ----- redirect -----

def test_redirect_als_to_new_critical_once_per_trip():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")]), incident("I02", "E", 1, [("critical", "trauma")]),
                    incident("I03", "C", 1, [("minor", "general")])])
    w.start()
    w.apply([SendToScene("ALS1", "I01"), SendToScene("BLS1", "I01")])
    w.step()
    assert rejected(w, Redirect("BLS1", "I02", by="dispatch")) == "capability_mismatch"
    assert rejected(w, Redirect("ALS1", "I02", by="H1")) == "not_permitted"
    assert rejected(w, Redirect("ALS1", "I01", by="dispatch")) == "same_incident"
    assert rejected(w, Redirect("ALS1", "I03", by="dispatch")) == "no_critical"
    assert rejected(w, Redirect("ALS1", "I02", by="dispatch")) is None
    assert w.units["ALS1"].incident == "I02"
    assert w.trips[-1].route.points[0] != w.trips[0].route.points[0]     # continues from where it is
    assert rejected(w, Redirect("ALS1", "I01", by="dispatch")) == "redirect_limit"


def test_cannot_redirect_after_loading():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")]), incident("I02", "E", 0, [("critical", "trauma")])])
    w.start()
    w.apply([SendToScene("ALS1", "I01"), SetDestination("ALS1", "H1", ("I01-V1",))])
    assert rejected(w, Redirect("ALS1", "I02", by="dispatch")) == "unit_busy"


# ----- reroute -----

def reroute_world(deadline=None):
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])],
                   hospitals=two_hospitals() + [hospital("H3", "A", caps=TRAUMA_H2)])
    if deadline is not None:
        w.victims["I01-V1"].deadline = deadline
    w.start()
    w.apply([SendToScene("ALS1", "I01"), SetDestination("ALS1", "H1", ("I01-V1",))])
    run_until(w, 7)                                    # transporting B -> C from minute 7
    return w


def test_reroute_moves_reservation_and_arrives_at_new_hospital():
    w = reroute_world()
    assert rejected(w, Reroute("ALS1", "H2", by="dispatch")) == "no_reservation"
    w.apply([Reserve("H2", ("I01-V1",), "firm", by="H2")])
    assert rejected(w, Reroute("ALS1", "H2", by="H1")) == "not_permitted"
    assert rejected(w, Reroute("ALS1", "H1", by="dispatch")) == "same_hospital"
    assert rejected(w, Reroute("ALS1", "H2", by="dispatch")) is None
    assert w.reservations["R001"].status == "released"
    evs = run_until(w, 12)
    assert of_type(evs, "patient_delivered")[0]["hospital"] == "H2"


def test_reroute_limit_unless_arbiter():
    w = reroute_world()
    w.apply([Reserve("H2", ("I01-V1",), "firm", by="H2"), Reserve("H3", ("I01-V1",), "firm", by="H3"),
             Reroute("ALS1", "H2", by="dispatch")])
    assert rejected(w, Reroute("ALS1", "H3", by="dispatch")) == "reroute_limit"
    assert rejected(w, Reroute("ALS1", "H3", by="arbiter")) is None


def test_reroute_rejected_if_deadline_would_be_missed():
    w = reroute_world(deadline=8)                      # H1 at 8 is on time, H2 is 2 minutes away
    w.apply([Reserve("H2", ("I01-V1",), "firm", by="H2")])
    assert rejected(w, Reroute("ALS1", "H2", by="dispatch")) == "deadline_miss"


def test_reroute_needs_a_transporting_unit():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])])
    w.start()
    assert rejected(w, Reroute("ALS1", "H2", by="dispatch")) == "unit_busy"


def test_lent_vehicle_finishes_its_job_after_the_loan_end_time():
    w = make_world([incident("I01", "D", 0, [("serious", "trauma")])], hospitals=[hospital("H1", "C", slots=4)])
    w.start()
    w.apply([LendVehicle("H1-TV1", 3, by="H1"), SendToScene("H1-TV1", "I01")])
    run_until(w, 8)                                     # on scene at D after the loan's end time
    assert rejected(w, SetDestination("H1-TV1", "H1", ("I01-V1",))) is None
    assert rejected(w, SendToScene("ALS1", "I01")) == "incident_resolved"
    evs = run_until(w, 40)
    assert of_type(evs, "patient_delivered")[0]["unit"] == "H1-TV1"
    assert of_type(evs, "vehicle_loan_ended")
