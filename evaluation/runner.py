"""Run an evaluation (DESIGN 11.2, 11.3): every seed with the baseline, the agents and the ablations.

Each run is saved light (config, scenario, metrics, LLM calls) under `runs/evals/<eval-id>/runs/`;
`results.jsonl` holds one row per run and `summary.json`, written last, marks the evaluation done.
The baseline and rules-only agents are deterministic, so they run once per seed; repeats only
apply to live LLM runs (and to replaying them).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from coordination.llm import LLMClient
from evaluation.summary import BASELINE, flatten, summarize
from policies.registry import POLICY_NAMES, make_llm
from sim.config import SimConfig
from sim.roads import RoadNetwork
from sim.runner import RUNS_DIR, run, write_json, write_jsonl

EVALS_DIR = RUNS_DIR / "evals"
LIVE_REPEATS = 3
EvalProgress = Callable[[int, int, str], None]
LLMFactory = Callable[[int, str, int], LLMClient]


@dataclass(frozen=True)
class EvalSettings:
    seeds: tuple[int, ...] = tuple(range(1, 21))
    policies: tuple[str, ...] = tuple(POLICY_NAMES)
    llm_mode: str = "off"
    repeats: int = LIVE_REPEATS
    replay_from: str | None = None

    def __post_init__(self) -> None:
        unknown = [p for p in self.policies if p not in POLICY_NAMES]
        if unknown:
            raise ValueError(f"unknown policies: {', '.join(unknown)}")
        if self.llm_mode not in ("off", "live", "replay"):
            raise ValueError(f"unknown LLM mode {self.llm_mode!r}")
        if self.llm_mode == "replay" and not self.replay_from:
            raise ValueError("replay needs the evaluation to replay")
        ordered = (BASELINE, *[p for p in POLICY_NAMES if p in self.policies and p != BASELINE])
        object.__setattr__(self, "policies", ordered)
        object.__setattr__(self, "seeds", tuple(sorted(set(self.seeds))))

    def repeats_for(self, policy: str) -> int:
        return 1 if policy == BASELINE or self.llm_mode == "off" else self.repeats

    def plan(self) -> list[tuple[int, str, int]]:
        """(seed, policy, repeat) in run order: each seed's baseline first, then the agent variants."""
        return [(seed, policy, r) for seed in self.seeds for policy in self.policies
                for r in range(1, self.repeats_for(policy) + 1)]


def next_eval_id(evals_dir: Path) -> str:
    taken = [int(p.name[5:]) for p in evals_dir.glob("eval-*") if p.name[5:].isdigit()]
    return f"eval-{max(taken, default=0) + 1:03d}"


def run_id_for(seed: int, policy: str, repeat: int) -> str:
    return f"seed{seed}-{policy}-r{repeat}"


def llm_for(settings: EvalSettings, evals_dir: Path, seed: int, policy: str, repeat: int,
            llm_factory: LLMFactory | None) -> LLMClient | None:
    if policy == BASELINE:
        return None
    if llm_factory is not None:
        return llm_factory(seed, policy, repeat)
    if settings.llm_mode == "replay":
        src = evals_dir / settings.replay_from / "runs" / run_id_for(seed, policy, repeat)
        if not (src / "llm_calls.jsonl").exists():
            raise ValueError(f"{settings.replay_from} has no recorded run {src.name} to replay")
        return make_llm("replay", src)
    return make_llm(settings.llm_mode)


def run_evaluation(settings: EvalSettings, progress_cb: EvalProgress | None = None, *,
                   evals_dir: Path = EVALS_DIR, cfg: SimConfig | None = None, roads: RoadNetwork | None = None,
                   places: dict | None = None, llm_factory: LLMFactory | None = None) -> Path:
    evals_dir.mkdir(parents=True, exist_ok=True)
    eval_id = next_eval_id(evals_dir)
    out = evals_dir / eval_id
    runs_dir = out / "runs"
    runs_dir.mkdir(parents=True)
    write_json(out / "settings.json", {"eval_id": eval_id, **asdict(settings)})
    plan = settings.plan()
    rows, scenarios = [], {}
    for done, (seed, policy, repeat) in enumerate(plan, 1):
        llm = llm_for(settings, evals_dir, seed, policy, repeat, llm_factory)
        folder = run(seed, policy, runs_dir=runs_dir, cfg=cfg, roads=roads, places=places, llm=llm, light=True,
                     run_id=run_id_for(seed, policy, repeat))
        scenario = (folder / "scenario.json").read_bytes()
        if scenarios.setdefault(seed, scenario) != scenario:
            raise RuntimeError(f"seed {seed}: {policy} ran a different scenario than the baseline")
        metrics = json.loads((folder / "metrics.json").read_text())
        rows.append({"seed": seed, "policy": policy, "repeat": repeat, "run_id": folder.name, **flatten(metrics)})
        if progress_cb:
            progress_cb(done, len(plan), f"seed {seed} · {policy}" + (f" · repeat {repeat}" if repeat > 1 else ""))
    write_jsonl(out / "results.jsonl", rows)
    write_json(out / "summary.json", summarize(rows))
    return out


def list_evaluations(evals_dir: Path = EVALS_DIR) -> list[str]:
    """Completed evaluations, newest first."""
    if not evals_dir.exists():
        return []
    done = [p.name for p in evals_dir.iterdir() if (p / "summary.json").exists()]
    return sorted(done, reverse=True)


def load_evaluation(eval_id: str, evals_dir: Path = EVALS_DIR) -> dict:
    d = evals_dir / eval_id
    return {"eval_id": eval_id,
            "settings": json.loads((d / "settings.json").read_text()),
            "rows": [json.loads(line) for line in (d / "results.jsonl").read_text().splitlines() if line],
            "summary": json.loads((d / "summary.json").read_text())}


def rebuild_run(eval_id: str, seed: int, policy: str, repeat: int = 1, *, evals_dir: Path = EVALS_DIR,
                runs_dir: Path = RUNS_DIR, cfg: SimConfig | None = None, roads: RoadNetwork | None = None,
                places: dict | None = None) -> Path:
    """The full run folder for one evaluated run, for playback: rules-only runs are re-simulated,
    LLM runs replay their recorded calls, so the result matches the evaluated metrics exactly."""
    src = evals_dir / eval_id / "runs" / run_id_for(seed, policy, repeat)
    cfg_src = json.loads((src / "config.json").read_text())
    llm = None
    if policy != BASELINE:
        mode = (cfg_src.get("llm") or {}).get("mode", "off")
        llm = make_llm("replay", src) if mode in ("live", "replay", "fake") else LLMClient("off")
    return run(seed, policy, runs_dir=runs_dir, cfg=cfg, roads=roads, places=places, llm=llm)
