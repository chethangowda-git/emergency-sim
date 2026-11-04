"""The Simulation page's "Version" choice (DESIGN 13.1): the four systems the Evaluation page compares,
and the recorded runs a replay can use for a seed. Reads config files and run folders only."""
from __future__ import annotations

import json
import re
from pathlib import Path

from sim.config import ROOT

RUNS_DIR = ROOT / "runs"
CATALOG = ROOT / "config" / "evaluations.json"

# key: (label, policy, uses the LLM, guard); labels match the Evaluation page's headline names
VERSIONS = {
    "greedy": ("Greedy baseline", "greedy", False, None),
    "rules": ("Agents, rules only", "agents", False, None),
    "llm-guard": ("Agents, LLM + guard", "agents", True, True),
    "llm-noguard": ("Agents, LLM, no guard", "agents", True, False),
}
EVAL_OF = {"llm-guard": "eval-011", "llm-noguard": "eval-012"}   # the final live evaluations
ABLATIONS = {"agents-nonego": "Agents, no negotiation (ablation)", "agents-noarb": "Agents, no referee (ablation)",
             "agents-rulehosp": "Agents, rule hospitals (ablation)"}


def label(version: str) -> str:
    return VERSIONS[version][0] if version in VERSIONS else ABLATIONS.get(version, version)


def policy(version: str) -> str:
    return VERSIONS[version][1] if version in VERSIONS else version


def uses_llm(version: str) -> bool:
    return version in VERSIONS and VERSIONS[version][2]


def guard(version: str) -> bool | None:
    return VERSIONS[version][3] if version in VERSIONS else None


def _config(path: Path) -> dict:
    return json.loads((path / "config.json").read_text())


def version_of(config: dict) -> str:
    """The version a run belongs to, from its config.json (an ablation keeps its policy name)."""
    if config["policy"] != "agents":
        return "greedy" if config["policy"] == "greedy" else config["policy"]
    llm = config.get("llm") or {}
    if llm.get("mode") in (None, "off"):
        return "rules"
    return "llm-guard" if llm.get("guard") else "llm-noguard"


def _recorded(path: Path) -> bool:
    calls = path / "llm_calls.jsonl"
    return (path / "config.json").exists() and calls.exists() and calls.stat().st_size > 0


def presets(runs_dir: Path = RUNS_DIR, catalog: Path = CATALOG) -> dict[str, str]:
    items = json.loads(catalog.read_text()).get("replay_presets", []) if catalog.exists() else []
    return {p["path"]: p["label"] for p in items if _recorded(runs_dir / p["path"])}


def recordings(version: str, seed: int, runs_dir: Path = RUNS_DIR, catalog: Path = CATALOG) -> dict[str, str]:
    """Recorded runs of this version and seed, as {path under runs/: label}: the demo and other presets,
    the final evaluation's repeats, then live runs saved on this machine."""
    if not uses_llm(version):
        return {}
    out: dict[str, str] = {}
    candidates = [(p, lab) for p, lab in presets(runs_dir, catalog).items()]
    for k in (1, 2, 3):
        candidates.append((f"evals/{EVAL_OF[version]}/runs/seed{seed}-agents-r{k}", f"Final evaluation, recording {k}"))
    saved = sorted(p.name for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.exists() else []
    candidates += [(name, f"Saved live run {name}") for name in saved]
    for path, lab in candidates:
        d = runs_dir / path
        if path in out or not _recorded(d):
            continue
        cfg = _config(d)
        live = (cfg.get("llm") or {}).get("mode") == "live"
        if cfg["seed"] == seed and version_of(cfg) == version and (live or path in presets(runs_dir, catalog)):
            out[path] = lab
    return out


def recorded_seeds(version: str, runs_dir: Path = RUNS_DIR, catalog: Path = CATALOG) -> list[int]:
    """Seeds with at least one recording of this version (for the 'none exists' message)."""
    if not uses_llm(version):
        return []
    seeds = set()
    for d in (runs_dir / "evals" / EVAL_OF[version] / "runs").glob("seed*-agents-r1"):
        if _recorded(d):
            seeds.add(int(re.match(r"seed(\d+)-", d.name)[1]))
    for path in presets(runs_dir, catalog):
        cfg = _config(runs_dir / path)
        if version_of(cfg) == version:
            seeds.add(cfg["seed"])
    return sorted(seeds)


def seeds_text(seeds: list[int]) -> str:
    """'1–20, 376'."""
    parts, start = [], None
    for i, s in enumerate(seeds):
        start = s if start is None else start
        if i + 1 == len(seeds) or seeds[i + 1] != s + 1:
            parts.append(str(s) if s == start else f"{start}–{s}")
            start = None
    return ", ".join(parts)


def source_of(run_dir: Path, runs_dir: Path = RUNS_DIR) -> str | None:
    """The recording a saved run replays (its path under runs/), or the run itself if it was recorded live."""
    llm = _config(run_dir).get("llm") or {}
    if llm.get("mode") == "live":
        return run_dir.name
    src = llm.get("replay_from")
    if llm.get("mode") != "replay" or not src:
        return None
    try:
        return Path(src).resolve().relative_to(runs_dir.resolve()).as_posix()
    except ValueError:
        return None
