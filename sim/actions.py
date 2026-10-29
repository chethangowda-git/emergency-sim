"""Actions a policy may return (DESIGN 3.9, Phase 1 subset). The World validates them."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SendToScene:
    unit: str
    incident: str
    name: str = "send_to_scene"

    def to_dict(self) -> dict:
        return {"action": self.name, "unit": self.unit, "incident": self.incident}


@dataclass(frozen=True)
class SetDestination:
    unit: str
    hospital: str
    victims: tuple[str, ...]
    name: str = "set_destination"

    def to_dict(self) -> dict:
        return {"action": self.name, "unit": self.unit, "hospital": self.hospital,
                "victims": list(self.victims)}


@dataclass(frozen=True)
class AssignTeam:
    team: str
    incident: str
    name: str = "assign_team"

    def to_dict(self) -> dict:
        return {"action": self.name, "team": self.team, "incident": self.incident}


# ----- agent-only actions (DESIGN 3.9). `by` is the requesting agent: H1..H3, dispatch, arbiter. -----

@dataclass(frozen=True)
class Reserve:
    """Firm for the given victims, or a hold: for one expected critical, or general if no victim."""

    hospital: str
    victims: tuple[str, ...]
    kind: str
    by: str
    name: str = "reserve"

    def to_dict(self) -> dict:
        return {"action": self.name, "hospital": self.hospital, "victims": list(self.victims),
                "kind": self.kind, "by": self.by}


@dataclass(frozen=True)
class ReleaseHold:
    hospital: str
    hold: str
    by: str
    name: str = "release_hold"

    def to_dict(self) -> dict:
        return {"action": self.name, "hospital": self.hospital, "hold": self.hold, "by": self.by}


@dataclass(frozen=True)
class ReleasePatient:
    victim: str
    by: str
    name: str = "release_patient"

    def to_dict(self) -> dict:
        return {"action": self.name, "victim": self.victim, "by": self.by}


@dataclass(frozen=True)
class TransferPatient:
    victim: str
    to_hospital: str
    vehicle: str
    by: str
    name: str = "transfer_patient"

    def to_dict(self) -> dict:
        return {"action": self.name, "victim": self.victim, "to_hospital": self.to_hospital,
                "vehicle": self.vehicle, "by": self.by}


@dataclass(frozen=True)
class LendVehicle:
    vehicle: str
    until: int
    by: str
    name: str = "lend_vehicle"

    def to_dict(self) -> dict:
        return {"action": self.name, "vehicle": self.vehicle, "until": self.until, "by": self.by}


@dataclass(frozen=True)
class LendStaff:
    staff: str
    to_hospital: str
    until: int
    by: str
    name: str = "lend_staff"

    def to_dict(self) -> dict:
        return {"action": self.name, "staff": self.staff, "to_hospital": self.to_hospital,
                "until": self.until, "by": self.by}


@dataclass(frozen=True)
class Redirect:
    unit: str
    incident: str
    by: str
    name: str = "redirect"

    def to_dict(self) -> dict:
        return {"action": self.name, "unit": self.unit, "incident": self.incident, "by": self.by}


@dataclass(frozen=True)
class Reroute:
    unit: str
    hospital: str
    by: str
    name: str = "reroute"

    def to_dict(self) -> dict:
        return {"action": self.name, "unit": self.unit, "hospital": self.hospital, "by": self.by}


AgentAction = (Reserve | ReleaseHold | ReleasePatient | TransferPatient | LendVehicle | LendStaff
               | Redirect | Reroute)
Action = SendToScene | SetDestination | AssignTeam | AgentAction
