from __future__ import annotations

import json
import re

from coordination.llm import LLMClient
from dashboard import agent_views, explain_agents, run_store, timeline
from sim.runner import run
from tests.test_generator_runner import PLACES, SMALL, roads
from tests.test_orchestrator import last_option

CODE = re.compile(r"\b[IS]\d\d-V\d\b|\bTH\d{3}\b|\b[a-z]+_[a-z_]+\b")   # victim IDs, thread IDs, snake_case codes


def recorded(tmp_path, llm=None):
    out = run(5, "agents", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES,
              **({"llm": llm} if llm else {"llm_mode": "off"}))
    return out, run_store.load_run(out.name, tmp_path)


def context(data, minute=None):
    minute = len(data["timeline"]) - 1 if minute is None else minute
    return timeline.Context(data, minute, agent_views.thread_index(data["events"]),
                            explain_agents.match_calls(data["events"], data.get("llm_calls", [])))


def test_every_incident_has_a_readable_timeline(tmp_path):
    _, data = recorded(tmp_path)
    ctx = context(data)
    entries = timeline.incident_list(data, ctx.minute, lambda iid: timeline.incident_steps(ctx, iid))
    assert entries and all(e["steps"] > 0 for e in entries)
    assert [e["active"] for e in entries] == sorted((e["active"] for e in entries), reverse=True)
    for e in entries:
        steps = timeline.incident_steps(ctx, e["id"])
        assert [s["t"] for s in steps] == sorted(s["t"] for s in steps)
        for s in steps:
            assert s["dot"] in timeline.DOTS and s["badge"] in timeline.BADGE
            visible = " ".join([s["title"], *s["lines"], s["rule"] or ""])
            assert not CODE.search(visible), visible
    first = timeline.incident_steps(ctx, entries[0]["id"])
    assert "reported" in first[0]["title"] and any(s["title"].startswith("Where should") for e in entries
                                                    for s in timeline.incident_steps(ctx, e["id"]))
    html = timeline.step_html(first[0])
    assert "min " in html and "class='badge" in html


def test_repeated_requests_are_numbered_attempts_and_outcomes_close_the_timeline(tmp_path):
    _, data = recorded(tmp_path)
    ctx = context(data)
    for e in timeline.incident_list(data, ctx.minute, lambda iid: timeline.incident_steps(ctx, iid)):
        steps = timeline.incident_steps(ctx, e["id"])
        titles = [s["title"] for s in steps if s["title"].startswith("Where should")]
        for title in titles:
            if "· Attempt " in title:
                n = int(title.rsplit("Attempt ", 1)[1])
                assert any(t.startswith(title.split(" · Attempt")[0]) for t in titles) and n >= 2
        if not e["active"] and e["id"]:
            assert any("reached" in s["title"] for s in steps)


def test_forcing_one_recorded_ai_call_to_the_rule(tmp_path):
    from tools.highlights import ForceOne
    out, _ = recorded(tmp_path, LLMClient("fake", fake=last_option))
    calls = [json.loads(line) for line in open(out / "llm_calls.jsonl")]
    llm = ForceOne(out, 0)
    first = calls[0]
    from coordination.schemas import Choice
    result = llm.ask(first["agent"], first["purpose"], "", first["input"], Choice)
    assert result.output is None and result.error == "forced_rule"


def test_replay_presets_are_offered_only_when_recorded_here(tmp_path):
    (tmp_path / "demo" / "demo-seed376").mkdir(parents=True)
    assert run_store.replay_presets(tmp_path) == []                       # no recording there yet
    (tmp_path / "demo" / "demo-seed376" / "llm_calls.jsonl").write_text("")
    assert [p["path"] for p in run_store.replay_presets(tmp_path)] == ["demo/demo-seed376"]
    assert run_store.featured_replay(tmp_path) == "demo/demo-seed376"


def visible(html: str) -> str:
    """What a viewer reads: no tags, no tooltips, and not the AI's own quoted words (shown verbatim)."""
    text = re.sub(r"<[^>]+>", " ", re.sub(r"title='[^']*'", "", html))
    return re.sub(r"AI(&#x27;|')s words: “[^”]*”", "", text)


def test_no_tab_but_the_log_shows_code_names(tmp_path):
    from dashboard import explain
    _, data = recorded(tmp_path, LLMClient("fake", fake=last_option))
    severity = {v["id"]: v["original_severity"] for v in data["victims"]}
    calls = explain_agents.match_calls(data["events"], data["llm_calls"])
    for m in (5, 20, len(data["timeline"]) - 1):
        texts = [explain.now_sentence(data, m)]
        texts += [explain.incident_card_html(c) for c in explain.incident_cards(data, m, [])]
        incidents = {i["id"]: i for i in data["timeline"][m]["incidents"]}
        texts += [explain_agents.decision_html(c)
                  for c in explain_agents.decision_cards(data["events"], m, incidents, calls, True, severity)]
        texts += [explain.hospital_card_html(h) for h in explain.hospital_cards(data, m)]
        ctx = context(data, m)
        for e in timeline.incident_list(data, m, lambda iid: timeline.incident_steps(ctx, iid)):
            for s in timeline.incident_steps(ctx, e["id"]):
                texts += [timeline.step_html(s), explain_agents.bubbles_html(s["messages"])]
        leaks = [code for t in texts for code in CODE.findall(visible(t))]
        assert leaks == [], (m, leaks[:5])
