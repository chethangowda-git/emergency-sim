"""Dispatch (DESIGN 4.2): unit plans for competing requests, and admission awards from offers."""
from __future__ import annotations

from coordination import prompts
from coordination.base import pick
from coordination.llm import LLMClient
from coordination.schemas import (AdmissionOffer, AdmissionRequest, Assignment, Award, AwardDecision,
                                  DispatchJudgment, IncidentReport, ProposedAction, Shortfall, ShortfallOption,
                                  UnitCounts, UnitPlan)
from coordination.tools import DispatchScope

AGENT = "dispatch"
SEVERITY_RANK = {"critical": 0, "serious": 1, "minor": 2}
MUTUAL_AID_WITHIN = 60   # minutes left before a non-critical's deadline that justify borrowing a vehicle


def rule_ranking(reports: list[IncidentReport]) -> list[str]:
    """Fewest minutes left first, then most victims at risk (DESIGN 4.1)."""
    key = lambda r: (r.minutes_left if r.minutes_left is not None else 10**9, -r.at_risk, r.incident)
    return [r.incident for r in sorted(reports, key=key)]


class DispatchAgent:
    def __init__(self, llm: LLMClient, negotiate: bool = True) -> None:
        self.llm = llm
        self.negotiate = negotiate
        self._last_situation: tuple | None = None
        self._last_judgment: DispatchJudgment | None = None

    # ----- unit plan -----

    def plan_units(self, scope: DispatchScope, reports: list[IncidentReport]) -> UnitPlan:
        by_id = {r.incident: r for r in reports}
        ranking = rule_ranking(reports)
        plan = self._allocate(scope, ranking, by_id, {})
        judgment_needed = plan.shortfalls and (len(ranking) > 1 or any(len(s.options) > 1 for s in plan.shortfalls))
        if not judgment_needed or not self.llm.enabled:
            return plan
        situation = (tuple(sorted(ranking)), tuple((s.incident, tuple(o.id for o in s.options)) for s in plan.shortfalls))
        if situation == self._last_situation and self._last_judgment is not None:
            j = self._last_judgment
            return self._allocate(scope, j.ranking, by_id, {p.incident: p.option_id for p in j.shortfall_plans}
                                  ).model_copy(update={"decided_by": "llm", "reason": j.reason})
        payload = {"t": scope.t, "reports": [r.model_dump() for r in reports],
                   "shortfalls": [s.model_dump() for s in plan.shortfalls]}
        result = self.llm.ask(AGENT, "plan_units", prompts.DISPATCH, payload, DispatchJudgment)
        j = result.output
        self._last_situation, self._last_judgment = situation, None
        if j is None or sorted(j.ranking) != sorted(ranking):
            why = result.error or "ranking is not a permutation of the incidents"
            return plan.model_copy(update={"decided_by": "fallback", "reason": f"LLM fallback: {why}"})
        self._last_judgment = j
        choices = {p.incident: p.option_id for p in j.shortfall_plans}  # checked against the new plan's options
        return self._allocate(scope, j.ranking, by_id, choices).model_copy(
            update={"decided_by": "llm", "reason": j.reason})

    def _allocate(self, scope: DispatchScope, ranking: list[str], reports: dict[str, IncidentReport],
                  choices: dict[str, str]) -> UnitPlan:
        """Pass 1: the right unit type per need, by urgency. Pass 2: free ALS units take remaining
        non-critical needs, but only if no critical anywhere still lacks an ALS ("send what is available")."""
        used: set[str] = set()
        assignments, actions = [], []
        incidents = {i["id"]: i for i in scope.incidents()}
        shorts: dict[str, UnitCounts] = {}
        for iid in ranking:
            missing = self._missing(scope, iid, reports[iid].units_needed)
            for kind in ("ALS", "BLS", "FIRE", "RESCUE"):
                kinds = ("BLS", "HV") if kind == "BLS" else (kind,)
                got = self._assign(scope, incidents[iid], getattr(missing, kind), kinds, used, assignments, actions)
                setattr(missing, kind, getattr(missing, kind) - got)
            shorts[iid] = missing
        if not any(s.ALS for s in shorts.values()):
            for iid in ranking:
                shorts[iid].BLS -= self._assign(scope, incidents[iid], shorts[iid].BLS, ("ALS",), used,
                                                assignments, actions)
        shortfalls = []
        for pos, iid in enumerate(ranking):
            short = shorts[iid]
            if not any(getattr(short, k) for k in ("ALS", "BLS", "FIRE", "RESCUE")):
                continue
            sf = self._shortfall(scope, iid, short, ranking[pos + 1:], used, choices.get(iid))
            shortfalls.append(sf)
            chosen = next(o for o in sf.options if o.id == sf.chosen)
            if chosen.plan in ("redirect", "swap"):
                used.add(chosen.unit)
                actions.append(ProposedAction(action="redirect", unit=chosen.unit, incident=iid, by=AGENT))
        return UnitPlan(ranking=ranking, assignments=assignments, shortfalls=shortfalls,
                        proposed_actions=actions, decided_by="rule", reason="nearest free unit by road, by urgency")

    def _assign(self, scope, inc: dict, count: int, kinds: tuple[str, ...], used: set[str],
                assignments: list, actions: list) -> int:
        got = 0
        for _ in range(count):
            unit = self._nearest(scope, inc["node"], used, kinds)
            if unit is None:
                break
            used.add(unit)
            got += 1
            assignments.append(Assignment(unit=unit, incident=inc["id"], eta=scope.eta(unit, inc["node"])))
            name = "assign_team" if kinds[0] in ("FIRE", "RESCUE") else "send_to_scene"
            actions.append(ProposedAction(action=name, unit=unit, incident=inc["id"]))
        return got

    def _missing(self, scope: DispatchScope, iid: str, needed: UnitCounts) -> UnitCounts:
        coming = [u for u in scope.units() if u["incident"] == iid and not u["hospital"]
                  and u["state"] in ("to_scene", "on_scene", "clearing")]
        count = lambda kinds: sum(1 for u in coming if u["kind"] in kinds)
        return UnitCounts(ALS=max(0, needed.ALS - count(("ALS",))), BLS=max(0, needed.BLS - count(("BLS", "HV"))),
                          FIRE=max(0, needed.FIRE - count(("FIRE",))), RESCUE=max(0, needed.RESCUE - count(("RESCUE",))))

    @staticmethod
    def _nearest(scope: DispatchScope, node: str, used: set[str], kinds: tuple[str, ...]) -> str | None:
        free = [u for u in scope.free_units() if u["kind"] in kinds and u["id"] not in used]
        best = min(free, key=lambda u: (scope.eta(u["id"], node), u["id"]), default=None)
        return best["id"] if best else None

    def _shortfall(self, scope: DispatchScope, iid: str, short: UnitCounts, lower: list[str],
                   used: set[str], choice: str | None) -> Shortfall:
        options: list[ShortfallOption] = []
        if short.ALS:
            options += self._redirect_options(scope, iid, lower, used)
            options += [ShortfallOption(id=f"swap:{o.unit}", plan="swap", unit=o.unit, expected_delay=o.expected_delay,
                                        detail=f"{o.detail}; a hospital vehicle takes over that job")
                        for o in options if o.plan == "redirect" and self.negotiate]
        kind = "ALS" if short.ALS else "BLS" if short.BLS else "FIRE" if short.FIRE else "RESCUE"
        wait = self._soonest_free(scope, kind, used)
        if wait:
            options.append(wait)
        if short.BLS and self.negotiate and self._at_risk_non_critical(scope, iid):
            options.append(ShortfallOption(id="mutual_aid", plan="mutual_aid", detail="ask hospitals for a vehicle"))
        options.append(ShortfallOption(id="queue", plan="queue", detail="wait for the next free unit"))
        ids = [o.id for o in options]
        chosen = choice if choice in ids else ids[0]
        return Shortfall(incident=iid, missing=short, options=options, chosen=chosen)

    @staticmethod
    def _at_risk_non_critical(scope: DispatchScope, iid: str) -> bool:
        """Borrow hospital vehicles only for non-criticals close to their deadline: moving minors in
        early fills ER slots that criticals need."""
        inc = next(i for i in scope.incidents() if i["id"] == iid)
        return any(v["status"] == "waiting" and v["severity"] != "critical" and v["deadline"] is not None
                   and v["deadline"] - scope.t <= MUTUAL_AID_WITHIN for v in inc["victims"])

    def _redirect_options(self, scope: DispatchScope, iid: str, lower: list[str], used: set[str]) -> list[ShortfallOption]:
        """ALS units heading to a less urgent job that has no critical waiting (DESIGN 4.2)."""
        incidents = {i["id"]: i for i in scope.incidents()}
        node = incidents[iid]["node"]
        out = []
        for u in scope.units():
            if (u["kind"] == "ALS" and u["id"] not in used and u["incident"] in lower and not u["hospital"]
                    and u["state"] in ("to_scene", "on_scene") and u["redirects"] == 0
                    and not any(v["severity"] == "critical" and v["status"] == "waiting"
                                for v in incidents[u["incident"]]["victims"])):
                out.append(ShortfallOption(id=f"redirect:{u['id']}", plan="redirect", unit=u["id"],
                                           expected_delay=scope.eta(u["id"], node),
                                           detail=f"leave {u['incident']} (no critical there)"))
        return sorted(out, key=lambda o: (o.expected_delay, o.id))

    @staticmethod
    def _soonest_free(scope: DispatchScope, kind: str, used: set[str]) -> ShortfallOption | None:
        busy = [(scope.free_at(u), u["id"]) for u in scope.units()
                if u["kind"] == kind and u["id"] not in used and scope.free_at(u) is not None]
        if not busy:
            return None
        at, uid = min(busy)
        return ShortfallOption(id=f"wait:{uid}", plan="wait", unit=uid, expected_delay=at - scope.t,
                               detail=f"{uid} frees up around minute {at}")

    # ----- admission award -----

    def award(self, scope: DispatchScope, req: AdmissionRequest, offers: list[AdmissionOffer]) -> AwardDecision:
        plans = self._award_plans(scope, req, offers)
        ids = list(plans)
        payload = {"t": scope.t, "request": req.model_dump(), "offers": [o.model_dump() for o in offers],
                   "plans": {k: [a.model_dump() for a in v[0]] for k, v in plans.items()}}
        picked = pick(self.llm, AGENT, "award", prompts.DISPATCH_AWARD, payload, ids, "earliest",
                      "earliest capable arrival for each victim")
        awards, escalate = plans[picked.option_id]
        actions = [ProposedAction(action="reserve", hospital=a.hospital, victims=a.victims, kind="firm", by=a.hospital)
                   for a in awards]
        return AwardDecision(incident=req.incident, awards=awards, escalate=escalate, proposed_actions=actions,
                             decided_by=picked.decided_by, reason=picked.reason)

    def _award_plans(self, scope, req, offers) -> dict[str, tuple[list[Award], list[str]]]:
        nodes = {h["id"]: h for h in scope.hospitals()}
        offered = self._offered(scope, req, offers, nodes)
        hopeless = self._hopeless(scope, req, nodes)
        victims = sorted(req.victims, key=lambda v: (SEVERITY_RANK[v.severity], v.deadline or 10**9, v.id))
        plans = {"earliest": self._plan(victims, offered, lambda o: (o[1], o[0]), hopeless=hopeless)}
        fewest_caps = lambda o: (len(nodes[o[0]]["capabilities"]), o[1], o[0])
        spare = self._plan(victims, offered, lambda o: (o[1], o[0]), spare_specialist=fewest_caps, hopeless=hopeless)
        if spare != plans["earliest"]:
            plans["spare_specialist"] = spare
        single = self._single_hospital(victims, offered)
        if single and single not in plans.values():
            plans["one_hospital"] = single
        return plans

    @staticmethod
    def _offered(scope, req, offers, nodes) -> dict[str, list[tuple[str, int]]]:
        """For each victim, (hospital, arrival minute) for every offer that would take them."""
        out: dict[str, list[tuple[str, int]]] = {v.id: [] for v in req.victims}
        for o in offers:
            node = nodes[o.hospital]["node"]
            base = (scope.t + scope.eta(req.unit, node) if req.aboard and req.unit
                    else req.ready_at + scope.travel(req.origin_node, node))
            for vid in o.accept:
                if vid in out:
                    out[vid].append((o.hospital, base))
            for c in o.conditional:
                for vid in c.victims:
                    if vid in out:
                        out[vid].append((o.hospital, max(base, c.after_minute or base)))
        return out

    @staticmethod
    def _hopeless(scope, req, nodes) -> set[str]:
        """Victims no capable hospital could reach in time even with free capacity."""
        out = set()
        for v in req.victims:
            arrivals = [scope.t + scope.eta(req.unit, h["node"]) if req.aboard and req.unit
                        else req.ready_at + scope.travel(req.origin_node, h["node"])
                        for h in nodes.values() if v.care_need in h["capabilities"]]
            if v.deadline is not None and arrivals and min(arrivals) > v.deadline:
                out.add(v.id)
        return out

    @staticmethod
    def _plan(victims, offered, order, spare_specialist=None, hopeless=frozenset()) -> tuple[list[Award], list[str]]:
        """Escalate a victim whose offers all arrive too late, unless no hospital ever could: then the
        earliest offer is the least-bad choice already (DESIGN 4.4, option 7). A victim nobody offered
        to take is left for the next round."""
        chosen: dict[str, list[tuple[str, int]]] = {}
        escalate = []
        for v in victims:
            fits = [o for o in offered[v.id] if v.deadline is None or o[1] <= v.deadline]
            if not fits and v.id in hopeless and offered[v.id]:
                fits = offered[v.id]
            if not fits:
                if offered[v.id]:
                    escalate.append(v.id)
                continue
            key = spare_specialist if spare_specialist and v.severity != "critical" else order
            h, arrival = min(fits, key=key)
            chosen.setdefault(h, []).append((v.id, arrival))
        awards = [Award(hospital=h, victims=[vid for vid, _ in vs], arrival=max(a for _, a in vs))
                  for h, vs in sorted(chosen.items())]
        return awards, escalate

    @staticmethod
    def _single_hospital(victims, offered) -> tuple[list[Award], list[str]] | None:
        hospitals = None
        for v in victims:
            fits = {h for h, a in offered[v.id] if v.deadline is None or a <= v.deadline}
            hospitals = fits if hospitals is None else hospitals & fits
        if not hospitals:
            return None
        best = min(hospitals, key=lambda h: (max(a for hh, a in offered[victims[0].id] if hh == h), h))
        arrival = max(a for v in victims for hh, a in offered[v.id] if hh == best)
        return [Award(hospital=best, victims=[v.id for v in victims], arrival=arrival)], []
