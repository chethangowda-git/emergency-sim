"""Which LLM choices cost criticals against rules-only agents? (docs/FINDINGS.md; development seeds only)

    python -m tools.diagnostics.llm_losses --live eval-002 --rules eval-001 --evals-dir runs/dev-evals
    python -m tools.diagnostics.llm_losses --live eval-002 --estimate      # fixes and guards, no API calls

Each live run is replayed once per kind of LLM choice with only that kind turned back to the rule.
A critical on time with rules only but not with the LLM is attributed to the kinds whose reversal
alone gets it to care on time. Every run also counts stuck negotiations: loads left "awarded" for
15 minutes or more, conditional awards that met another pending one at the same hospital (it overwrote it
before fix B1), unit minutes at a hospital door with victims aboard outside the load (split loads,
fix B2), awards splitting a load across hospitals, and ALS units on scene without a load.

`--estimate` replays the same recorded calls under the current code, with the guard off and on
(DESIGN 12.8), and with the proposed guards G3 (re-rank only within a 15-minute deadline band) and
G4 (an award plan other than "earliest" only if every critical keeps its earliest arrival and every
victim keeps 15 minutes of slack) applied to the recorded answers. These are estimates: after the
runs diverge, answers come from the most similar recorded question.

Counterfactual replay: a call whose input matches the recording exactly gets the recorded answer.
Once the run has diverged, a call takes the next unused recorded answer for the same decision (same
incident, load, hospital or victim), assuming the model answers a near-identical question the same
way; calls with no match fall back to the rule. With nothing reverted this reproduces the live run,
and with every kind reverted the rules-only run (both checked and printed).
"""
from __future__ import annotations

import argparse
import collections
import json
from dataclasses import replace
from pathlib import Path

from coordination.dispatch import rule_ranking
from coordination.choices import rule_default
from coordination.llm import LLMClient
from coordination.schemas import IncidentReport
from evaluation.runner import load_evaluation
from sim.config import default_config, load_places
from sim.runner import Stepper, incident_victims, load_roads, prepare, simulate
from tools.diagnostics.negotiation_gap import stages, trace

KINDS = ("conditional_offer", "other_admission", "reranking", "shortfall_plan", "extra_units", "award_plan",
         "other")
DEADLINE_BAND = 15   # minutes; the at-risk threshold (DESIGN 4.4, AT_RISK_MINUTES)
STUCK_MINUTES = 15   # longer than any award may wait for its answer (a conditional one: 11 + 1 minutes)


# ----- what the rule would have answered -----

def kinds_of(agent: str, purpose: str, payload: dict, out: dict) -> set[str]:
    """The kinds of deviation from the rule in one recorded answer (empty when it agrees)."""
    if purpose == "assess":
        return {"extra_units"} if out["extra_als"] or out["extra_bls"] else set()
    if purpose == "plan_units":
        reports = [IncidentReport.model_validate(r) for r in payload["reports"]]
        rule = rule_ranking(reports)
        if sorted(out["ranking"]) != sorted(rule):
            return set()   # not a permutation: Dispatch fell back to the rule
        firsts = {s["incident"]: s["options"][0]["id"] for s in payload["shortfalls"]}
        kinds = {"reranking"} if out["ranking"] != rule else set()
        if any(p["option_id"] != firsts[p["incident"]] for p in out["shortfall_plans"] if p["incident"] in firsts):
            kinds.add("shortfall_plan")
        return kinds
    if out.get("option_id") == rule_default(agent, purpose, payload):
        return set()
    if purpose == "admission":
        return {"conditional_offer" if out["option_id"] == "after_transfer" else "other_admission"}
    return {"award_plan" if purpose == "award" else "other"}


def without(kinds: set[str], purpose: str, payload: dict, out: dict) -> dict | None:
    """The answer with those deviations turned back to the rule; None means "use the rule"."""
    if purpose == "assess":
        return {**out, "extra_als": 0, "extra_bls": 0}
    if purpose == "plan_units":
        out = dict(out)
        if "reranking" in kinds:
            out["ranking"] = rule_ranking([IncidentReport.model_validate(r) for r in payload["reports"]])
        if "shortfall_plan" in kinds:
            out["shortfall_plans"] = []
        return out
    return None


