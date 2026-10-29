"""Greedy baseline, exactly DESIGN section 10. No negotiation, no looking ahead, zero decision time.

1. Incidents most severe first, then oldest.
2. Nearest free unit of the right type by road (ALS for criticals, BLS for others; if none of
   that type is free, any free unit allowed to carry the victim; otherwise wait).
3. Nearest hospital by road with a free slot and the right capability, reserved once the
   ambulance is on a cleared scene.
4. Nearest capable team to fires and collapses.
5. After `arrived_no_slot`, the next hospital by road with space.
"""
from __future__ import annotations

import math

from policies.base import Policy
from sim.actions import Action, AssignTeam, SendToScene, SetDestination
from sim.models import TEAM_FOR_REQUIREMENT
from sim.rules import SEVERITY_RANK, carry_rejection, victim_priority
from sim.views import WorldView

FREE = ("idle", "returning")


def incident_priority(inc: dict) -> tuple:
    waiting = [v for v in inc["victims"] if v["status"] == "waiting"]
    worst = min((SEVERITY_RANK[v["severity"]] for v in waiting), default=len(SEVERITY_RANK))
    return (worst, inc["reported_at"], inc["id"])


def by_priority(victims: list[dict]) -> list[dict]:
    return sorted(victims, key=lambda v: victim_priority(v["severity"], v["deadline"], v["id"]))


class GreedyPolicy(Policy):
    name = "greedy"

    def decide(self, view: WorldView, events: list[dict]) -> list[Action]:
        d = view.data
        incidents = sorted(d["incidents"], key=incident_priority)
        units = {u["id"]: u for u in d["units"]}
        hospitals = d["hospitals"]
        free_slots = {h["id"]: h["free_slots"] for h in hospitals}
        busy: set[str] = set()
        claimed: set[str] = set()
        actions: list[Action] = []
        actions += self._teams(view, incidents, units, busy)
        actions += self._destinations(view, incidents, units, hospitals, free_slots, busy, claimed)
        actions += self._dispatch(view, incidents, units, busy, claimed)
        return actions

    # ----- step 4: teams -----

    def _teams(self, view, incidents, units, busy) -> list[Action]:
        out = []
        for inc in incidents:
            if not inc["requires"] or inc["team"] or inc["cleared"]:
                continue
            kind = TEAM_FOR_REQUIREMENT[inc["requires"]]
            team = self._nearest(view, units, busy, inc["node"], lambda u: u["kind"] == kind)
            if team:
                busy.add(team)
                out.append(AssignTeam(team, inc["id"]))
        return out

    # ----- steps 3 and 5: destinations -----

    def _destinations(self, view, incidents, units, hospitals, free_slots, busy, claimed) -> list[Action]:
        out = []
        for u in sorted(units.values(), key=lambda u: u["id"]):
            if u["state"] == "at_hospital_no_slot":
                act = self._from_door(view, u, hospitals, free_slots)
                if act:
                    busy.add(u["id"])
                    out.append(act)
        for inc in incidents:
            if not inc["cleared"]:
                continue
            for u in sorted(units.values(), key=lambda u: u["id"]):
                if u["state"] == "on_scene" and u["incident"] == inc["id"] and not u["hospital"]:
                    act = self._load(view, u, inc, hospitals, free_slots, claimed)
                    if act:
                        busy.add(u["id"])
                        out.append(act)
        return out

    def _load(self, view, u, inc, hospitals, free_slots, claimed) -> Action | None:
        waiting = by_priority([v for v in inc["victims"] if v["status"] == "waiting" and v["id"] not in claimed])
        first, h = None, None
        for v in waiting:
            if carry_rejection(u["kind"], [v["severity"]]) is None:
                h = self._nearest_hospital(view, inc["node"], hospitals, free_slots, [v["care_need"]], 1)
                if h is not None:
                    first = v
                    break
        if first is None:
            return None
        load = [first]
        if free_slots[h["id"]] >= 2:
            for v in waiting:
                if (v is not first and v["care_need"] in h["capabilities"]
                        and carry_rejection(u["kind"], [first["severity"], v["severity"]]) is None):
                    load.append(v)
                    break
        free_slots[h["id"]] -= len(load)
        claimed.update(v["id"] for v in load)
        return SetDestination(u["id"], h["id"], tuple(v["id"] for v in load))

    def _from_door(self, view, u, hospitals, free_slots) -> Action | None:
        needs = [v["care_need"] for inc in view.data["incidents"] for v in inc["victims"] if v["id"] in u["victims"]]
        h = self._nearest_hospital(view, u["node"], hospitals, free_slots, needs, len(u["victims"]))
        if h is None:
            return None
        free_slots[h["id"]] -= len(u["victims"])
        return SetDestination(u["id"], h["id"], tuple(u["victims"]))

    @staticmethod
    def _nearest_hospital(view, origin, hospitals, free_slots, needs, count) -> dict | None:
        able = [h for h in hospitals
                if free_slots[h["id"]] >= count and all(n in h["capabilities"] for n in needs)]
        return min(able, key=lambda h: (view.travel(origin, h["node"]), h["id"]), default=None)

    # ----- steps 1 and 2: dispatch -----

    def _dispatch(self, view, incidents, units, busy, claimed) -> list[Action]:
        out = []
        for inc in incidents:
            crit_need, other_need = self._uncovered(inc, units, busy, claimed)
            for _ in range(crit_need):
                unit = self._nearest(view, units, busy, inc["node"], lambda u: u["kind"] == "ALS")
                if unit:
                    busy.add(unit)
                    out.append(SendToScene(unit, inc["id"]))
            for _ in range(other_need):
                unit = (self._nearest(view, units, busy, inc["node"], lambda u: u["kind"] == "BLS")
                        or self._nearest(view, units, busy, inc["node"], lambda u: u["kind"] == "ALS"))
                if unit:
                    busy.add(unit)
                    out.append(SendToScene(unit, inc["id"]))
        return out

    @staticmethod
    def _uncovered(inc, units, busy, claimed) -> tuple[int, int]:
        """(criticals without an ALS, ambulances still needed for the others)."""
        waiting = [v for v in inc["victims"] if v["status"] == "waiting" and v["id"] not in claimed]
        crit = sum(1 for v in waiting if v["severity"] == "critical")
        other = len(waiting) - crit
        coming = [u for u in units.values() if u["incident"] == inc["id"] and not u["hospital"]
                  and u["state"] in ("to_scene", "on_scene") and u["id"] not in busy]
        als = sum(1 for u in coming if u["kind"] == "ALS")
        bls = len(coming) - als
        spare_als = max(0, als - crit)
        missing_crit = max(0, crit - als)
        missing_other = max(0, other - 2 * (bls + spare_als))
        return missing_crit, math.ceil(missing_other / 2)

    @staticmethod
    def _nearest(view, units, busy, node, ok) -> str | None:
        free = [u for u in units.values() if u["state"] in FREE and u["id"] not in busy and ok(u)]
        best = min(free, key=lambda u: (view.eta(u["id"], node), u["id"]), default=None)
        return best["id"] if best else None
