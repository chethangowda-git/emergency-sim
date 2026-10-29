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
SWAP_IF_WAIT_OVER = 15


def rule_ranking(reports: list[IncidentReport]) -> list[str]:
    """Fewest minutes left first, then most victims at risk (DESIGN 4.1)."""
    key = lambda r: (r.minutes_left if r.minutes_left is not None else 10**9, -r.at_risk, r.incident)
    return [r.incident for r in sorted(reports, key=key)]


class DispatchAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    # ----- unit plan -----

    def plan_units(self, scope: DispatchScope, reports: list[IncidentReport]) -> UnitPlan:
        by_id = {r.incident: r for r in reports}
        ranking = rule_ranking(reports)
        plan = self._allocate(scope, ranking, by_id, {})
        if not any(len(s.options) > 1 for s in plan.shortfalls) or not self.llm.enabled:
            return plan
        payload = {"t": scope.t, "reports": [r.model_dump() for r in reports],
                   "shortfalls": [s.model_dump() for s in plan.shortfalls]}
        result = self.llm.ask(AGENT, "plan_units", prompts.DISPATCH, payload, DispatchJudgment)
        j = result.output
        if j is None or sorted(j.ranking) != sorted(ranking):
            why = result.error or "ranking is not a permutation of the incidents"
            return plan.model_copy(update={"decided_by": "fallback", "reason": f"LLM fallback: {why}"})
        choices = {p.incident: p.option_id for p in j.shortfall_plans}  # checked against the new plan's options
        return self._allocate(scope, j.ranking, by_id, choices).model_copy(
            update={"decided_by": "llm", "reason": j.reason})

    def _allocate(self, scope: DispatchScope, ranking: list[str], reports: dict[str, IncidentReport],
                  choices: dict[str, str]) -> UnitPlan:
        used: set[str] = set()
        assignments, shortfalls, actions = [], [], []
        incidents = {i["id"]: i for i in scope.incidents()}
        for pos, iid in enumerate(ranking):
            node = incidents[iid]["node"]
            missing = self._missing(scope, iid, reports[iid].units_needed)
            got = UnitCounts()
            for kind in ("ALS", "BLS", "FIRE", "RESCUE"):
                for _ in range(getattr(missing, kind)):
                    unit = self._nearest(scope, node, used, ("BLS", "HV") if kind == "BLS" else (kind,))
                    if unit is None:
                        continue
                    used.add(unit)
                    setattr(got, kind, getattr(got, kind) + 1)
                    eta = scope.eta(unit, node)
                    assignments.append(Assignment(unit=unit, incident=iid, eta=eta))
                    name = "assign_team" if kind in ("FIRE", "RESCUE") else "send_to_scene"
                    actions.append(ProposedAction(action=name, unit=unit, incident=iid))
            short = UnitCounts(**{k: getattr(missing, k) - getattr(got, k) for k in ("ALS", "BLS", "FIRE", "RESCUE")})
            if any(getattr(short, k) for k in ("ALS", "BLS", "FIRE", "RESCUE")):
                sf = self._shortfall(scope, iid, short, ranking[pos + 1:], used, choices.get(iid))
                shortfalls.append(sf)
                chosen = next(o for o in sf.options if o.id == sf.chosen)
                if chosen.plan == "redirect":
                    used.add(chosen.unit)
                    actions.append(ProposedAction(action="redirect", unit=chosen.unit, incident=iid, by=AGENT))
        return UnitPlan(ranking=ranking, assignments=assignments, shortfalls=shortfalls,
                        proposed_actions=actions, decided_by="rule", reason="nearest free unit by road, by urgency")

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
        kind = "ALS" if short.ALS else "BLS" if short.BLS else "FIRE" if short.FIRE else "RESCUE"
        wait = self._soonest_free(scope, kind, used)
        if wait:
            options.append(wait)
        if short.BLS:
            options.append(ShortfallOption(id="mutual_aid", plan="mutual_aid", detail="ask hospitals for a vehicle"))
        if short.ALS and (wait is None or (wait.expected_delay or 0) > SWAP_IF_WAIT_OVER):
            options.append(ShortfallOption(id="swap", plan="swap",
                                           detail="ask a hospital to take a non-critical job and free an ALS"))
        options.append(ShortfallOption(id="queue", plan="queue", detail="wait for the next free unit"))
        ids = [o.id for o in options]
        chosen = choice if choice in ids else ids[0]
        return Shortfall(incident=iid, missing=short, options=options, chosen=chosen)

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
        victims = sorted(req.victims, key=lambda v: (SEVERITY_RANK[v.severity], v.deadline or 10**9, v.id))
        plans = {"earliest": self._plan(victims, offered, lambda o: (o[1], o[0]))}
        fewest_caps = lambda o: (len(nodes[o[0]]["capabilities"]), o[1], o[0])
        spare = self._plan(victims, offered, lambda o: (o[1], o[0]), spare_specialist=fewest_caps)
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
            base = req.ready_at + scope.travel(req.origin_node, nodes[o.hospital]["node"])
            for vid in o.accept:
                if vid in out:
                    out[vid].append((o.hospital, base))
            for c in o.conditional:
                for vid in c.victims:
                    if vid in out:
                        out[vid].append((o.hospital, max(base, c.after_minute or base)))
        return out

    @staticmethod
    def _plan(victims, offered, order, spare_specialist=None) -> tuple[list[Award], list[str]]:
        chosen: dict[str, list[tuple[str, int]]] = {}
        escalate = []
        for v in victims:
            fits = [o for o in offered[v.id] if v.deadline is None or o[1] <= v.deadline]
            if not fits:
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
