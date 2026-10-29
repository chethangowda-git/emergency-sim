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


Action = SendToScene | SetDestination | AssignTeam
