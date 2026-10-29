"""Resources, events and KPI panels. Everything here formats recorded data; no simulation."""
from __future__ import annotations

import pandas as pd

FREE = ("idle", "returning")
KIND_NAMES = {"ALS": "ALS ambulances", "BLS": "BLS ambulances", "FIRE": "Fire teams", "RESCUE": "Rescue teams",
              "HV": "Hospital vehicles"}


def unit_table(minute: dict) -> pd.DataFrame:
    rows = []
    for kind, name in KIND_NAMES.items():
        units = [u for u in minute["units"] if u["kind"] == kind]
        free = sum(1 for u in units if u["state"] in FREE)
        rows.append({"Units": name, "Free": free, "Busy": len(units) - free, "Total": len(units)})
    return pd.DataFrame(rows).set_index("Units")


def hospital_table(minute: dict) -> pd.DataFrame:
    rows = []
    for h in minute["hospitals"]:
        busy_docs = sum(1 for d in h["doctors"] if d["patients"])
        busy_nurses = sum(1 for n in h["nurses"] if n["patients"])
        rows.append({
            "Hospital": f"{h['id']} {h['name']}",
            "ER": f"{h['er_used']}/{h['er_slots']}",
            "Reserved": h["reserved"],
            "Waiting for staff": h["waiting_for_staff"],
            "In treatment": h["in_treatment"],
            "Ward": f"{h['ward_used']}/{h['ward_beds']}",
            "Boarding": h["boarding"],
            "Doctors busy": f"{busy_docs}/{len(h['doctors'])}",
            "Nurses busy": f"{busy_nurses}/{len(h['nurses'])}",
            "Utilization": f"{round(100 * h['utilization'])}%",
        })
    return pd.DataFrame(rows).set_index("Hospital")


def unit_states(minute: dict) -> pd.DataFrame:
    rows = [{"Unit": u["id"], "State": u["state"].replace("_", " "),
             "Job": u["incident"] or u["hospital"] or "", "Victims": ", ".join(u["victims"])}
            for u in minute["units"]]
    return pd.DataFrame(rows).set_index("Unit")


def describe(e: dict) -> str:
    k = e["type"]
    if k == "incident_reported":
        n = len(e["victims"])
        crit = sum(1 for v in e["victims"] if v["severity"] == "critical")
        return f"🚨 {e['incident']} {e['incident_type'].replace('_', ' ')}: {e['description']} ({n} victims, {crit} critical)"
    if k == "action_rejected":
        who = e.get("unit") or e.get("team")
        return f"⛔ {e['action']} {who} rejected: {e['reason']}"
    if k == "action_applied":
        who = e.get("unit") or e.get("team")
        target = e.get("incident") or e.get("hospital")
        extra = f" with {', '.join(e['victims'])}" if e.get("victims") else ""
        return f"✅ {e['action']} {who} → {target}{extra}"
    templates = {
        "unit_dispatched": "🚑 {unit} dispatched to {incident} (ETA min {eta})",
        "unit_on_scene": "📍 {unit} on scene at {incident}",
        "scene_cleared": "🧯 {incident} cleared by {team}",
        "transport_started": "🏥 {unit} transporting to {hospital} (ETA min {eta})",
        "patient_delivered": "🛏️ {victim} delivered to {hospital} by {unit}",
        "arrived_no_slot": "⚠️ {unit} arrived at {hospital}: no slot",
        "reservation_broken": "💔 walk-in {walk_in} took {hospital}'s slot reserved for {victim} ({unit})",
        "walk_in": "🚶 walk-in {victim} ({severity}) at {hospital}",
        "treatment_started": "🩺 {victim} treatment started at {hospital}",
        "stabilized": "💉 {victim} stabilized at {hospital}",
        "ready_for_release": "📋 {victim} ready for release at {hospital}",
        "patient_released": "➡️ {victim} released at {hospital}: {outcome}",
        "moved_to_ward": "🛏️ {victim} moved to ward at {hospital}",
        "deadline_missed": "❌ {victim} ({severity}) missed deadline",
        "unit_returning": "↩️ {unit} returning to base",
        "unit_at_base": "🏠 {unit} at base",
    }
    return templates.get(k, k).format(**e)


def event_feed(events: list[dict], t: int, n: int = 25) -> list[tuple[int, str]]:
    """Latest n events up to minute t, newest first."""
    past = [e for e in events if e["t"] <= t]
    return [(e["t"], describe(e)) for e in reversed(past[-n:])]


def fmt(value, suffix: str = "") -> str:
    return "–" if value is None else f"{value}{suffix}"


def kpis(m: dict) -> list[tuple[str, str]]:
    return [
        ("Criticals on time", f"{fmt(m['critical_on_time_pct'], '%')} ({m['criticals_on_time']}/{m['criticals_total']})"),
        ("Unserved criticals", fmt(m["unserved_criticals"])),
        ("Avg report → care", fmt(m["avg_report_to_care_min"], " min")),
        ("Avg response time", fmt(m["avg_response_time_min"], " min")),
        ("ER load balance", fmt(m["er_load_balance_pct"], " pts")),
        ("Unit utilization", fmt(m["unit_utilization_pct"].get("overall"), "%")),
        ("Rejected actions", fmt(m["rejected_actions"])),
        ("Deadlines missed", fmt(m["deadlines_missed"])),
    ]


def per_hospital_kpis(m: dict) -> pd.DataFrame:
    rows = [{"Hospital": hid, "Peak ER": f"{m['er_peak_pct'].get(hid, 0)}%",
             "Idle min": m["idle_minutes"].get(hid, 0), "Overload min": m["overload_minutes"].get(hid, 0)}
            for hid in sorted(m["er_peak_pct"])]
    return pd.DataFrame(rows).set_index("Hospital") if rows else pd.DataFrame()
