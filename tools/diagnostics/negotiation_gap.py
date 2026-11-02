"""Why do full agents get fewer criticals to care on time than "no negotiation"? (docs/FINDINGS.md)

Runs both variants rules-only on development seeds (never the evaluation seeds 1 to 20), then
traces every critical victim that one variant got to care on time and the other did not.

    python -m tools.diagnostics.negotiation_gap --seeds 101-120 --out /tmp/gap

Prints: criticals on time, the swap counts, award declines by cause, a primary cause per lost
critical (the stage that lost the most time), contributing factors, and aggregate mechanism counts.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from cli import seed_range
from sim.config import default_config, load_places
from sim.runner import load_roads, run

POLICIES = ("agents", "agents-nonego")
ADMISSION = ("admission_cfp", "admission_counter", "admission_offer", "admission_award", "ack", "decline")


# ----- runs -----

def make_runs(seeds, out: Path) -> None:
    cfg = default_config()
    roads, places = load_roads(cfg), load_places()
    for seed in seeds:
        for policy in POLICIES:
            if not (out / f"s{seed}-{policy}").exists():
                run(seed, policy, runs_dir=out, cfg=cfg, roads=roads, places=places, llm_mode="off",
                    run_id=f"s{seed}-{policy}")


def load(out: Path, seed: int, policy: str) -> dict:
    d = out / f"s{seed}-{policy}"
    return {"events": [json.loads(line) for line in open(d / "events.jsonl")],
            "victims": {v["id"]: v for v in json.load(open(d / "victims.json"))},
            "timeline": [json.loads(line) for line in open(d / "timeline.jsonl")]}


def on_time(v: dict) -> bool:
    return v["delivered_at"] is not None and v["delivered_at"] <= v["deadline"]


# ----- one victim -----

def trace(r: dict, vid: str) -> dict:
    ev, v = r["events"], r["victims"][vid]
    inc = v["incident_id"]
    o = {"reported": v["reported_at"], "deadline": v["deadline"], "delivered": v["delivered_at"], "hospital": None}
    trans = next((e for e in ev if e["type"] == "transport_started" and vid in e["victims"]), None)
    if trans:
        u = trans["unit"]
        deliv = next((e for e in ev if e["type"] == "patient_delivered" and e["victim"] == vid), trans)
        o.update(unit=u, transport=trans["t"], hospital=deliv["hospital"])
        disp = [e["t"] for e in ev if e["t"] <= trans["t"] and e.get("unit") == u and e.get("incident") == inc
                and e["type"] in ("unit_dispatched", "unit_redirected")]
        scene = [e["t"] for e in ev if e["type"] == "unit_on_scene" and e["unit"] == u and e["incident"] == inc
                 and e["t"] <= trans["t"]]
        o["dispatch"], o["on_scene"] = (disp or [None])[-1], (scene or [None])[-1]
        cleared = next((e["t"] for e in ev if e["type"] == "scene_cleared" and e["incident"] == inc), 0)
        if o["on_scene"] is not None:
            o["ready"] = max(o["on_scene"], cleared) + 5
    msgs = [e for e in ev if e["type"] == "message" and vid in (e.get("victim_ids") or [])]
    o["declines"] = sum(1 for m in msgs if m["msg_type"] == "decline")
    o["arbiter"] = next((m["proposed_action"].get("option_name") for m in msgs if m["msg_type"] == "arbiter_decision"),
                        None)
    o["no_slot"] = any(e["type"] == "arrived_no_slot" and vid in e["victims"] for e in ev)
    o["reserved_at"] = next((e["t"] for e in ev if e["type"] == "reservation_placed" and e["victim"] == vid), None)
    return o


def stages(o: dict) -> dict[str, int]:
    s = {}
    if o.get("dispatch") is not None:
        s["report_to_dispatch"] = o["dispatch"] - o["reported"]
        if o.get("on_scene") is not None:
            s["drive_to_scene"] = o["on_scene"] - o["dispatch"]
    if o.get("ready") is not None:
        s["wait_at_scene"] = o["transport"] - o["ready"]
    if o.get("transport") is not None and o["delivered"] is not None:
        s["drive_to_hospital"] = o["delivered"] - o["transport"]
    return s


def hold_blocked(r: dict, vid: str, lo: int, hi: int, caps: set[str]) -> bool:
    return any(h["id"] in caps and h["free_slots"] == 0 and any(x["victim"] != vid for x in h["holds"])
               for t in range(lo, min(hi, len(r["timeline"]))) for h in r["timeline"][t]["hospitals"])


def primary_cause(a: dict, n: dict) -> str:
    sa, sn = stages(a), stages(n)
    delta = {k: sa.get(k, 0) - sn.get(k, 0) for k in set(sa) | set(sn)}
    main = max(delta, key=delta.get) if delta else "never transported"
    if main == "report_to_dispatch":
        return "C. ALS reached the critical later (tied up longer on earlier jobs)"
    if main in ("drive_to_hospital", "never transported"):
        return "D. Longer drive to hospital (arrived to no slot, or a farther least-bad hospital)"
    if a["declines"] > n["declines"]:
        return "A. Award declined after an accept offer ('capacity taken since my offer')"
    if a["arbiter"] in ("least_bad", None) and n["arbiter"] == "release_ready_patients":
        return "B. Arbiter had no ready patient to release, fell to least-bad"
    return "E. Waited at the scene with no accept offer"


def factors(a: dict, n: dict, r: dict, vid: str, caps: set[str]) -> set[str]:
    f = set()
    if (a.get("dispatch") or 10**9) - (n.get("dispatch") or 0) >= 5:
        f.add("ALS reached the job later")
    if a["declines"] > n["declines"]:
        f.add("award declined: capacity taken since the offer")
    if hold_blocked(r, vid, a["reported"], a.get("transport") or len(r["timeline"]), caps):
        f.add("a hold for another victim kept a full hospital's slot")
    if a["arbiter"] in ("least_bad", None) and n["arbiter"] == "release_ready_patients":
        f.add("Arbiter: no ready patient left to release")
    if a["no_slot"] and not n["no_slot"]:
        f.add("arrived to no slot, drove on")
    if (a["reserved_at"] or 10**9) > (n["reserved_at"] or 10**9) + 5:
        f.add("firm reservation came later")
    if a["hospital"] != n["hospital"]:
        f.add("different hospital")
    return f


# ----- whole runs -----

def declines_by_cause(r: dict) -> collections.Counter:
    """Award declines 'capacity taken since my offer', by what the declined award relied on."""
    c, last_award, released = collections.Counter(), {}, collections.defaultdict(set)
    for e in r["events"]:
        if e["type"] == "patient_released" and e.get("early"):
            released[e["t"]].add(e["victim"])
        elif e["type"] == "message" and e["msg_type"] == "admission_award":
            last_award[(e["thread_id"], e["recipient"])] = e
        elif e["type"] == "message" and e["msg_type"] == "decline" and e["reason"].startswith("capacity taken"):
            aw = last_award.get((e["thread_id"], e["sender"]))
            rel = [a["victim"] for a in (aw["proposed_action"].get("actions", []) if aw else [])
                   if a["action"] == "release_patient"]
            if not rel:
                c["accept-now offer overtaken"] += 1
            elif any(x in released[e["t"]] | released[e["t"] - 1] for x in rel):
                c["promised ready patient already released for another award"] += 1
            else:
                c["promised a release, capacity gone"] += 1
    return c


def mechanisms(r: dict) -> collections.Counter:
    sev = {k: v["original_severity"] for k, v in r["victims"].items()}
    c = collections.Counter()
    for e in r["events"]:
        if e["type"] == "message" and e["msg_type"] == "arbiter_decision":
            c[f"Arbiter: {e['proposed_action'].get('option_name')}"] += 1
        elif e["type"] == "message" and e["msg_type"] == "escalation":
            c["escalations"] += 1
        elif e["type"] == "patient_released" and e.get("early"):
            c["early releases"] += 1
        elif e["type"] == "action_applied" and e["action"] == "reserve" and e["kind"] == "hold":
            c["holds"] += 1
        elif e["type"] == "transfer_completed":
            c["transfers"] += 1
        elif e["type"] == "message" and e["msg_type"] == "admission_award":
            rel = [a for a in e["proposed_action"].get("actions", []) if a["action"] == "release_patient"]
            crit = any(sev.get(v) == "critical" for v in e["victim_ids"])
            if rel:
                c["releases promised to loads with a critical" if crit else "releases promised to non-critical loads"] += len(rel)
    for m in r["timeline"]:
        c["ALS minutes waiting on scene"] += sum(1 for u in m["units"] if u["kind"] == "ALS" and u["state"] == "on_scene"
                                                 and u["on_scene_since"] is not None and m["t"] - u["on_scene_since"] >= 5)
    return c


def report(seeds, out: Path) -> dict:
    pct = {p: [] for p in POLICIES}
    swaps = collections.Counter()
    primary, contrib = collections.Counter(), collections.Counter()
    declines = {p: collections.Counter() for p in POLICIES}
    mech = {p: collections.Counter() for p in POLICIES}
    for seed in seeds:
        A, N = load(out, seed, "agents"), load(out, seed, "agents-nonego")
        caps_of = {h["id"]: set(h["capabilities"]) for h in A["timeline"][0]["hospitals"]}
        for p, r in (("agents", A), ("agents-nonego", N)):
            crit = [v for v in r["victims"].values() if v["original_severity"] == "critical"]
            if crit:
                pct[p].append(100 * sum(map(on_time, crit)) / len(crit))
            declines[p] += declines_by_cause(r)
            mech[p] += mechanisms(r)
        for vid, v in sorted(A["victims"].items()):
            if v["original_severity"] != "critical":
                continue
            a_ok, n_ok = on_time(v), on_time(N["victims"][vid])
            if a_ok and not n_ok:
                swaps["agents only"] += 1
            if n_ok and not a_ok:
                swaps["no negotiation only"] += 1
                a, n = trace(A, vid), trace(N, vid)
                primary[primary_cause(a, n)] += 1
                contrib.update(factors(a, n, A, vid, {h for h, c in caps_of.items() if v["care_need"] in c}))
    return {"criticals_on_time_pct": {p: round(sum(x) / len(x), 2) for p, x in pct.items()},
            "swaps": dict(swaps), "primary_causes": dict(primary.most_common()),
            "contributing_factors": dict(contrib.most_common()),
            "declines_capacity_taken": {p: dict(c, total=sum(c.values())) for p, c in declines.items()},
            "mechanisms": {p: dict(sorted(c.items())) for p, c in mech.items()}}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="negotiation_gap")
    ap.add_argument("--seeds", type=seed_range, default=tuple(range(101, 121)))
    ap.add_argument("--out", type=Path, required=True, help="folder for the full runs (reused if present)")
    args = ap.parse_args(argv)
    if set(args.seeds) & set(range(1, 21)):
        raise SystemExit("Seeds 1 to 20 are the evaluation seeds; diagnose on other seeds.")
    make_runs(args.seeds, args.out)
    print(json.dumps(report(args.seeds, args.out), indent=1))


if __name__ == "__main__":
    main()
