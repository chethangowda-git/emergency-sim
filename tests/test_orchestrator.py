from __future__ import annotations

import json

from coordination.llm import LLMClient
from coordination.messages import MessageBus
from coordination.orchestrator import AgentPolicy
from sim import metrics
from sim.models import UnitSpec
from tests.helpers import hospital, incident, make_world, of_type, walk_in

TRAUMA_GENERAL = ("trauma", "general")


def run_agents(world, policy, until):
    events = world.start()
    while world.t < until:
        actions = policy.decide(world.snapshot(), events)
        world.log(policy.drain_log())
        events = world.apply(actions) + world.step()
    return world.events


def messages(events, kind=None):
    return [e for e in events if e["type"] == "message" and (kind is None or e["msg_type"] == kind)]


def applied(events, action):
    return [e for e in events if e["type"] == "action_applied" and e["action"] == action]


def agents(variant="full", llm=None):
    return AgentPolicy(llm or LLMClient("off"), variant)


# ----- bus -----

def test_bus_ids_threads_and_time_costs():
    bus = MessageBus()
    th = bus.new_thread()
    a = bus.send(5, "dispatch", "H1", "admission_cfp", th)
    b = bus.send(5, "dispatch", "arbiter", "escalation", th)
    assert (a.id, b.id, th) == ("M0001", "M0002", "TH001")
    assert a.deliver_at == 6 and b.deliver_at == 8          # 1 minute; escalation 1 + 2
    assert bus.due(6) == [a] and bus.due(8) == [b]


# ----- unit request and admission -----

def test_unit_request_and_contract_net_timing():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])],
                   units=[UnitSpec("ALS1", "ALS", "A", "A"), UnitSpec("BLS1", "BLS", "A", "A")])
    evs = run_agents(w, agents(), 20)
    assert messages(evs, "unit_request")[0]["deliver_at"] == 1
    assert messages(evs, "unit_response")[0]["thread_id"] == messages(evs, "unit_request")[0]["thread_id"]
    assert applied(evs, "send_to_scene")[0]["t"] == 1              # one message round after the report
    assert messages(evs, "admission_cfp")[0]["t"] == 1
    assert messages(evs, "admission_offer")[0]["t"] == 2
    assert messages(evs, "admission_award")[0]["t"] == 3
    assert of_type(evs, "reservation_placed")[0]["t"] == 4
    assert applied(evs, "set_destination")[0]["t"] == 5
    assert of_type(evs, "transport_started")[0]["t"] == 8         # on scene at 3, plus 5
    assert of_type(evs, "patient_delivered")[0]["hospital"] == "H1"


def busy_world():
    return make_world([incident("I01", "B", 0, [("critical", "trauma"), ("serious", "trauma"), ("minor", "general")]),
                       incident("I02", "E", 4, [("serious", "general"), ("minor", "general")], kind="building_fire"),
                       incident("I03", "D", 9, [("critical", "trauma")], kind="structural_collapse")],
                      hospitals=[hospital("H1", "C", slots=6, doctors=("trauma", "trauma", "burns"), nurses=3),
                                 hospital("H2", "E", caps=TRAUMA_GENERAL, slots=4, doctors=("trauma", "trauma"),
                                          nurses=2)],
                      units=[UnitSpec("ALS1", "ALS", "A", "A"), UnitSpec("ALS2", "ALS", "E", "E"),
                             UnitSpec("BLS1", "BLS", "A", "A"), UnitSpec("FIRE1", "FIRE", "D", "D"),
                             UnitSpec("RESCUE1", "RESCUE", "D", "D")])


def test_busy_scenario_delivers_everyone_and_is_deterministic():
    first = run_agents(busy_world(), agents(), 200)
    again = run_agents(busy_world(), agents(), 200)
    assert first == again
    delivered = {e["victim"] for e in of_type(first, "patient_delivered")}
    assert delivered == {f"I0{i}-V{k}" for i, n in ((1, 3), (2, 2), (3, 1)) for k in range(1, n + 1)}
    m = metrics.negotiation(first)
    assert m["threads"] > 0 and m["deals_without_arbiter"] > 0 and m["decisions_by"]["rule"] == 100.0


def test_three_rounds_then_retry_when_no_one_can_take_a_non_critical():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])],
                   hospitals=[hospital("H1", "C", slots=1), hospital("H2", "E", caps=TRAUMA_GENERAL, slots=1)],
                   walk_ins=[walk_in("W01", "H1", 0, "serious", "trauma"), walk_in("W02", "H2", 0, "serious", "trauma")])
    evs = run_agents(w, agents(), 10)
    rounds = sorted({m["round"] for m in messages(evs, "admission_counter")})
    assert rounds == [2, 3]
    assert any(e["type"] == "agent_decision" and e["kind"] == "retry" for e in evs)
    assert not messages(evs, "escalation")


