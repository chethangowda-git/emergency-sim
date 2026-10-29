"""Hospital agent (DESIGN 4.3): mood, admission offers by the capacity ladder, and replies to
transfer, staff and vehicle requests under the simplified EMTALA rules."""
from __future__ import annotations

from coordination import prompts
from coordination.base import pick
from coordination.llm import LLMClient
from coordination.schemas import (AdmissionOffer, AdmissionRequest, Conditional, Decline, LoanResponse,
                                  ProposedAction, RequestedVictim, StaffRequest, TransferRequest, TransferResponse,
                                  VehicleRequest)
from coordination.tools import HospitalScope

SEVERITY_RANK = {"critical": 0, "serious": 1, "minor": 2}


def by_urgency(victims: list[RequestedVictim]) -> list[RequestedVictim]:
    return sorted(victims, key=lambda v: (SEVERITY_RANK[v.severity], v.deadline or 10**9, v.id))


def fit_now(scope: HospitalScope, victims: list[RequestedVictim], extra_slots: int = 0) -> list[str]:
    """Victims (most urgent first) that free slots, doctors with the skill and nurses can take now."""
    slots = scope.me["free_slots"] + extra_slots
    nurses = sum(max(0, n["max"] - len(n["patients"])) for n in scope.me["nurses"])
    doctors = {d["id"]: max(0, d["max"] - len(d["patients"])) for d in scope.me["doctors"]}
    skills = {d["id"]: d["skills"] for d in scope.me["doctors"]}
    taken = []
    for v in victims:
        doc = next((d for d in sorted(doctors) if doctors[d] > 0 and v.care_need in skills[d]), None)
        if slots <= 0 or nurses <= 0 or doc is None:
            continue
        slots, nurses, doctors[doc] = slots - 1, nurses - 1, doctors[doc] - 1
        taken.append(v.id)
    return taken