# ----- counterfactual replay -----

def loose_key(agent: str, purpose: str, p: dict) -> tuple:
    if purpose == "assess":
        return (agent, purpose, p["incident"]["id"])
    if purpose == "plan_units":
        return (agent, purpose, tuple(sorted(r["incident"] for r in p["reports"])),
                tuple(sorted(s["incident"] for s in p["shortfalls"])))
    if purpose in ("admission", "award"):
        return (agent, purpose, p["request"]["unit"], tuple(v["id"] for v in p["request"]["victims"]))
    if purpose == "transfer":
        return (agent, purpose, p["request"]["victim"])
    if purpose == "staff":
        r = p["request"]
        return (agent, purpose, r["from_hospital"], r["kind"], r["skill"])
    return (agent, purpose, p["escalation"]["victim"], p["escalation"]["trigger"])


def similar_key(agent: str, purpose: str, p: dict) -> tuple | None:
    """A question of the same shape: what the model answered there stands in for an unmatched call."""
    if purpose == "plan_units":
        return (purpose, tuple(sorted(r["incident"] for r in p["reports"])))
    if purpose in ("admission", "staff"):
        return (purpose, p["mood"], tuple(sorted(p["options"])))
    if purpose == "award":
        return (purpose, tuple(sorted(p["options"])))
    if purpose == "transfer":
        return (purpose, p["mood"])
    return None


class Counterfactual(LLMClient):
    def __init__(self, src: Path, revert: frozenset[str] = frozenset(), similar: bool = True,
                 trial: frozenset[str] = frozenset()) -> None:
        super().__init__("replay", replay_path=src / "llm_calls.jsonl")
        self.revert, self.trial = revert, trial
        self.similar = collections.defaultdict(list)
        self.records = [json.loads(line) for line in (src / "llm_calls.jsonl").read_text().splitlines() if line]
        self.used = [False] * len(self.records)
        self.exact, self.loose = collections.defaultdict(list), collections.defaultdict(list)
        for i, r in enumerate(self.records):
            self.exact[(r["agent"], r["purpose"], r["key"])].append(i)
            self.loose[loose_key(r["agent"], r["purpose"], r["input"])].append(i)
            sk = similar_key(r["agent"], r["purpose"], r["input"])
            if similar and sk and r["output"] is not None and not r.get("error"):
                self.similar[sk].append((r["input"]["t"], i))
        self.matched = collections.Counter()

    def _take(self, queue: list[int], how: str) -> int | None:
        i = next((i for i in queue if not self.used[i]), None)
        if i is not None:
            self.used[i] = True
            self.matched[how] += 1
        return i

    def _answer(self, agent, purpose, key, instructions, payload, schema, tools):
        i = self._take(self.exact[(agent, purpose, key)], "exact")
        if i is None:
            i = self._take(self.loose[loose_key(agent, purpose, payload)], "loose")
        if i is None and (near := self.similar.get(similar_key(agent, purpose, payload))):
            i = min(near, key=lambda ti: (abs(ti[0] - payload["t"]), ti[0]))[1]
            self.matched["similar"] += 1
        if i is None:
            self.matched["unmatched"] += 1
            return None, "replay_missing"
        rec = self.records[i]
        if rec.get("error") or rec["output"] is None:
            return rec["output"], rec.get("error")
        kinds = kinds_of(agent, purpose, payload, rec["output"]) & self.revert
        if not kinds:
            return self._tried(purpose, payload, rec["output"])
        self.matched["reverted"] += 1
        out = without(kinds, purpose, payload, rec["output"])
        return (out, None) if out is not None else (None, "reverted_to_rule")

    def _tried(self, purpose: str, payload: dict, out: dict):
        if "G3" in self.trial and purpose == "plan_units" and "reranking" in kinds_of("", purpose, payload, out):
            left = {r["incident"]: r["minutes_left"] for r in payload["reports"]}
            band = lambda i: left[i] // DEADLINE_BAND if left[i] is not None else 10**9
            ranked = sorted(out["ranking"], key=band)          # stable: the LLM's order within a band
            if ranked != out["ranking"]:
                self.matched["G3 changed"] += 1
            return {**out, "ranking": ranked}, None
        if "G4" in self.trial and purpose == "award" and out["option_id"] != "earliest" and not g4_ok(payload, out):
            self.matched["G4 changed"] += 1
            return None, "guard_G4"
        return out, None


