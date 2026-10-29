"""Shared agent step: rules list the options, the LLM may pick one, a rule covers the rest."""
from __future__ import annotations

from dataclasses import dataclass

from coordination.llm import LLMClient
from coordination.schemas import Choice, DecidedBy


@dataclass
class Picked:
    option_id: str
    decided_by: DecidedBy
    reason: str


def pick(llm: LLMClient, agent: str, purpose: str, instructions: str, payload: dict,
         options: list[str], default: str, default_reason: str) -> Picked:
    """Fast path when only one option fits; otherwise ask the LLM, falling back to `default`."""
    if len(options) == 1:
        return Picked(options[0], "rule", default_reason)
    if not llm.enabled:
        return Picked(default, "rule", default_reason)
    result = llm.ask(agent, purpose, instructions, {**payload, "options": options}, Choice)
    if result.output is not None and result.output.option_id in options:
        return Picked(result.output.option_id, "llm", result.output.reason)
    why = result.error or f"unknown option {result.output.option_id!r}"
    return Picked(default, "fallback", f"{default_reason} (LLM fallback: {why})")
