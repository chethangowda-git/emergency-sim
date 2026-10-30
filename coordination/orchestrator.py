"""Orchestrator (DESIGN 9): one simulated minute of the multi-agent system, as a Policy.

Each minute: world events and due messages become agent triggers, triggered agents decide,
replies are scheduled with their time cost, and agreed actions go back to the simulator.
Agents only ever return actions; the World validates them (golden rule 1).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from coordination.arbiter import ArbiterAgent
from coordination.dispatch import DispatchAgent
from coordination.hospital import HospitalAgent, RuleHospital
from coordination.incident_assessment import IncidentAssessmentAgent
from coordination.llm import LLMClient
from coordination.messages import Message, MessageBus
from coordination.protocols import HospitalProtocols
from coordination.schemas import (AdmissionOffer, AdmissionRequest, ArbiterDecision, Escalation, IncidentReport,
                                  ProposedAction, RequestedVictim)
from coordination.tools import ArbiterScope, DispatchScope, HospitalScope, IncidentScope
from policies.base import Policy
from sim.actions import Action, Redirect, ReleasePatient, Reroute, SetDestination
from sim.rules import SEVERITY_RANK

VARIANTS = ("full", "nonego", "noarb", "rulehosp")
NEGOTIATION_MINUTES = 4      # offers, award, reservation, destination
MAX_ROUNDS = 3
RETRY_MINUTES = 5
REPLAN_EVERY = 10
AT_RISK_MINUTES = 15
FREEING_EVENTS = ("unit_returning", "unit_at_base", "vehicle_lent", "scene_cleared")


@dataclass
class Load:
    """The victims one unit will carry, and the admission negotiation for them."""

    unit: str
    incident: str
    victims: list[str]
    thread: str | None = None
    round: int = 1
    status: str = "new"
    hospital: str | None = None
    exclude: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    offers: dict[str, AdmissionOffer] = field(default_factory=dict)
    cfp_at: int = 0
    retry_at: int | None = None
    aboard: bool = False
    final: bool = False       # the Arbiter ruled; no renegotiation (DESIGN 4.4)


class AgentPolicy(HospitalProtocols, Policy):
    def __init__(self, llm: LLMClient, variant: str = "full") -> None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r}")
        self.variant = variant
        self.name = "agents" if variant == "full" else f"agents-{variant}"
        # Hospital-side negotiation (holds, conditional offers, transfers, staff loans) needs hospital
        # agents; Dispatch-side negotiation (mutual aid, swap) is off only without negotiation.
        self.negotiate = variant in ("full", "noarb")
        self.dispatch_negotiates = variant != "nonego"
        self.llm = llm
        self.bus = MessageBus()
        self.ia, self.arbiter = IncidentAssessmentAgent(llm), ArbiterAgent(llm)
        self.dispatch = DispatchAgent(llm, negotiate=variant != "nonego")
        self.hospital_agents: dict = {}
        self.reports: dict[str, IncidentReport] = {}
        self.extras: dict[str, IncidentReport] = {}
        self.extra_units: dict[str, dict[str, int]] = {}
        self.sent_needs: dict[str, dict[str, int]] = {}
        self.loads: dict[str, Load] = {}
        self.threads: dict[str, dict] = {}
        self.unit_threads: dict[str, str] = {}
        self.queue: list[tuple[int, Action]] = []
        self.cooldown: dict[tuple[str, str], int] = {}
        self.pending_reserve: dict[str, dict] = {}
        self.log: list[dict] = []
        self.replan, self.last_plan = False, -REPLAN_EVERY
        self.last_shortfalls: list[dict] = []
        self.last_response: dict[str, dict] = {}
        self.minute_reserved: dict[str, int] = {}
        self.minute_released: set[str] = set()
        self.minute = -1
        self.t, self.view = 0, None

    # ----- the minute -----

    def decide(self, view, events: list[dict]) -> list[Action]:
        self.t, self.view = view.t, view
        self.minute_reserved, self.minute_released = {}, set()
        if not self.hospital_agents:
            self._make_hospitals(view)
        self._absorb(events)
        for m in self.bus.due(self.t):
            self._route(m)
        self._upkeep()
        self._ia_updates()
        if self.replan or self.t - self.last_plan >= REPLAN_EVERY:
            self._plan()
        self._hospital_initiatives()
        return self._due_actions()

    def drain_log(self) -> list[dict]:
        out, self.log = self.log, []
        return out

    def _make_hospitals(self, view) -> None:
        for h in view.data["hospitals"]:
            agent = (RuleHospital(h["id"]) if self.variant == "rulehosp"
                     else HospitalAgent(h["id"], self.llm, negotiate=self.negotiate))
            self.hospital_agents[h["id"]] = agent

    def _route(self, m: Message) -> None:
        protocol = self.threads[m.thread_id]["protocol"]
        getattr(self, f"_h_{protocol}_{m.type}")(m)

    # ----- messages, decisions and actions -----

    def send(self, sender: str, recipient: str, type_: str, thread: str, round_: int = 1, **fields) -> Message:
        m = self.bus.send(self.t, sender, recipient, type_, thread, round_, **fields)
        self.log.append({**{("msg_type" if k == "type" else k): v for k, v in m.model_dump().items()},
                         "type": "message"})
        return m

    def open_thread(self, protocol: str, **data) -> str:
        th = self.bus.new_thread()
        self.threads[th] = {"protocol": protocol, **data}
        return th

    def decision(self, agent: str, kind: str, decided_by: str, reason: str, **summary) -> None:
        self.log.append({"type": "agent_decision", "agent": agent, "kind": kind, "decided_by": decided_by,
                         "reason": reason, **summary})

    def act(self, *actions, not_before: int | None = None) -> None:
        for a in actions:
            if isinstance(a, ProposedAction):
                when = a.not_before if a.not_before is not None else (not_before or self.t)
                self.queue.append((when, a.to_action()))
            else:
                self.queue.append((not_before or self.t, a))

    def _due_actions(self) -> list[Action]:
        due = [a for when, a in self.queue if when <= self.t]
        self.queue = [(when, a) for when, a in self.queue if when > self.t]
        return due

    # ----- world events -----

    def _absorb(self, events: list[dict]) -> None:
        for e in events:
            kind = e["type"]
            if kind == "incident_reported":
                self._new_incident(e["incident"])
            elif kind in FREEING_EVENTS:
                self.replan = True
            elif kind == "reservation_broken" and e.get("unit"):
                self._capacity_alert(e)
            elif kind == "arrived_no_slot":
                self._renegotiate(e["unit"], exclude=e["hospital"])
            elif kind == "stabilized":
                self._consider_transfer(e["hospital"], e["victim"])
            elif kind == "transfer_started":
                self._after_transfer(e["from_hospital"])
            elif kind == "action_rejected":
                self._rejected(e)

    def _new_incident(self, iid: str) -> None:
        scope = IncidentScope(self.view)
        rule = self.ia.rule_report(scope, iid).units_needed.model_dump()
        report = self.ia.assess(scope, iid)
        self.extras[iid] = report
        self.extra_units[iid] = {k: v - rule[k] for k, v in report.units_needed.model_dump().items()}
        self.sent_needs[iid] = report.units_needed.model_dump()
        self.decision("ia", "assess", report.decided_by, report.note or "rules", incident=iid,
                      units_needed=report.units_needed.model_dump(), flags=report.flags)
        th = self._unit_thread(iid)
        self.send("ia", "dispatch", "unit_request", th, incident_id=iid, proposed_action=report.model_dump())

    def _unit_thread(self, iid: str) -> str:
        """One unit-request thread per incident: the request, its updates and Dispatch's responses."""
        if iid not in self.unit_threads:
            self.unit_threads[iid] = self.open_thread("unit", incident=iid)
        return self.unit_threads[iid]

    def _ia_updates(self) -> None:
        """Incident Assessment re-reports when victims leave the scene (incident_update), keeping the
        extra units its LLM judgment asked for at first assessment."""
        scope = IncidentScope(self.view)
        for iid, first in self.extras.items():
            if not scope.at_scene(iid):
                continue
            rule = self.ia.rule_report(scope, iid)
            needed = {k: v + self.extra_units[iid][k] for k, v in rule.units_needed.model_dump().items()}
            if needed == self.sent_needs[iid]:
                continue
            self.sent_needs[iid] = needed
            report = rule.model_copy(update={"units_needed": rule.units_needed.model_validate(needed),
                                             "flags": first.flags, "note": first.note})
            th = self._unit_thread(iid)
            self.send("ia", "dispatch", "incident_update", th, incident_id=iid, proposed_action=report.model_dump())

    def _rejected(self, e: dict) -> None:
        unit = e.get("unit")
        if e["action"] in ("set_destination", "reroute") and unit in self.loads:
            load = self.loads[unit]
            if load.final:
                load.status = "arbiter_wait"
            else:
                self._retry(load, f"{e['action']} rejected: {e['reason']}")
        elif e["action"] in ("send_to_scene", "redirect"):
            self.loads.pop(unit, None)
            self.replan = True

    # ----- unit requests and planning -----

    def _h_unit_unit_request(self, m: Message) -> None:
        self.reports[m.incident_id] = IncidentReport.model_validate(m.proposed_action)
        self.replan = True

    def _h_unit_incident_update(self, m: Message) -> None:
        self.reports[m.incident_id] = IncidentReport.model_validate(m.proposed_action)
        self.replan = True

    def _h_unit_unit_response(self, m: Message) -> None:
        pass

    def _plan(self) -> None:
        self.replan, self.last_plan = False, self.t
        scope = IncidentScope(self.view)
        open_ids = [i for i in self.reports if scope.at_scene(i)]
        if not open_ids:
            return
        plan = self.dispatch.plan_units(DispatchScope(self.view), [self.reports[i] for i in open_ids])
        shortfalls = [{"incident": s.incident, "chosen": s.chosen} for s in plan.shortfalls]
        if not plan.assignments and shortfalls == self.last_shortfalls:
            return
        self.last_shortfalls = shortfalls
        self.decision("dispatch", "plan_units", plan.decided_by, plan.reason, ranking=plan.ranking,
                      assignments=[a.model_dump() for a in plan.assignments], shortfalls=shortfalls)
        for p in plan.proposed_actions:
            if p.action == "send_to_scene":
                self.act(p)
                self._new_load(p.unit, p.incident)
            elif p.action == "assign_team":
                self.act(p)
        for s in plan.shortfalls:
            self._shortfall(s)
        for iid in sorted({a.incident for a in plan.assignments} | {s.incident for s in plan.shortfalls}):
            sent = [a.unit for a in plan.assignments if a.incident == iid]
            short = next((s for s in plan.shortfalls if s.incident == iid), None)
            body = {"sent": sent, "shortfall": short.missing.model_dump() if short else None,
                    "plan": short.chosen if short else None}
            if not sent and self.last_response.get(iid) == body:
                continue
            self.last_response[iid] = body
            th = self._unit_thread(iid)
            self.send("dispatch", "ia", "unit_response", th, incident_id=iid, proposed_action=body)

    def _shortfall(self, s) -> None:
        chosen = next(o for o in s.options if o.id == s.chosen)
        if chosen.plan == "redirect":
            self._redirect(chosen.unit, s.incident)
        elif chosen.plan == "swap" and self._cool("swap", s.incident):
            self._ask_vehicle("swap", s.incident, unit=chosen.unit)
        elif chosen.plan == "mutual_aid" and self._cool("mutual_aid", s.incident):
            self._ask_vehicle("mutual_aid", s.incident)
        if s.missing.ALS and self.negotiate:
            self._pre_alert(s.incident)

    def _cool(self, kind: str, key: str, minutes: int = 15) -> bool:
        if self.cooldown.get((kind, key), -1) > self.t:
            return False
        self.cooldown[(kind, key)] = self.t + minutes
        return True

    def _redirect(self, unit: str, iid: str) -> None:
        self.act(Redirect(unit, iid, by="dispatch"))
        self.loads.pop(unit, None)
        self._new_load(unit, iid)
        self.replan = True

    # ----- loads and admission (Contract Net, DESIGN 5.2) -----

    def _claimed(self) -> set[str]:
        return {v for load in self.loads.values() for v in load.victims}

    def _new_load(self, unit: str, iid: str) -> None:
        u = self._unit(unit)
        waiting = [v for v in IncidentScope(self.view).at_scene(iid)
                   if v["status"] == "waiting" and v["id"] not in self._claimed()]
        waiting.sort(key=lambda v: (SEVERITY_RANK[v["severity"]], v["deadline"] or 10**9, v["id"]))
        crit = [v for v in waiting if v["severity"] == "critical"]
        others = [v for v in waiting if v["severity"] != "critical"]
        take = crit[:1] if u["kind"] == "ALS" and crit else others[:2]
        if take:
            self.loads[unit] = Load(unit=unit, incident=iid, victims=[v["id"] for v in take])
            self._start_admission(self.loads[unit])

    def _unit(self, uid: str) -> dict:
        return next(u for u in self.view.data["units"] if u["id"] == uid)

    def _incident(self, iid: str) -> dict:
        return next(i for i in self.view.data["incidents"] if i["id"] == iid)

    def _victim(self, vid: str) -> dict:
        return ArbiterScope(self.view).victim(vid)

    def _request(self, load: Load) -> AdmissionRequest:
        inc, u = self._incident(load.incident), self._unit(load.unit)
        victims = [RequestedVictim(id=v["id"], severity=v["severity"], care_need=v["care_need"],
                                   deadline=v.get("deadline")) for v in map(self._victim, load.victims)]
        return AdmissionRequest(incident=load.incident, origin_node=inc["node"], ready_at=self._ready_at(load),
                                victims=victims, unit=load.unit, aboard=load.aboard or u["state"] == "transporting")

    def _ready_at(self, load: Load) -> int:
        """Rule estimate of when the load can leave the scene (DESIGN 3.11)."""
        inc, u = self._incident(load.incident), self._unit(load.unit)
        if u["state"] in ("transporting", "at_hospital_no_slot"):
            return self.t
        arrive = u["on_scene_since"] if u["state"] == "on_scene" else self.t + DispatchScope(self.view).eta(
            load.unit, inc["node"])
        if inc["cleared"] or not inc["requires"]:
            return max(self.t, arrive + 5)
        clear = inc["cleared_at"] if inc["cleared_at"] is not None else arrive + 10
        return max(self.t, max(arrive, clear) + 5)

    def _earliest(self, req: AdmissionRequest, hospitals: list[dict]) -> int | None:
        scope = DispatchScope(self.view)
        times = [scope.t + scope.eta(req.unit, h["node"]) if req.aboard
                 else req.ready_at + scope.travel(req.origin_node, h["node"]) for h in hospitals]
        return min(times, default=None)

    def _start_admission(self, load: Load) -> None:
        req = self._request(load)
        needs = {v.care_need for v in req.victims}
        hospitals = [h for h in DispatchScope(self.view).hospitals()
                     if h["id"] not in load.exclude and needs & set(h["capabilities"])]
        earliest = self._earliest(req, hospitals)
        deadline = min((v.deadline for v in req.victims if v.deadline is not None), default=None)
        if load.round == 1:
            load.thread = self.open_thread("admission", unit=load.unit)
        if deadline is not None and earliest is not None and 0 <= deadline - earliest < NEGOTIATION_MINUTES:
            self._escalate(load, "no_time")
            return
        kind = "admission_cfp" if load.round == 1 else "admission_counter"
        for h in hospitals:
            if load.round == 1 and any(v.severity == "critical" for v in req.victims):
                self.send("dispatch", h["id"], "pre_alert", load.thread, load.round, incident_id=load.incident,
                          victim_ids=load.victims)
            self.send("dispatch", h["id"], kind, load.thread, load.round, incident_id=load.incident,
                      victim_ids=load.victims, proposed_action=req.model_dump())
        load.status, load.cfp_at, load.offers = "awaiting_offers", self.t, {}
        load.expected = [h["id"] for h in hospitals]

    def _h_admission_pre_alert(self, m: Message) -> None:
        pass  # informational: the call for proposals in the same round carries the details

    def _h_admission_admission_cfp(self, m: Message) -> None:
        agent = self.hospital_agents[m.recipient]
        offer = agent.respond_admission(HospitalScope(self.view, m.recipient),
                                        AdmissionRequest.model_validate(m.proposed_action))
        self.decision(m.recipient, "admission_offer", offer.decided_by, offer.reason, accept=offer.accept,
                      thread=m.thread_id)
        self.send(m.recipient, "dispatch", "admission_offer", m.thread_id, m.round, incident_id=m.incident_id,
                  victim_ids=offer.accept, proposed_action=offer.model_dump(), reason=offer.reason,
                  decided_by=offer.decided_by)

    _h_admission_admission_counter = _h_admission_admission_cfp

    def _h_admission_admission_offer(self, m: Message) -> None:
        load = self._load_for(m.thread_id)
        if load is None or load.status != "awaiting_offers" or m.round != load.round:
            return
        load.offers[m.sender] = AdmissionOffer.model_validate(m.proposed_action)
        if set(load.offers) >= set(load.expected):
            self._award(load)

    def _load_for(self, thread: str) -> Load | None:
        unit = self.threads[thread].get("unit")
        load = self.loads.get(unit)
        return load if load is not None and load.thread == thread else None

    def _award(self, load: Load) -> None:
        req = self._request(load)
        offers = [load.offers[h] for h in sorted(load.offers)]
        d = self.dispatch.award(DispatchScope(self.view), req, offers)
        self.decision("dispatch", "award", d.decided_by, d.reason, thread=load.thread,
                      awards=[a.model_dump() for a in d.awards], escalate=d.escalate)
        lead = load.victims[0]
        mine = next((a for a in d.awards if lead in a.victims), None)
        if mine is None:
            self._no_award(load, req, escalate=lead in d.escalate)
            return
        if set(mine.victims) != set(load.victims):
            load.victims = [v for v in load.victims if v in mine.victims]
            self.replan = True
        offer = load.offers[mine.hospital]
        conditional = [v for c in offer.conditional for v in c.victims if v in load.victims]
        load.status, load.hospital = "awarded", mine.hospital
        self.send("dispatch", mine.hospital, "admission_award", load.thread, load.round, incident_id=load.incident,
                  victim_ids=load.victims, proposed_action={
                      "victims": load.victims, "conditional": conditional,
                      "actions": [a.model_dump() for a in offer.proposed_actions]}, reason=d.reason,
                  decided_by=d.decided_by)

    def _no_award(self, load: Load, req: AdmissionRequest, escalate: bool) -> None:
        if escalate:
            self._escalate(load, "no_offer_in_time")
        elif load.round < MAX_ROUNDS:
            load.round += 1
            self._start_admission(load)
        elif self._at_risk(req):
            self._escalate(load, "rounds_exhausted")
        else:
            self._retry(load, "no hospital accepted after 3 rounds")

    def _at_risk(self, req: AdmissionRequest) -> bool:
        earliest = self._earliest(req, DispatchScope(self.view).hospitals())
        return any(v.deadline is not None and earliest is not None and v.deadline - earliest < AT_RISK_MINUTES
                   for v in req.victims)

    def _retry(self, load: Load, why: str) -> None:
        load.status, load.retry_at, load.round, load.hospital = "retry", self.t + RETRY_MINUTES, 1, None
        self.decision("dispatch", "retry", "rule", why, unit=load.unit, victims=load.victims)

    def _h_admission_ack(self, m: Message) -> None:
        """The hospital placed the firm reservation: set the destination (or reroute)."""
        load = self._load_for(m.thread_id)
        if load is None or load.hospital != m.sender:
            return
        self._send_to_hospital(load)

    def _send_to_hospital(self, load: Load) -> None:
        u = self._unit(load.unit)
        if u["state"] == "transporting":
            if u["reroutes"] >= 1:
                self._escalate(load, "reroute_needed")
                return
            self.act(Reroute(load.unit, load.hospital, by="dispatch"))
        elif u["state"] in ("to_scene", "on_scene", "at_hospital_no_slot") and (
                not u["hospital"] or u["state"] == "at_hospital_no_slot"):
            self.act(SetDestination(load.unit, load.hospital, tuple(load.victims)))
        else:
            load.status = "reroute_pending"
            return
        load.status = "destination_set"

    def _h_admission_decline(self, m: Message) -> None:
        load = self._load_for(m.thread_id)
        if load is not None and load.hospital == m.sender:
            self._retry(load, m.reason)

    # ----- reroute after a broken reservation (DESIGN 5.7) -----

    def _capacity_alert(self, e: dict) -> None:
        th = self.open_thread("alert", unit=e["unit"])
        self.send(e["hospital"], "dispatch", "capacity_alert", th, victim_ids=[e["victim"]],
                  reason=f"walk-in {e['walk_in']} took the slot reserved for {e['victim']}")

    def _h_alert_capacity_alert(self, m: Message) -> None:
        self._renegotiate(self.threads[m.thread_id]["unit"], exclude=m.sender)

    def _renegotiate(self, unit: str, exclude: str) -> None:
        load = self.loads.get(unit)
        if load is None or load.status in ("awaiting_offers", "escalated"):
            return
        load.exclude = sorted({*load.exclude, exclude})
        load.round, load.status, load.hospital = 1, "new", None
        load.aboard = self._unit(unit)["state"] in ("transporting", "at_hospital_no_slot")
        self._start_admission(load)

    # ----- upkeep -----

    def _upkeep(self) -> None:
        for unit in sorted(self.loads):
            load = self.loads[unit]
            u = self._unit(unit)
            if u["state"] in ("idle", "returning", "handover") or u["incident"] != load.incident and not u["victims"]:
                del self.loads[unit]
                continue
            if load.status == "awaiting_offers" and self.t >= load.cfp_at + 2:
                self._award(load)   # no reply within the round counts as a decline
            elif load.status == "retry" and self.t >= load.retry_at:
                self._start_admission(load)
            elif load.status == "reroute_pending":
                self._send_to_hospital(load)
            elif load.status == "arbiter_wait":
                self._arbiter_wait(load)

    # ----- the Arbiter (DESIGN 4.4) -----

    def _escalate(self, load: Load, trigger: str) -> None:
        load.status = "escalated"
        if self.variant == "noarb":
            self._apply_arbiter(load, self.arbiter.least_bad(ArbiterScope(self.view), self._escalation(load, trigger)))
            return
        self.send("dispatch", "arbiter", "escalation", load.thread, load.round, incident_id=load.incident,
                  victim_ids=load.victims, reason=trigger)

    def _escalation(self, load: Load, trigger: str) -> Escalation:
        u = self._unit(load.unit)
        return Escalation(victim=load.victims[0], trigger=trigger, unit=load.unit,
                          origin_node=u["node"] if u["state"] == "at_hospital_no_slot" else self._incident(load.incident)["node"],
                          ready_at=self._ready_at(load))

    def _h_admission_escalation(self, m: Message) -> None:
        load = self._load_for(m.thread_id)
        if load is None:
            return
        d = self.arbiter.resolve(ArbiterScope(self.view), self._escalation(load, m.reason))
        self._apply_arbiter(load, d)
        for party in sorted({"dispatch", *(a.hospital for a in d.proposed_actions if a.hospital)}):
            self.send("arbiter", party, "arbiter_decision", load.thread, load.round, incident_id=load.incident,
                      victim_ids=[d.victim], proposed_action=d.model_dump(), reason=d.reason, decided_by=d.decided_by)

    def _apply_arbiter(self, load: Load, d: ArbiterDecision) -> None:
        self.decision("arbiter", "resolve", d.decided_by, d.reason, option=d.option, hospital=d.hospital,
                      victim=d.victim, loss=d.loss)
        self.act(*d.proposed_actions)
        for a in d.proposed_actions:
            if a.action == "reserve" and a.kind == "firm":
                self.minute_reserved[a.hospital] = self.minute_reserved.get(a.hospital, 0) + len(a.victims)
        load.final, load.hospital = True, d.hospital
        if d.victim in load.victims and len(load.victims) > 1:
            load.victims = [d.victim]
            self.replan = True
        moves = [a for a in d.proposed_actions if a.action in ("set_destination", "reroute") and a.unit == load.unit]
        if moves:
            load.status = "destination_set"
        elif d.hospital:
            load.status = "arbiter_wait"
        else:
            self._retry(load, "no hospital can treat this care need")

    def _arbiter_wait(self, load: Load) -> None:
        """Least-bad ruling with no slot free yet: go to the first capable hospital that has room,
        closest first. Binding, so no renegotiation (DESIGN 4.4, option 7)."""
        req = self._request(load)
        needs = {v.care_need for v in req.victims}
        scope = DispatchScope(self.view)
        capable = [h for h in self.view.data["hospitals"] if needs <= set(h["capabilities"])]
        room = [h for h in capable if h["free_slots"] - self.minute_reserved.get(h["id"], 0) >= len(load.victims)]
        if not room:
            room = self._release_for(capable, len(load.victims))
        if not room:
            return
        arrive = lambda h: (scope.t + scope.eta(load.unit, h["node"]) if req.aboard
                            else req.ready_at + scope.travel(req.origin_node, h["node"]))
        best = min(room, key=lambda h: (arrive(h), h["id"]))
        load.hospital = best["id"]
        self.minute_reserved[best["id"]] = self.minute_reserved.get(best["id"], 0) + len(load.victims)
        self._send_to_hospital(load)

    def _release_for(self, capable: list[dict], count: int) -> list[dict]:
        """Arbiter option 2 while waiting: release ready-for-release patients to make room."""
        for h in capable:
            ready = sorted((p for p in h["patients"] if p["status"] == "ready_for_release"
                            and p["id"] not in self.minute_released), key=lambda p: (p["ready_at"], p["id"]))
            if len(ready) >= count - h["free_slots"] + self.minute_reserved.get(h["id"], 0):
                take = ready[: max(0, count - h["free_slots"] + self.minute_reserved.get(h["id"], 0))]
                self.act(*[ReleasePatient(p["id"], by="arbiter") for p in take])
                self.minute_released |= {p["id"] for p in take}
                self.decision("arbiter", "release", "rule", f"release {', '.join(p['id'] for p in take)} to make room",
                              hospital=h["id"])
                return [{**h, "free_slots": h["free_slots"] + len(take)}]
        return []

    def _h_admission_arbiter_decision(self, m: Message) -> None:
        pass  # binding decisions were applied when made; the message informs the parties