def g4_ok(payload: dict, out: dict) -> bool:
    if out["option_id"] not in payload["plans"]:
        return True   # not a current option: pick() falls back to the rule anyway
    arrival = lambda plan: {v: a["arrival"] for a in payload["plans"][plan] for v in a["victims"]}
    mine, first = arrival(out["option_id"]), arrival("earliest")
    for v in payload["request"]["victims"]:
        if v["id"] not in mine:
            continue
        if v["severity"] == "critical" and mine[v["id"]] != first.get(v["id"]):
            return False
        if v["deadline"] is not None and v["deadline"] - mine[v["id"]] < DEADLINE_BAND:
            return False
    return True


def prepared(seed: int, llm: LLMClient, cfg, roads, places, guard: bool | None):
    """`guard` None keeps the run's own setting, and works with code from before the guard (tag v1-live-eval)."""
    extra = {} if guard is None else {"llm_guard": guard}
    return prepare(seed, "agents", cfg=cfg, roads=roads, places=places, llm=llm, **extra)


def victims_after(seed: int, src: Path, revert: frozenset[str], cfg, roads, places,
                  guard: bool | None = None) -> tuple[dict, dict]:
    llm = Counterfactual(src, revert)
    prep = prepared(seed, llm, cfg, roads, places, guard)
    simulate(prep.world, prep.policy)
    return {v["id"]: v for v in incident_victims(prep.world)}, dict(llm.matched)


def full_run(seed: int, llm: LLMClient, cfg, roads, places, guard: bool | None = None) -> dict:
    """Simulate minute by minute, counting stuck negotiations as they happen."""
    prep = prepared(seed, llm, cfg, roads, places, guard)
    w, pol = prep.world, prep.policy
    stepper, counts, awarded_since = Stepper(w, pol), collections.Counter(), {}
    while not stepper.done:
        stepper.advance()
        stuck_minute(w, pol, awarded_since, counts)
    counts["loads stuck 'awarded' at the end"] = sum(1 for load in pol.loads.values() if load.status == "awarded")
    counts.update(event_counts(w.events))
    return {"events": w.events, "timeline": w.timeline, "victims": {v["id"]: v for v in incident_victims(w)},
            "calls": llm.calls, "stuck": counts, "matched": dict(getattr(llm, "matched", {}))}


def stuck_minute(w, pol, awarded_since: dict, c: collections.Counter) -> None:
    for unit, load in pol.loads.items():
        key = (unit, load.thread)
        if load.status != "awarded":
            awarded_since.pop(key, None)
            continue
        awarded_since.setdefault(key, w.t)
        if w.t - awarded_since[key] >= STUCK_MINUTES:
            c["load-minutes stuck 'awarded' for 15+ minutes"] += 1
    for u in w.units.values():
        if u.kind == "ALS" and u.state == "on_scene" and not u.victims and u.id not in pol.loads:
            c["ALS minutes on scene without a load"] += 1
        if u.state == "at_hospital_no_slot":
            c["unit minutes at a door with no slot"] += 1
            load = pol.loads.get(u.id)
            if load is None or not set(u.victims) <= set(load.victims):
                c["door minutes with victims aboard outside the load"] += 1


