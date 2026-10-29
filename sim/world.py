"""The World: the only thing that changes simulation state (DESIGN 3, golden rule 1).

Per minute: the policy sees `snapshot()` and the events since its last call, returns
actions, `apply()` validates them, `record_minute()` stores the state, `step()` advances.
"""
from __future__ import annotations

import copy
from collections import defaultdict

from sim import hospital_flow
from sim.actions import Action, AssignTeam, SendToScene, SetDestination
from sim.config import SimConfig
from sim.models import TEAM_FOR_REQUIREMENT, TEAM_KINDS, Reservation, Scenario, Trip, Unit, Victim
from sim.roads import RoadNetwork
from sim.rules import carry_rejection, free_slots, loading_ready_at, victim_priority
from sim.views import WorldView, build_snapshot

FREE_STATES = ("idle", "returning")
NOT_DEPARTED = 10**9


class World:
    def __init__(self, scenario: Scenario, roads: RoadNetwork, cfg: SimConfig) -> None:
        sc = copy.deepcopy(scenario)
        self.cfg, self.roads, self.seed = cfg, roads, sc.seed
        self.t = 0
        self.incidents = {i.id: i for i in sc.incidents}
        self.victims = {v.id: v for v in sc.victims}
        self.hospitals = {h.id: h for h in sc.hospitals}
        self.units = {u.id: Unit(u.id, u.kind, u.base, u.base_node, u.base_node) for u in sc.units}
        self.reported: list[str] = []
        self.reservations: dict[str, Reservation] = {}
        self.trips: list[Trip] = []
        self.events: list[dict] = []
        self.timeline: list[dict] = []
        self._incidents_at = defaultdict(list)
        for inc in sc.incidents:
            self._incidents_at[inc.reported_at].append(inc.id)
        self._walk_ins_at = defaultdict(list)
        for w in sc.walk_ins:
            self._walk_ins_at[w.t].append(w)

    # ----- clock -----

    def start(self) -> list[dict]:
        mark = len(self.events)
        self._process_minute()
        return self.events[mark:]

    def step(self) -> list[dict]:
        mark = len(self.events)
        self.t += 1
        self._process_minute()
        return self.events[mark:]

    def _process_minute(self) -> None:
        self._report_incidents()
        self._arrivals()
        self._clearances()
        self._departures()
        self._handovers()
        self._walk_ins()
        for hid in sorted(self.hospitals):
            hospital_flow.run_minute(self.hospitals[hid], self.victims, self.cfg, self.t, self.emit)
        self._deadlines()
        self._idle_on_scene()

    def emit(self, type_: str, **fields) -> dict:
        ev = {"seq": len(self.events) + 1, "t": self.t, "type": type_, **fields}
        self.events.append(ev)
        return ev

    def snapshot(self) -> WorldView:
        return WorldView(build_snapshot(self), self.roads, self._unit_motion())

    def record_minute(self) -> None:
        self.timeline.append(build_snapshot(self))

    # ----- incidents and scenes -----

    def _report_incidents(self) -> None:
        for iid in self._incidents_at.get(self.t, []):
            inc = self.incidents[iid]
            self.reported.append(iid)
            if inc.requires is None:
                inc.cleared_at = self.t
            victims = [{"id": v, "severity": self.victims[v].severity, "care_need": self.victims[v].care_need,
                        "deadline": self.victims[v].deadline} for v in inc.victim_ids]
            self.emit("incident_reported", incident=iid, incident_type=inc.type, node=inc.node,
                      lat=inc.lat, lon=inc.lon, requires=inc.requires, description=inc.description,
                      surge=inc.surge, victims=victims)

    def _clearances(self) -> None:
        for iid in self.reported:
            inc = self.incidents[iid]
            if inc.requires and inc.cleared_at == self.t and inc.team_arrived_at is not None:
                self.emit("scene_cleared", incident=iid, team=inc.team)
                self._return_to_base(self.units[inc.team])

    def waiting_victims(self, iid: str) -> list[Victim]:
        vs = [self.victims[v] for v in self.incidents[iid].victim_ids if self.victims[v].status == "waiting"]
        return sorted(vs, key=lambda v: victim_priority(v.severity, v.deadline, v.id))

    def _deadlines(self) -> None:
        for iid in self.reported:
            for vid in self.incidents[iid].victim_ids:
                v = self.victims[vid]
                if v.deadline == self.t and v.delivered_at is None:
                    self.emit("deadline_missed", victim=vid, incident=iid, severity=v.original_severity)

    def _idle_on_scene(self) -> None:
        for uid in sorted(self.units):
            u = self.units[uid]
            if u.state == "on_scene" and not u.victims and not any(
                    carry_rejection(u.kind, [v.severity]) is None for v in self.waiting_victims(u.incident)):
                self._return_to_base(u)

    def _walk_ins(self) -> None:
        for w in self._walk_ins_at.get(self.t, []):
            v = Victim(w.id, None, w.severity, w.care_need, self.t, None, walk_in=True)
            self.victims[v.id] = v
            hospital_flow.walk_in(self.hospitals[w.hospital], v, self.t, self.reservations,
                                  self._expected_arrival, self.emit)

    def _expected_arrival(self, res: Reservation) -> int:
        u = self.units[res.unit]
        if u.state == "transporting" and u.trip is not None:
            return u.trip.arrive_t
        return NOT_DEPARTED

    # ----- unit movement -----

    def _moving(self, u: Unit) -> bool:
        return u.trip is not None and u.trip.ended_at is None and self.t < u.trip.arrive_t

    def _unit_motion(self) -> dict:
        return {uid: (u.node, u.trip.route if self._moving(u) else None,
                      self.t - u.trip.depart_t if self._moving(u) else 0.0)
                for uid, u in self.units.items()}

    def _start_trip(self, u: Unit, purpose: str, dest: str) -> Trip:
        if self._moving(u):
            route = self.roads.continue_route(u.trip.route, self.t - u.trip.depart_t, dest)
            u.trip.ended_at = self.t
        else:
            route = self.roads.route(u.node, dest)
        trip = Trip(f"T{len(self.trips) + 1:04d}", u.id, purpose, route, self.t, dest)
        self.trips.append(trip)
        u.trip = trip
        return trip

    def _return_to_base(self, u: Unit) -> None:
        u.incident, u.hospital, u.victims = None, None, []
        u.on_scene_since = u.handover_until = None
        if not self._moving(u) and u.node == u.base_node:
            u.state, u.trip = "idle", None
            self.emit("unit_at_base", unit=u.id)
            return
        u.state = "returning"
        trip = self._start_trip(u, "to_base", u.base_node)
        self.emit("unit_returning", unit=u.id, trip=trip.id, eta=trip.arrive_t)

    def _arrivals(self) -> None:
        for uid in sorted(self.units):
            u = self.units[uid]
            if u.trip is None or u.trip.ended_at is not None or u.trip.arrive_t != self.t:
                continue
            purpose, u.node, u.trip = u.trip.purpose, u.trip.dest_node, None
            if purpose == "to_scene":
                self._on_scene(u)
            elif purpose == "to_hospital":
                self._deliver(u)
            else:
                u.state = "idle"
                self.emit("unit_at_base", unit=u.id)

    def _on_scene(self, u: Unit) -> None:
        inc = self.incidents[u.incident]
        if u.kind in TEAM_KINDS:
            u.state = "clearing"
            inc.team_arrived_at = self.t
            inc.cleared_at = self.t + self.cfg.clearance_minutes
        else:
            u.state = "on_scene"
            u.on_scene_since = self.t
        self.emit("unit_on_scene", unit=u.id, incident=inc.id)

    def _ready_at(self, u: Unit) -> int | None:
        inc = self.incidents[u.incident]
        if u.state != "on_scene" or inc.cleared_at is None or inc.cleared_at > self.t:
            return None
        return loading_ready_at(u.on_scene_since, inc.cleared_at, self.cfg.on_scene_minutes)

    def _departures(self) -> None:
        for uid in sorted(self.units):
            u = self.units[uid]
            if u.hospital and u.victims and (ready := self._ready_at(u)) is not None and self.t >= ready:
                self._depart(u)

    def _depart(self, u: Unit) -> None:
        for vid in u.victims:
            v = self.victims[vid]
            v.status = "in_transit"
            if v.picked_up_at is None:
                v.picked_up_at = self.t
        u.state = "transporting"
        trip = self._start_trip(u, "to_hospital", self.hospitals[u.hospital].node)
        self.emit("transport_started", unit=u.id, hospital=u.hospital, victims=list(u.victims),
                  trip=trip.id, eta=trip.arrive_t)

    def _deliver(self, u: Unit) -> None:
        h = self.hospitals[u.hospital]
        left = []
        for vid in u.victims:
            v = self.victims[vid]
            res = self.reservations[v.reservation]
            if res.status != "active":
                left.append(vid)
                continue
            res.status = "used"
            hospital_flow.admit(h, v, self.t)
            self.emit("patient_delivered", victim=vid, hospital=h.id, unit=u.id,
                      on_time=v.deadline is not None and self.t <= v.deadline)
        u.victims = left
        if left:
            u.state = "at_hospital_no_slot"
            for vid in left:
                self.victims[vid].status = "at_door"
            self.emit("arrived_no_slot", unit=u.id, hospital=h.id, victims=left)
        else:
            u.state = "handover"
            u.handover_until = self.t + self.cfg.handover_minutes

    def _handovers(self) -> None:
        for uid in sorted(self.units):
            u = self.units[uid]
            if u.state == "handover" and u.handover_until == self.t:
                self._return_to_base(u)

    # ----- actions -----

    def apply(self, actions: list[Action]) -> list[dict]:
        mark = len(self.events)
        for action in actions:
            reason = self._validate(action)
            if reason:
                self.emit("action_rejected", reason=reason, **action.to_dict())
            else:
                self.emit("action_applied", **action.to_dict())
                self._execute(action)
        return self.events[mark:]

    def _validate(self, a: Action) -> str | None:
        if isinstance(a, SendToScene):
            return self._check_send(a)
        if isinstance(a, AssignTeam):
            return self._check_team(a)
        if isinstance(a, SetDestination):
            return self._check_destination(a)
        return "unknown_action"

    def _execute(self, a: Action) -> None:
        if isinstance(a, SendToScene):
            self._dispatch(self.units[a.unit], a.incident)
        elif isinstance(a, AssignTeam):
            self.incidents[a.incident].team = a.team
            self._dispatch(self.units[a.team], a.incident)
        else:
            self._set_destination(a)

    def _check_send(self, a: SendToScene) -> str | None:
        u, inc = self.units.get(a.unit), a.incident
        if u is None:
            return "unknown_unit"
        if inc not in self.reported:
            return "unknown_incident"
        if not u.is_ambulance:
            return "capability_mismatch"
        if u.state not in FREE_STATES:
            return "unit_busy"
        if not self.waiting_victims(inc):
            return "incident_resolved"
        return None

    def _check_team(self, a: AssignTeam) -> str | None:
        u = self.units.get(a.team)
        if u is None:
            return "unknown_unit"
        if a.incident not in self.reported:
            return "unknown_incident"
        inc = self.incidents[a.incident]
        if inc.requires is None:
            return "not_required"
        if u.kind != TEAM_FOR_REQUIREMENT[inc.requires]:
            return "capability_mismatch"
        if inc.team is not None:
            return "already_covered"
        if u.state not in FREE_STATES:
            return "unit_busy"
        return None

    def _check_destination(self, a: SetDestination) -> str | None:
        u, h = self.units.get(a.unit), self.hospitals.get(a.hospital)
        if u is None:
            return "unknown_unit"
        if h is None:
            return "unknown_hospital"
        if not u.is_ambulance:
            return "capability_mismatch"
        if not a.victims:
            return "no_victims"
        if any(v not in self.victims for v in a.victims):
            return "unknown_victim"
        reason = self._check_destination_state(u, a)
        if reason:
            return reason
        vs = [self.victims[v] for v in a.victims]
        reason = carry_rejection(u.kind, [v.severity for v in vs])
        if reason:
            return reason
        if any(v.care_need not in h.capabilities for v in vs):
            return "capability_mismatch"
        if free_slots(h, hospital_flow.reserved_count(h, self.reservations)) < len(vs):
            return "er_full"
        return None

    def _check_destination_state(self, u: Unit, a: SetDestination) -> str | None:
        if len(set(a.victims)) != len(a.victims):
            return "victim_unavailable"
        if u.state == "at_hospital_no_slot":
            return None if sorted(a.victims) == sorted(u.victims) else "victim_unavailable"
        if u.state not in ("to_scene", "on_scene"):
            return "unit_busy"
        if u.hospital is not None:
            return "destination_already_set"
        for vid in a.victims:
            v = self.victims[vid]
            if v.incident_id != u.incident:
                return "wrong_incident"
            if v.status != "waiting":
                return "victim_unavailable"
        return None

    def _dispatch(self, u: Unit, iid: str) -> None:
        u.incident, u.hospital, u.victims = iid, None, []
        u.state = "to_scene"
        trip = self._start_trip(u, "to_scene", self.incidents[iid].node)
        self.emit("unit_dispatched", unit=u.id, incident=iid, trip=trip.id, eta=trip.arrive_t)

    def _set_destination(self, a: SetDestination) -> None:
        u = self.units[a.unit]
        from_door = u.state == "at_hospital_no_slot"
        ordered = sorted(a.victims, key=lambda vid: victim_priority(
            self.victims[vid].severity, self.victims[vid].deadline, vid))
        for vid in ordered:
            res = Reservation(f"R{len(self.reservations) + 1:03d}", a.hospital, u.id, vid, self.t)
            self.reservations[res.id] = res
            v = self.victims[vid]
            v.reservation, v.unit = res.id, u.id
            v.status = "at_door" if from_door else "assigned"
        u.hospital = a.hospital
        u.victims = ordered
        if from_door or ((ready := self._ready_at(u)) is not None and self.t >= ready):
            self._depart(u)
