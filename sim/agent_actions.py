"""Validation and effects of the agent-only actions (DESIGN 3.8, 3.9, 3.11).

Mixed into World so they share its state; the greedy baseline never sends them, but the
rules they enforce are the same world rules for everyone.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sim import hospital_flow
from sim.actions import (LendStaff, LendVehicle, Redirect, ReleaseHold, ReleasePatient, Reroute, Reserve,
                         TransferPatient)
from sim.models import Reservation, Staff
from sim.rules import free_slots

if TYPE_CHECKING:
    from sim.world import World

LIVE_VICTIM = ("waiting", "assigned", "in_transit", "at_door", "transferable")
NOT_DELIVERED = ("waiting", "assigned", "in_transit", "at_door")
ARBITER = "arbiter"
DISPATCH = "dispatch"


class AgentActions:
    # ----- reserve -----

    def _check_reserve(self: World, a: Reserve) -> str | None:
        h = self.hospitals.get(a.hospital)
        if h is None:
            return "unknown_hospital"
        if a.by not in (a.hospital, ARBITER):
            return "not_permitted"
        if any(v not in self.victims for v in a.victims):
            return "unknown_victim"
        if len(set(a.victims)) != len(a.victims):
            return "victim_unavailable"
        if a.kind == "hold":
            return self._check_hold(a)
        if a.kind != "firm":
            return "invalid_kind"
        if not a.victims:
            return "no_victims"
        for vid in a.victims:
            v = self.victims[vid]
            if v.status not in LIVE_VICTIM or (v.status == "transferable" and v.hospital == h.id):
                return "victim_unavailable"
            if self._firm_at(h.id, vid):
                return "already_reserved"
            if v.care_need not in h.capabilities:
                return "capability_mismatch"
        needed = sum(1 for vid in a.victims if not self._hold_for(h.id, vid))
        if free_slots(h, hospital_flow.reserved_count(h, self.reservations)) < needed:
            return "er_full"
        return None

    def _check_hold(self: World, a: Reserve) -> str | None:
        h = self.hospitals[a.hospital]
        if len(a.victims) > 1:
            return "invalid_hold"
        if a.victims:
            v = self.victims[a.victims[0]]
            if v.severity != "critical" or v.status not in NOT_DELIVERED:
                return "invalid_hold"
            if self._hold_for(h.id, v.id) or self._firm_at(h.id, v.id):
                return "already_reserved"
        elif any(r.victim is None for r in hospital_flow.active_at(h, self.reservations, "hold")):
            return "hold_limit"
        if free_slots(h, hospital_flow.reserved_count(h, self.reservations)) < 1:
            return "er_full"
        return None

    def _do_reserve(self: World, a: Reserve) -> None:
        if a.kind == "hold":
            vid = a.victims[0] if a.victims else None
            res = Reservation(self._next_res_id(), a.hospital, None, vid, self.t, kind="hold",
                              expires_at=self.t + self.cfg.hold_minutes)
            self.reservations[res.id] = res
            self.emit("hold_placed", reservation=res.id, hospital=a.hospital, victim=vid, expires_at=res.expires_at)
            return
        for vid in a.victims:
            res = self._place_firm(a.hospital, vid, self.victims[vid].unit)
            self.emit("reservation_placed", reservation=res.id, hospital=a.hospital, victim=vid, by=a.by)

    # ----- holds and early release -----

    def _check_release_hold(self: World, a: ReleaseHold) -> str | None:
        r = self.reservations.get(a.hold)
        if r is None or r.kind != "hold" or r.hospital != a.hospital or r.status != "active":
            return "unknown_hold"
        if a.by not in (a.hospital, ARBITER):
            return "not_permitted"
        return None

    def _do_release_hold(self: World, a: ReleaseHold) -> None:
        self._end_reservation(self.reservations[a.hold], "released", "hold_released", reason="released")

    def _check_release_patient(self: World, a: ReleasePatient) -> str | None:
        v = self.victims.get(a.victim)
        if v is None:
            return "unknown_victim"
        if a.by not in (v.hospital, ARBITER):
            return "not_permitted"
        if v.status != "ready_for_release":
            return "not_ready"
        return None

    def _do_release_patient(self: World, a: ReleasePatient) -> None:
        v = self.victims[a.victim]
        hospital_flow.release_now(self.hospitals[v.hospital], v, self.t, self.emit, early=True)

    # ----- transfers and vehicle loans -----

    def _check_transfer(self: World, a: TransferPatient) -> str | None:
        v, u, to = self.victims.get(a.victim), self.units.get(a.vehicle), self.hospitals.get(a.to_hospital)
        if v is None:
            return "unknown_victim"
        if u is None or not u.is_vehicle:
            return "unknown_vehicle"
        if to is None:
            return "unknown_hospital"
        if a.by not in (v.hospital, ARBITER):
            return "not_permitted"
        if v.status != "transferable" or self._vehicle_for(v.id):
            return "not_transferable"
        if u.owner != v.hospital:
            return "wrong_hospital"
        if to.id == v.hospital:
            return "same_hospital"
        if not self._firm_at(to.id, v.id):
            return "no_reservation"
        if u.state != "idle" or u.lent_until is not None:
            return "vehicle_busy"
        return None

    def _do_transfer(self: World, a: TransferPatient) -> None:
        u = self.units[a.vehicle]
        u.state, u.prep_until = "transfer_prep", self.t + self.cfg.transfer_prep_minutes
        u.hospital, u.victims = a.to_hospital, [a.victim]
        res = self._firm_at(a.to_hospital, a.victim)
        res.unit = u.id
        self.emit("transfer_prep", victim=a.victim, vehicle=u.id, from_hospital=u.owner,
                  to_hospital=a.to_hospital, departs_at=u.prep_until)

    def _transfer_departures(self: World) -> None:
        for uid in sorted(self.units):
            u = self.units[uid]
            if u.state == "transfer_prep" and u.prep_until == self.t:
                self._start_transfer(u)

    def _start_transfer(self: World, u) -> None:
        v = self.victims[u.victims[0]]
        sender = self.hospitals[u.owner]
        u.prep_until = None
        res = self._firm_at(u.hospital, v.id)
        if v.status != "transferable" or res is None:
            if res is not None:
                self._end_reservation(res, "released", "reservation_released", reason="transfer_cancelled")
            u.state, u.hospital, u.victims = "idle", None, []
            self.emit("transfer_cancelled", victim=v.id, vehicle=u.id)
            return
        v.treatment_remaining = max(1, v.treatment_minutes - (self.t - v.treatment_started_at))
        hospital_flow.free_staff_of(sender, v.id)
        sender.er.remove(v.id)
        v.status, v.unit, v.reservation = "in_transit", u.id, res.id
        u.state = "transporting"
        trip = self._start_trip(u, "transfer", self.hospitals[u.hospital].node)
        self.emit("transfer_started", victim=v.id, vehicle=u.id, from_hospital=sender.id,
                  to_hospital=u.hospital, trip=trip.id, eta=trip.arrive_t)

    def _vehicle_for(self: World, vid: str) -> str | None:
        return next((u.id for u in self.units.values() if u.is_vehicle and vid in u.victims), None)

    def _check_lend_vehicle(self: World, a: LendVehicle) -> str | None:
        u = self.units.get(a.vehicle)
        if u is None or not u.is_vehicle:
            return "unknown_vehicle"
        if a.by != u.owner:
            return "not_permitted"
        if u.state != "idle" or u.lent_until is not None:
            return "vehicle_busy"
        if a.until <= self.t:
            return "invalid_time"
        return None

    def _do_lend_vehicle(self: World, a: LendVehicle) -> None:
        self.units[a.vehicle].lent_until = a.until
        self.emit("vehicle_lent", vehicle=a.vehicle, hospital=self.units[a.vehicle].owner, until=a.until)

    def _lending_ends(self: World) -> None:
        for uid in sorted(self.units):
            u = self.units[uid]
            if u.lent_until is not None and self.t >= u.lent_until and u.state in ("idle", "returning"):
                u.lent_until = None
                self.emit("vehicle_loan_ended", vehicle=u.id, hospital=u.owner)

    # ----- staff loans -----

    def _find_staff(self: World, sid: str) -> Staff | None:
        """On duty at any hospital, or on the road between two."""
        for hid in sorted(self.hospitals):
            h = self.hospitals[hid]
            for s in (*h.doctors, *h.nurses):
                if s.id == sid:
                    return s
        return next((s for _, _, s in self.staff_moving if s.id == sid), None)

    def _check_lend_staff(self: World, a: LendStaff) -> str | None:
        s = self._find_staff(a.staff)
        if s is None:
            return "unknown_staff"
        if a.to_hospital not in self.hospitals:
            return "unknown_hospital"
        if a.by not in (s.home, ARBITER):
            return "not_permitted"
        if s.hospital != s.home or s.patients or s.lent_until is not None:
            return "staff_busy"
        if a.to_hospital == s.home:
            return "same_hospital"
        if a.until <= self.t:
            return "invalid_time"
        return None

    def _do_lend_staff(self: World, a: LendStaff) -> None:
        s = self._find_staff(a.staff)
        s.lent_until = a.until
        arrive = self._move_staff(s, a.to_hospital)
        self.emit("staff_lent", staff=s.id, from_hospital=s.home, to_hospital=a.to_hospital,
                  arrives_at=arrive, until=a.until)

    def _move_staff(self: World, s: Staff, dest: str) -> int:
        here = self.hospitals[s.hospital]
        (here.doctors if s.kind == "doctor" else here.nurses).remove(s)
        arrive = self.t + self.roads.minutes(here.node, self.hospitals[dest].node)
        self.staff_moving.append((arrive, dest, s))
        s.hospital = ""
        return arrive

    def _staff_moves(self: World) -> None:
        for arrive, dest, s in sorted(self.staff_moving, key=lambda m: (m[0], m[2].id)):
            if arrive != self.t:
                continue
            self.staff_moving.remove((arrive, dest, s))
            h = self.hospitals[dest]
            (h.doctors if s.kind == "doctor" else h.nurses).append(s)
            s.hospital = dest
            if dest == s.home:
                s.lent_until = None
                self.emit("staff_returned", staff=s.id, hospital=dest)
            else:
                self.emit("staff_arrived", staff=s.id, hospital=dest)
        for hid in sorted(self.hospitals):
            for s in sorted((*self.hospitals[hid].doctors, *self.hospitals[hid].nurses), key=lambda x: x.id):
                if s.hospital != s.home and s.lent_until is not None and self.t >= s.lent_until and not s.patients:
                    arrive = self._move_staff(s, s.home)
                    self.emit("staff_returning", staff=s.id, from_hospital=hid, arrives_at=arrive)

    # ----- redirect and reroute -----

    def _check_redirect(self: World, a: Redirect) -> str | None:
        u = self.units.get(a.unit)
        if u is None:
            return "unknown_unit"
        if a.by not in (DISPATCH, ARBITER):
            return "not_permitted"
        if u.kind != "ALS":
            return "capability_mismatch"
        if u.state not in ("to_scene", "on_scene") or u.hospital or u.victims:
            return "unit_busy"
        if u.redirects >= 1:
            return "redirect_limit"
        if a.incident not in self.reported:
            return "unknown_incident"
        if a.incident == u.incident:
            return "same_incident"
        if not any(v.severity == "critical" for v in self.waiting_victims(a.incident)):
            return "no_critical"
        return None

    def _do_redirect(self: World, a: Redirect) -> None:
        u = self.units[a.unit]
        old = u.incident
        u.incident, u.state, u.on_scene_since = a.incident, "to_scene", None
        u.redirects += 1
        trip = self._start_trip(u, "to_scene", self.incidents[a.incident].node)
        self.emit("unit_redirected", unit=u.id, from_incident=old, incident=a.incident, eta=trip.arrive_t)

    def _check_reroute(self: World, a: Reroute) -> str | None:
        u, h = self.units.get(a.unit), self.hospitals.get(a.hospital)
        if u is None:
            return "unknown_unit"
        if h is None:
            return "unknown_hospital"
        if a.by not in (DISPATCH, ARBITER):
            return "not_permitted"
        if u.state != "transporting" or not self._moving(u):
            return "unit_busy"
        if a.hospital == u.hospital:
            return "same_hospital"
        if u.reroutes >= 1 and a.by != ARBITER:
            return "reroute_limit"
        if any(not self._firm_at(h.id, vid) for vid in u.victims):
            return "no_reservation"
        arrive = self.t + self.roads.minutes_from(u.trip.route, self.t - u.trip.depart_t, h.node)
        for vid in u.victims:
            d = self.victims[vid].deadline
            if d is not None and d >= self.t and arrive > d:
                return "deadline_miss"
        return None

    def _do_reroute(self: World, a: Reroute) -> None:
        u = self.units[a.unit]
        old = u.hospital
        for vid in u.victims:
            v = self.victims[vid]
            prev = self.reservations.get(v.reservation)
            if prev is not None and prev.status == "active":
                self._end_reservation(prev, "released", "reservation_released", reason="reroute")
            new = self._firm_at(a.hospital, vid)
            new.unit, v.reservation = u.id, new.id
        u.hospital = a.hospital
        u.reroutes += 1
        trip = self._start_trip(u, u.trip.purpose, self.hospitals[a.hospital].node)
        self.emit("unit_rerouted", unit=u.id, from_hospital=old, hospital=a.hospital, by=a.by, eta=trip.arrive_t)

    # ----- reservation helpers -----

    def _next_res_id(self: World) -> str:
        return f"R{len(self.reservations) + 1:03d}"

    def _firm_at(self: World, hid: str, vid: str) -> Reservation | None:
        return next((r for r in self.reservations.values() if r.kind == "firm" and r.hospital == hid
                     and r.victim == vid and r.status == "active"), None)

    def _hold_for(self: World, hid: str, vid: str) -> Reservation | None:
        return next((r for r in self.reservations.values() if r.kind == "hold" and r.hospital == hid
                     and r.victim == vid and r.status == "active"), None)

    def _place_firm(self: World, hid: str, vid: str, unit: str | None) -> Reservation:
        """A held slot for this victim here converts; holds for it elsewhere are released."""
        hold = self._hold_for(hid, vid)
        if hold is not None:
            hold.status, hold.ended_at = "used", self.t
        for r in list(self.reservations.values()):
            if r.kind == "hold" and r.victim == vid and r.hospital != hid and r.status == "active":
                self._end_reservation(r, "released", "hold_released", reason="went_elsewhere")
        res = Reservation(self._next_res_id(), hid, unit, vid, self.t)
        self.reservations[res.id] = res
        return res

    def _end_reservation(self: World, r: Reservation, status: str, event: str, **fields) -> None:
        r.status, r.ended_at = status, self.t
        self.emit(event, reservation=r.id, hospital=r.hospital, victim=r.victim, **fields)

    def _expire_holds(self: World) -> None:
        for r in list(self.reservations.values()):
            if r.kind == "hold" and r.status == "active" and r.expires_at == self.t:
                self._end_reservation(r, "expired", "hold_expired", minutes=self.t - r.created_at)
