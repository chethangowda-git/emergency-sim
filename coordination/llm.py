"""LLM wrapper with record and replay (DESIGN 12).

Modes:
  off     never calls a model; agents use their rules.
  fake    a Python callable answers (tests); calls are recorded like real ones.
  replay  answers come from a recorded llm_calls.jsonl; a missing answer is a logged fallback.
  live    calls the OpenAI Agents SDK with the pinned model at temperature 0 and records every call.

Every answer is validated against a Pydantic schema; anything invalid is reported as an
error so the calling agent falls back to its rule.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from sim.config import ROOT

LLM_CONFIG = ROOT / "config" / "llm.json"
MODES = ("off", "fake", "replay", "live")

FakeFn = Callable[[str, str, dict], Any]
Runner = Callable[[Any, str], Any]


@dataclass
class LLMResult:
    output: BaseModel | None
    error: str | None = None


def call_key(agent: str, purpose: str, payload: dict) -> str:
    """Deterministic key for an LLM input, used to match recorded answers."""
    text = json.dumps({"agent": agent, "purpose": purpose, "payload": payload}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_llm_config(path: Path = LLM_CONFIG) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"mode": "off", "model": None, "temperature": 0}


def _sdk_runner(agent, text: str):
    from agents import Runner as SDKRunner

    return SDKRunner.run_sync(agent, text)


class LLMClient:
    def __init__(self, mode: str = "off", model: str | None = None, *, fake: FakeFn | None = None,
                 replay_path: Path | None = None, runner: Runner | None = None, temperature: float = 0.0) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown LLM mode {mode!r}")
        if mode == "fake" and fake is None:
            raise ValueError("fake mode needs a fake answer function")
        if mode == "live" and not model:
            raise ValueError("live mode needs a pinned model in config/llm.json")
        if mode == "live" and runner is None and not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("live mode needs OPENAI_API_KEY")
        self.mode, self.model, self.temperature = mode, model, temperature
        self._fake, self._runner = fake, runner or _sdk_runner
        self._replay = self._load_replay(replay_path) if mode == "replay" else {}
        self.calls: list[dict] = []

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @staticmethod
    def _load_replay(path: Path | None) -> dict[tuple[str, str, str], list[dict]]:
        if path is None or not path.exists():
            raise ValueError("replay mode needs an existing llm_calls.jsonl")
        table: dict[tuple[str, str, str], list[dict]] = {}
        for line in path.read_text().splitlines():
            if line:
                rec = json.loads(line)
                table.setdefault((rec["agent"], rec["purpose"], rec["key"]), []).append(rec)
        return table

    def ask(self, agent: str, purpose: str, instructions: str, payload: dict,
            schema: type[BaseModel], tools: list | None = None) -> LLMResult:
        if self.mode == "off":
            return LLMResult(None, "llm_off")
        key = call_key(agent, purpose, payload)
        raw, error = self._answer(agent, purpose, key, instructions, payload, schema, tools or [])
        output = None
        if error is None:
            try:
                output = schema.model_validate(raw)
            except ValidationError as exc:
                error = f"invalid_output: {exc.errors()[0]['msg']}"
        self.calls.append({"seq": len(self.calls) + 1, "agent": agent, "purpose": purpose, "key": key,
                           "mode": self.mode, "model": self.model, "input": payload,
                           "output": output.model_dump() if output else raw, "error": error})
        return LLMResult(output, error)

    def _answer(self, agent, purpose, key, instructions, payload, schema, tools) -> tuple[Any, str | None]:
        if self.mode == "fake":
            raw = self._fake(agent, purpose, payload)
            return (raw.model_dump() if isinstance(raw, BaseModel) else raw), None
        if self.mode == "replay":
            queue = self._replay.get((agent, purpose, key))
            if not queue:
                return None, "replay_missing"
            rec = queue.pop(0)
            return rec["output"], rec.get("error")
        return self._live(agent, instructions, payload, schema, tools)

    def _live(self, agent, instructions, payload, schema, tools) -> tuple[Any, str | None]:
        from agents import Agent, ModelSettings

        sdk_agent = Agent(name=agent, instructions=instructions, model=self.model, tools=tools,
                          model_settings=ModelSettings(temperature=self.temperature), output_type=schema)
        try:
            result = self._runner(sdk_agent, json.dumps(payload, sort_keys=True))
        except Exception as exc:  # network, quota or model errors all become a logged fallback
            return None, f"llm_error: {type(exc).__name__}"
        out = result.final_output
        return (out.model_dump() if isinstance(out, BaseModel) else out), None

    def save(self, path: Path) -> None:
        path.write_text("".join(json.dumps(c, sort_keys=True) + "\n" for c in self.calls))