# ----- the Arbiter -----

def arbiter_world():
    """A critical trauma victim; the only trauma hospital is full and nothing can be freed."""
    return make_world([incident("I01", "B", 0, [("critical", "trauma")])],
                      hospitals=[hospital("H1", "C", slots=1), hospital("H2", "E", caps=("general",))],
                      walk_ins=[walk_in("W01", "H1", 0, "serious", "trauma")])


def test_escalation_takes_three_minutes_and_ruling_is_binding():
    evs = run_agents(arbiter_world(), agents(), 110)
    esc = messages(evs, "escalation")[0]
    assert esc["deliver_at"] == esc["t"] + 3
    ruling = next(e for e in evs if e["type"] == "agent_decision" and e["agent"] == "arbiter")
    assert ruling["t"] == esc["deliver_at"] and ruling["option"] == 7 and ruling["loss"]
    assert len(messages(evs, "escalation")) == 1                    # final: no renegotiation
    delivered = of_type(evs, "patient_delivered")[0]
    assert delivered["hospital"] == "H1" and delivered["t"] < 70      # released W01 early to make room


def test_no_arbiter_ablation_goes_straight_to_least_bad():
    evs = run_agents(arbiter_world(), agents("noarb"), 70)
    assert not messages(evs, "escalation")
    assert any(e["type"] == "agent_decision" and e["agent"] == "arbiter" and e["option"] == 7 for e in evs)


# ----- reroute after a broken reservation -----

def reroute_world():
    return make_world([incident("I01", "D", 0, [("serious", "general")])],
                      hospitals=[hospital("H1", "C", slots=1), hospital("H2", "E", caps=("general",), slots=2)],
                      units=[UnitSpec("ALS1", "ALS", "A", "A")], walk_ins=[walk_in("W01", "H1", 12)])


def test_capacity_alert_reroutes_before_arrival():
    evs = run_agents(reroute_world(), agents(), 40)
    assert messages(evs, "capacity_alert")
    rerouted = of_type(evs, "unit_rerouted")
    assert rerouted and rerouted[0]["hospital"] == "H2"
    assert not of_type(evs, "arrived_no_slot")
    assert of_type(evs, "patient_delivered")[0]["hospital"] == "H2"


# ----- mutual aid, transfers, staff -----

def test_mutual_aid_vehicle_takes_an_at_risk_non_critical():
    w = make_world([incident("I01", "B", 0, [("serious", "general")])],
                   hospitals=[hospital("H1", "C", slots=4)], units=[UnitSpec("FIRE1", "FIRE", "D", "D")])
    w.victims["I01-V1"].deadline = 40
    evs = run_agents(w, agents(), 30)
    assert messages(evs, "mutual_aid_request") and of_type(evs, "vehicle_lent")
    assert of_type(evs, "patient_delivered")[0]["unit"] == "H1-TV1"


def test_no_negotiation_ablation_sends_no_mutual_aid():
    w = make_world([incident("I01", "B", 0, [("serious", "general")])],
                   hospitals=[hospital("H1", "C", slots=4)], units=[UnitSpec("FIRE1", "FIRE", "D", "D")])
    w.victims["I01-V1"].deadline = 40
    evs = run_agents(w, agents("nonego"), 30)
    assert not messages(evs, "mutual_aid_request") and not of_type(evs, "vehicle_lent")


def test_protective_hospital_transfers_a_stabilized_patient():
    w = make_world([], hospitals=[hospital("H1", "C", slots=4, doctors=("trauma", "trauma"), nurses=2),
                                  hospital("H2", "E", caps=TRAUMA_GENERAL, slots=4)],
                   walk_ins=[walk_in("W01", "H1", 0, "critical", "trauma")]
                   + [walk_in(f"W0{k}", "H1", 0, "minor", "general") for k in (2, 3, 4)])
    evs = run_agents(w, agents(), 45)
    assert messages(evs, "transfer_request") and messages(evs, "transfer_response")
    done = of_type(evs, "transfer_completed")
    assert done and done[0]["victim"] == "W01" and done[0]["hospital"] == "H2"


