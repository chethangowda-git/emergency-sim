from __future__ import annotations

import json
from dataclasses import replace

from sim.config import SimConfig
from sim.generator import generate
from sim.roads import RoadNetwork
from sim.runner import run
from tests.conftest import tiny_graph

PLACES = {
    "hospitals": [{"id": "H1", "node": "C", "lat": 42.0, "lon": -70.985},
                  {"id": "H2", "node": "E", "lat": 41.99, "lon": -70.985},
                  {"id": "H3", "node": "A", "lat": 42.0, "lon": -71.0}],
    "ambulance_stations": [{"id": "AS1", "node": "B", "lat": 42.0, "lon": -70.99}],
    "fire_stations": [{"id": "FS1", "node": "D", "lat": 41.99, "lon": -71.0},
                      {"id": "FS2", "node": "B", "lat": 42.0, "lon": -70.99}],
}
CFG = SimConfig(emergency_factor=0.85)
SMALL = replace(CFG, horizon=150, incident_window=40, n_incidents=5, surge_window=(5, 20))


def roads() -> RoadNetwork:
    return RoadNetwork(tiny_graph(), 0.85)


def test_generator_is_seeded():
    a = generate(7, CFG, roads(), PLACES).to_dict()
    b = generate(7, CFG, roads(), PLACES).to_dict()
    c = generate(8, CFG, roads(), PLACES).to_dict()
    assert a == b and a != c


def test_generator_follows_scenario_rules():
    sc = generate(3, CFG, roads(), PLACES)
    assert len(sc.incidents) == 12
    assert [i.id for i in sc.incidents] == [f"I{k:02d}" for k in range(1, 13)]
    assert [i.reported_at for i in sc.incidents] == sorted(i.reported_at for i in sc.incidents)
    surges = [i for i in sc.incidents if i.surge]
    assert len(surges) == 1 and 6 <= len(surges[0].victim_ids) <= 10 and 30 <= surges[0].reported_at <= 90
    for inc in sc.incidents:
        assert 0 <= inc.reported_at < 120
        if not inc.surge:
            assert 1 <= len(inc.victim_ids) <= 4
        assert inc.requires == {"building_fire": "fire", "bus_fire": "fire",
                                "structural_collapse": "rescue"}.get(inc.type)
    for v in sc.victims:
        assert v.id.startswith(v.incident_id + "-V")
        assert v.deadline == v.reported_at + CFG.deadline[v.severity]
        if v.severity == "minor":
            assert v.care_need == "general"


def test_walk_ins_and_fleet():
    sc = generate(3, CFG, roads(), PLACES)
    hosp = {h.id: h for h in sc.hospitals}
    for w in sc.walk_ins:
        assert 0 <= w.t < CFG.horizon and w.care_need in hosp[w.hospital].capabilities
    kinds = [u.kind for u in sc.units]
    assert kinds.count("ALS") == 3 and kinds.count("BLS") == 3
    assert kinds.count("FIRE") == 2 and kinds.count("RESCUE") == 1
    assert {u.base for u in sc.units if u.kind == "ALS"} == {"H1", "H2", "H3"}
    assert all(u.base.startswith("FS") for u in sc.units if u.kind in ("FIRE", "RESCUE"))
    assert [d.skills for d in hosp["H1"].doctors] == [("general", "trauma"), ("general", "trauma"),
                                                      ("general", "burns")]


def test_run_writes_all_files_and_is_deterministic(tmp_path):
    a = run(5, "greedy", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES)
    b = run(5, "greedy", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES)
    assert (a.name, b.name) == ("seed5-greedy-001", "seed5-greedy-002")
    for name in ("config.json", "scenario.json", "events.jsonl", "routes.json", "timeline.jsonl",
                 "metrics.json", "victims.json"):
        assert (a / name).exists()
    for name in ("events.jsonl", "routes.json", "metrics.json", "timeline.jsonl", "scenario.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    assert len((a / "timeline.jsonl").read_text().splitlines()) == SMALL.horizon


def test_run_progress_and_metrics(tmp_path):
    seen = []
    out = run(5, "greedy", lambda d, total: seen.append((d, total)), runs_dir=tmp_path, cfg=SMALL,
              roads=roads(), places=PLACES)
    assert seen[0] == (1, SMALL.horizon) and seen[-1] == (SMALL.horizon, SMALL.horizon)
    m = json.loads((out / "metrics.json").read_text())
    assert m["rejected_actions"] == 0 and m["victims_total"] > 0


def test_agents_run_writes_messages_and_is_deterministic(tmp_path):
    a = run(5, "agents", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES, llm_mode="off")
    b = run(5, "agents", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES, llm_mode="off")
    assert a.name == "seed5-agents-001" and (a / "messages.jsonl").exists() and (a / "llm_calls.jsonl").exists()
    for name in ("events.jsonl", "routes.json", "timeline.jsonl", "metrics.json", "messages.jsonl"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    cfg = json.loads((a / "config.json").read_text())
    assert cfg["policy"] == "agents" and cfg["llm"]["mode"] == "off"


def test_replay_through_the_runner_matches_the_recording(tmp_path):
    from coordination.llm import LLMClient
    from tests.test_orchestrator import last_option

    rec = run(5, "agents", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES,
              llm=LLMClient("fake", fake=last_option))
    assert (rec / "llm_calls.jsonl").read_text()
    rep = run(5, "agents", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES, llm_mode="replay",
              replay_from=rec)
    for name in ("events.jsonl", "messages.jsonl", "metrics.json", "routes.json"):
        assert (rec / name).read_bytes() == (rep / name).read_bytes(), name


def test_agent_variants_run(tmp_path):
    for policy in ("agents-nonego", "agents-noarb", "agents-rulehosp"):
        out = run(5, policy, runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES, llm_mode="off")
        assert json.loads((out / "config.json").read_text())["policy"] == policy


def test_agents_on_the_real_network_smoke(tmp_path):
    out = run(42, "agents", runs_dir=tmp_path, llm_mode="off")
    m = json.loads((out / "metrics.json").read_text())
    assert m["victims_delivered"] > 0 and m["negotiation"]["threads"] > 0
