"""Patient flow inside one hospital, one minute at a time (DESIGN 3.5 to 3.7, 3.11)."""
from __future__ import annotations

from typing import Callable

from sim.config import SimConfig
from sim.models import Hospital, Reservation, Victim
from sim.rules import SEVERITY_RANK, free_slots, pick_staff, treatment_minutes

Emit = Callable[..., dict]
TREATING = ("in_treatment", "transferable")


def admit(h: Hospital, v: Victim, t: int) -> None:
    h.er.append(v.id)
    v.hospital = h.id
    v.status = "in_er"
    if v.delivered_at is None:
        v.delivered_at = t


def active_at(h: Hospital, reservations: dict[str, Reservation], kind: str | None = None) -> list[Reservation]:
    return [r for r in reservations.values()
            if r.hospital == h.id and r.status == "active" and (kind is None or r.kind == kind)]


def reserved_count(h: Hospital, reservations: dict[str, Reservation]) -> int:
    """Slots promised but not yet occupied: firm reservations and holds."""
    return len(active_at(h, reservations))


def reservation_to_break(h: Hospital, reservations: dict[str, Reservation],
                         expected: Callable[[Reservation], int]) -> Reservation | None:
    """A hold first (latest created), else the firm reservation expected latest (DESIGN 3.11)."""
    holds = active_at(h, reservations, "hold")
    if holds:
        return max(holds, key=lambda r: (r.created_at, r.id))
    firm = active_at(h, reservations, "firm")
    return max(firm, key=lambda r: (expected(r), r.created_at, r.id), default=None)


def walk_in(h: Hospital, v: Victim, t: int, reservations: dict[str, Reservation],
            expected: Callable[[Reservation], int], emit: Emit) -> None:
    over = False
    if free_slots(h, reserved_count(h, reservations)) == 0:
        broken = reservation_to_break(h, reservations, expected)
        if broken is None:
            over = True
        else:
            broken.status, broken.ended_at = "broken", t
            extra = {"kind": "hold"} if broken.kind == "hold" else {}
            emit("reservation_broken", reservation=broken.id, hospital=h.id, unit=broken.unit,
                 victim=broken.victim, walk_in=v.id, **extra)
    admit(h, v, t)
    emit("walk_in", victim=v.id, hospital=h.id, severity=v.severity, care_need=v.care_need,
         over_capacity=over)


def free_staff_of(h: Hospital, vid: str) -> None:
    for staff in (*h.doctors, *h.nurses):
        if vid in staff.patients:
            staff.patients.remove(vid)


def finish_treatments(h: Hospital, victims: dict[str, Victim], t: int, emit: Emit) -> None:
    for vid in list(h.er):
        v = victims[vid]
        if v.status in TREATING and v.treatment_started_at + v.treatment_minutes == t:
            free_staff_of(h, vid)
            v.status = "ready_for_release"
            v.ready_at = t
            emit("ready_for_release", victim=vid, hospital=h.id)


def stabilize(h: Hospital, victims: dict[str, Victim], cfg: SimConfig, t: int, emit: Emit) -> None:
    for vid in h.er:
        v = victims[vid]
        if (v.status == "in_treatment" and v.severity == "critical"
                and v.treatment_started_at + cfg.stabilize_after == t):
            v.status = "transferable"
            v.severity = "serious"
            v.transferable_at = t
            v.transfer_deadline = t + cfg.transfer_deadline
            emit("stabilized", victim=vid, hospital=h.id, transfer_deadline=v.transfer_deadline)


def release_now(h: Hospital, v: Victim, t: int, emit: Emit, early: bool = False) -> None:
    """Minor: discharged. Others: ward bed if free, else boarding in the ER."""
    v.released_at = t
    if v.severity == "minor":
        h.er.remove(v.id)
        v.status = "discharged"
    elif len(h.ward) < h.ward_beds:
        h.er.remove(v.id)
        h.ward.append(v.id)
        v.status = "ward"
    else:
        v.status = "boarding"
    extra = {"early": True} if early else {}
    emit("patient_released", victim=v.id, hospital=h.id, outcome=v.status, **extra)


def release(h: Hospital, victims: dict[str, Victim], cfg: SimConfig, t: int, emit: Emit) -> None:
    for vid in list(h.er):
        v = victims[vid]
        if v.status == "ready_for_release" and v.ready_at + cfg.release_delay == t:
            release_now(h, v, t, emit)


def move_boarders(h: Hospital, victims: dict[str, Victim], t: int, emit: Emit) -> None:
    boarders = sorted((victims[vid] for vid in h.er if victims[vid].status == "boarding"),
                      key=lambda v: (v.released_at, v.id))
    for v in boarders:
        if len(h.ward) >= h.ward_beds:
            return
        h.er.remove(v.id)
        h.ward.append(v.id)
        v.status = "ward"
        emit("moved_to_ward", victim=v.id, hospital=h.id)


def assign_staff(h: Hospital, victims: dict[str, Victim], cfg: SimConfig, t: int, emit: Emit) -> None:
    waiting = sorted((victims[vid] for vid in h.er if victims[vid].status == "in_er"),
                     key=lambda v: (SEVERITY_RANK[v.severity], v.delivered_at, v.id))
    for v in waiting:
        doctor = pick_staff(h.doctors, v.care_need)
        nurse = pick_staff(h.nurses)
        if doctor is None or nurse is None:
            continue
        doctor.patients.append(v.id)
        nurse.patients.append(v.id)
        v.doctor, v.nurse = doctor.id, nurse.id
        v.status = "transferable" if v.transferable_at is not None else "in_treatment"
        v.treatment_started_at = t
        v.treatment_minutes = treatment_minutes(v.original_severity, v.treatment_remaining, cfg.treatment)
        v.treatment_remaining = None
        emit("treatment_started", victim=v.id, hospital=h.id, doctor=doctor.id, nurse=nurse.id)


def run_minute(h: Hospital, victims: dict[str, Victim], cfg: SimConfig, t: int, emit: Emit) -> None:
    finish_treatments(h, victims, t, emit)
    stabilize(h, victims, cfg, t, emit)
    release(h, victims, cfg, t, emit)
    move_boarders(h, victims, t, emit)
    assign_staff(h, victims, cfg, t, emit)