def event_counts(events: list[dict]) -> collections.Counter:
    c, pending = collections.Counter(), collections.defaultdict(set)
    for e in events:
        if e["type"] == "message" and e["msg_type"] == "admission_award" and e["proposed_action"]["conditional"]:
            c["conditional awards"] += 1
            if pending[e["recipient"]]:
                c["conditional awards while another was pending at that hospital"] += 1
            pending[e["recipient"]].add(e["thread_id"])
        elif e["type"] == "message" and e["msg_type"] in ("ack", "decline"):
            pending[e["sender"]].discard(e["thread_id"])
        elif e["type"] == "agent_decision" and e["kind"] == "award" and len(e["awards"]) > 1:
            c[f"awards splitting a load across hospitals ({e['decided_by']})"] += 1
        elif e["type"] == "transfer_started":
            c["transfers started"] += 1
    return c


def on_time(v: dict) -> bool:
    return v["delivered_at"] is not None and v["delivered_at"] <= v["deadline"]


def criticals(victims: dict) -> dict[str, bool]:
    return {k: on_time(v) for k, v in victims.items() if v["original_severity"] == "critical"}


# ----- mechanisms behind one lost critical -----

def admission_story(r: dict, vid: str) -> dict:
    msgs = [e for e in r["events"] if e["type"] == "message" and vid in (e.get("victim_ids") or [])]
    cond = [m["t"] for m in msgs if m["msg_type"] == "admission_award" and vid in m["proposed_action"]["conditional"]]
    return {"conditional_awards": len(cond),
            "transfer_failed": sum(1 for m in msgs if m["msg_type"] == "decline" and m["reason"] == "transfer did not happen"),
            "first_escalation": next((m["t"] for m in msgs if m["msg_type"] == "escalation"), None),
            "first_award": next((m["t"] for m in msgs if m["msg_type"] == "admission_award"), None),
            "reserved": next((e["t"] for e in r["events"] if e["type"] == "reservation_placed" and e["victim"] == vid), None)}


def stage_lost(live: dict, rules: dict, vid: str) -> str:
    sl, sr = stages(trace(live, vid)), stages(trace(rules, vid))
    delta = {k: sl.get(k, 0) - sr.get(k, 0) for k in set(sl) | set(sr)}
    if not sl.get("drive_to_hospital") and live["victims"][vid]["delivered_at"] is None:
        return "never delivered"
    return max(delta, key=delta.get) if delta else "never delivered"


# ----- facts behind the guardrail proposals -----

def call_facts(calls: list[dict], r: dict) -> collections.Counter:
    c = collections.Counter()
    for k in calls:
        if k["output"] is None or k.get("error"):
            continue
        p, out = k["input"], k["output"]
        kinds = kinds_of(k["agent"], k["purpose"], p, out)
        if k["purpose"] == "admission" and kinds:
            c[f"admission {rule_default(k['agent'], 'admission', p)} -> {out['option_id']}"] += 1
            if "conditional_offer" in kinds:
                accept_now = p["option_details"]["accept_now"]["accept"]
                c["conditional offer when nothing was accepted now" if not accept_now
                  else "conditional offer on top of accepting some now"] += 1
                crit = [v for v in p["request"]["victims"] if v["severity"] == "critical"]
                if crit:
                    c["conditional offer for a load with a critical"] += 1
        if "reranking" in kinds:
            rule = rule_ranking([IncidentReport.model_validate(x) for x in p["reports"]])
            left = {x["incident"]: x["minutes_left"] for x in p["reports"]}
            pos = {i: n for n, i in enumerate(out["ranking"])}
            inv = [(a, b) for n, a in enumerate(rule) for b in rule[n + 1:] if pos[b] < pos[a]]
            gaps = [(left[b] or 10**9) - (left[a] or 10**9) for a, b in inv]
            c["reranked plans"] += 1
            c["reranked plans moving an incident ahead of one with >= 15 fewer minutes left"] += any(
                g >= DEADLINE_BAND for g in gaps)
            c["reranked plans demoting an incident with < 15 minutes left"] += any(
                left[a] is not None and left[a] < DEADLINE_BAND for a, _ in inv)
        if "extra_units" in kinds:
            c["assessments asking for extra units"] += 1
            c["extra ALS requested"] += out["extra_als"]
            c["extra BLS requested"] += out["extra_bls"]
    for e in r["events"]:
        if e["type"] == "message" and e["msg_type"] == "admission_award" and e["proposed_action"]["conditional"]:
            c["conditional awards"] += 1
        elif e["type"] == "message" and e["msg_type"] == "decline" and e["reason"] == "transfer did not happen":
            c["conditional awards declined: transfer did not happen"] += 1
        elif e["type"] == "message" and e["msg_type"] == "escalation":
            c["escalations"] += 1
    return c


