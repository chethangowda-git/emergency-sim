"""Resources, events and KPI panels. Everything here formats recorded data; no simulation."""
from __future__ import annotations

from html import escape

import pandas as pd

from dashboard.explain import tip
from dashboard.names import hospital_label

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
            "Hospital": hospital_label(h),
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
    if k == "message":
        why = f": {e['reason']}" if e.get("reason") else ""
        return f"💬 {e['sender']} → {e['recipient']}: {e['msg_type'].replace('_', ' ')}{why}"
    if k == "agent_decision":
        return f"🧠 {e['agent']} {e['kind'].replace('_', ' ')} ({e['decided_by']}): {e['reason']}"
    if k == "guard":
        what = e.get("suggested") or e.get("withheld") or f"conditional offer from {e.get('hospital')}"
        return f"🛡️ guard {e['rule']} at {e['agent']}: {what} ({e['why']})"
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
        "reservation_placed": "📌 {hospital} reserved a slot for {victim}",
        "reservation_released": "🔓 reservation {reservation} at {hospital} released ({reason})",
        "hold_placed": "⏳ {hospital} holds a slot for {victim}",
        "hold_released": "🔓 {hospital} hold {reservation} released ({reason})",
        "hold_expired": "⌛ {hospital} hold {reservation} expired unused",
        "transfer_prep": "🚐 {from_hospital} preparing {victim} for transfer to {to_hospital}",
        "transfer_started": "🚐 {victim} transferring {from_hospital} → {to_hospital}",
        "transfer_completed": "🏥 {victim} transferred to {hospital}",
        "transfer_cancelled": "✖️ transfer of {victim} cancelled",
        "vehicle_lent": "🤝 {hospital} lent {vehicle} to Dispatch until minute {until}",
        "vehicle_loan_ended": "🤝 {vehicle} back with {hospital}",
        "staff_lent": "🤝 {staff} lent {from_hospital} → {to_hospital}",
        "staff_arrived": "🩺 {staff} arrived at {hospital}",
        "staff_returning": "🩺 {staff} heading back from {from_hospital}",
        "staff_returned": "🩺 {staff} back at {hospital}",
        "unit_redirected": "🔀 {unit} redirected {from_incident} → {incident}",
        "unit_rerouted": "🔀 {unit} rerouted {from_hospital} → {hospital}",
    }
    return templates.get(k, k).format(**e)


AGENT_EVENTS = ("message", "agent_decision")


def event_feed(events: list[dict], t: int, n: int = 25, agents: bool = False) -> list[tuple[int, str]]:
    """Latest n events up to minute t, newest first; agent messages and decisions only if asked."""
    past = [e for e in events if e["t"] <= t and (agents or e["type"] not in AGENT_EVENTS)]
    return [(e["t"], describe(e)) for e in reversed(past[-n:])]


def fmt(value, suffix: str = "") -> str:
    """One decimal for measured values (39.6%), whole numbers as they are."""
    if value is None:
        return "–"
    return f"{value:.1f}{suffix}" if isinstance(value, float) else f"{value}{suffix}"


