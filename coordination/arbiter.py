"""Arbiter (DESIGN 4.4): tries options 1 to 7 in order and stops at the first that meets the
deadline. Rules build every candidate; when one option has several, the LLM may choose."""
from __future__ import annotations

from coordination import prompts
from coordination.base import pick
from coordination.llm import LLMClient
from coordination.schemas import ArbiterCandidate, ArbiterDecision, Escalation, ProposedAction
from coordination.tools import ArbiterScope, HospitalScope, staff_free_slots

AGENT = "arbiter"
LOAN_MINUTES = 120
TRANSFER_PREP = 5  # DESIGN 7: transfer preparation at the sending hospital
MAX_CHOICES = 5
OPTION_NAMES = {1: "another_hospital", 2: "release_ready_patients", 3: "stabilize_and_transfer_swap",
                4: "override_hold", 5: "reroute_other_patient", 6: "urgent_staff_loan", 7: "least_bad"}


def act(action: str, **kw) -> ProposedAction:
    return ProposedAction(action=action, by=AGENT, **kw)


class ArbiterAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def resolve(self, scope: ArbiterScope, esc: Escalation) -> ArbiterDecision:
        v = scope.victim(esc.victim)
        ctx = _Context(scope, esc, v)
        for option in range(1, 7):
            cands = sorted(getattr(self, f"_option{option}")(ctx), key=lambda c: (c.arrival, c.id))[:MAX_CHOICES]
            if cands:
                return self._decide(ctx, option, cands, meets=True)
        return self._decide(ctx, 7, self._option7(ctx), meets=False)

    def least_bad(self, scope: ArbiterScope, esc: Escalation) -> ArbiterDecision:
        """Ablation 'no Arbiter' (DESIGN 11.3): escalations go straight to option 7."""
        ctx = _Context(scope, esc, scope.victim(esc.victim))
        return self._decide(ctx, 7, self._option7(ctx), meets=False)

    def _decide(self, ctx, option: int, cands: list[ArbiterCandidate], meets: bool) -> ArbiterDecision:
        if not cands:
            return ArbiterDecision(victim=ctx.vid, option=7, option_name=OPTION_NAMES[7], hospital=None, arrival=None,
                                   meets_deadline=False, loss=True, proposed_actions=[], decided_by="rule",
                                   reason="no hospital can treat this care need")
        payload = {"t": ctx.scope.t, "escalation": ctx.esc.model_dump(), "victim": ctx.v,
                   "option": OPTION_NAMES[option], "candidates": [c.model_dump() for c in cands]}
        picked = pick(self.llm, AGENT, "resolve", prompts.ARBITER, payload, [c.id for c in cands], cands[0].id,
                      f"{OPTION_NAMES[option].replace('_', ' ')}: earliest arrival")
        c = next(c for c in cands if c.id == picked.option_id)
        on_time = ctx.deadline is None or c.arrival <= ctx.deadline
        return ArbiterDecision(victim=ctx.vid, option=option, option_name=OPTION_NAMES[option], hospital=c.hospital,
                               arrival=c.arrival, meets_deadline=meets and on_time, loss=option == 7 or not on_time,
                               proposed_actions=c.actions, decided_by=picked.decided_by, reason=picked.reason)

    # ----- the seven options -----

    def _option1(self, ctx) -> list[ArbiterCandidate]:
        return [ctx.candidate(h, "free capacity") for h in ctx.in_time() if h.real_capacity(ctx.need) > 0]

    def _option2(self, ctx) -> list[ArbiterCandidate]:
        out = []
        for h in ctx.in_time():
            ready = sorted(h.patients("ready_for_release"), key=lambda p: (p["ready_at"], p["id"]))
            if ready and h.me["free_slots"] == 0 and ctx.staff_ok(h):
                release = act("release_patient", victim=ready[0]["id"])
                out.append(ctx.candidate(h, f"release {ready[0]['id']} early", before=[release]))
        return out

    def _option3(self, ctx) -> list[ArbiterCandidate]:
        scope, t = ctx.scope, ctx.scope.t
        out = []
        for h in ctx.in_time():
            vehicles = h.idle_vehicles()
            for p in sorted(h.patients("transferable"), key=lambda p: p["id"]):
                for r in scope.hospitals():
                    leaves = t + TRANSFER_PREP
                    if (r.id == h.id or not vehicles or p["care_need"] not in r.me["capabilities"]
                            or r.real_capacity(p["care_need"]) == 0
                            or leaves + scope.travel(h.me["node"], r.me["node"]) > p["transfer_deadline"]):
                        continue
                    before = [act("reserve", hospital=r.id, victims=[p["id"]], kind="firm"),
                              act("transfer_patient", victim=p["id"], hospital=r.id, vehicle=vehicles[0])]
                    out.append(ctx.candidate(h, f"transfer {p['id']} to {r.id}", before=before,
                                             not_before=leaves, cid=f"{h.id}:{p['id']}>{r.id}"))
        return out

    def _option4(self, ctx) -> list[ArbiterCandidate]:
        out = []
        for h in ctx.in_time():
            holds = [x for x in h.me["holds"] if x["victim"] != ctx.vid]
            if holds and ctx.staff_ok(h):
                hold = sorted(holds, key=lambda x: (x["created_at"], x["id"]))[-1]
                before = [act("release_hold", hospital=h.id, hold=hold["id"])]
                out.append(ctx.candidate(h, f"override hold {hold['id']}", before=before))
        return out

    def _option5(self, ctx) -> list[ArbiterCandidate]:
        scope = ctx.scope
        out = []
        for h in ctx.in_time():
            if not ctx.staff_ok(h):
                continue
            for u in sorted({f["unit"] for f in h.me["firm"] if f["unit"]}):
                unit = scope.unit(u)
                if unit["kind"] == "HV" or unit["state"] != "transporting" or unit["hospital"] != h.id:
                    continue  # an inter-hospital transfer is not rerouted to make room
                riders = [scope.victim(x) for x in unit["victims"]]
                if any(r["severity"] == "critical" for r in riders):
                    continue
                for r in scope.hospitals():
                    arrive = scope.t + scope.eta(u, r.me["node"])
                    if (r.id == h.id or r.real_capacity() < len(riders)
                            or any(x["care_need"] not in r.me["capabilities"] for x in riders)
                            or any(x.get("deadline") is not None and x["deadline"] >= scope.t and arrive > x["deadline"]
                                   for x in riders)):
                        continue
                    before = [act("reserve", hospital=r.id, victims=[x["id"] for x in riders], kind="firm"),
                              act("reroute", unit=u, hospital=r.id)]
                    out.append(ctx.candidate(h, f"reroute {u} to {r.id}", before=before, cid=f"{h.id}:{u}>{r.id}"))
        return out

    def _option6(self, ctx) -> list[ArbiterCandidate]:
        scope = ctx.scope
        out = []
        for h in ctx.in_time():
            if h.me["free_slots"] == 0 or ctx.staff_ok(h):
                continue
            kind = "doctor" if staff_free_slots(h.me["doctors"], ctx.need) == 0 else "nurse"
            for lender in scope.hospitals():
                free = lender.free_staff(kind, ctx.need) if lender.id != h.id else []
                if not free or scope.t + scope.travel(lender.me["node"], h.me["node"]) > (ctx.deadline or 10**9):
                    continue
                before = [act("lend_staff", staff=free[0], hospital=h.id, until=scope.t + LOAN_MINUTES)]
                out.append(ctx.candidate(h, f"borrow {free[0]} from {lender.id}", before=before,
                                         cid=f"{h.id}:{free[0]}"))
        return out

    def _option7(self, ctx) -> list[ArbiterCandidate]:
        """The closest capable hospital even if late; one with a slot if there is one. Logged as a loss."""
        capable = sorted((h for h in ctx.scope.hospitals() if ctx.need in h.me["capabilities"]),
                         key=lambda h: (h.real_capacity(ctx.need) == 0, ctx.arrival(h), h.id))
        if not capable:
            return []
        h = capable[0]
        if h.real_capacity(ctx.need) > 0:
            return [ctx.candidate(h, "closest capable hospital, deadline will be missed")]
        return [ArbiterCandidate(id=h.id, hospital=h.id, arrival=ctx.arrival(h), actions=[],
                                 note="closest capable hospital, no slot yet; deadline will be missed")]


