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
