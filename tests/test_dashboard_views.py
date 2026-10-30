from __future__ import annotations

from dashboard import agent_views as av
from dashboard import panels, run_store
from sim.runner import run
from tests.test_generator_runner import PLACES, SMALL, roads

MINUTE = {"hospitals": [{"id": "H1", "name": "North", "utilization": 0.9, "er_used": 4, "er_slots": 4, "holds": []},
                        {"id": "H2", "name": "South", "utilization": 0.2, "er_used": 1, "er_slots": 4, "holds": []}]}


class Log:
    """Hand-built event stream: messages cost one round (DESIGN 7), escalations three."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def msg(self, t, sender, recipient, kind, thread, round_=1, payload=None, reason="", **extra) -> dict:
        e = {"seq": len(self.events) + 1, "t": t, "type": "message", "id": f"M{len(self.events) + 1:04d}",
             "thread_id": thread, "round": round_, "sent_at": t, "deliver_at": t + (3 if kind == "escalation" else 1),
             "sender": sender, "recipient": recipient, "msg_type": kind, "incident_id": extra.get("incident", "I01"),
             "victim_ids": extra.get("victims", ["I01-V1"]), "proposed_action": payload or {}, "reason": reason,
             "decided_by": "rule"}
        self.events.append(e)
        return e

    def decision(self, t, agent, kind, reason="r", **summary) -> dict:
        e = {"seq": len(self.events) + 1, "t": t, "type": "agent_decision", "agent": agent, "kind": kind,
             "decided_by": "rule", "reason": reason, **summary}
        self.events.append(e)
        return e


CFP = {"victims": [{"id": "I01-V1", "severity": "critical", "care_need": "trauma", "deadline": 60}],
       "unit": "ALS1", "ready_at": 8}


def offer(h, accept=(), decline=()):
    return {"hospital": h, "mood": "normal", "accept": list(accept), "conditional": [],
            "decline": [{"victims": list(decline), "reason": "no free capacity now"}] if decline else []}


def admission(log: Log, award_to="H2", then="ack") -> None:
    for h in ("H1", "H2"):
        log.msg(1, "dispatch", h, "admission_cfp", "TH001", payload=CFP)
    log.decision(2, "H1", "admission_offer", thread="TH001", accept=[])
    log.msg(2, "H1", "dispatch", "admission_offer", "TH001", payload=offer("H1", decline=["I01-V1"]))
    log.msg(2, "H2", "dispatch", "admission_offer", "TH001", payload=offer("H2", accept=["I01-V1"]))
    log.decision(3, "dispatch", "award", thread="TH001", awards=[{"hospital": award_to, "victims": ["I01-V1"],
                                                                   "arrival": 20}], escalate=[])
    log.msg(3, "dispatch", award_to, "admission_award", "TH001", payload={"victims": ["I01-V1"]})
    log.msg(4, award_to, "dispatch", then, "TH001", reason="firm reservation placed")


# ----- agent activity -----

def test_pending_requests_and_status_follow_the_round():
    log = Log()
    log.msg(0, "ia", "dispatch", "unit_request", "TH001",
            payload={"units_needed": {"ALS": 1, "BLS": 0}, "at_risk": 1, "minutes_left": 60})
    cards = {c["id"]: c for c in av.agent_cards(log.events, MINUTE, 0)}
    assert cards["dispatch"]["status"] == "1 request to answer" and cards["ia"]["status"] == "waiting for 1 reply"
    assert cards["dispatch"]["pending"][0]["Details"] == "needs ALS 1; 1 at risk, 60 min left"
    log.msg(1, "dispatch", "ia", "unit_response", "TH001", payload={"sent": ["ALS1"], "shortfall": None})
    cards = {c["id"]: c for c in av.agent_cards(log.events, MINUTE, 1)}
    assert not cards["dispatch"]["pending"] and cards["dispatch"]["status"] == "active"
    assert cards["ia"]["recent"][0] == {"Min": 1, "With": "← dispatch", "Message": "unit response",
                                        "Details": "sent ALS1", "Reason": "", "By": "rule"}
    cards = {c["id"]: c for c in av.agent_cards(log.events, MINUTE, 5)}
    assert cards["dispatch"]["status"] == "idle" and cards["arbiter"]["status"] == "idle"


def test_cards_cover_every_agent_with_hospital_mood_and_last_decision():
    log = Log()
    admission(log)
    cards = av.agent_cards(log.events, MINUTE, 3)
    assert [c["id"] for c in cards] == ["ia", "dispatch", "H1", "H2", "arbiter"]
    h1 = cards[2]
    assert (h1["name"], h1["mood"], h1["er"]) == ("H1 North", "protective", "4/4")
    assert av.decision_text(cards[1]["decision"]) == "min 3 · award (rule): r"
    assert av.decision_text(cards[4]["decision"]) == "no decision yet"


def test_escalation_is_pending_for_the_arbiter_for_three_minutes():
    log = Log()
    log.msg(10, "dispatch", "arbiter", "escalation", "TH002", 3, reason="rounds_exhausted")
    for t in (10, 13):
        arbiter = av.agent_cards(log.events, MINUTE, t)[-1]
        assert arbiter["status"] == "1 request to answer"
    assert av.agent_cards(log.events, MINUTE, 14)[-1]["status"] == "idle"


# ----- negotiation history -----

def test_admission_thread_summary_and_detail():
    log = Log()
    admission(log)
    index = av.thread_index(log.events)
    th = index["TH001"]
    assert th["protocol"] == "Admission" and len(th["decisions"]) == 2
    row = av.thread_row(th, 10)
    assert (row["Messages"], row["Declines"], row["Rounds"], row["Started"], row["Last"]) == (6, 1, 1, 1, 4)
    assert row["Outcome"] == "awarded to H2, reserved"
    assert av.thread_row(th, 1)["Outcome"] == "round 1 in progress"
    detail = av.thread_detail(th, 3)
    assert [d["What"] for d in detail] == ["admission cfp", "admission cfp", "admission offer", "admission offer",
                                           "admission offer", "award", "admission award"]
    assert detail[3]["Details"] == "normal: decline I01-V1 (no free capacity now)"
    assert detail[5]["From → to"] == "dispatch decides" and detail[5]["Details"] == "H2: I01-V1 (arrives 20)"
    assert av.thread_detail(th, 0) == []


def test_outcomes_for_declined_awards_escalations_and_exhausted_rounds():
    declined = Log()
    admission(declined, then="decline")
    assert av.thread_row(av.thread_index(declined.events)["TH001"], 9)["Outcome"] == "award declined by H2"

    ruled = Log()
    ruled.msg(5, "dispatch", "arbiter", "escalation", "TH001", 3)
    assert av.thread_row(av.thread_index(ruled.events)["TH001"], 6)["Outcome"] == "escalated to the Arbiter"
    ruled.msg(8, "arbiter", "H1", "arbiter_decision", "TH001", 3,
              payload={"option": 7, "option_name": "least_bad", "hospital": "H1", "victim": "I01-V1",
                       "arrival": 70, "meets_deadline": False, "loss": True})
    index = av.thread_index(ruled.events)
    assert av.thread_row(index["TH001"], 8)["Outcome"] == "Arbiter: least bad at H1 (loss)"
    assert av.thread_detail(index["TH001"], 8)[1]["Details"] == \
        "option 7 least bad for I01-V1: H1 by 70, misses deadline"

    exhausted = Log()
    for r in (1, 2, 3):
        exhausted.msg(2 * r, "dispatch", "H1", "admission_cfp" if r == 1 else "admission_counter", "TH001", r,
                      payload=CFP)
        exhausted.msg(2 * r + 1, "H1", "dispatch", "admission_offer", "TH001", r, payload=offer("H1", decline=["I01-V1"]))
    row = av.thread_row(av.thread_index(exhausted.events)["TH001"], 20)
    assert (row["Outcome"], row["Counters"], row["Declines"]) == ("no award after 3 rounds", 2, 3)


def test_thread_rows_filter_and_sort_by_last_activity():
    log = Log()
    admission(log)
    log.msg(6, "H1", "H2", "transfer_request", "TH002", incident=None, victims=[],
            payload={"victim": "W01", "care_need": "trauma", "from_hospital": "H1"})
    log.msg(7, "H2", "H1", "transfer_response", "TH002", incident=None, victims=[], payload={"accept": True})
    index = av.thread_index(log.events)
    assert [r["Thread"] for r in av.thread_rows(index, 10)] == ["TH002", "TH001"]
    assert [r["Thread"] for r in av.thread_rows(index, 5)] == ["TH001"]
    assert [r["Thread"] for r in av.thread_rows(index, 10, ["Transfer"])] == ["TH002"]
    assert [r["Thread"] for r in av.thread_rows(index, 10, incident="I01")] == ["TH001"]
    assert av.thread_row(index["TH002"], 10)["Outcome"] == "accepted by H2"


def test_unit_request_thread_lists_every_unit_sent():
    log = Log()
    log.msg(0, "ia", "dispatch", "unit_request", "TH001", payload={"units_needed": {"ALS": 2}, "at_risk": 2,
                                                                    "minutes_left": 50})
    log.msg(1, "dispatch", "ia", "unit_response", "TH001",
            payload={"sent": ["ALS1"], "shortfall": {"ALS": 1}, "plan": "queue"})
    log.msg(9, "dispatch", "ia", "unit_response", "TH001", payload={"sent": ["ALS2"], "shortfall": None})
    index = av.thread_index(log.events)
    assert av.thread_row(index["TH001"], 5)["Outcome"] == "sent ALS1; short ALS 1 (plan: queue)"
    assert av.thread_row(index["TH001"], 9)["Outcome"] == "sent ALS1, ALS2"


# ----- baseline vs agents -----

def test_baseline_for_needs_the_same_seed_and_scenario(tmp_path):
    agents = run(5, "agents", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES, llm_mode="off").name
    assert run_store.baseline_for(agents, tmp_path) is None
    run(6, "greedy", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES)
    run(5, "greedy", runs_dir=tmp_path, cfg=SMALL.__class__(**{**SMALL.__dict__, "n_incidents": 4}),
        roads=roads(), places=PLACES)
    assert run_store.baseline_for(agents, tmp_path) is None
    base = run(5, "greedy", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES).name
    assert run_store.baseline_for(agents, tmp_path) == base


def test_kpi_comparison_marks_better_and_worse():
    base = {"critical_on_time_pct": 40.0, "avg_report_to_care_min": 80.0, "rejected_actions": 0,
            "unserved_criticals": 2}
    agents = {"critical_on_time_pct": 60.0, "avg_report_to_care_min": 90.0, "rejected_actions": 0,
              "unserved_criticals": None}
    table = panels.kpi_comparison(base, agents)
    assert table.loc["Criticals on time"].tolist() == ["40.0%", "60.0%", "+20 pts", "✅ better"]
    assert table.loc["Avg report → care"].tolist() == ["80.0 min", "90.0 min", "+10 min", "🔻 worse"]
    assert table.loc["Rejected actions", ""] == "" and table.loc["Unserved criticals", "Change"] == ""
