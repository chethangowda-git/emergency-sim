"""Metrics, DESIGN 11.1 (Phase 1 subset), as pure functions over a finished run.

Inputs: incident victims (dicts with original_severity, reported_at, deadline, delivered_at),
the per-minute timeline (World snapshots) and the event log.
"""
from __future__ import annotations

from collections import Counter

IDLE_UTILIZATION = 0.60
BUSY_EXCLUDES = ("idle", "returning")


def pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 2) if whole else None


def critical_on_time(victims: list[dict]) -> dict:
    crit = [v for v in victims if v["original_severity"] == "critical"]
    on_time = [v for v in crit if v["delivered_at"] is not None and v["delivered_at"] <= v["deadline"]]
    return {"criticals_total": len(crit), "criticals_on_time": len(on_time),
            "critical_on_time_pct": pct(len(on_time), len(crit))}


def unserved_criticals(victims: list[dict]) -> int:
    return sum(1 for v in victims if v["original_severity"] == "critical" and v["delivered_at"] is None)


def avg_report_to_care(victims: list[dict]) -> float | None:
    times = [v["delivered_at"] - v["reported_at"] for v in victims if v["delivered_at"] is not None]
    return round(sum(times) / len(times), 2) if times else None


def er_peaks(timeline: list[dict]) -> dict[str, float]:
    peaks: dict[str, float] = {}
    for minute in timeline:
        for h in minute["hospitals"]:
            load = 100 * h["er_used"] / h["er_slots"]
            peaks[h["id"]] = round(max(peaks.get(h["id"], 0.0), load), 2)
    return peaks


def er_load_balance(peaks: dict[str, float]) -> float | None:
    return round(max(peaks.values()) - min(peaks.values()), 2) if peaks else None


def idle_minutes(timeline: list[dict]) -> dict[str, int]:
    """Minutes under 60% utilization while any incident victim is still waiting for care."""
    out: Counter = Counter()
    for minute in timeline:
        for h in minute["hospitals"]:
            out[h["id"]] += int(minute["waiting_victims"] > 0 and h["utilization"] < IDLE_UTILIZATION)
    return dict(sorted(out.items()))


def overload_minutes(timeline: list[dict]) -> dict[str, int]:
    """Minutes with a patient waiting in the ER for staff, or the ER at (or over) 100%."""
    out: Counter = Counter()
    for minute in timeline:
        for h in minute["hospitals"]:
            out[h["id"]] += int(h["waiting_for_staff"] > 0 or h["er_used"] >= h["er_slots"])
    return dict(sorted(out.items()))


def response_times(events: list[dict], ambulances: set[str]) -> dict[str, int]:
    """Minutes from report to the first ambulance on scene, per incident."""
    reported = {e["incident"]: e["t"] for e in events if e["type"] == "incident_reported"}
    first: dict[str, int] = {}
    for e in events:
        if e["type"] == "unit_on_scene" and e["unit"] in ambulances and e["incident"] not in first:
            first[e["incident"]] = e["t"] - reported[e["incident"]]
    return dict(sorted(first.items()))


def unit_utilization(timeline: list[dict]) -> dict[str, float]:
    """Share of minutes each unit kind spends busy (anything but idle or returning)."""
    busy: Counter = Counter()
    total: Counter = Counter()
    for minute in timeline:
        for u in minute["units"]:
            total[u["kind"]] += 1
            busy[u["kind"]] += int(u["state"] not in BUSY_EXCLUDES)
    out = {k: pct(busy[k], total[k]) for k in sorted(total)}
    out["overall"] = pct(sum(busy.values()), sum(total.values()))
    return out


def rejected_actions(events: list[dict]) -> dict:
    reasons = Counter(e["reason"] for e in events if e["type"] == "action_rejected")
    return {"rejected_actions": sum(reasons.values()), "rejected_by_reason": dict(sorted(reasons.items()))}


def compute(victims: list[dict], timeline: list[dict], events: list[dict]) -> dict:
    ambulances = {u["id"] for u in timeline[0]["units"] if u["kind"] in ("ALS", "BLS")} if timeline else set()
    responses = response_times(events, ambulances)
    peaks = er_peaks(timeline)
    kinds = Counter(e["type"] for e in events)
    return {
        **critical_on_time(victims),
        "unserved_criticals": unserved_criticals(victims),
        "avg_report_to_care_min": avg_report_to_care(victims),
        "er_peak_pct": peaks,
        "er_load_balance_pct": er_load_balance(peaks),
        "idle_minutes": idle_minutes(timeline),
        "overload_minutes": overload_minutes(timeline),
        "avg_response_time_min": round(sum(responses.values()) / len(responses), 2) if responses else None,
        "response_time_min": responses,
        "unit_utilization_pct": unit_utilization(timeline),
        **rejected_actions(events),
        "victims_total": len(victims),
        "victims_delivered": sum(1 for v in victims if v["delivered_at"] is not None),
        "deadlines_missed": kinds["deadline_missed"],
        "walk_ins": kinds["walk_in"],
        "reservations_broken": kinds["reservation_broken"],
    }


def at_minute(victims: list[dict], timeline: list[dict], events: list[dict], t: int) -> dict:
    """Metrics as they stood at minute t, for the dashboard's playback."""
    cut = [v if v["delivered_at"] is not None and v["delivered_at"] <= t else {**v, "delivered_at": None}
           for v in victims if v["reported_at"] <= t]
    return compute(cut, timeline[: t + 1], [e for e in events if e["t"] <= t])
