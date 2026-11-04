"""Readable evaluation names and the Evaluation page's overview (DESIGN 13.1), from saved summaries.

The text comes from config/evaluations.json; every number is read from the evaluations' summary.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from evaluation.summary import PRIMARY
from sim.config import ROOT

CATALOG = ROOT / "config" / "evaluations.json"
MODES = {"off": "rules only", "live": "live LLM", "replay": "replay"}
Loader = Callable[[str], dict]   # eval id -> {"settings", "summary", ...} (evaluation.runner.load_evaluation)


def load_catalog(path: Path = CATALOG) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"evaluations": {}}


def auto_label(eval_id: str, settings: dict) -> str:
    seeds = settings["seeds"]
    mode = MODES.get(settings["llm_mode"], settings["llm_mode"])
    if settings["llm_mode"] == "replay":
        mode += f" of {settings.get('replay_from')}"
    elif settings["llm_mode"] == "live" and settings.get("llm_guard") is not None:
        mode += ", guard on" if settings["llm_guard"] else ", guard off"
    variants = len(settings["policies"]) - 1
    return f"{eval_id} · {mode} · {variants} agent variant{'s' * (variants != 1)} (seeds {seeds[0]}-{seeds[-1]})"


def label(eval_id: str, settings: dict, catalog: dict) -> str:
    return catalog["evaluations"].get(eval_id) or auto_label(eval_id, settings)


def visible(eval_ids: list[str], catalog: dict, show_all: bool) -> list[str]:
    """Named evaluations in catalog order; with show_all, every other one after them, newest first."""
    named = [e for e in catalog["evaluations"] if e in eval_ids]
    return named + ([e for e in eval_ids if e not in named] if show_all else [])


def stat(loader: Loader, eval_id: str, policy: str) -> dict | None:
    """The primary metric of one policy in one evaluation, or None if it is not here."""
    try:
        metrics = loader(eval_id)["summary"]["metrics"]
    except (FileNotFoundError, KeyError):
        return None
    return metrics.get(policy, {}).get(PRIMARY)


def headline(catalog: dict, loader: Loader) -> list[dict]:
    out = []
    for item in catalog.get("headline", []):
        s = stat(loader, item["eval"], item["policy"])
        if s is not None:
            out.append({**item, "mean": s["mean"], "sd": s["sd"], "n": s["n"], "repeat_sd": s.get("repeat_sd")})
    return out


def contributions(catalog: dict, loader: Loader) -> list[dict]:
    """Each step's criticals on time and its change against the row it builds on."""
    rows, means = [], {}
    for item in catalog.get("contributions", []):
        s = stat(loader, item["eval"], item["policy"])
        if s is None:
            continue
        means[item["label"]] = s["mean"]
        ref = means.get(item.get("vs"))
        change = None if ref is None or s["mean"] is None else round(s["mean"] - ref, 1)
        rows.append({"label": item["label"], "mean": s["mean"], "change": change, "side": item.get("side", False),
                     "part": item.get("part", "this part"), "plural": item.get("plural", False)})
    return rows


def resources(catalog: dict, loader: Loader) -> list[dict]:
    """The headline versions with all their summary metrics, for the resource use and conflicts table."""
    out = []
    for item in catalog.get("headline", []):
        try:
            metrics = loader(item["eval"])["summary"]["metrics"][item["policy"]]
        except (FileNotFoundError, KeyError):
            continue
        out.append({"label": item["label"], "metrics": metrics})
    return out


def timeline(catalog: dict, loader: Loader) -> list[dict]:
    out = []
    for step in catalog.get("timeline", []):
        s = stat(loader, step["eval"], step["policy"])
        out.append({**step, "mean": None if s is None else s["mean"]})
    return out
