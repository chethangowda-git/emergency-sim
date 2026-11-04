"""Run one simulation and save it as a run folder (CLAUDE.md, Phase 1.4)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from coordination.llm import LLMClient
from policies.base import Policy
from policies.registry import POLICY_NAMES, make_llm, make_policy
from sim import metrics
from sim.config import ROOT, SimConfig, default_config, load_area, load_places
from sim.generator import generate, with_spawns
from sim.models import Scenario
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


class Stepper:
    """Advances a world one minute at a time: the policy decides on what it saw, the world applies the
    actions and records the minute, then the clock moves on (DESIGN 9)."""

    def __init__(self, world: World, policy: Policy) -> None:
        self.world, self.policy = world, policy
        self._carry: list[dict] | None = None

    @property
    def minutes(self) -> int:
        return len(self.world.timeline)

    @property
    def done(self) -> bool:
        return self.minutes >= self.world.cfg.horizon

    def advance(self) -> None:
        w = self.world
        events = w.start() if self._carry is None else self._carry + w.step()
        actions = self.policy.decide(w.snapshot(), events)
        w.log(self.policy.drain_log())
        self._carry = w.apply(actions)
        w.record_minute()


def simulate(world: World, policy: Policy, progress_cb: ProgressCb | None = None) -> None:
    stepper = Stepper(world, policy)
    while not stepper.done:
        stepper.advance()
        if progress_cb:
            progress_cb(stepper.minutes, world.cfg.horizon)


def incident_victims(world: World) -> list[dict]:
    return [asdict(v) for v in world.victims.values() if not v.walk_in]


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1) + "\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@dataclass
class Prepared:
    """Everything one run needs before its first minute."""

    seed: int
    scenario: Scenario
    world: World
    policy: Policy
    llm: LLMClient | None
    replay_from: Path | None
    spawns: list[dict] = field(default_factory=list)


def prepare(seed: int, policy: str | Policy = "greedy", *, cfg: SimConfig | None = None,
            roads: RoadNetwork | None = None, places: dict | None = None, llm_mode: str | None = None,
            replay_from: Path | None = None, llm: LLMClient | None = None, spawns: list[dict] = (),
            llm_guard: bool | None = None) -> Prepared:
    cfg = cfg or default_config()
    roads = roads or load_roads(cfg)
    places = places or load_places()
    if isinstance(policy, str) and policy != "greedy" and llm is None:
        llm = make_llm(llm_mode, replay_from)
    pol = make_policy(policy, llm, llm_guard) if isinstance(policy, str) else policy
    scenario = generate(seed, cfg, roads, places)
    world = World(with_spawns(scenario, cfg, roads, list(spawns)), roads, cfg)
    return Prepared(seed, scenario, world, pol, llm, replay_from, list(spawns))


def save(prep: Prepared, runs_dir: Path = RUNS_DIR, run_id: str | None = None, light: bool = False) -> Path:
    world, pol, llm = prep.world, prep.policy, prep.llm
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_id or next_run_id(runs_dir, prep.seed, pol.name)
    out = runs_dir / run_id
    out.mkdir()
    victims = incident_victims(world)
    llm_info = {"mode": llm.mode, "model": llm.model,
                "replay_from": str(prep.replay_from) if prep.replay_from else None} if llm is not None else None
    if llm_info is not None and hasattr(pol, "guard"):
        llm_info["guard"] = pol.guard
    config = {"run_id": run_id, "seed": prep.seed, "policy": pol.name, "area": load_area()["name"],
              "llm": llm_info, "sim": world.cfg.to_dict()}
    if prep.spawns:
        config["spawns"] = prep.spawns
    write_json(out / "config.json", config)
    write_json(out / "scenario.json", with_spawns(prep.scenario, world.cfg, world.roads, prep.spawns).to_dict())
    write_json(out / "metrics.json", metrics.compute(victims, world.timeline, world.events))
    if llm is not None:
        llm.save(out / "llm_calls.jsonl")
    if light:
        return out
    write_jsonl(out / "events.jsonl", world.events)
    write_json(out / "routes.json", [t.to_dict() for t in world.trips])
    write_jsonl(out / "timeline.jsonl", world.timeline)
    write_json(out / "victims.json", victims)
    if hasattr(pol, "bus"):
        write_jsonl(out / "messages.jsonl", [m.model_dump() for m in pol.bus.sent])
    return out


def run(seed: int, policy: str | Policy = "greedy", progress_cb: ProgressCb | None = None, *,
        runs_dir: Path = RUNS_DIR, cfg: SimConfig | None = None, roads: RoadNetwork | None = None,
        places: dict | None = None, llm_mode: str | None = None, replay_from: Path | None = None,
        llm: LLMClient | None = None, light: bool = False, run_id: str | None = None,
        spawns: list[dict] = (), llm_guard: bool | None = None) -> Path:
    """Simulate one seed and save the run folder. Agent policies take an LLM mode: `off` (rules only),
    `live` (records every call) or `replay` from an earlier run folder. `spawns` are incidents placed
    during a live run (DESIGN 3.3), so a run with them can be reproduced headless.

    A light run (evaluation) keeps only config, scenario, metrics and LLM calls; the full
    folder can be rebuilt exactly from them (rules are deterministic, LLM calls replay)."""
    prep = prepare(seed, policy, cfg=cfg, roads=roads, places=places, llm_mode=llm_mode,
                   replay_from=replay_from, llm=llm, spawns=spawns, llm_guard=llm_guard)
    simulate(prep.world, prep.policy, progress_cb)
    return save(prep, runs_dir, run_id, light)
