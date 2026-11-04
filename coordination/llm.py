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
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from sim.config import ROOT

LLM_CONFIG = ROOT / "config" / "llm.json"
ENV_FILE = ROOT / ".env"
MODES = ("off", "fake", "replay", "live")

FakeFn = Callable[[str, str, dict], Any]
Runner = Callable[[Any, str], Any]

RETRY_ATTEMPTS = 6        # per call, after the SDK's own quick retries
RETRY_MAX_SECONDS = 300   # total time one call may spend waiting
BACKOFF_FIRST = 2.0       # seconds; doubles each attempt when the server gives no Retry-After


@dataclass
class LLMResult:
    output: BaseModel | None
    error: str | None = None


def call_key(agent: str, purpose: str, payload: dict) -> str:
    """Deterministic key for an LLM input, used to match recorded answers."""
    text = json.dumps({"agent": agent, "purpose": purpose, "payload": payload}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_env_file(path: Path = ENV_FILE) -> None:
    """KEY=VALUE lines from the gitignored .env (OPENAI_API_KEY for live runs started from the
    launcher); variables already set in the environment win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip().removeprefix("export ").strip(), value.strip().strip("\"'"))


def load_llm_config(path: Path = LLM_CONFIG) -> dict:
    load_env_file()
    return json.loads(path.read_text()) if path.exists() else {"mode": "off", "model": None, "temperature": 0}


def disable_tracing() -> None:
    """No Agents SDK traces are uploaded: every call is already recorded in llm_calls.jsonl."""
    os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    from agents import set_tracing_disabled

    set_tracing_disabled(True)


def _usage_of(result) -> dict | None:
    """Token counts of one live call (all model requests in it, tool rounds included), for cost reports."""
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    if usage is None:
        return None
    return {"requests": usage.requests, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}


def retry_wait(exc: Exception, attempt: int, jitter: float) -> float | None:
    """Seconds to wait before retrying a failed live call, or None if it must not be retried:
    429 rate limits (honouring Retry-After), connection errors, timeouts and 5xx server errors
    are transient; quota and billing errors need the user's action."""
    import openai

    if isinstance(exc, openai.RateLimitError):
        code = (getattr(exc, "body", None) or {}).get("code") if isinstance(getattr(exc, "body", None), dict) else None
        if code == "insufficient_quota" or "insufficient_quota" in str(exc):
            return None
    elif not isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError)):
        return None
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        after = float(headers.get("retry-after"))
    except (TypeError, ValueError):
        after = None
    base = after if after is not None and after >= 0 else BACKOFF_FIRST * 2 ** (attempt - 1)
    return base + jitter


def _sdk_runner(agent, text: str):
    from agents import Runner as SDKRunner

    return SDKRunner.run_sync(agent, text)


class LLMClient:
    def __init__(self, mode: str = "off", model: str | None = None, *, fake: FakeFn | None = None,
                 replay_path: Path | None = None, runner: Runner | None = None,
                 temperature: float | None = 0.0, sleep: Callable[[float], None] = time.sleep) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown LLM mode {mode!r}")
        if mode == "fake" and fake is None:
            raise ValueError("fake mode needs a fake answer function")
        if mode == "live" and not model:
            raise ValueError("live mode needs a pinned model in config/llm.json")
        if mode == "live" and runner is None and not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("live mode needs OPENAI_API_KEY")
        self.mode, self.model, self.temperature = mode, model, temperature
        self._fake, self._runner, self._sleep = fake, runner or _sdk_runner, sleep
        self._jitter = random.Random()  # only spreads retry waits; never touches simulation state
        if mode == "live":
            disable_tracing()
        self._replay = self._load_replay(replay_path) if mode == "replay" else {}
        self.replay_path = replay_path if mode == "replay" else None
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
        self._usage = None
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
        if self._usage is not None:
            self.calls[-1]["usage"] = self._usage
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

        settings = ModelSettings() if self.temperature is None else ModelSettings(temperature=self.temperature)
        sdk_agent = Agent(name=agent, instructions=instructions, model=self.model, tools=tools,
                          model_settings=settings, output_type=schema)
        text, waited = json.dumps(payload, sort_keys=True), 0.0
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                result = self._runner(sdk_agent, text)
                break
            except Exception as exc:  # what cannot be retried becomes a logged llm_error
                wait = retry_wait(exc, attempt, self._jitter.uniform(0, 1))
                if wait is None or attempt == RETRY_ATTEMPTS or waited + wait > RETRY_MAX_SECONDS:
                    return None, f"llm_error: {type(exc).__name__}: {str(exc)[:300]}"
                self._sleep(wait)
                waited += wait
        self._usage = _usage_of(result)
        out = result.final_output
        return (out.model_dump() if isinstance(out, BaseModel) else out), None

    def save(self, path: Path) -> None:
        path.write_text("".join(json.dumps(c, sort_keys=True) + "\n" for c in self.calls))
