from __future__ import annotations

from sim import metrics


def victim(sev, reported, deadline, delivered):
    return {"original_severity": sev, "reported_at": reported, "deadline": deadline, "delivered_at": delivered}


VICTIMS = [
    victim("critical", 0, 60, 30),      # on time, 30 min
    victim("critical", 10, 70, 70),     # on time exactly at the deadline, 60 min
    victim("critical", 20, 80, 95),     # late, 75 min
    victim("critical", 30, 90, None),   # unserved
    victim("serious", 0, 120, 15),      # 15 min
]


def hosp(hid, used, slots, util, waiting=0):
    return {"id": hid, "er_used": used, "er_slots": slots, "utilization": util, "waiting_for_staff": waiting}


def unit(uid, kind, state):
    return {"id": uid, "kind": kind, "state": state}


TIMELINE = [
    {"t": 0, "waiting_victims": 2, "hospitals": [hosp("H1", 1, 4, 0.25), hosp("H2", 2, 2, 1.0)],
     "units": [unit("ALS1", "ALS", "to_scene"), unit("BLS1", "BLS", "idle")]},
    {"t": 1, "waiting_victims": 1, "hospitals": [hosp("H1", 3, 4, 0.75), hosp("H2", 1, 2, 0.5, waiting=1)],
     "units": [unit("ALS1", "ALS", "on_scene"), unit("BLS1", "BLS", "returning")]},
    {"t": 2, "waiting_victims": 0, "hospitals": [hosp("H1", 2, 4, 0.5), hosp("H2", 0, 2, 0.0)],
     "units": [unit("ALS1", "ALS", "transporting"), unit("BLS1", "BLS", "to_scene")]},
]

EVENTS = [
    {"t": 0, "type": "incident_reported", "incident": "I01"},
    {"t": 3, "type": "incident_reported", "incident": "I02"},
    {"t": 4, "type": "unit_on_scene", "unit": "FIRE1", "incident": "I01"},
    {"t": 6, "type": "unit_on_scene", "unit": "ALS1", "incident": "I01"},
    {"t": 9, "type": "unit_on_scene", "unit": "BLS1", "incident": "I01"},
    {"t": 10, "type": "unit_on_scene", "unit": "BLS1", "incident": "I02"},
    {"t": 5, "type": "action_rejected", "reason": "er_full"},
    {"t": 6, "type": "action_rejected", "reason": "er_full"},
    {"t": 7, "type": "action_rejected", "reason": "unit_busy"},
]


def test_critical_on_time():
    assert metrics.critical_on_time(VICTIMS) == {"criticals_total": 4, "criticals_on_time": 2,
                                                 "critical_on_time_pct": 50.0}


def test_critical_on_time_with_no_criticals_is_none():
    assert metrics.critical_on_time([VICTIMS[-1]])["critical_on_time_pct"] is None


def test_unserved_criticals():
    assert metrics.unserved_criticals(VICTIMS) == 1


def test_average_report_to_care():
    assert metrics.avg_report_to_care(VICTIMS) == (30 + 60 + 75 + 15) / 4


def test_er_peaks_and_load_balance():
    peaks = metrics.er_peaks(TIMELINE)
    assert peaks == {"H1": 75.0, "H2": 100.0}
    assert metrics.er_load_balance(peaks) == 25.0


def test_idle_minutes_only_count_while_victims_wait():
    # H1: t0 util .25 (idle), t1 .75, t2 nobody waiting.  H2: t0 1.0, t1 .5 (idle), t2 nobody waiting.
    assert metrics.idle_minutes(TIMELINE) == {"H1": 1, "H2": 1}


def test_overload_minutes():
    # H2 full at t0, patient waiting for staff at t1.
    assert metrics.overload_minutes(TIMELINE) == {"H1": 0, "H2": 2}


def test_response_time_uses_first_ambulance():
    assert metrics.response_times(EVENTS, {"ALS1", "BLS1"}) == {"I01": 6, "I02": 7}


def test_unit_utilization_excludes_idle_and_returning():
    assert metrics.unit_utilization(TIMELINE) == {"ALS": 100.0, "BLS": 33.33, "overall": 66.67}


def test_rejected_actions():
    assert metrics.rejected_actions(EVENTS) == {"rejected_actions": 3,
                                                "rejected_by_reason": {"er_full": 2, "unit_busy": 1}}


def test_compute_bundles_everything():
    m = metrics.compute(VICTIMS, TIMELINE, EVENTS)
    assert m["critical_on_time_pct"] == 50.0 and m["avg_response_time_min"] == 6.5
    assert m["victims_total"] == 5 and m["victims_delivered"] == 4


def test_at_minute_only_sees_the_past():
    m = metrics.at_minute(VICTIMS, TIMELINE, EVENTS, 1)
    assert m["criticals_total"] == 1 and m["criticals_on_time"] == 0
    assert m["rejected_actions"] == 0
