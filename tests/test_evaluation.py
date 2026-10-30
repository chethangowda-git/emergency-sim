from __future__ import annotations

import json

import pytest

from cli import seed_range
from coordination.llm import LLMClient
from dashboard import eval_views
from evaluation import runner as ev
from evaluation.summary import METRICS, claim, summarize
from tests.test_generator_runner import PLACES, SMALL, roads
from tests.test_orchestrator import last_option


def row(seed, policy, value, repeat=1, **other):
    return {"seed": seed, "policy": policy, "repeat": repeat, "run_id": f"seed{seed}-{policy}-r{repeat}",
            **{k: other.get(k, 0) for k in METRICS}, "critical_on_time_pct": value}


ROWS = [row(1, "greedy", 40.0), row(2, "greedy", 50.0), row(3, "greedy", None), row(4, "greedy", 60.0),
        row(1, "agents", 60.0, 1), row(1, "agents", 80.0, 2), row(2, "agents", 50.0), row(3, "agents", 100.0),
        row(4, "agents", 50.0)]


# ----- summary (DESIGN 11.2) -----

def test_repeats_average_per_seed_and_spread_is_across_seeds():
    s = summarize(ROWS)
    assert s["per_seed"]["agents"] == {1: 70.0, 2: 50.0, 3: 100.0, 4: 50.0}
    assert s["per_seed"]["greedy"] == {1: 40.0, 2: 50.0, 4: 60.0}        # no criticals on seed 3
    agents = s["metrics"]["agents"]["critical_on_time_pct"]
    assert (agents["mean"], agents["sd"], agents["n"]) == (67.5, 23.63, 4)
    assert agents["repeat_sd"] == 14.14                                    # stdev of 60 and 80
    assert s["metrics"]["greedy"]["critical_on_time_pct"]["repeat_sd"] is None


def test_improvement_and_wins_against_the_baseline():
    vs = summarize(ROWS)["versus_baseline"]["agents"]
    assert vs["critical_on_time_pct"] == {"diff": 17.5, "improvement_pct": 35.0, "better": True}
    assert vs["wins"] == {"wins": 1, "ties": 1, "losses": 1, "seeds": 3, "win_rate_pct": 33.3, "won_seeds": [1]}


def test_lower_is_better_metrics_and_zero_baselines():
    rows = [row(1, "greedy", 50.0, avg_report_to_care_min=80.0, rejected_actions=0),
            row(1, "agents", 50.0, avg_report_to_care_min=60.0, rejected_actions=4)]
    vs = summarize(rows)["versus_baseline"]["agents"]
    assert vs["avg_report_to_care_min"] == {"diff": -20.0, "improvement_pct": -25.0, "better": True}
    assert vs["rejected_actions"] == {"diff": 4.0, "improvement_pct": None, "better": False}
    assert vs["threads"]["better"] is None                                 # diagnostic, no direction


def test_claim_sentence():
    assert claim(summarize(ROWS)) == (
        "With the same seeds, incidents, walk-ins and resources, the multi-agent system got 67.5% of critical "
        "victims to care on time versus 50.0% for the greedy baseline, winning on 1 of 3 seeds.")
    assert claim(summarize([row(1, "greedy", 40.0)])) is None


# ----- settings -----

def test_settings_always_include_the_baseline_and_repeat_only_live_runs():
    s = ev.EvalSettings(seeds=(3, 1, 2, 1), policies=("agents-noarb", "agents"))
    assert s.policies == ("greedy", "agents", "agents-noarb") and s.seeds == (1, 2, 3)
    assert {s.repeats_for(p) for p in s.policies} == {1}
    live = ev.EvalSettings(seeds=(1,), policies=("agents",), llm_mode="live", repeats=3)
    assert live.plan() == [(1, "greedy", 1), (1, "agents", 1), (1, "agents", 2), (1, "agents", 3)]
    with pytest.raises(ValueError):
        ev.EvalSettings(policies=("nope",))
    with pytest.raises(ValueError):
        ev.EvalSettings(llm_mode="replay")


def test_seed_ranges():
    assert seed_range("1-3,7") == (1, 2, 3, 7) and seed_range("42") == (42,)


# ----- running an evaluation -----

def evaluate(tmp_path, settings, **kw):
    return ev.run_evaluation(settings, evals_dir=tmp_path / "evals", cfg=SMALL, roads=roads(), places=PLACES, **kw)