def test_staff_loan_when_patients_wait_for_staff():
    w = make_world([], hospitals=[hospital("H1", "C", doctors=("trauma", "trauma")), hospital("H2", "E", doctors=())],
                   walk_ins=[walk_in("W01", "H2", 0, "minor", "general")])
    evs = run_agents(w, agents(), 10)
    assert messages(evs, "staff_request") and messages(evs, "staff_offer")
    assert of_type(evs, "staff_lent")[0]["to_hospital"] == "H2"
    assert w.victims["W01"].doctor is not None


# ----- LLM record and replay -----

def last_option(agent, purpose, payload):
    if purpose == "assess":
        return {"flags": ["test flag"], "extra_als": 0, "extra_bls": 1, "note": "test"}
    if purpose == "plan_units":
        return {"ranking": [r["incident"] for r in payload["reports"]][::-1], "shortfall_plans": [], "reason": "r"}
    return {"option_id": payload["options"][-1], "reason": "last option"}


def test_replay_of_a_recorded_run_matches_exactly(tmp_path):
    rec = LLMClient("fake", fake=last_option)
    first = run_agents(busy_world(), agents(llm=rec), 150)
    assert rec.calls and any(e["type"] == "agent_decision" and e["decided_by"] == "llm" for e in first)
    rec.save(tmp_path / "llm_calls.jsonl")
    replay = LLMClient("replay", replay_path=tmp_path / "llm_calls.jsonl")
    again = run_agents(busy_world(), agents(llm=replay), 150)
    assert json.dumps(again) == json.dumps(first)


# ----- declined awards (DESIGN 5.2, 8) -----

def declined_award_world(h2=True, deadline=None):
    """H1 accepts I01-V1, then a walk-in takes its only slot before the award arrives."""
    inc = incident("I01", "B", 0, [("critical", "trauma")])
    if deadline is not None:
        inc[1][0].deadline = deadline
    hs = [hospital("H1", "C", caps=TRAUMA_GENERAL, slots=1)] + ([hospital("H2", "E", caps=TRAUMA_GENERAL, slots=1)]
                                                                 if h2 else [])
    return make_world([inc], hospitals=hs, units=[UnitSpec("ALS1", "ALS", "A", "A")],
                      walk_ins=[walk_in("W01", "H1", 3, "serious", "trauma")])


def test_a_declined_award_goes_to_the_next_offer_of_the_same_round():
    evs = run_agents(declined_award_world(), agents(), 12)
    declined = messages(evs, "decline")[0]
    awards = messages(evs, "admission_award")
    assert (declined["sender"], declined["reason"]) == ("H1", "capacity taken since my offer")
    assert [(a["recipient"], a["round"], a["t"]) for a in awards] == [("H1", 1, 3), ("H2", 1, declined["t"] + 1)]
    assert of_type(evs, "reservation_placed")[0]["hospital"] == "H2"
    assert not any(e["type"] == "agent_decision" and e["kind"] == "retry" for e in evs)


def test_a_declined_award_counts_as_a_round_or_escalates_a_critical_at_risk():
    evs = run_agents(declined_award_world(h2=False), agents(), 12)
    assert [m["round"] for m in messages(evs, "admission_counter")] == [2, 3]   # no 5-minute wait, no reset
    assert messages(evs, "admission_counter")[0]["t"] == messages(evs, "decline")[0]["t"] + 1
    evs = run_agents(declined_award_world(h2=False, deadline=20), agents(), 12)
    esc = messages(evs, "escalation")
    assert [e["reason"] for e in esc] == ["award_declined"]
    assert esc[0]["t"] == messages(evs, "decline")[0]["t"] + 1 and not messages(evs, "admission_counter")


def test_a_ready_patient_is_promised_in_one_open_offer_at_a_time():
    from coordination.schemas import AdmissionRequest, RequestedVictim
    w = make_world([], hospitals=[hospital("H1", "C", slots=2, doctors=("trauma", "trauma"), nurses=2)],
                   walk_ins=[walk_in("W01", "H1", 0, "minor", "general"), walk_in("W02", "H1", 0, "serious", "trauma")])
    p = agents()
    events = w.start()
    while w.t < 31:                                   # W01 is ready for release from minute 30
        w.apply(p.decide(w.snapshot(), events))
        events = w.step()
    p.decide(w.snapshot(), events)

    def cfp(victim):
        th = p.open_thread("admission", unit="ALS1")
        req = AdmissionRequest(incident="I01", origin_node="B", ready_at=35,
                               victims=[RequestedVictim(id=victim, severity="critical", care_need="trauma", deadline=90)])
        p._h_admission_admission_cfp(p.bus.send(p.t, "dispatch", "H1", "admission_cfp", th,
                                                proposed_action=req.model_dump()))
        offer = [e for e in p.log if e.get("msg_type") == "admission_offer"][-1]["proposed_action"]
        return [a["victim"] for a in offer["proposed_actions"] if a["action"] == "release_patient"]

    assert cfp("I01-V1") == ["W01"]
    assert cfp("I02-V1") == []                        # W01 is already promised to the first offer
    p.t += 4                                          # the first promise lapses unanswered
    assert cfp("I03-V1") == ["W01"]


