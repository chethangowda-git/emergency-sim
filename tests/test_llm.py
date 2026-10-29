from __future__ import annotations

from types import SimpleNamespace

import pytest

from coordination.llm import LLMClient, call_key
from coordination.schemas import Choice, IAJudgment
from coordination.tools import DispatchScope, HospitalScope, function_tools
from tests.helpers import make_world


def ask(client, payload=None):
    return client.ask("H1", "admission", "be brief", payload or {"x": 1}, Choice)


def test_off_mode_never_answers():
    assert ask(LLMClient("off")).error == "llm_off"


def test_fake_answer_is_validated_and_recorded():
    client = LLMClient("fake", fake=lambda agent, purpose, payload: {"option_id": "a", "reason": "ok"})
    res = ask(client)
    assert res.output == Choice(option_id="a", reason="ok") and res.error is None
    rec = client.calls[0]
    assert rec["agent"] == "H1" and rec["key"] == call_key("H1", "admission", {"x": 1})


def test_invalid_answer_becomes_an_error():
    client = LLMClient("fake", fake=lambda *a: {"flags": [], "extra_als": 7})
    res = client.ask("ia", "assess", "", {}, IAJudgment)
    assert res.output is None and res.error.startswith("invalid_output")


def test_replay_reproduces_recorded_answers(tmp_path):
    rec = LLMClient("fake", fake=lambda agent, purpose, payload: {"option_id": f"o{payload['x']}", "reason": "r"})
    first = [ask(rec, {"x": i}).output for i in range(3)]
    rec.save(tmp_path / "llm_calls.jsonl")
    rep = LLMClient("replay", replay_path=tmp_path / "llm_calls.jsonl")
    assert [ask(rep, {"x": i}).output for i in range(3)] == first
    assert ask(rep, {"x": 99}).error == "replay_missing"


def test_replay_reproduces_a_recorded_fallback(tmp_path):
    rec = LLMClient("fake", fake=lambda *a: {"bad": True})
    ask(rec)
    rec.save(tmp_path / "calls.jsonl")
    res = ask(LLMClient("replay", replay_path=tmp_path / "calls.jsonl"))
    assert res.output is None and res.error.startswith("invalid_output")


def test_live_mode_pins_model_and_temperature():
    seen = {}

    def runner(agent, text):
        seen["agent"] = agent
        return SimpleNamespace(final_output=Choice(option_id="b", reason="fine"))

    client = LLMClient("live", model="pinned-model-x", runner=runner)
    res = ask(client)
    agent = seen["agent"]
    assert res.output.option_id == "b"
    assert agent.model == "pinned-model-x" and agent.model_settings.temperature == 0
    assert agent.output_type is Choice


def test_live_errors_become_fallbacks():
    def runner(agent, text):
        raise TimeoutError("slow")

    assert ask(LLMClient("live", model="m", runner=runner)).error == "llm_error: TimeoutError"


def test_live_mode_requires_model_and_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="model"):
        LLMClient("live")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        LLMClient("live", model="m")
    with pytest.raises(ValueError):
        LLMClient("replay")


def test_function_tools_are_read_only_and_scoped():
    w = make_world([])
    w.start()
    view = w.snapshot()
    assert [t.name for t in function_tools(HospitalScope(view, "H1"))] == ["get_my_hospital"]
    assert [t.name for t in function_tools(DispatchScope(view))] == ["list_units", "list_hospitals"]
    assert "free_slots" not in DispatchScope(view).hospitals()[0]