class HospitalAgent:
    def __init__(self, hospital_id: str, llm: LLMClient, negotiate: bool = True) -> None:
        self.id = hospital_id
        self.llm = llm
        self.negotiate = negotiate

    # ----- admission -----

    def respond_admission(self, scope: HospitalScope, req: AdmissionRequest) -> AdmissionOffer:
        mood = scope.mood()
        caps = scope.me["capabilities"]
        treatable = by_urgency([v for v in req.victims if v.care_need in caps])
        no_cap = [v.id for v in req.victims if v.care_need not in caps]
        base_decline = [Decline(victims=no_cap, reason="cannot treat this care need")] if no_cap else []
        options = self._admission_options(scope, mood, treatable, base_decline)
        if not self.negotiate:
            options = {"accept_now": options["accept_now"]}
        ids = list(options)
        default = self._admission_default(mood, ids)
        if not treatable:
            ids, default = ["accept_now"], "accept_now"
        elif len(options["accept_now"].accept) == len(treatable) and mood != "protective":
            ids, default = ["accept_now"], "accept_now"
        payload = {"t": scope.t, "mood": mood, "request": req.model_dump(),
                   "options": {k: o.model_dump(exclude={"decided_by"}) for k, o in options.items()},
                   "ready_for_release": len(scope.patients("ready_for_release")),
                   "transferable": len(scope.patients("transferable"))}
        picked = pick(self.llm, self.id, "admission", prompts.HOSPITAL, payload, ids, default,
                      f"{mood}: {default.replace('_', ' ')}")
        return options[picked.option_id].model_copy(update={"decided_by": picked.decided_by, "reason": picked.reason})

    def _offer(self, mood, ids, accept, reason, base_decline, conditional=(), actions=()) -> AdmissionOffer:
        held = {v for c in conditional for v in c.victims}
        rest = [i for i in ids if i not in accept and i not in held]
        decline = base_decline + ([Decline(victims=rest, reason=reason)] if rest else [])
        return AdmissionOffer(hospital=self.id, mood=mood, accept=list(accept), conditional=list(conditional),
                              decline=decline, proposed_actions=list(actions), decided_by="rule")

    def _admission_options(self, scope, mood, treatable, base_decline) -> dict[str, AdmissionOffer]:
        """The section 4.3 ladder: free capacity, release ready patients, after a transfer, protect criticals."""
        ids = [v.id for v in treatable]
        full = "no free capacity now"
        mine = [h for h in scope.me["holds"] if h["victim"] in ids]
        now = fit_now(scope, treatable, extra_slots=len(mine))
        opts = {"accept_now": self._offer(mood, ids, now, full, base_decline)}
        ready = sorted(scope.patients("ready_for_release"), key=lambda p: (p["ready_at"], p["id"]))
        if ready and len(now) < len(ids):
            more = fit_now(scope, treatable, extra_slots=len(ready))
            if len(more) > len(now):
                releases = [ProposedAction(action="release_patient", victim=p["id"], by=self.id)
                            for p in ready[: len(more) - len(now)]]
                opts["release_then_accept"] = self._offer(mood, ids, more, full, base_decline, actions=releases)
        left = [i for i in ids if i not in now]
        transferable = scope.patients("transferable")
        if transferable and scope.idle_vehicles() and left:
            cond = [Conditional(victims=left[: len(transferable)], after="transfer", after_minute=scope.t + 5)]
            opts["after_transfer"] = self._offer(mood, ids, now, full, base_decline, conditional=cond)
        if mood == "protective":
            crit = [v.id for v in treatable if v.severity == "critical" and v.id in now]
            if len(crit) < len(now):
                opts["protect_criticals"] = self._offer(mood, ids, crit, "keeping my last slots for criticals",
                                                        base_decline)
        return opts

    @staticmethod
    def _admission_default(mood: str, ids: list[str]) -> str:
        if mood == "protective" and "protect_criticals" in ids:
            return "protect_criticals"
        return "release_then_accept" if "release_then_accept" in ids else "accept_now"

    # ----- transfers -----

    def respond_transfer(self, scope: HospitalScope, req: TransferRequest) -> TransferResponse:
        reply = lambda accept, reason, by="rule": TransferResponse(
            hospital=self.id, accept=accept, reason=reason, decided_by=by,
            proposed_actions=[ProposedAction(action="reserve", hospital=self.id, victims=[req.victim],
                                             kind="firm", by=self.id)] if accept else [])
        if req.care_need not in scope.me["capabilities"]:
            return reply(False, "cannot treat this care need")
        if scope.real_capacity(req.care_need) == 0:
            return reply(False, "no free capacity")
        if req.care_need not in req.sender_capabilities:
            return reply(True, "specialty the sender lacks and I have capacity (EMTALA)")
        mood = scope.mood()
        if mood != "normal":
            return reply(mood == "eager", "eager: idle capacity" if mood == "eager"
                         else "lateral transfer; keeping capacity for criticals")
        payload = {"t": scope.t, "mood": mood, "request": req.model_dump(),
                   "real_capacity": scope.real_capacity(req.care_need)}
        picked = pick(self.llm, self.id, "transfer", prompts.HOSPITAL, payload, ["accept", "decline"],
                      "accept", "normal load: lateral transfer accepted")
        return reply(picked.option_id == "accept", picked.reason, picked.decided_by)

    # ----- loans -----

    def respond_staff(self, scope: HospitalScope, req: StaffRequest) -> LoanResponse:
        free = scope.free_staff(req.kind, req.skill)
        on_duty = [s for s in (scope.me["doctors"] if req.kind == "doctor" else scope.me["nurses"])
                   if s["home"] == self.id]
        later = scope.staff_free_at(req.kind, req.skill)
        reply = lambda answer, reason, by="rule", **kw: LoanResponse(hospital=self.id, answer=answer, reason=reason,
                                                                     decided_by=by, **kw)
        if scope.mood() == "protective" or (not free and later is None):
            return reply("decline", "no staff to spare")
        options = []
        if free and len(on_duty) > 1:
            options.append("send_now")
        if later is not None:
            options.append("send_later")
        options.append("decline")
        default = "send_now" if "send_now" in options else options[0]
        if scope.mood() == "eager" and "send_now" in options:
            options = ["send_now"]
        payload = {"t": scope.t, "mood": scope.mood(), "request": req.model_dump(), "free_staff": free,
                   "busy_staff_free_at": later}
        picked = pick(self.llm, self.id, "staff", prompts.HOSPITAL, payload, options, default,
                      f"{scope.mood()}: {default.replace('_', ' ')}")
        if picked.option_id == "send_now":
            action = ProposedAction(action="lend_staff", staff=free[0], hospital=req.from_hospital,
                                    until=scope.t + req.minutes, by=self.id)
            return reply("send_now", picked.reason, picked.decided_by, resource=free[0], proposed_actions=[action])
        if picked.option_id == "send_later":
            return reply("send_later", picked.reason, picked.decided_by, at_minute=later)
        return reply("decline", picked.reason, picked.decided_by)

    def respond_vehicle(self, scope: HospitalScope, req: VehicleRequest) -> LoanResponse:
        idle = scope.idle_vehicles()
        if not idle:
            return LoanResponse(hospital=self.id, answer="decline", reason="my vehicle is busy", decided_by="rule")
        if scope.mood() == "protective" and scope.patients("transferable"):
            return LoanResponse(hospital=self.id, answer="decline", decided_by="rule",
                                reason="I may need my vehicle for a transfer")
        action = ProposedAction(action="lend_vehicle", vehicle=idle[0], until=req.until, by=self.id)
        return LoanResponse(hospital=self.id, answer="send_now", resource=idle[0], decided_by="rule",
                            reason="vehicle idle", proposed_actions=[action])


