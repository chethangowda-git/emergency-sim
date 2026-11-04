from __future__ import annotations

import re

from coordination.llm import LLMClient
from dashboard import agent_views, conversation as C, explain_agents, run_store
from sim.runner import run
from tests.test_generator_runner import PLACES, SMALL, roads
from tests.test_orchestrator import last_option

CODE = re.compile(r"\b[IS]\d\d-V\d+\b|\bTH\d{3}\b|\b[a-z]+_[a-z_]+\b")   # victim IDs, thread IDs, snake_case codes
LINE = re.compile(r"^min \d+ (Incident Assessment|Dispatch|Referee|H\d)(, (Incident Assessment|Dispatch|Referee|H\d))* → ")


def recorded(tmp_path, llm=None):
    out = run(5, "agents", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES,
              **({"llm": llm} if llm else {"llm_mode": "off"}))
    return run_store.load_run(out.name, tmp_path)


def context(data, minute=None, net=None):
    minute = len(data["timeline"]) - 1 if minute is None else minute
    return C.Context(data, minute, agent_views.thread_index(data["events"]),
                     explain_agents.match_calls(data["events"], data.get("llm_calls", [])), net)


def visible(html: str) -> str:
    """What a viewer reads: no tags and no tooltips (where IDs and the AI's own words live)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"title='[^']*'", "", html)))


def ids(data, minute):
    return [i["id"] for i in data["timeline"][minute]["incidents"]]


def test_incident_list_puts_active_first_and_finished_on_one_line(tmp_path):
    data = recorded(tmp_path)
    for m in (3, len(data["timeline"]) - 1):
        rows = C.entries(data, m, has_between=True)
        assert rows[-1]["id"] == C.BETWEEN
        rows = rows[:-1]
        assert [r["active"] for r in rows] == sorted((r["active"] for r in rows), reverse=True)
        for r in rows:
            assert r["label"].startswith("✓ ") == (not r["active"])
            assert not CODE.search(r["label"])


def test_each_message_is_one_line_sender_to_recipients_in_time_order(tmp_path):
    data = recorded(tmp_path)
    ctx = context(data, net=roads())
    said = 0
    for iid in ids(data, ctx.minute) + [C.BETWEEN]:
        lines = C.conversation(ctx, iid, ids(data, ctx.minute))
        assert [l["t"] for l in lines] == sorted(l["t"] for l in lines)
        msgs = [l for l in lines if l["kind"] == "msg"]
        said += len(msgs)
        keys = [(l["t"], l["sender"], tuple(l["to"]), l["text"]) for l in msgs]
        assert len(keys) == len(set(keys)), "a message to several hospitals is one line"
        html = C.conversation_html(lines)
        assert not CODE.search(visible(html)), visible(html)
        for row in re.findall(r"<div class='cv'.*?</div>", html):
            assert LINE.match(visible(row).strip()), visible(row)
        for row in re.findall(r"<div class='cv-n'.*?</div>", html):
            assert visible(row).strip().startswith("· ")
        if iid != C.BETWEEN and not any(r["active"] for r in C.entries(data, ctx.minute, False) if r["id"] == iid):
            assert any(l["kind"] == "narr" and "reached H" in l["text"] for l in lines)
    assert said > 10


def test_requests_say_when_the_patient_arrives_from_the_road_network(tmp_path):
    data = recorded(tmp_path)
    ctx = context(data, net=roads())
    asks = [l for iid in ids(data, ctx.minute) for l in C.conversation(ctx, iid)
            if l["kind"] == "msg" and l["text"].startswith("Can you take")]
    assert asks and any(re.search(r"Arrives in about \d+ min\.$", l["text"]) for l in asks)
    assert all("Arrives" not in l["text"] for iid in ids(data, ctx.minute)
               for l in C.conversation(context(data), iid))          # no road network, no estimate


def test_ai_lines_get_one_tag_naming_the_agent_with_its_words_on_hover(tmp_path):
    data = recorded(tmp_path, LLMClient("fake", fake=last_option))
    ctx = context(data)
    tagged = [l for iid in ids(data, ctx.minute) + [C.BETWEEN] for l in C.conversation(ctx, iid, ids(data, ctx.minute))
              if l.get("ai")]
    assert tagged, "the fake LLM makes some decisions"
    for l in tagged:
        assert l["ai"].startswith(f"{C.name(l['sender'])}'s AI")
    html = C.conversation_html(tagged)
    assert html.count("(AI)</span>") == len(tagged) and "class='ai-tag' title='" in html
    assert "badge" not in html and "chip" not in html and "🔴" not in html


def message(seq, t, kind, sender, recipient, round_=1, thread="TH001", vids=("I01-V1",), reason="", **p):
    return {"seq": seq, "t": t, "type": "message", "msg_type": kind, "sender": sender, "recipient": recipient,
            "round": round_, "victim_ids": list(vids), "reason": reason, "decided_by": "rule", "proposed_action": p,
            "incident_id": "I01" if vids and vids[0].startswith("I01") else None, "thread_id": thread,
            "deliver_at": t + 1}


def hand_built(events):
    data = {"events": events, "victims": [{"id": "I01-V1", "original_severity": "critical", "deadline": 60},
                                          {"id": "I09-V1", "original_severity": "critical", "deadline": 90}],
            "timeline": [{"incidents": [], "hospitals": [{"id": "H1", "node": "a"}, {"id": "H2", "node": "b"}]}]}
    return C.Context(data, 100, agent_views.thread_index(events), {})


def test_repeated_rounds_fold_into_one_narrator_line_and_merge_recipients():
    full = "protective: decline (no free capacity now)"
    ms = [message(1, 10, "admission_cfp", "dispatch", "H1"), message(2, 10, "admission_cfp", "dispatch", "H2")]
    for r, t in ((1, 11), (2, 13), (3, 15)):
        if r > 1:
            ms += [message(10 * r, t - 1, "admission_counter", "dispatch", h, r) for h in ("H1", "H2")]
        ms += [message(10 * r + 5, t, "admission_offer", h, "dispatch", r, reason=full, accept=[], conditional=[],
                       decline=[{"victims": ["I01-V1"], "reason": full}]) for h in ("H1", "H2")]
    lines = C.conversation(hand_built(ms), "I01")
    texts = [l["text"] for l in lines]
    assert texts[0] == "Can you take the critical patient?" and lines[0]["to"] == ["H1", "H2"]
    assert texts[1] == texts[2] == "No, my ER is full."
    assert texts[3] == "Asking again: can you take the critical patient?"
    assert texts[-1] == "Dispatch asked once more; still no hospital agreed." and lines[-1]["kind"] == "narr"


def test_transfers_follow_the_patient_they_help_and_the_rest_are_between_hospitals():
    award = message(1, 20, "admission_award", "dispatch", "H1", conditional=True)
    helps = message(2, 21, "transfer_request", "H1", "H2", thread="TH002", vids=("I09-V1",), care_need="trauma")
    loan = message(3, 40, "staff_request", "H2", "H1", thread="TH003", vids=(), skill="doctor", minutes=90)
    no = message(4, 41, "staff_offer", "H1", "H2", thread="TH003", vids=(), reason="no staff to spare", answer="none")
    ctx = hand_built([award, helps, loan, no])
    mine = C.conversation(ctx, "I01", ["I01", "I09"])
    assert [l["text"] for l in mine][1].startswith("Can you take one of my patients")
    between = C.conversation(ctx, C.BETWEEN, ["I01", "I09"])
    assert [l["sender"] for l in between] == ["H2", "H1"] and between[1]["text"] == "No: I have no staff to spare."
    assert all(l["t"] != 21 for l in between)


def test_guard_blocks_are_narrator_lines_naming_the_agent():
    cfp = message(1, 30, "admission_cfp", "dispatch", "H1")
    why = "a transfer was tried here in the last 10 minutes, so another cannot start now"
    guards = [{"seq": 5 + i, "t": 31, "type": "guard", "rule": "G1", "agent": h, "why": why, "thread": "TH001",
               "incident": "I01", "withheld": "after transfer"} for i, h in enumerate(("H2", "H1"))]
    extra = {"seq": 9, "t": 29, "type": "guard", "rule": "G2", "agent": "ia", "why": "at most 1 extra ALS per incident",
             "incident": "I01", "suggested": "3 extra ALS", "allowed": "1 extra ALS", "ai_reason": "big fire"}
    lines = C.conversation(hand_built([cfp, *guards, extra]), "I01")
    narr = [l for l in lines if l["kind"] == "narr"]
    assert narr[0]["text"] == "The guard cut Incident Assessment's AI request from 3 extra paramedic ambulances to 1."
    assert narr[0]["tip"] == "big fire"
    assert narr[1]["text"] == f"The guard kept the option “after a transfer” from the AI of H1 and H2: {why}."
