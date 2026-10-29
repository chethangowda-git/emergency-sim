"""Run one simulation and save it as a run folder (CLAUDE.md, Phase 1.4)."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from coordination.llm import LLMClient
from policies.base import Policy
from policies.registry import POLICY_NAMES, make_llm, make_policy
from sim import metrics
from sim.config import ROOT, SimConfig, default_config, load_area, load_places
from sim.generator import generate
from sim.roads import RoadNetwork, load_graph
from sim.world import World

RUNS_DIR = ROOT / "runs"
POLICIES = POLICY_NAMES

ProgressCb = Callable[[int, int], None]


def load_roads(cfg: SimConfig) -> RoadNetwork:
    return RoadNetwork(load_graph(), cfg.emergency_factor)


def next_run_id(runs_dir: Path, seed: int, policy: str) -> str:
    prefix = f"seed{seed}-{policy}-"
    taken = [int(p.name[len(prefix):]) for p in runs_dir.glob(f"{prefix}*") if p.name[len(prefix):].isdigit()]
    return f"{prefix}{max(taken, default=0) + 1:03d}"


def simulate(world: World, policy: Policy, progress_cb: ProgressCb | None = None) -> None:
    horizon = world.cfg.horizon
    events = world.start()
    while True:
        actions = policy.decide(world.snapshot(), events)
        world.log(policy.drain_log())
        action_events = world.apply(actions)
        world.record_minute()
        if progress_cb:
            progress_cb(world.t + 1, horizon)
        if world.t >= horizon - 1:
            return
        events = action_events + world.step()


def incident_victims(world: World) -> list[dict]:
    return [asdict(v) for v in world.victims.values() if not v.walk_in]


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1) + "\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def run(seed: int, policy: str | Policy = "greedy", progress_cb: ProgressCb | None = None, *,
        runs_dir: Path = RUNS_DIR, cfg: SimConfig | None = None, roads: RoadNetwork | None = None,
        places: dict | None = None, llm_mode: str | None = None, replay_from: Path | None = None,
        llm: LLMClient | None = None) -> Path:
    """Simulate one seed and save the run folder. Agent policies take an LLM mode: `off` (rules only),
    `live` (records every call) or `replay` from an earlier run folder."""
    cfg = cfg or default_config()
    roads = roads or load_roads(cfg)
    places = places or load_places()
    if isinstance(policy, str) and policy != "greedy" and llm is None:
        llm = make_llm(llm_mode, replay_from)
    pol = make_policy(policy, llm) if isinstance(policy, str) else policy
    scenario = generate(seed, cfg, roads, places)
    world = World(scenario, roads, cfg)
    simulate(world, pol, progress_cb)

    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = next_run_id(runs_dir, seed, pol.name)
    out = runs_dir / run_id
    out.mkdir()
    victims = incident_victims(world)
    llm_info = {"mode": llm.mode, "model": llm.model, "replay_from": str(replay_from) if replay_from else None} \
        if llm is not None else None
    write_json(out / "config.json", {"run_id": run_id, "seed": seed, "policy": pol.name,
                                     "area": load_area()["name"], "llm": llm_info, "sim": cfg.to_dict()})
    write_json(out / "scenario.json", scenario.to_dict())
    write_jsonl(out / "events.jsonl", world.events)
    write_json(out / "routes.json", [t.to_dict() for t in world.trips])
    write_jsonl(out / "timeline.jsonl", world.timeline)
    write_json(out / "metrics.json", metrics.compute(victims, world.timeline, world.events))
    write_json(out / "victims.json", victims)
    if hasattr(pol, "bus"):
        write_jsonl(out / "messages.jsonl", [m.model_dump() for m in pol.bus.sent])
        llm.save(out / "llm_calls.jsonl")
    return out
