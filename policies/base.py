"""Policy interface: called once per minute, before World.step()."""
from __future__ import annotations

from abc import ABC, abstractmethod

from sim.actions import Action
from sim.views import WorldView


class Policy(ABC):
    name: str = "base"

    @abstractmethod
    def decide(self, view: WorldView, events: list[dict]) -> list[Action]:
        """Return actions for this minute. Must not change the world."""

    def drain_log(self) -> list[dict]:
        """Messages and decisions to record in the event stream this minute (agents only)."""
        return []