# ----- conditional awards and loads aboard (DESIGN 5.2, 8) -----

def step_agents(world, policy, events, until):
    while world.t < until:
        actions = policy.decide(world.snapshot(), events)
        world.log(policy.drain_log())
        events = world.apply(actions) + world.step()
    return events


def conditional_award(p, hospital_id, victim, unit):
    th = p.open_thread("admission", unit=unit)
    p.send("dispatch", hospital_id, "admission_award", th, victim_ids=[victim], proposed_action={
        "victims": [victim], "conditional": [victim], "actions": [], "after_minute": p.t + 11})
    return th


def replies(p, thread, world=None):
    logged = (world.events if world else []) + p.log
    return [(e["msg_type"], e.get("reason")) for e in logged if e.get("type") == "message"
            and e["thread_id"] == thread and e["msg_type"] in ("ack", "decline")]


def test_two_conditional_awards_at_one_hospital_each_get_an_answer():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma"), ("serious", "trauma")])],
                   hospitals=[hospital("H1", "C", slots=2, doctors=("trauma", "trauma"), nurses=2),
                              hospital("H2", "E", caps=TRAUMA_GENERAL, slots=4, doctors=("trauma",), nurses=2)],
                   units=[], walk_ins=[walk_in("W01", "H1", 0, "critical", "trauma")])
    p = agents()
    events = step_agents(w, p, w.start(), 21)              # W01 is transferable, H1's vehicle idle
    a = conditional_award(p, "H1", "I01-V1", "X1")         # delivered next minute: starts W01's transfer
    b = conditional_award(p, "H1", "I01-V2", "X2")         # in the transfer cooldown: no second transfer
    events = step_agents(w, p, events, 22)
    assert replies(p, b, w) == [("decline", "transfer did not happen")]
    assert list(p.pending_reserve["H1"]) == [a]            # the first award is kept, not overwritten
    step_agents(w, p, events, 33)
    assert replies(p, a, w) == [("ack", "firm reservation placed")] and p.pending_reserve["H1"] == {}
    assert of_type(w.events, "transfer_started")[0]["victim"] == "W01"


def test_a_conditional_award_not_started_in_time_is_declined():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma")])], units=[])
    p = agents()
    events = w.start()
    p.decide(w.snapshot(), events)
    th = p.open_thread("admission", unit="X1")
    p.pending_reserve["H1"] = {th: {"victims": ["I01-V1"], "round": 1, "due": 5}}
    p.t = 4
    p._conditional_timeouts()
    assert replies(p, th) == []
    p.t = 5
    p._conditional_timeouts()
    assert replies(p, th) == [("decline", "transfer not started in time")] and p.pending_reserve["H1"] == {}


def test_a_load_aboard_is_exactly_the_victims_aboard():
    w = make_world([incident("I01", "B", 0, [("serious", "trauma"), ("minor", "general")])],
                   units=[UnitSpec("BLS1", "BLS", "A", "A")])
    p = agents()
    events = step_agents(w, p, w.start(), 1)
    while not (w.units["BLS1"].state == "transporting"):
        events = step_agents(w, p, events, w.t + 1)
    load = p.loads["BLS1"]
    assert sorted(load.victims) == ["I01-V1", "I01-V2"]
    load.victims = ["I01-V1"]                              # a stale split: one victim aboard left out
    p.view = w.snapshot()
    p._sync_aboard(load)
    assert load.victims == ["I01-V1", "I01-V2"]


def test_guard_transfer_ready_checks_cooldown_pending_and_failed_threads():
    p = agents()
    p.t = 30
    assert p.guard and p._transfer_ready("H1", "TH001")
    p.cooldown[("transfer", "H1")] = 35
    assert not p._transfer_ready("H1", "TH001")
    p.cooldown.clear()
    p.pending_reserve["H1"] = {"TH002": {"victims": ["V"], "round": 1, "due": 40}}
    assert not p._transfer_ready("H1", "TH001")
    p.pending_reserve["H1"] = {}
    p.failed_conditional.add("TH001")
    assert not p._transfer_ready("H1", "TH001") and p._transfer_ready("H1", "TH003")
    assert AgentPolicy(LLMClient("off"), guard=False)._transfer_ready("H1", "TH001")
