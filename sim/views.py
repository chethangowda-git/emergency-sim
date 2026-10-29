"""Read-only views of the World: the policy's input and the dashboard's per-minute record."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sim.roads import RoadNetwork, Route, position_on
from sim.rules import free_doctor_slots, free_nurse_slots, free_slots, real_free_capacity, utilization

if TYPE_CHECKING:
    from sim.world import World


class WorldView:
    """Plain data plus road-time helpers that match what the World will do exactly."""

    def __init__(self, data: dict, roads: RoadNetwork,
                 motion: dict[str, tuple[str, Route | None, float]]) -> None:
        self.data = data
        self._roads = roads
        self._motion = motion

    @property
    def t(self) -> int:
        return self.data["t"]

    def eta(self, unit_id: str, node: str) -> int:
        """Minutes for the unit to reach `node` if sent now, from wherever it is."""
        at, route, elapsed = self._motion[unit_id]
        if route is None:
            return self._roads.minutes(at, node)
        return self._roads.minutes_from(route, elapsed, node)

    def travel(self, origin: str, dest: str) -> int:
        return self._roads.minutes(origin, dest)


def build_snapshot(w: World) -> dict:
    return {
        "t": w.t,
        "incidents": [_incident(w, iid) for iid in w.reported],
        "units": [_unit(w, uid) for uid in sorted(w.units)],
        "hospitals": [_hospital(w, hid) for hid in sorted(w.hospitals)],
        "waiting_victims": sum(1 for iid in w.reported for vid in w.incidents[iid].victim_ids
                               if w.victims[vid].delivered_at is None),
        "staff_moving": [{"staff": s.id, "kind": s.kind, "to": dest, "arrives_at": arrive, "home": s.home}
                         for arrive, dest, s in sorted(w.staff_moving, key=lambda m: (m[0], m[2].id))],
    }


def _incident(w: World, iid: str) -> dict:
    inc = w.incidents[iid]
    victims = [{"id": v.id, "severity": v.severity, "original_severity": v.original_severity,
                "care_need": v.care_need, "deadline": v.deadline, "status": v.status,
                "unit": v.unit, "hospital": v.hospital}
               for v in (w.victims[vid] for vid in inc.victim_ids)]
    return {"id": inc.id, "type": inc.type, "node": inc.node, "lat": inc.lat, "lon": inc.lon,
            "reported_at": inc.reported_at, "requires": inc.requires, "description": inc.description,
            "surge": inc.surge, "team": inc.team, "cleared_at": inc.cleared_at,
            "cleared": inc.cleared_at is not None and inc.cleared_at <= w.t, "victims": victims}


def _unit(w: World, uid: str) -> dict:
    u = w.units[uid]
    moving = u.trip is not None and u.trip.ended_at is None and w.t < u.trip.arrive_t
    lat, lon = position_on(u.trip.route, w.t - u.trip.depart_t) if moving else w.roads.latlon(u.node)
    return {"id": u.id, "kind": u.kind, "state": u.state, "node": u.node, "base": u.base,
            "base_node": u.base_node, "incident": u.incident, "hospital": u.hospital,
            "victims": list(u.victims), "trip": u.trip.id if moving else None,
            "eta": u.trip.arrive_t if moving else None, "on_scene_since": u.on_scene_since,
            "handover_until": u.handover_until, "owner": u.owner, "lent_until": u.lent_until,
            "prep_until": u.prep_until, "redirects": u.redirects, "reroutes": u.reroutes,
            "lat": round(lat, 6), "lon": round(lon, 6)}


def _hospital(w: World, hid: str) -> dict:
    h = w.hospitals[hid]
    active = [r for r in w.reservations.values() if r.hospital == hid and r.status == "active"]
    reserved = len(active)
    statuses = [w.victims[v].status for v in h.er]
    return {
        "id": h.id, "name": h.name, "node": h.node, "lat": h.lat, "lon": h.lon,
        "capabilities": list(h.capabilities), "er_slots": h.er_slots, "er_used": len(h.er),
        "reserved": reserved, "free_slots": free_slots(h, reserved),
        "waiting_for_staff": statuses.count("in_er"),
        "in_treatment": statuses.count("in_treatment") + statuses.count("transferable"),
        "ready_for_release": statuses.count("ready_for_release"), "boarding": statuses.count("boarding"),
        "ward_beds": h.ward_beds, "ward_used": len(h.ward),
        "doctors": [{"id": d.id, "skills": list(d.skills), "patients": list(d.patients), "home": d.home,
                     "lent_until": d.lent_until} for d in h.doctors],
        "nurses": [{"id": n.id, "patients": list(n.patients), "home": n.home, "lent_until": n.lent_until}
                   for n in h.nurses],
        "vehicles": [{"id": u.id, "state": u.state, "lent_until": u.lent_until}
                     for u in (w.units[v.id] for v in h.vehicles)],
        "patients": [_patient(w.victims[v]) for v in h.er],
        "holds": [{"id": r.id, "victim": r.victim, "created_at": r.created_at, "expires_at": r.expires_at}
                  for r in active if r.kind == "hold"],
        "firm": [{"id": r.id, "victim": r.victim, "unit": r.unit} for r in active if r.kind == "firm"],
        "free_doctor_slots": free_doctor_slots(h), "free_nurse_slots": free_nurse_slots(h),
        "real_free": real_free_capacity(h, reserved), "utilization": round(utilization(h, reserved), 4),
    }


def _patient(v) -> dict:
    return {"id": v.id, "status": v.status, "severity": v.severity, "original_severity": v.original_severity,
            "care_need": v.care_need, "walk_in": v.walk_in, "delivered_at": v.delivered_at,
            "treatment_started_at": v.treatment_started_at, "treatment_minutes": v.treatment_minutes,
            "ready_at": v.ready_at, "transferable_at": v.transferable_at, "transfer_deadline": v.transfer_deadline}