def conditional_waits(r: dict) -> list[int]:
    """Minutes from a conditional award to its firm reservation or decline."""
    pending, out = {}, []
    for e in r["events"]:
        if e["type"] != "message":
            continue
        if e["msg_type"] == "admission_award" and e["proposed_action"]["conditional"]:
            pending[(e["thread_id"], e["recipient"])] = e["t"]
        elif e["msg_type"] in ("ack", "decline") and (e["thread_id"], e["sender"]) in pending:
            out.append(e["t"] - pending.pop((e["thread_id"], e["sender"])))
    return out


def extra_while_short(calls: list[dict]) -> collections.Counter:
    """Extra units asked for while another incident's plan, that same minute, lacked an ALS."""
    c = collections.Counter()
    plans = collections.defaultdict(list)
    for k in calls:
        if k["purpose"] == "plan_units":
            plans[k["input"]["t"]].append(k["input"])
    for k in calls:
        if k["purpose"] != "assess" or not k["output"] or not (k["output"]["extra_als"] or k["output"]["extra_bls"]):
            continue
        iid, t = k["input"]["incident"]["id"], k["input"]["incident"].get("reported_at")
        short = [s for p in plans.get(t, []) + plans.get((t or 0) + 1, []) for s in p["shortfalls"]
                 if s["incident"] != iid and s["missing"]["ALS"] > 0]
        c["extra units while another incident lacked an ALS" if short else "extra units with no other ALS shortfall"] += 1
    return c


# ----- report -----

def seed_report(seed: int, live_src: Path, rules_src: Path, cfg, roads, places) -> dict:
    live = full_run(seed, Counterfactual(live_src), cfg, roads, places)
    rules = full_run(seed, LLMClient("off"), cfg, roads, places)
    same_rules = sum(criticals(rules["victims"]).values()) == json.loads(
        (rules_src / "metrics.json").read_text())["criticals_on_time"]
    same_live = sum(criticals(live["victims"]).values()) == json.loads(
        (live_src / "metrics.json").read_text())["criticals_on_time"]   # False once the code has changed
    cl, cr = criticals(live["victims"]), criticals(rules["victims"])
    lost = sorted(v for v in cl if cr[v] and not cl[v])
    gained = sorted(v for v in cl if cl[v] and not cr[v])
    per_kind, only, matched = {}, {}, {}
    for kind in KINDS:
        vic, matched[kind] = victims_after(seed, live_src, frozenset({kind}), cfg, roads, places)
        per_kind[kind] = criticals(vic)
        vic, matched[f"only {kind}"] = victims_after(seed, live_src, frozenset(KINDS) - {kind}, cfg, roads, places)
        only[kind] = criticals(vic)
    vic_all, matched["all"] = victims_after(seed, live_src, frozenset(KINDS), cfg, roads, places)
    all_ok = criticals(vic_all) == cr
    rows = []
    for vid in lost:
        rows.append({"victim": vid, "recovered_by": [k for k in KINDS if per_kind[k][vid]],
                     "lost_with_only": [k for k in KINDS if not only[k][vid]],
                     "stage": stage_lost(live, rules, vid), "live": admission_story(live, vid),
                     "rules": admission_story(rules, vid)})
    return {"seed": seed, "live": sum(cl.values()), "rules": sum(cr.values()), "total": len(cl),
            "lost": rows, "gained": gained, "all_reverted_equals_rules": all_ok,
            "replay_equals_live": same_live, "rules_equal_recorded": same_rules,
            "stuck": {"live": live["stuck"], "rules": rules["stuck"]},
            "revert_one": {k: sum(per_kind[k].values()) for k in KINDS},
            "only_one": {k: sum(only[k].values()) for k in KINDS}, "matched": matched,
            "facts": call_facts(live["calls"], live) + extra_while_short(live["calls"]),
            "rules_escalations": sum(1 for e in rules["events"] if e["type"] == "message"
                                     and e["msg_type"] == "escalation"),
            "conditional_waits": conditional_waits(live)}


