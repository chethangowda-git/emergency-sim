"""Agent messages (DESIGN 6) and the bus that delivers them after their time cost (DESIGN 7)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ROUND_MINUTES = 1
ESCALATION_EXTRA = 2

MessageType = Literal[
    "unit_request", "unit_response", "incident_update", "pre_alert", "admission_cfp", "admission_offer",
    "admission_award", "admission_counter", "decline", "mutual_aid_request", "mutual_aid_offer", "swap_request",
    "swap_response", "transfer_request", "transfer_response", "staff_request", "staff_offer", "capacity_alert",
    "escalation", "arbiter_decision", "ack",
]


class Message(BaseModel):
    id: str
    thread_id: str
    round: int = Field(ge=1, le=3)
    sent_at: int
    deliver_at: int
    sender: str
    recipient: str
    type: MessageType
    incident_id: str | None = None
    victim_ids: list[str] = Field(default_factory=list)
    proposed_action: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    decided_by: Literal["rule", "llm", "fallback"] = "rule"


class MessageBus:
    """Deterministic ids and delivery; the world keeps running while messages travel."""

    def __init__(self) -> None:
        self.sent: list[Message] = []
        self._threads = 0

    def new_thread(self) -> str:
        self._threads += 1
        return f"TH{self._threads:03d}"

    def send(self, t: int, sender: str, recipient: str, type_: str, thread: str, round_: int = 1,
             **fields) -> Message:
        delay = ROUND_MINUTES + (ESCALATION_EXTRA if type_ == "escalation" else 0)
        msg = Message(id=f"M{len(self.sent) + 1:04d}", thread_id=thread, round=round_, sent_at=t,
                      deliver_at=t + delay, sender=sender, recipient=recipient, type=type_, **fields)
        self.sent.append(msg)
        return msg

    def due(self, t: int) -> list[Message]:
        return [m for m in self.sent if m.deliver_at == t]
