"""Every decision-maker the runner and dashboard can choose, by name."""
from __future__ import annotations

import json
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


def llm_guard_for(llm: LLMClient | None) -> bool:
    """The guard setting of a run: a replay keeps the recorded run's (off for runs recorded before
    the guard existed), anything else follows config/llm.json (DESIGN 12.8)."""
    if llm is not None and llm.mode == "replay" and llm.replay_path is not None:
        src = Path(llm.replay_path).parent / "config.json"
        if src.exists():
            return bool((json.loads(src.read_text()).get("llm") or {}).get("guard", False))
    return bool(load_llm_config().get("llm_guard", True))


def make_policy(name: str, llm: LLMClient | None = None, guard: bool | None = None) -> Policy:
    if name == "greedy":
        return GreedyPolicy()
    if name in AGENT_VARIANTS:
        return AgentPolicy(llm or LLMClient("off"), AGENT_VARIANTS[name],
                           guard=llm_guard_for(llm) if guard is None else guard)
    raise ValueError(f"unknown policy {name!r}; choose from {', '.join(POLICY_NAMES)}")
