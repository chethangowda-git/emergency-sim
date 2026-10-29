"""Typed agent outputs (Pydantic). Phase 2 agents return these decisions; Phase 3 turns them
into messages. Proposed actions convert into simulator actions, which the World still validates."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from sim import actions as sa

DecidedBy = Literal["rule", "llm", "fallback"]
ActionName = Literal["send_to_scene", "set_destination", "assign_team", "reserve", "release_hold",
                     "release_patient", "transfer_patient", "lend_vehicle", "lend_staff", "redirect", "reroute"]

REQUIRED = {
    "send_to_scene": ("unit", "incident"), "set_destination": ("unit", "hospital", "victims"),
    "assign_team": ("unit", "incident"), "reserve": ("hospital", "victims", "kind", "by"),
    "release_hold": ("hospital", "hold", "by"), "release_patient": ("victim", "by"),
    "transfer_patient": ("victim", "hospital", "vehicle", "by"), "lend_vehicle": ("vehicle", "until", "by"),
    "lend_staff": ("staff", "hospital", "until", "by"), "redirect": ("unit", "incident", "by"),
    "reroute": ("unit", "hospital", "by"),
}


class ProposedAction(BaseModel):
    """One simulator action an agent proposes. `not_before` delays it (e.g. until a transfer has left)."""

    action: ActionName
    unit: str | None = None
    incident: str | None = None
    hospital: str | None = None
    victims: list[str] = Field(default_factory=list)
    victim: str | None = None
    kind: Literal["firm", "hold"] | None = None
    hold: str | None = None
    vehicle: str | None = None
    staff: str | None = None
    until: int | None = None
    by: str | None = None
    not_before: int | None = None

    @model_validator(mode="after")
    def _required(self) -> ProposedAction:
        missing = [f for f in REQUIRED[self.action]
                   if getattr(self, f) is None or (f == "victims" and self.action == "set_destination" and not self.victims)]
        if missing:
            raise ValueError(f"{self.action} needs {', '.join(missing)}")
        return self

    def to_action(self) -> sa.Action:
        a, v = self.action, tuple(self.victims)
        builders = {
            "send_to_scene": lambda: sa.SendToScene(self.unit, self.incident),
            "set_destination": lambda: sa.SetDestination(self.unit, self.hospital, v),
            "assign_team": lambda: sa.AssignTeam(self.unit, self.incident),
            "reserve": lambda: sa.Reserve(self.hospital, v, self.kind, by=self.by),
            "release_hold": lambda: sa.ReleaseHold(self.hospital, self.hold, by=self.by),
            "release_patient": lambda: sa.ReleasePatient(self.victim, by=self.by),
            "transfer_patient": lambda: sa.TransferPatient(self.victim, self.hospital, self.vehicle, by=self.by),
            "lend_vehicle": lambda: sa.LendVehicle(self.vehicle, self.until, by=self.by),
            "lend_staff": lambda: sa.LendStaff(self.staff, self.hospital, self.until, by=self.by),
            "redirect": lambda: sa.Redirect(self.unit, self.incident, by=self.by),
            "reroute": lambda: sa.Reroute(self.unit, self.hospital, by=self.by),
        }
        return builders[a]()


class UnitCounts(BaseModel):
    ALS: int = 0
    BLS: int = 0
    FIRE: int = 0
    RESCUE: int = 0


# ----- LLM outputs: the LLM only judges; rules supply every number and option -----

class Choice(BaseModel):
    option_id: str = Field(max_length=40)
    reason: str = Field(max_length=200)


class IAJudgment(BaseModel):
    flags: list[str] = Field(default_factory=list, max_length=5)
    extra_als: int = Field(default=0, ge=0, le=2)
    extra_bls: int = Field(default=0, ge=0, le=2)
    note: str = Field(default="", max_length=200)


class ShortfallPick(BaseModel):
    incident: str
    option_id: str


class DispatchJudgment(BaseModel):
    ranking: list[str]
    shortfall_plans: list[ShortfallPick] = Field(default_factory=list)
    reason: str = Field(max_length=200)


# ----- Incident Assessment -----

class IncidentReport(BaseModel):
    incident: str
    response_types: list[Literal["critical_care", "rescue_first", "transport"]]
    units_needed: UnitCounts
    minutes_left: int | None
    at_risk: int
    care_counts: dict[str, dict[str, int]]
    flags: list[str] = Field(default_factory=list)
    note: str = ""
    decided_by: DecidedBy


# ----- Dispatch -----

class Assignment(BaseModel):
    unit: str
    incident: str
    eta: int


class ShortfallOption(BaseModel):
    id: str
    plan: Literal["queue", "wait", "redirect", "mutual_aid", "swap"]
    unit: str | None = None
    expected_delay: int | None = None
    detail: str = ""


class Shortfall(BaseModel):
    incident: str
    missing: UnitCounts
    options: list[ShortfallOption]
    chosen: str


class UnitPlan(BaseModel):
    ranking: list[str]
    assignments: list[Assignment]
    shortfalls: list[Shortfall]
    proposed_actions: list[ProposedAction]
    decided_by: DecidedBy
    reason: str = ""


class RequestedVictim(BaseModel):
    id: str
    severity: str
    care_need: str
    deadline: int | None


class AdmissionRequest(BaseModel):
    incident: str
    origin_node: str
    ready_at: int
    victims: list[RequestedVictim]
    others_in_time: bool = True


class Conditional(BaseModel):
    victims: list[str]
    after: Literal["release", "transfer"]
    after_minute: int | None = None


class Decline(BaseModel):
    victims: list[str]
    reason: str


class AdmissionOffer(BaseModel):
    hospital: str
    mood: Literal["eager", "normal", "protective"]
    accept: list[str]
    conditional: list[Conditional] = Field(default_factory=list)
    decline: list[Decline] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    decided_by: DecidedBy
    reason: str = ""


class Award(BaseModel):
    hospital: str
    victims: list[str]
    arrival: int


class AwardDecision(BaseModel):
    incident: str
    awards: list[Award]
    escalate: list[str]
    proposed_actions: list[ProposedAction]
    decided_by: DecidedBy
    reason: str = ""


# ----- Hospital replies -----

class TransferRequest(BaseModel):
    victim: str
    care_need: str
    from_hospital: str
    sender_capabilities: list[str]


class TransferResponse(BaseModel):
    hospital: str
    accept: bool
    reason: str
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    decided_by: DecidedBy


class StaffRequest(BaseModel):
    from_hospital: str
    kind: Literal["doctor", "nurse"]
    skill: str = "general"
    minutes: int = 90


class LoanResponse(BaseModel):
    hospital: str
    answer: Literal["send_now", "send_later", "decline"]
    resource: str | None = None
    at_minute: int | None = None
    reason: str
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    decided_by: DecidedBy


class VehicleRequest(BaseModel):
    incident: str
    until: int


# ----- Arbiter -----

class Escalation(BaseModel):
    victim: str
    trigger: Literal["no_offer_in_time", "rounds_exhausted", "reroute_needed", "urgent_loan", "no_time"]
    unit: str | None = None
    origin_node: str
    ready_at: int
    target_hospital: str | None = None


class ArbiterCandidate(BaseModel):
    id: str
    hospital: str
    arrival: int
    actions: list[ProposedAction]
    note: str = ""


class ArbiterDecision(BaseModel):
    victim: str
    option: int = Field(ge=1, le=7)
    option_name: str
    hospital: str | None
    arrival: int | None
    meets_deadline: bool
    loss: bool
    proposed_actions: list[ProposedAction]
    decided_by: DecidedBy
    reason: str = ""
