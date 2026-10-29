"""Small pure rule functions (DESIGN 3.2 to 3.6, 3.11)."""
from __future__ import annotations

from sim.models import Hospital, Staff

SEVERITY_RANK = {"critical": 0, "serious": 1, "minor": 2}

REQUIRES = {"building_fire": "fire", "bus_fire": "fire", "structural_collapse": "rescue"}
SPECIALTY = {"traffic_accident": "trauma", "structural_collapse": "trauma",
             "building_fire": "burns", "bus_fire": "burns", "medical_emergency": "general"}


def requires_for(incident_type: str) -> str | None:
    return REQUIRES.get(incident_type)


def care_need_for(incident_type: str, severity: str) -> str:
    if severity == "minor":
        return "general"
    return SPECIALTY[incident_type]


def carry_rejection(kind: str, severities: list[str]) -> str | None:
    """None if a unit of `kind` may carry these victims together, else a reason."""
    if kind not in ("ALS", "BLS"):
        return "capability_mismatch"
    criticals = severities.count("critical")
    if criticals and kind != "ALS":
        return "capability_mismatch"
    if criticals and len(severities) > 1:
        return "over_capacity"
    if len(severities) > 2:
        return "over_capacity"
    return None


def can_treat(hospital: Hospital, care_need: str) -> bool:
    return care_need in hospital.capabilities


def free_slots(hospital: Hospital, reserved: int) -> int:
    return max(0, hospital.er_slots - len(hospital.er) - reserved)


def doctor_can_treat(doctor: Staff, care_need: str) -> bool:
    return care_need in doctor.skills and doctor.free > 0


def free_doctor_slots(hospital: Hospital) -> int:
    return sum(max(0, d.free) for d in hospital.doctors)


def free_nurse_slots(hospital: Hospital) -> int:
    return sum(max(0, n.free) for n in hospital.nurses)


def real_free_capacity(hospital: Hospital, reserved: int) -> int:
    return min(free_slots(hospital, reserved), free_doctor_slots(hospital), free_nurse_slots(hospital))


def utilization(hospital: Hospital, reserved: int) -> float:
    return 1 - real_free_capacity(hospital, reserved) / hospital.er_slots


def pick_staff(staff: list[Staff], care_need: str | None = None) -> Staff | None:
    """The least-loaded staff member who can take one more patient (ties by id)."""
    able = [s for s in staff if s.free > 0 and (care_need is None or care_need in s.skills)]
    return min(able, key=lambda s: (len(s.patients), s.id), default=None)


def loading_ready_at(on_scene_since: int, cleared_at: int, on_scene_minutes: int) -> int:
    return max(on_scene_since, cleared_at) + on_scene_minutes


def victim_priority(severity: str, deadline: int | None, victim_id: str) -> tuple:
    return (SEVERITY_RANK[severity], deadline if deadline is not None else 10**9, victim_id)