def summarize(reports: list[dict]) -> dict:
    lost = [row for r in reports for row in r["lost"]]
    attributed = collections.Counter()
    for row in lost:
        ks = row["recovered_by"]
        for k in ks:
            attributed[k] += 1 / len(ks)
    single = collections.Counter(row["recovered_by"][0] if len(row["recovered_by"]) == 1 else
                                 "several kinds" if row["recovered_by"] else "no single kind" for row in lost)
    live_tot, rules_tot = sum(r["live"] for r in reports), sum(r["rules"] for r in reports)
    facts = sum((r["facts"] for r in reports), collections.Counter())
    waits = [w for r in reports for w in r["conditional_waits"]]
    esc = lambda side: collections.Counter(
        "escalated, live later" if side == "live" and row["live"]["first_escalation"] and row["rules"]["first_escalation"]
        and row["live"]["first_escalation"] > row["rules"]["first_escalation"] else
        "escalated with rules, never live" if side == "live" and row["rules"]["first_escalation"] and not row["live"]["first_escalation"]
        else "other" for row in lost)
    return {
        "criticals_on_time": {"rules_only": rules_tot, "live": live_tot,
                              "total": sum(r["total"] for r in reports)},
        "lost_vs_rules": len(lost), "gained_vs_rules": sum(len(r["gained"]) for r in reports),
        "revert_one_kind_criticals_on_time": {k: sum(r["revert_one"][k] for r in reports) for k in KINDS},
        "only_one_kind_criticals_on_time": {k: sum(r["only_one"][k] for r in reports) for k in KINDS},
        "lost_recovered_by_removing": {k: sum(k in row["recovered_by"] for row in lost) for k in KINDS},
        "lost_again_with_only": {k: sum(k in row["lost_with_only"] for row in lost) for k in KINDS},
        "lost_attributed_fractional": {k: round(v, 1) for k, v in attributed.most_common()},
        "lost_by_sole_cause": dict(single.most_common()),
        "lost_by_stage": dict(collections.Counter(row["stage"] for row in lost).most_common()),
        "lost_with_conditional_award": sum(1 for row in lost if row["live"]["conditional_awards"]),
        "lost_with_failed_transfer": sum(1 for row in lost if row["live"]["transfer_failed"]),
        "lost_escalation": dict(esc("live")),
        "conditional_award_wait_minutes": {"n": len(waits), "mean": round(sum(waits) / len(waits), 1) if waits else None,
                                           "over_5": sum(1 for w in waits if w > 5)},
        "escalations": {"live": facts["escalations"], "rules_only": sum(r["rules_escalations"] for r in reports)},
        "facts": dict(sorted(facts.items())),
        "stuck": {side: dict(sorted(sum((collections.Counter(r["stuck"][side]) for r in reports),
                                         collections.Counter()).items())) for side in ("live", "rules")},
        "checks": {"replay_equals_live": all(r["replay_equals_live"] for r in reports),
                   "rules_equal_recorded": all(r["rules_equal_recorded"] for r in reports),
                   "all_reverted_equals_rules": all(r["all_reverted_equals_rules"] for r in reports),
                   "matched": dict(sum((collections.Counter(m) for r in reports for m in r["matched"].values()),
                                       collections.Counter()))},
    }


# ----- free estimates of fixes and guards -----

SCENARIOS = {"B1+B2": (False, frozenset()), "B1+B2+G1+G2": (True, frozenset()),
             "B1+B2+G1+G2+G3": (True, frozenset({"G3"})), "B1+B2+G1+G2+G4": (True, frozenset({"G4"})),
             "B1+B2+G1+G2+G3+G4": (True, frozenset({"G3", "G4"}))}