def kpis(m: dict) -> list[tuple[str, str]]:
    return [
        ("Criticals on time", f"{fmt(m['critical_on_time_pct'], '%')} ({m['criticals_on_time']}/{m['criticals_total']})"),
        ("Unserved critical victims", fmt(m["unserved_criticals"])),
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


COMPARE = (  # label, metric key, +1 when higher is better, -1 when lower is better, 0 when neither
    ("Criticals on time", "critical_on_time_pct", 1, "%"),
    ("Unserved critical victims", "unserved_criticals", -1, ""),
    ("Avg report → care", "avg_report_to_care_min", -1, " min"),
    ("Avg response time", "avg_response_time_min", -1, " min"),
    ("ER load balance", "er_load_balance_pct", -1, " pts"),
    ("Victims delivered", "victims_delivered", 1, ""),
    ("Deadlines missed", "deadlines_missed", -1, ""),
    ("Rejected actions", "rejected_actions", -1, ""),
)


def verdict(base, agents, better: int) -> str:
    if base is None or agents is None or base == agents or better == 0:
        return ""
    return "✅ better" if (agents - base) * better > 0 else "🔻 worse"


def kpi_comparison(base: dict, agents: dict) -> pd.DataFrame:
    """Baseline vs agents on the same seed at the same minute (DESIGN 13.1, compare with baseline)."""
    rows = []
    for name, key, better, suffix in COMPARE:
        b, a = base.get(key), agents.get(key)
        change = "" if b is None or a is None else f"{a - b:+.4g}{' pts' if suffix == '%' else suffix}"
        rows.append({"Metric": name, "Baseline": fmt(b, suffix), "Agents": fmt(a, suffix), "Change": change,
                     "": verdict(b, a, better)})
    return pd.DataFrame(rows).set_index("Metric")


# ----- compact right panel (DESIGN 13.1): KPI cards, units on one line, one row per hospital -----

UNIT_SHORT = {"ALS": "Paramedic (ALS)", "BLS": "Basic (BLS)", "FIRE": "Fire", "RESCUE": "Rescue", "HV": "Hospital vehicles"}
KPI_TERMS = ("Criticals on time", "Unserved critical victims", "Response time", "Avg to care", "Delivered",
             "Allocation conflicts")


def kpi_cards(m: dict) -> list[tuple[str, str, str]]:
    """(title, value, detail) for the six headline numbers, as they stand at the minute."""
    return [
        ("Criticals on time", fmt(m["critical_on_time_pct"], "%"), f"{m['criticals_on_time']} of {m['criticals_total']}"),
        ("Unserved critical victims", fmt(m["unserved_criticals"]), "not at a hospital"),
        ("Response time", fmt(m["avg_response_time_min"], " min"), "to 1st ambulance"),
        ("Avg to care", fmt(m["avg_report_to_care_min"], " min"), "to hospital"),
        ("Delivered", fmt(m["victims_delivered"]), "victims"),
        ("Allocation conflicts", fmt(m.get("allocation_conflicts")), "so far"),
    ]


def kpi_cards_html(m: dict) -> str:
    cards = "".join(f"<div class='kpi'><div class='kpi-t'>{tip(t)}</div><div class='kpi-v'>{v}</div>"
                    f"<div class='kpi-d'>{d}</div></div>" for t, v, d in kpi_cards(m))
    return f"<div class='kpis'>{cards}</div>"


def units_free(minute: dict) -> str:
    parts = []
    for kind, short in UNIT_SHORT.items():
        units = [u for u in minute["units"] if u["kind"] == kind]
        if units:
            free = sum(1 for u in units if u["state"] in FREE)
            parts.append(f"<b>{short}</b> {free}/{len(units)}")
    return " · ".join(parts)


def unit_status(minute: dict, m: dict) -> list[dict]:
    """Per unit kind: free now, and the share of minutes busy so far (DESIGN 11.1 unit utilization)."""
    out = []
    for kind, short in UNIT_SHORT.items():
        units = [u for u in minute["units"] if u["kind"] == kind]
        if units:
            out.append({"kind": kind, "name": short, "free": sum(1 for u in units if u["state"] in FREE),
                        "total": len(units), "busy_pct": m["unit_utilization_pct"].get(kind)})
    return out


def units_html(minute: dict, m: dict) -> str:
    cells = "".join(f"<div class='res'><div class='res-n'>{tip(u['kind'], u['name']) if u['kind'] in ('ALS', 'BLS') else u['name']}"
                    f"</div><div class='res-v'>{u['free']}/{u['total']} free</div>"
                    f"<div class='res-d'>{fmt(u['busy_pct'], '%')} busy so far</div></div>"
                    for u in unit_status(minute, m))
    return f"<div class='resources'>{cells}</div>"


def free_staff(staff: list[dict]) -> int:
    return sum(1 for x in staff if len(x["patients"]) < x["max"])


def hospital_line(h: dict, used_pct: float | None = None) -> dict:
    walk_ins = sum(1 for p in h.get("patients", []) if p["walk_in"])
    load = h["er_used"] / h["er_slots"] if h["er_slots"] else 0.0
    return {"name": hospital_label(h), "er": f"{h['er_used']}/{h['er_slots']}", "reserved": h["reserved"],
            "load_pct": round(100 * min(1.0, load)), "level": "full" if load >= 1 else "busy" if load >= 0.6 else "ok",
            "free_doctors": free_staff(h["doctors"]), "doctors": len(h["doctors"]),
            "free_nurses": free_staff(h["nurses"]), "nurses": len(h["nurses"]), "walk_ins": walk_ins,
            "used_pct": used_pct}


def hospitals_html(minute: dict, m: dict | None = None) -> str:
    rows = []
    used = (m or {}).get("hospital_utilization_pct") or {}
    for h in (hospital_line(h, used.get(h["id"])) for h in minute["hospitals"]):
        reserved = f" (+{h['reserved']})" if h["reserved"] else ""
        so_far = f" · {tip('Utilization', fmt(h['used_pct'], '%') + ' used so far')}" if h["used_pct"] is not None else ""
        rows.append(f"<div class='hosp'><span class='hosp-n'>{h['name']}</span> <span class='hosp-s'"
                    f" title='ER slots in use out of the total; (+n) reserved for ambulances on the way'>ER {h['er']}"
                    f"{reserved}</span><span class='hosp-s'> · doctors free {h['free_doctors']}/{h['doctors']}"
                    f" · nurses free {h['free_nurses']}/{h['nurses']} · walk-ins {h['walk_ins']}{so_far}</span>"
                    f"<div class='bar'><div class='fill {h['level']}' style='width:{h['load_pct']}%'></div></div></div>")
    return "".join(rows)


def events_html(feed: list[tuple[int, str]], minute: int) -> str:
    if not feed:
        return "<div class='feed'><i>No events yet.</i></div>"
    lines = "".join(f"<div class='ev{' now' if t == minute else ''}'><span class='ev-t'>{t}</span> {escape(text)}</div>"
                    for t, text in feed)
    return f"<div class='feed'>{lines}</div>"


def compare_resources_html(base: dict, agents: dict) -> str:
    """Compare mode: units free and each hospital's ER, baseline against agents, one line each."""
    units = "".join(f"<div class='units-line'><b>{name}</b> free: {units_free(m)}</div>"
                    for name, m in (("Baseline", base), ("Agents", agents)))
    rows = "".join(f"<div class='hosp'><span class='hosp-n'>{b['name']}</span> <span class='hosp-s'>ER baseline "
                   f"{b['er']} · agents {a['er']}</span></div>"
                   for b, a in zip(map(hospital_line, base["hospitals"]), map(hospital_line, agents["hospitals"])))
    return units + rows