class RuleHospital:
    """Ablation 'rule hospitals' (DESIGN 11.3): accept if real capacity is free, nothing else."""

    def __init__(self, hospital_id: str) -> None:
        self.id = hospital_id
        self.negotiate = False

    def respond_admission(self, scope: HospitalScope, req: AdmissionRequest) -> AdmissionOffer:
        treatable = by_urgency([v for v in req.victims if v.care_need in scope.me["capabilities"]])
        now = fit_now(scope, treatable)
        rest = [v.id for v in req.victims if v.id not in now]
        return AdmissionOffer(hospital=self.id, mood=scope.mood(), accept=now, decided_by="rule",
                              decline=[Decline(victims=rest, reason="no free capacity")] if rest else [],
                              reason="rule hospital: accept if real capacity is free")

    def respond_transfer(self, scope: HospitalScope, req: TransferRequest) -> TransferResponse:
        ok = req.care_need in scope.me["capabilities"] and scope.real_capacity(req.care_need) > 0
        return TransferResponse(hospital=self.id, accept=ok, decided_by="rule",
                                reason="real capacity free" if ok else "no free capacity",
                                proposed_actions=[ProposedAction(action="reserve", hospital=self.id, victims=[req.victim],
                                                                 kind="firm", by=self.id)] if ok else [])

    def respond_staff(self, scope: HospitalScope, req: StaffRequest) -> LoanResponse:
        return LoanResponse(hospital=self.id, answer="decline", reason="rule hospital does not lend", decided_by="rule")

    def respond_vehicle(self, scope: HospitalScope, req: VehicleRequest) -> LoanResponse:
        idle = scope.idle_vehicles()
        if not idle:
            return LoanResponse(hospital=self.id, answer="decline", reason="vehicle busy", decided_by="rule")
        action = ProposedAction(action="lend_vehicle", vehicle=idle[0], until=req.until, by=self.id)
        return LoanResponse(hospital=self.id, answer="send_now", resource=idle[0], decided_by="rule",
                            reason="rule hospital: vehicle idle", proposed_actions=[action])
