"""Incident Assessment (DESIGN 4.1): rules build the report; the LLM adds context from the description."""
from __future__ import annotations

import math

from coordination import prompts
from coordination.llm import LLMClient
from coordination.schemas import IAJudgment, IncidentReport, UnitCounts
from coordination.tools import IncidentScope

AGENT = "ia"


def units_needed(victims: list[dict], requires: str | None, team: str | None, cleared: bool) -> UnitCounts:
    crit = sum(1 for v in victims if v["severity"] == "critical")
    others = len(victims) - crit
    needs_team = bool(requires) and not team and not cleared
    return UnitCounts(ALS=crit, BLS=math.ceil(others / 2),
                      FIRE=int(needs_team and requires == "fire"), RESCUE=int(needs_team and requires == "rescue"))


def response_types(victims: list[dict], requires: str | None) -> list[str]:
    types = ["critical_care" if any(v["severity"] == "critical" for v in victims) else "transport"]
    if requires:
        types.append("rescue_first")
    return types


def urgency(victims: list[dict], t: int) -> tuple[int | None, int]:
    """Minutes left to the earliest deadline among waiting victims, and how many share it."""
    deadlines = [v["deadline"] for v in victims if v["deadline"] is not None]
    if not deadlines:
        return None, 0
    first = min(deadlines)
    return first - t, deadlines.count(first)


def care_counts(victims: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for v in sorted(victims, key=lambda v: v["id"]):
        out.setdefault(v["care_need"], {}).setdefault(v["severity"], 0)
        out[v["care_need"]][v["severity"]] += 1
    return out


class IncidentAssessmentAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def rule_report(self, scope: IncidentScope, iid: str) -> IncidentReport:
        inc = scope.incident(iid)
        waiting = scope.at_scene(iid)
        minutes_left, at_risk = urgency(waiting, scope.t)
        return IncidentReport(incident=iid, response_types=response_types(waiting, inc["requires"]),
                              units_needed=units_needed(waiting, inc["requires"], inc["team"], inc["cleared"]),
                              minutes_left=minutes_left, at_risk=at_risk, care_counts=care_counts(waiting),
                              decided_by="rule")

    def assess(self, scope: IncidentScope, iid: str) -> IncidentReport:
        report = self.rule_report(scope, iid)
        description = scope.incident(iid)["description"]
        if not description or not self.llm.enabled:
            return report
        payload = {"incident": scope.incident(iid), "rule_report": report.model_dump()}
        result = self.llm.ask(AGENT, "assess", prompts.INCIDENT, payload, IAJudgment)
        if result.output is None:
            return report.model_copy(update={"decided_by": "fallback", "note": f"LLM fallback: {result.error}"})
        j: IAJudgment = result.output
        needed = report.units_needed.model_copy(update={"ALS": report.units_needed.ALS + j.extra_als,
                                                        "BLS": report.units_needed.BLS + j.extra_bls})
        return report.model_copy(update={"units_needed": needed, "flags": j.flags, "note": j.note,
                                         "decided_by": "llm"})