def pct(victims: dict) -> float | None:
    c = criticals(victims)
    return 100 * sum(c.values()) / len(c) if c else None


def estimate(seeds, live_runs: dict[int, Path], cfg, roads, places) -> dict:
    out = {name: {"on_time": 0, "pct": [], "stuck": collections.Counter(), "matched": collections.Counter()}
           for name in ("rules only", "recorded live", *SCENARIOS)}
    total = 0
    for seed in seeds:
        rules = full_run(seed, LLMClient("off"), cfg, roads, places)
        total += len(criticals(rules["victims"]))
        runs = {"rules only": rules}
        for name, (guard, trial) in SCENARIOS.items():
            runs[name] = full_run(seed, Counterfactual(live_runs[seed], trial=trial), cfg, roads, places, guard)
        for name, r in runs.items():
            add(out[name], r)
        m = json.loads((live_runs[seed] / "metrics.json").read_text())
        out["recorded live"]["on_time"] += m["criticals_on_time"]
        if m["criticals_total"]:
            out["recorded live"]["pct"].append(100 * m["criticals_on_time"] / m["criticals_total"])
        print(f"seed {seed}: " + ", ".join(f"{k} {sum(criticals(r['victims']).values())}" for k, r in runs.items()),
              flush=True)
    return {name: {"criticals_on_time": f"{o['on_time']} of {total}",
                   "criticals_on_time_pct_mean": round(sum(o["pct"]) / len(o["pct"]), 2),
                   "stuck": dict(sorted(o["stuck"].items())),
                   "replay": dict(sorted(o["matched"].items()))} for name, o in out.items()}


def add(acc: dict, r: dict) -> None:
    acc["on_time"] += sum(criticals(r["victims"]).values())
    if (p := pct(r["victims"])) is not None:
        acc["pct"].append(p)
    acc["stuck"].update(r["stuck"])
    acc["matched"].update(r["matched"])


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="llm_losses")
    ap.add_argument("--live", required=True)
    ap.add_argument("--rules", help="the rules-only evaluation (for the loss attribution)")
    ap.add_argument("--estimate", action="store_true", help="estimate the fixes and guards instead")
    ap.add_argument("--evals-dir", type=Path, default=Path("runs/dev-evals"))
    ap.add_argument("--out", type=Path, help="also write the per-seed details here as JSON")
    args = ap.parse_args(argv)
    live = load_evaluation(args.live, args.evals_dir)
    seeds = sorted({r["seed"] for r in live["rows"] if r["policy"] == "agents"})
    if set(seeds) & set(range(1, 21)):
        raise SystemExit("Seeds 1 to 20 are the evaluation seeds; diagnose on other seeds.")
    rows = {r["seed"]: r["run_id"] for r in live["rows"] if r["policy"] == "agents" and r["repeat"] == 1}
    sim = json.loads((args.evals_dir / args.live / "runs" / rows[seeds[0]] / "config.json").read_text())["sim"]
    cfg = replace(default_config(), n_incidents=sim["n_incidents"])
    roads, places = load_roads(cfg), load_places()
    if args.estimate:
        runs = {s: args.evals_dir / args.live / "runs" / rows[s] for s in seeds}
        print(json.dumps(estimate(seeds, runs, cfg, roads, places), indent=1))
        return
    if not args.rules:
        raise SystemExit("--rules is needed for the loss attribution")
    rules = load_evaluation(args.rules, args.evals_dir)
    rrows = {r["seed"]: r["run_id"] for r in rules["rows"] if r["policy"] == "agents"}
    reports = []
    for seed in seeds:
        reports.append(seed_report(seed, args.evals_dir / args.live / "runs" / rows[seed],
                                   args.evals_dir / args.rules / "runs" / rrows[seed], cfg, roads, places))
        print(f"seed {seed}: rules {reports[-1]['rules']}, live {reports[-1]['live']} of {reports[-1]['total']}",
              flush=True)
    if args.out:
        args.out.write_text(json.dumps(reports, indent=1, default=dict))
    print(json.dumps(summarize(reports), indent=1))


if __name__ == "__main__":
    main()
