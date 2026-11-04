"""What the rule would have answered to a recorded LLM call (for explaining and diagnosing; no decisions)."""
from __future__ import annotations

from coordination.dispatch import rule_ranking
from coordination.hospital import HospitalAgent
from coordination.schemas import IncidentReport


def rule_default(agent: str, purpose: str, payload: dict) -> str | None:
    """The option id the rule takes for a pick-style call, from the call's own input."""
    opts = payload.get("options") or []
    if purpose == "admission":
        return HospitalAgent._admission_default(payload["mood"], opts)
    if purpose == "staff":
        return "send_now" if "send_now" in opts else opts[0]
    return {"award": "earliest", "transfer": "accept", "resolve": opts[0] if opts else None}.get(purpose)


def rule_ranking_of(payload: dict) -> list[str]:
    """Dispatch's rule ranking for a recorded plan_units call: fewest minutes left first."""
    return rule_ranking([IncidentReport.model_validate(r) for r in payload["reports"]])
