"""List, load and save runs, and start live runs. The dashboard only reads run folders and live runs'
published state (plus starting and steering a run)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from sim.config import ROOT
from sim.roads import Route

RUNS_DIR = ROOT / "runs"
REQUIRED = ("config.json", "events.jsonl", "routes.json", "timeline.jsonl", "metrics.json", "victims.json")


def list_runs(runs_dir: Path = RUNS_DIR, policies: tuple[str, ...] | None = None) -> list[str]:
    """Complete runs, newest first, optionally only those of the given policies."""
    if not runs_dir.exists():
        return []
    done = [p for p in runs_dir.iterdir() if p.is_dir() and all((p / f).exists() for f in REQUIRED)]
    names = [p.name for p in sorted(done, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)]
    if policies is None:
        return names
    return [n for n in names if run_config(n, runs_dir).get("policy") in policies]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def route_of(trip: dict) -> Route:
    return Route(tuple(trip["nodes"]), tuple(tuple(p) for p in trip["points"]), tuple(trip["cum"]),
                 (), 0.0, trip["minutes"])


def load_run(run_id: str, runs_dir: Path = RUNS_DIR) -> dict:
    d = runs_dir / run_id
    trips = json.loads((d / "routes.json").read_text())
    by_unit: dict[str, list[dict]] = {}
    for trip in trips:
        trip["end_t"] = trip["ended_at"] if trip["ended_at"] is not None else trip["arrive_t"]
        trip["route"] = route_of(trip)
        by_unit.setdefault(trip["unit"], []).append(trip)
    return {
        "run_id": run_id,
        "config": json.loads((d / "config.json").read_text()),
        "events": _jsonl(d / "events.jsonl"),
        "trips_by_unit": by_unit,
        "timeline": _jsonl(d / "timeline.jsonl"),
        "metrics": json.loads((d / "metrics.json").read_text()),
        "victims": json.loads((d / "victims.json").read_text()),
        "llm_calls": _jsonl(d / "llm_calls.jsonl") if (d / "llm_calls.jsonl").exists() else [],
    }


def scenario_settings(n_incidents: int | None, replay_from: str | None, runs_dir: Path = RUNS_DIR):
    """Config and placed incidents for a new run. A replay always uses the recorded run's incident count
    and the incidents placed during it."""
    from dataclasses import replace

    from sim.config import default_config

    spawns: list[dict] = []
    if replay_from:
        n_incidents, spawns = incidents_of(replay_from, runs_dir), spawns_of(replay_from, runs_dir)
    return (replace(default_config(), n_incidents=n_incidents) if n_incidents else None), spawns


def run_and_save(seed: int, policy: str, progress_cb: Callable[[int, int], None] | None = None,
                 roads=None, llm_mode: str | None = None, replay_from: str | None = None,
                 n_incidents: int | None = None, spawns: list[dict] | None = None) -> str:
    """A whole run at once (the baseline behind Compare)."""
    from sim.runner import run

    cfg, recorded = scenario_settings(n_incidents, replay_from)
    src = RUNS_DIR / replay_from if replay_from else None
    return run(seed, policy, progress_cb, roads=roads, llm_mode=llm_mode, replay_from=src, cfg=cfg,
               spawns=spawns if spawns is not None else recorded).name


def start_live(seed: int, policy: str, *, paused: bool, rate: float, roads=None, llm_mode: str | None = None,
               replay_from: str | None = None, n_incidents: int | None = None, skip_quiet: bool = False):
    """A live run (DESIGN 13.3), already started in the background; saved under runs/ when it finishes."""
    from sim.live import LiveRun

    cfg, spawns = scenario_settings(n_incidents, replay_from)
    extra = {} if policy == "greedy" else {"llm_mode": llm_mode,
                                            "replay_from": RUNS_DIR / replay_from if replay_from else None}
    return LiveRun(seed, policy, rate=rate, paused=paused, skip_quiet=skip_quiet, runs_dir=RUNS_DIR, cfg=cfg,
                   roads=roads, spawns=spawns, **extra).start()


def incidents_of(run_id: str, runs_dir: Path = RUNS_DIR) -> int:
    return run_config(run_id, runs_dir)["sim"]["n_incidents"]


def spawns_of(run_id: str, runs_dir: Path = RUNS_DIR) -> list[dict]:
    return run_config(run_id, runs_dir).get("spawns", [])


def replayable_runs(policy: str, runs_dir: Path = RUNS_DIR) -> list[str]:
    """Saved runs of this agent policy that recorded at least one LLM call."""
    out = []
    for run_id in list_runs(runs_dir):
        d = runs_dir / run_id
        cfg = json.loads((d / "config.json").read_text())
        calls = d / "llm_calls.jsonl"
        if cfg.get("policy") == policy and calls.exists() and calls.stat().st_size > 0:
            out.append(run_id)
    return out


def run_config(run_id: str, runs_dir: Path = RUNS_DIR) -> dict:
    return json.loads((runs_dir / run_id / "config.json").read_text())


def baseline_for(run_id: str, runs_dir: Path = RUNS_DIR) -> str | None:
    """The newest greedy run of the same seed on the very same scenario (byte-identical scenario.json)."""
    d = runs_dir / run_id
    seed, scenario = run_config(run_id, runs_dir)["seed"], (d / "scenario.json").read_bytes()
    for other in list_runs(runs_dir):
        o = runs_dir / other
        cfg = run_config(other, runs_dir)
        if (cfg.get("policy") == "greedy" and cfg.get("seed") == seed and (o / "scenario.json").exists()
                and (o / "scenario.json").read_bytes() == scenario):
            return other
    return None


def replay_presets(runs_dir: Path = RUNS_DIR) -> list[dict]:
    """Recorded runs the Simulation page offers first (config/evaluations.json): the demo, then a final-evaluation
    run; paths under runs/ such as demo/demo-seed376, only those on this machine."""
    catalog = ROOT / "config" / "evaluations.json"
    presets = json.loads(catalog.read_text()).get("replay_presets", []) if catalog.exists() else []
    return [p for p in presets if (runs_dir / p["path"] / "llm_calls.jsonl").exists()]


def featured_replay(runs_dir: Path = RUNS_DIR) -> str | None:
    presets = replay_presets(runs_dir)
    return presets[0]["path"] if presets else None


def highlights() -> list[dict]:
    path = ROOT / "config" / "highlights.json"
    return json.loads(path.read_text()).get("highlights", []) if path.exists() else []


def materialize(ref: dict, roads=None) -> str:
    """A full run folder for a highlight's run: an evaluated run is rebuilt, a saved recording replayed."""
    from evaluation.runner import rebuild_run

    if (done := rebuilt(ref)) is not None:
        return done
    if ref["kind"] == "eval":
        return rebuild_run(ref["eval"], ref["seed"], ref["policy"], ref["repeat"], roads=roads).name
    cfg = run_config(ref["path"])
    return run_and_save(cfg["seed"], cfg["policy"], roads=roads, llm_mode="replay", replay_from=ref["path"])


def source_of(ref: dict, runs_dir: Path = RUNS_DIR) -> Path:
    if ref["kind"] == "eval":
        from evaluation.runner import EVALS_DIR, run_id_for
        return EVALS_DIR / ref["eval"] / "runs" / run_id_for(ref["seed"], ref["policy"], ref["repeat"])
    return runs_dir / ref["path"]


def rebuilt(ref: dict, runs_dir: Path = RUNS_DIR) -> str | None:
    """An earlier full rebuild of the same recording (same seed, policy and replayed calls), if any."""
    src = source_of(ref, runs_dir)
    if not (src / "config.json").exists():
        return None
    want = json.loads((src / "config.json").read_text())
    for run_id in list_runs(runs_dir, policies=(want["policy"],)):
        cfg = run_config(run_id, runs_dir)
        same = cfg.get("seed") == want["seed"] and cfg.get("spawns", []) == want.get("spawns", [])
        if same and (runs_dir / run_id / "llm_calls.jsonl").exists() and \
                (runs_dir / run_id / "metrics.json").read_bytes() == (src / "metrics.json").read_bytes() and \
                (cfg.get("llm") or {}).get("mode") == "replay":
            return run_id
    return None