def test_evaluation_saves_light_runs_results_and_summary(tmp_path):
    seen = []
    out = evaluate(tmp_path, ev.EvalSettings(seeds=(5, 6), policies=("agents",)), progress_cb=lambda *a: seen.append(a))
    assert out.name == "eval-001" and seen[-1] == (4, 4, "seed 6 · agents")
    assert sorted(p.name for p in (out / "runs").iterdir()) == ["seed5-agents-r1", "seed5-greedy-r1",
                                                                "seed6-agents-r1", "seed6-greedy-r1"]
    assert sorted(p.name for p in (out / "runs" / "seed5-agents-r1").iterdir()) == \
        ["config.json", "llm_calls.jsonl", "metrics.json", "scenario.json"]
    data = ev.load_evaluation("eval-001", tmp_path / "evals")
    assert [(r["seed"], r["policy"]) for r in data["rows"]] == [(5, "greedy"), (5, "agents"), (6, "greedy"),
                                                                (6, "agents")]
    assert data["summary"]["policies"] == ["greedy", "agents"] and ev.list_evaluations(tmp_path / "evals") == ["eval-001"]
    again = evaluate(tmp_path, ev.EvalSettings(seeds=(5, 6), policies=("agents",)))
    assert (again / "results.jsonl").read_bytes() == (out / "results.jsonl").read_bytes()


def test_recorded_evaluation_replays_exactly(tmp_path):
    fake = lambda seed, policy, repeat: LLMClient("fake", fake=last_option)
    rec = evaluate(tmp_path, ev.EvalSettings(seeds=(5,), policies=("agents",), llm_mode="live", repeats=2),
                   llm_factory=fake)
    assert (rec / "runs" / "seed5-agents-r2" / "llm_calls.jsonl").read_text()
    rep = evaluate(tmp_path, ev.EvalSettings(seeds=(5,), policies=("agents",), llm_mode="replay", repeats=2,
                                             replay_from=rec.name))
    assert (rep / "results.jsonl").read_bytes() == (rec / "results.jsonl").read_bytes()
    with pytest.raises(ValueError):
        evaluate(tmp_path, ev.EvalSettings(seeds=(6,), policies=("agents",), llm_mode="replay",
                                           replay_from=rec.name))


def test_rebuilt_run_matches_the_evaluated_metrics(tmp_path):
    fake = lambda seed, policy, repeat: LLMClient("fake", fake=last_option)
    for settings, kw in ((ev.EvalSettings(seeds=(5,), policies=("agents",)), {}),
                         (ev.EvalSettings(seeds=(5,), policies=("agents",), llm_mode="live", repeats=1),
                          {"llm_factory": fake})):
        out = evaluate(tmp_path, settings, **kw)
        for policy in ("greedy", "agents"):
            full = ev.rebuild_run(out.name, 5, policy, evals_dir=tmp_path / "evals", runs_dir=tmp_path / "runs",
                                  cfg=SMALL, roads=roads(), places=PLACES)
            src = out / "runs" / f"seed5-{policy}-r1" / "metrics.json"
            assert (full / "metrics.json").read_bytes() == src.read_bytes()
            assert (full / "timeline.jsonl").exists()


# ----- dashboard formatting -----

def test_eval_tables_and_charts_read_a_saved_summary():
    summary = json.loads(json.dumps(summarize(ROWS)))                      # seed keys become strings
    seeds = eval_views.per_seed_table(summary)
    assert seeds.loc[3].isna().tolist() == [True, False] and seeds.loc[3, "Agents"] == 100.0
    table = eval_views.versus_table(summary, "agents")
    assert table.loc["Criticals on time (%)"].tolist() == [50.0, 67.5, 17.5, "+35.0%", "✅ better"]
    assert eval_views.wins_table(summary).loc["Agents"].tolist() == [1, 1, 1, 3, "33.3%"]
    assert eval_views.policy_table(summary).loc["Criticals on time (%)", "Agents"] == "67.5 ± 23.63 (repeats ± 14.14)"
    bars = eval_views.primary_bars(summary)
    assert list(bars.data[0].y) == ["Greedy baseline", "Agents"]
    assert list(bars.data[0].marker.color) == [eval_views.MUTED, eval_views.ACCENT["dark"]]
    dumbbell = eval_views.per_seed_dumbbell(summary, "agents", "light")
    assert list(dumbbell.data[2].y) == ["Seed 1", "Seed 2", "Seed 4"]
