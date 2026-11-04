from __future__ import annotations

import json

from dashboard import versions as V
from sim.runner import write_json
from tools import backfill_metrics


def recorded(path, seed, policy="agents", mode="live", guard=True, replay_from=None):
    path.mkdir(parents=True)
    (path / "config.json").write_text(json.dumps({"seed": seed, "policy": policy, "llm": {
        "mode": mode, "guard": guard, "replay_from": replay_from}, "sim": {"n_incidents": 12}}))
    (path / "llm_calls.jsonl").write_text('{"x": 1}\n')


def test_versions_match_the_evaluation_names_and_their_guard():
    catalog = json.loads(V.CATALOG.read_text())
    assert [V.label(v) for v in V.VERSIONS] == [h["label"] for h in catalog["headline"]]
    assert (V.policy("greedy"), V.uses_llm("rules"), V.guard("llm-guard"), V.guard("llm-noguard")) == \
        ("greedy", False, True, False)
    assert V.version_of({"policy": "agents", "llm": {"mode": "off"}}) == "rules"
    assert V.version_of({"policy": "agents", "llm": {"mode": "replay", "guard": False}}) == "llm-noguard"
    assert V.version_of({"policy": "agents-noarb"}) == "agents-noarb" and "ablation" in V.label("agents-noarb")


def test_replay_finds_the_recordings_for_a_seed_and_version(tmp_path):
    runs = tmp_path / "runs"
    catalog = tmp_path / "evaluations.json"
    catalog.write_text(json.dumps({"replay_presets": [{"path": "demo/d376", "label": "Demo"}]}))
    recorded(runs / "demo" / "d376", 376)
    for k in (1, 2):
        recorded(runs / "evals" / "eval-011" / "runs" / f"seed9-agents-r{k}", 9)
    recorded(runs / "evals" / "eval-012" / "runs" / "seed9-agents-r1", 9, guard=False)
    recorded(runs / "seed9-agents-004", 9)
    recorded(runs / "seed9-agents-005", 9, mode="replay", replay_from=str(runs / "seed9-agents-004"))
    assert V.recordings("llm-guard", 376, runs, catalog) == {"demo/d376": "Demo"}
    assert list(V.recordings("llm-guard", 9, runs, catalog)) == [
        "evals/eval-011/runs/seed9-agents-r1", "evals/eval-011/runs/seed9-agents-r2", "seed9-agents-004"]
    assert list(V.recordings("llm-noguard", 9, runs, catalog)) == ["evals/eval-012/runs/seed9-agents-r1"]
    assert V.recordings("llm-noguard", 42, runs, catalog) == {} and V.recordings("rules", 9, runs, catalog) == {}
    assert V.recorded_seeds("llm-guard", runs, catalog) == [9, 376]
    assert V.seeds_text(list(range(1, 21)) + [376]) == "1–20, 376"
    assert V.source_of(runs / "seed9-agents-005", runs) == "seed9-agents-004"
    assert V.source_of(runs / "seed9-agents-004", runs) == "seed9-agents-004"


def test_backfill_adds_new_metrics_but_never_changes_a_saved_one(tmp_path):
    path = tmp_path / "metrics.json"
    write_json(path, {"a": 1, "b": 2.5})
    assert backfill_metrics.merge(path, {"a": 1, "b": 2.5}) == "same"
    assert backfill_metrics.merge(path, {"a": 1, "b": 2.6, "c": 3}) == "mismatch: b"
    assert json.loads(path.read_text()) == {"a": 1, "b": 2.5}
    assert backfill_metrics.merge(path, {"c": 3, "a": 1, "b": 2.5}) == "added"
    assert list(json.loads(path.read_text())) == ["c", "a", "b"]          # the order a new run writes
