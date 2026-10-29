"""Every decision-maker the runner and dashboard can choose, by name."""
from __future__ import annotations

from pathlib import Path

from coordination.llm import LLMClient, load_llm_config
from coordination.orchestrator import AgentPolicy
from policies.base import Policy
from policies.greedy import GreedyPolicy

AGENT_VARIANTS = {"agents": "full", "agents-nonego": "nonego", "agents-noarb": "noarb", "agents-rulehosp": "rulehosp"}
POLICY_NAMES = ["greedy", *AGENT_VARIANTS]
LLM_MODES = ("off", "live", "replay")


def make_llm(mode: str | None = None, replay_from: Path | None = None) -> LLMClient:
    """`off` uses rules only; `live` calls the pinned model; `replay` reads a run's llm_calls.jsonl."""
    cfg = load_llm_config()
    mode = mode or cfg.get("mode", "off")
    if mode == "replay":
        return LLMClient("replay", replay_path=Path(replay_from) / "llm_calls.jsonl")
    return LLMClient(mode, model=cfg.get("model"), temperature=cfg.get("temperature", 0))


def make_policy(name: str, llm: LLMClient | None = None) -> Policy:
    if name == "greedy":
        return GreedyPolicy()
    if name in AGENT_VARIANTS:
        return AgentPolicy(llm or LLMClient("off"), AGENT_VARIANTS[name])
    raise ValueError(f"unknown policy {name!r}; choose from {', '.join(POLICY_NAMES)}")
