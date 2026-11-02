from __future__ import annotations

import json
import time

import pytest

from sim.generator import SPAWN_TYPES, spawn_spec
from sim.live import LiveRun
from sim.runner import run
from tests.test_generator_runner import PLACES, SMALL, roads

FILES = ("config.json", "scenario.json", "events.jsonl", "routes.json", "timeline.jsonl", "metrics.json",
         "victims.json")


def live(tmp_path, seed=5, policy="greedy", **kw) -> LiveRun:
    extra = {"llm_mode": "off"} if policy != "greedy" else {}
    return LiveRun(seed, policy, runs_dir=tmp_path / "live", cfg=SMALL, roads=roads(), places=PLACES, **extra, **kw)


def to_minute(lr: LiveRun, minutes: int) -> None:
    while lr._stepper.minutes < minutes:
        lr.advance()


def same_files(a, b) -> None:
    for f in FILES:
        assert (a / f).read_bytes() == (b / f).read_bytes(), f


@pytest.mark.parametrize("policy", ["greedy", "agents"])
def test_a_live_run_saves_exactly_what_a_headless_run_saves(tmp_path, policy):
    lr = live(tmp_path, policy=policy)
    to_minute(lr, SMALL.horizon)
    out = lr.runs_dir / lr.finish()
    ref = run(5, policy, runs_dir=tmp_path / "ref", cfg=SMALL, roads=roads(), places=PLACES,
              **({"llm_mode": "off"} if policy != "greedy" else {}))
    same_files(out, ref)
    assert "spawns" not in json.loads((out / "config.json").read_text())


def test_view_is_shaped_like_a_saved_run(tmp_path):
    lr = live(tmp_path)
    assert lr.view() is None and lr.clock() == 0.0
    to_minute(lr, 12)
    view = lr.view()
    assert len(view["timeline"]) == 12 and view["config"]["policy"] == "greedy"
    trips = [t for ts in view["trips_by_unit"].values() for t in ts]
    assert trips and all("route" in t and "end_t" in t for t in trips)


@pytest.mark.parametrize("policy", ["greedy", "agents"])
def test_a_placed_incident_is_reported_next_minute_and_replays_headless(tmp_path, policy):
    lr = live(tmp_path, policy=policy)
    to_minute(lr, 10)
    raw = lr.spawn("D", "building_fire", 3)
    assert (raw["t"], raw["type"], raw["node"], len(raw["severities"])) == (10, "building_fire", "D", 3)
    to_minute(lr, SMALL.horizon)
    out = lr.runs_dir / lr.finish()
    reported = [e for e in lr.prep.world.events if e["type"] == "incident_reported" and e["incident"] == "S01"]
    assert [e["t"] for e in reported] == [10]
    spawns = json.loads((out / "config.json").read_text())["spawns"]
    assert spawns == [raw]
    ref = run(5, policy, runs_dir=tmp_path / "ref", cfg=SMALL, roads=roads(), places=PLACES, spawns=spawns,
              **({"llm_mode": "off"} if policy != "greedy" else {}))
    same_files(out, ref)


def test_spawn_rejections(tmp_path):
    lr = live(tmp_path)
    with pytest.raises(ValueError, match="run_not_running"):
        lr.spawn("D")
    lr.advance()
    for args, reason in ((("nowhere",), "unknown_node"), (("D", "volcano"), "unknown_incident_type"),
                         (("D", None, 0), "bad_victim_count"), (("D", None, 11), "bad_victim_count")):
        with pytest.raises(ValueError, match=reason):
            lr.spawn(*args)
    to_minute(lr, SMALL.horizon)
    with pytest.raises(ValueError, match="too_late"):
        lr.spawn("D")


def test_spawn_spec_is_seeded_and_fills_what_was_left_open():
    a = spawn_spec(5, SMALL, 1, 30, "D")
    assert a == spawn_spec(5, SMALL, 1, 30, "D") and a != spawn_spec(5, SMALL, 2, 30, "D")
    assert a["type"] in SPAWN_TYPES and 1 <= len(a["severities"]) <= 4
    chosen = spawn_spec(5, SMALL, 1, 30, "D", "bus_fire", 8)
    assert (chosen["type"], len(chosen["severities"]), chosen["surge"]) == ("bus_fire", 8, True)


def wait_for(cond, seconds=10.0) -> None:
    end = time.monotonic() + seconds
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def test_pause_step_resume_in_the_background(tmp_path):
    lr = live(tmp_path, paused=True, rate=10_000).start()
    wait_for(lambda: lr.view() is not None and not lr.ticking())
    assert len(lr.view()["timeline"]) == 1 and lr.clock() == 0.0
    lr.step()
    wait_for(lambda: len(lr.view()["timeline"]) == 2 and not lr.ticking())
    time.sleep(0.05)
    assert len(lr.view()["timeline"]) == 2
    lr.resume()
    wait_for(lambda: lr.done)
    assert (lr.runs_dir / lr.run_id / "metrics.json").exists() and not lr.ticking()


def test_stopping_abandons_the_run(tmp_path):
    lr = live(tmp_path, paused=True).start()
    wait_for(lambda: lr.view() is not None)
    lr.stop()
    lr._thread.join(2)
    assert not lr._thread.is_alive() and not lr.done and not (tmp_path / "live").exists()