class _Context:
    """Arrival times and action building for one escalated victim."""

    def __init__(self, scope: ArbiterScope, esc: Escalation, victim: dict) -> None:
        self.scope, self.esc, self.v = scope, esc, victim
        self.vid, self.need, self.deadline = esc.victim, victim["care_need"], victim.get("deadline")

    def arrival(self, h: HospitalScope) -> int:
        unit = self.scope.unit(self.esc.unit) if self.esc.unit else None
        if unit is not None and unit["state"] == "transporting":
            return self.scope.t + self.scope.eta(unit["id"], h.me["node"])
        return max(self.scope.t, self.esc.ready_at) + self.scope.travel(self.esc.origin_node, h.me["node"])

    def in_time(self) -> list[HospitalScope]:
        return [h for h in self.scope.hospitals() if self.need in h.me["capabilities"]
                and (self.deadline is None or self.arrival(h) <= self.deadline)]

    def staff_ok(self, h: HospitalScope) -> bool:
        return staff_free_slots(h.me["doctors"], self.need) > 0 and staff_free_slots(h.me["nurses"]) > 0

    def destination(self, h: HospitalScope, not_before: int | None) -> list[ProposedAction]:
        if not self.esc.unit:
            return []
        unit = self.scope.unit(self.esc.unit)
        if unit["state"] == "transporting":
            return [] if unit["hospital"] == h.id else [act("reroute", unit=unit["id"], hospital=h.id,
                                                            not_before=not_before)]
        return [ProposedAction(action="set_destination", unit=unit["id"], hospital=h.id, victims=[self.vid],
                               not_before=not_before)]

    def candidate(self, h: HospitalScope, note: str, before: list[ProposedAction] | None = None,
                  not_before: int | None = None, cid: str | None = None) -> ArbiterCandidate:
        reserve = act("reserve", hospital=h.id, victims=[self.vid], kind="firm", not_before=not_before)
        arrival = max(self.arrival(h), not_before or 0)
        return ArbiterCandidate(id=cid or h.id, hospital=h.id, arrival=arrival, note=note,
                                actions=[*(before or []), reserve, *self.destination(h, not_before)])
