from __future__ import annotations

import re

from coordination.llm import LLMClient
from dashboard import agent_views, explain_agents, run_store, story
from sim.runner import run
from tests.test_generator_runner import PLACES, SMALL, roads
from tests.test_orchestrator import last_option

CODE = re.compile(r"\b[IS]\d\d-V\d+\b|\bTH\d{3}\b|\b[a-z]+_[a-z_]+\b")   # victim IDs, thread IDs, snake_case codes


def recorded(tmp_path, llm=None):
    out = run(5, "agents", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES,
              **({"llm": llm} if llm else {"llm_mode": "off"}))
    return run_store.load_run(out.name, tmp_path)


def context(data, minute=None):
    minute = len(data["timeline"]) - 1 if minute is None else minute
    return story.Context(data, minute, agent_views.thread_index(data["events"]),
                         explain_agents.match_calls(data["events"], data.get("llm_calls", [])))


def visible(html: str) -> str:
    """What a viewer reads: no tags and no tooltips (where IDs and the AI's own words live)."""
    return re.sub(r"<[^>]+>", " ", re.sub(r"title='[^']*'", "", html))


def test_incident_list_puts_active_first_and_finished_on_one_line(tmp_path):
    data = recorded(tmp_path)
    for m in (3, len(data["timeline"]) - 1):
        rows = story.entries(data, m)
        assert [r["active"] for r in rows] == sorted((r["active"] for r in rows), reverse=True)
        for r in rows:
            assert r["label"].startswith("✓ ") == (not r["active"])
            assert not CODE.search(r["label"])


def test_a_story_is_numbered_plain_sentences_in_time_order(tmp_path):
    data = recorded(tmp_path)
    ctx = context(data)
    for r in story.entries(data, ctx.minute):
        items = story.story(ctx, r["id"])
        assert items and items[0]["text"].startswith("Reported at min ")
        assert [i["t"] for i in items] == sorted(i["t"] for i in items)
        html = story.story_html(items, story.now_line(ctx, r["id"]))
        assert html.startswith("<ol class='story'>") and not CODE.search(visible(html)), visible(html)
        requests = [i for i in items if i["text"].startswith("Dispatch asked")]
        for i in requests:
            assert all(s["text"].split(" ")[0][0].isupper() for s in i["sub"])   # each reply under its request
        if not r["active"]:
            assert any("reached" in i["text"] and ("On time." in i["text"] or "Late." in i["text"]) for i in items)


def test_ai_decisions_get_one_small_tag_with_the_ais_words_on_hover(tmp_path):
    data = recorded(tmp_path, LLMClient("fake", fake=last_option))
    ctx = context(data)
    tagged = [(i, s) for r in story.entries(data, ctx.minute) for i in story.story(ctx, r["id"])
              for s in [i, *i["sub"]] if s.get("ai")]
    assert tagged, "the fake LLM makes some decisions"
    for _, s in tagged:
        assert s["ai"].startswith(("The AI's words:", "The AI gave no written reason"))
    html = story.story_html([tagged[0][0]])
    assert "(AI)</span>" in html and "class='ai-tag' title='" in html
    assert "badge" not in html and "🔴" not in html and "chip" not in html


def test_repeated_requests_fold_into_one_line():
    ctx = story.Context({"events": [], "victims": [{"id": "I01-V1", "original_severity": "critical", "deadline": 60}],
                         "timeline": [{"incidents": [], "units": []}]}, 0, {}, {})
    def m(seq, t, kind, sender, recipient, round_=1, **p):
        return {"seq": seq, "t": t, "msg_type": kind, "sender": sender, "recipient": recipient, "round": round_,
                "victim_ids": ["I01-V1"], "reason": "protective: decline (no free capacity now)", "decided_by": "rule",
                "proposed_action": p, "incident_id": "I01", "thread_id": "TH1", "deliver_at": t + 1}
    ms = [m(1, 10, "admission_cfp", "dispatch", "H1"), m(2, 11, "admission_offer", "H1", "dispatch", accept=[],
                                                        conditional=[], decline=[{"victims": ["I01-V1"], "reason": "x"}])]
    ms += [m(3, 12, "admission_counter", "dispatch", "H1", 2), m(4, 14, "admission_counter", "dispatch", "H1", 3)]
    it = story.request_item(ctx, ("I01-V1",), [{"th": {"id": "TH1", "decisions": []}, "ms": ms}])
    texts = [s["text"] for s in it["sub"]]
    assert it["text"] == "Dispatch asked South End General to take the critical patient."
    assert texts[0] == "South End General said no: its ER is full."
    assert "Dispatch asked again twice; still no hospital agreed." in texts
