"""Read-only, scoped views of the world for each agent (DESIGN 2.3, 4).

Incident Assessment sees incidents; Dispatch sees its units, incidents and hospital
capabilities only; a Hospital sees only itself; the Arbiter sees everything. In live mode
the same functions are offered to the LLM as function tools.
"""
from __future__ import annotations

import json

from sim.views import WorldView

FREE = ("idle", "returning")
AT_SCENE = ("waiting", "assigned")
EAGER_BELOW, PROTECTIVE_ABOVE = 0.60, 0.85


def mood_for(utilization: float) -> str:
    if utilization < EAGER_BELOW:
        return "eager"
    return "protective" if utilization > PROTECTIVE_ABOVE else "normal"


def staff_free_slots(staff: list[dict], need: str | None = None) -> int:
    return sum(max(0, s["max"] - len(s["patients"])) for s in staff if need is None or need in s.get("skills", [need]))


class IncidentScope:
    def __init__(self, view: WorldView) -> None:
        self._v = view
        self.t = view.t

    def incident(self, iid: str) -> dict:
        inc = next(i for i in self._v.data["incidents"] if i["id"] == iid)
        return {k: inc[k] for k in ("id", "type", "description", "requires", "cleared", "reported_at", "team",
                                    "victims", "surge")}

    def at_scene(self, iid: str) -> list[dict]:
        return [v for v in self.incident(iid)["victims"] if v["status"] in AT_SCENE]


class DispatchScope:
    def __init__(self, view: WorldView) -> None:
        self._v = view
        self.t = view.t

    def units(self) -> list[dict]:
        """The Dispatch fleet, plus hospital vehicles only while they are lent to it."""
        return [u for u in self._v.data["units"] if u["kind"] != "HV"
                or (u["lent_until"] is not None and self.t < u["lent_until"])]

    def free_units(self, kind: str | None = None) -> list[dict]:
        return [u for u in self.units() if u["state"] in FREE and (kind is None or u["kind"] == kind)]

    def free_at(self, unit: dict) -> int | None:
        """When a busy unit should be free again, if a rule can tell (transport or handover)."""
        if unit["state"] in FREE:
            return self.t
        if unit["state"] == "transporting" and unit["eta"] is not None:
            return unit["eta"] + 5
        if unit["state"] == "handover":
            return unit["handover_until"]
        return None

    def incidents(self) -> list[dict]:
        return self._v.data["incidents"]

    def hospitals(self) -> list[dict]:
        return [{"id": h["id"], "name": h["name"], "node": h["node"], "capabilities": h["capabilities"]}
                for h in self._v.data["hospitals"]]

    def eta(self, unit_id: str, node: str) -> int:
        return self._v.eta(unit_id, node)

    def travel(self, origin: str, dest: str) -> int:
        return self._v.travel(origin, dest)


class HospitalScope:
    def __init__(self, view: WorldView, hospital_id: str) -> None:
        self._v = view
        self.t = view.t
        self.id = hospital_id
        self.me = next(h for h in view.data["hospitals"] if h["id"] == hospital_id)

    def mood(self) -> str:
        return mood_for(self.me["utilization"])

    def real_capacity(self, need: str | None = None) -> int:
        return max(0, min(self.me["free_slots"], staff_free_slots(self.me["doctors"], need),
                          staff_free_slots(self.me["nurses"])))

    def patients(self, status: str) -> list[dict]:
        return [p for p in self.me["patients"] if p["status"] == status]

    def idle_vehicles(self) -> list[str]:
        return [v["id"] for v in self.me["vehicles"] if v["state"] == "idle" and v["lent_until"] is None]

    def free_staff(self, kind: str, skill: str = "general") -> list[str]:
        """Own staff at home with no patients, who could be lent."""
        staff = self.me["doctors"] if kind == "doctor" else self.me["nurses"]
        return [s["id"] for s in staff if not s["patients"] and s["home"] == self.id
                and s["lent_until"] is None and (kind == "nurse" or skill in s["skills"])]

    def staff_free_at(self, kind: str, skill: str = "general") -> int | None:
        """Earliest minute a busy own staff member of that kind finishes all their patients."""
        staff = self.me["doctors"] if kind == "doctor" else self.me["nurses"]
        ends = {p["id"]: p["treatment_started_at"] + p["treatment_minutes"] for p in self.me["patients"]
                if p["treatment_started_at"] is not None and p["treatment_minutes"] is not None}
        times = [max(ends.get(pid, self.t) for pid in s["patients"]) for s in staff
                 if s["patients"] and s["home"] == self.id and (kind == "nurse" or skill in s["skills"])]
        return min(times, default=None)


class ArbiterScope:
    def __init__(self, view: WorldView) -> None:
        self._v = view
        self.t = view.t
        self.data = view.data

    def hospital(self, hid: str) -> HospitalScope:
        return HospitalScope(self._v, hid)

    def hospitals(self) -> list[HospitalScope]:
        return [HospitalScope(self._v, h["id"]) for h in self.data["hospitals"]]

    def unit(self, uid: str) -> dict:
        return next(u for u in self.data["units"] if u["id"] == uid)

    def victim(self, vid: str) -> dict:
        for inc in self.data["incidents"]:
            for v in inc["victims"]:
                if v["id"] == vid:
                    return v
        for h in self.data["hospitals"]:
            for p in h["patients"]:
                if p["id"] == vid:
                    return p
        raise KeyError(vid)

    def eta(self, unit_id: str, node: str) -> int:
        return self._v.eta(unit_id, node)

    def travel(self, origin: str, dest: str) -> int:
        return self._v.travel(origin, dest)


def function_tools(scope) -> list:
    """Read-only SDK function tools for live mode; each returns JSON text."""
    from agents import function_tool

    if isinstance(scope, HospitalScope):
        def get_my_hospital() -> str:
            """Your own hospital: ER, ward, staff, patients, holds and vehicles."""
            return json.dumps(scope.me)
        return [function_tool(get_my_hospital)]
    if isinstance(scope, DispatchScope):
        def list_units() -> str:
            """Your units with state, position and current job."""
            return json.dumps(scope.units())

        def list_hospitals() -> str:
            """Hospital names and capabilities (no capacity data)."""
            return json.dumps(scope.hospitals())
        return [function_tool(list_units), function_tool(list_hospitals)]
    if isinstance(scope, IncidentScope):
        def list_incidents() -> str:
            """All reported incidents with their victims."""
            return json.dumps(scope._v.data["incidents"])
        return [function_tool(list_incidents)]

    def get_world() -> str:
        """The whole world state at this minute."""
        return json.dumps(scope.data)
    return [function_tool(get_world)]
