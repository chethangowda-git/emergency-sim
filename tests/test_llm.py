from __future__ import annotations

from types import SimpleNamespace

import pytest

from coordination.llm import LLMClient, call_key, load_env_file
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

    assert ask(LLMClient("live", model="m", runner=runner)).error == "llm_error: TimeoutError: slow"


def test_live_mode_without_temperature_for_models_that_reject_it():
    seen = {}

    def runner(agent, text):
        seen["settings"] = agent.model_settings
        return SimpleNamespace(final_output=Choice(option_id="b", reason="fine"))

    ask(LLMClient("live", model="m", runner=runner, temperature=None))
    assert seen["settings"].temperature is None


def test_env_file_supplies_the_key_but_the_environment_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nexport OPENAI_API_KEY="sk-test"\nOTHER = two\nnot a pair\n')
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OTHER", "already set")
    load_env_file(env)
    import os
    assert os.environ["OPENAI_API_KEY"] == "sk-test" and os.environ["OTHER"] == "already set"
    load_env_file(tmp_path / "missing.env")


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


def test_live_calls_record_token_usage():
    usage = SimpleNamespace(requests=2, input_tokens=900, output_tokens=40)

    def runner(agent, text):
        return SimpleNamespace(final_output=Choice(option_id="b", reason="fine"), context_wrapper=SimpleNamespace(usage=usage))

    client = LLMClient("live", model="m", runner=runner)
    ask(client)
    assert client.calls[-1]["usage"] == {"requests": 2, "input_tokens": 900, "output_tokens": 40}


# ----- live retries and tracing -----

def api_error(kind, status=None, headers=None, code=None):
    import httpx2 as httpx
    import openai
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    if kind is openai.APIConnectionError:
        return openai.APIConnectionError(request=req)
    resp = httpx.Response(status, headers=headers or {}, request=req)
    return kind("boom", response=resp, body={"code": code} if code else None)


def flaky(errors):
    """A live runner that raises the given errors in turn, then answers."""
    left = list(errors)

    def runner(agent, text):
        if left:
            raise left.pop(0)
        return SimpleNamespace(final_output=Choice(option_id="b", reason="fine"))
    return runner


def test_rate_limits_wait_for_retry_after_then_succeed():
    import openai
    waits = []
    client = LLMClient("live", model="m", sleep=waits.append,
                       runner=flaky([api_error(openai.RateLimitError, 429, {"retry-after": "3"})]))
    assert ask(client).output == Choice(option_id="b", reason="fine") and client.calls[-1]["error"] is None
    assert len(waits) == 1 and 3 <= waits[0] < 4


def test_transient_errors_back_off_exponentially():
    import openai
    waits = []
    errors = [api_error(openai.APIConnectionError), api_error(openai.InternalServerError, 503)]
    client = LLMClient("live", model="m", sleep=waits.append, runner=flaky(errors))
    assert ask(client).error is None
    assert 2 <= waits[0] < 3 and 4 <= waits[1] < 5


def test_quota_errors_and_exhausted_retries_become_llm_errors():
    import openai

    from coordination import llm as llm_mod
    waits = []
    quota = api_error(openai.RateLimitError, 429, code="insufficient_quota")
    res = ask(LLMClient("live", model="m", sleep=waits.append, runner=flaky([quota])))
    assert res.error.startswith("llm_error: RateLimitError") and waits == []
    always = flaky([api_error(openai.RateLimitError, 429, {"retry-after": "1"})] * llm_mod.RETRY_ATTEMPTS)
    res = ask(LLMClient("live", model="m", sleep=waits.append, runner=always))
    assert res.error.startswith("llm_error") and len(waits) == llm_mod.RETRY_ATTEMPTS - 1


def test_live_mode_turns_off_sdk_tracing(monkeypatch):
    import agents
    seen = []
    monkeypatch.setattr(agents, "set_tracing_disabled", seen.append)
    LLMClient("live", model="m", runner=flaky([]))
    assert seen == [True]
