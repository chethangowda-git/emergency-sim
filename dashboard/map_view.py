"""pydeck layers for one playback moment. Positions come from the recorded routes."""
from __future__ import annotations

import math

import pydeck as pdk

from sim.roads import haversine_m, position_on

UNIT_STYLE = {
    "ALS": {"color": [200, 30, 60], "label": "ALS ambulance"},
    "BLS": {"color": [30, 110, 220], "label": "BLS ambulance"},
    "FIRE": {"color": [240, 120, 0], "label": "Fire team"},
    "RESCUE": {"color": [120, 70, 30], "label": "Rescue team"},
}
INCIDENT_RED, INCIDENT_AMBER, INCIDENT_DONE = [220, 20, 20, 190], [245, 165, 0, 190], [150, 150, 150, 90]
HOSPITAL_COLOR = [20, 140, 90]


def active_trip(trips: list[dict], t: float) -> dict | None:
    for trip in trips:
        if trip["depart_t"] <= t < trip["end_t"]:
            return trip
    return None


def units_at(run: dict, t: float) -> list[dict]:
    minute = run["timeline"][min(int(t), len(run["timeline"]) - 1)]
    out = []
    for u in minute["units"]:
        trip = active_trip(run["trips_by_unit"].get(u["id"], []), t)
        if trip is None:
            out.append({**u, "path": None})
            continue
        elapsed = t - trip["depart_t"]
        lat, lon = position_on(trip["route"], elapsed)
        ahead = [p for p, c in zip(trip["points"], trip["cum"]) if c > elapsed]
        out.append({**u, "lat": lat, "lon": lon, "path": [[lat, lon], *ahead]})
    return out


def dashed(path: list[list[float]], dash_m: float = 45, gap_m: float = 30) -> list[list[list[float]]]:
    """Split a (lat, lon) polyline into short [lon, lat] pieces so it draws dashed."""
    pieces, current, pos, period = [], [], 0.0, dash_m + gap_m
    for a, b in zip(path, path[1:]):
        seg = haversine_m(tuple(a), tuple(b))
        steps = max(1, math.ceil(seg / 10))
        for k in range(steps):
            f0, f1 = k / steps, (k + 1) / steps
            p0 = [a[1] + (b[1] - a[1]) * f0, a[0] + (b[0] - a[0]) * f0]
            p1 = [a[1] + (b[1] - a[1]) * f1, a[0] + (b[0] - a[0]) * f1]
            on = (pos % period) < dash_m
            if on:
                current = current or [p0]
                current.append(p1)
            elif current:
                pieces.append(current)
                current = []
            pos += seg / steps
    if len(current) > 1:
        pieces.append(current)
    return pieces


def incident_rows(minute: dict) -> list[dict]:
    rows = []
    for inc in minute["incidents"]:
        open_v = [v for v in inc["victims"] if v["status"] in ("waiting", "assigned", "in_transit", "at_door")]
        crit = any(v["original_severity"] == "critical" for v in open_v)
        color = INCIDENT_RED if crit else INCIDENT_AMBER if open_v else INCIDENT_DONE
        waiting = sum(1 for v in inc["victims"] if v["status"] == "waiting")
        rows.append({"lon": inc["lon"], "lat": inc["lat"], "color": color,
                     "radius": 60 + 25 * len(inc["victims"]),
                     "label": f"{inc['id']} {inc['type'].replace('_', ' ')}: {inc['description']}\n"
                              f"{len(inc['victims'])} victims, {waiting} waiting"
                              + ("" if inc["cleared"] else f", needs {inc['requires']} clearance")})
    return rows


def hospital_rows(minute: dict) -> list[dict]:
    rows = []
    for h in minute["hospitals"]:
        load = h["er_used"] / h["er_slots"]
        red = min(255, int(255 * load))
        rows.append({"lon": h["lon"], "lat": h["lat"], "color": [red, max(0, 160 - red // 2), 90, 230],
                     "text": f"{h['id']} ER {h['er_used']}/{h['er_slots']}",
                     "label": f"{h['id']} {h['name']}\nER {h['er_used']}/{h['er_slots']} "
                              f"(+{h['reserved']} reserved), ward {h['ward_used']}/{h['ward_beds']}"})
    return rows


def unit_rows(units: list[dict]) -> list[dict]:
    return [{"lon": u["lon"], "lat": u["lat"], "color": UNIT_STYLE[u["kind"]]["color"] + [255],
             "text": u["id"], "label": f"{u['id']} ({u['state'].replace('_', ' ')})"
             + (f" -> {u['incident'] or u['hospital']}" if (u["incident"] or u["hospital"]) else "")}
            for u in units]


def build_deck(run: dict, t: float, view: dict) -> pdk.Deck:
    minute = run["timeline"][min(int(t), len(run["timeline"]) - 1)]
    units = units_at(run, t)
    paths = [{"path": piece, "color": UNIT_STYLE[u["kind"]]["color"] + [200]}
             for u in units if u["path"] for piece in dashed(u["path"])]
    layers = [
        pdk.Layer("PathLayer", paths, id="routes", get_path="path", get_color="color",
                  width_min_pixels=3, width_max_pixels=4),
        pdk.Layer("ScatterplotLayer", incident_rows(minute), id="incidents", get_position=["lon", "lat"],
                  get_fill_color="color", get_radius="radius", radius_min_pixels=6, pickable=True,
                  stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=1),
        pdk.Layer("ScatterplotLayer", hospital_rows(minute), id="hospitals", get_position=["lon", "lat"],
                  get_fill_color="color", get_radius=90, radius_min_pixels=9, pickable=True,
                  stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=3),
        pdk.Layer("TextLayer", hospital_rows(minute), id="hospital-labels", get_position=["lon", "lat"],
                  get_text="text", get_size=13, get_color=[20, 60, 40], get_pixel_offset=[0, -22],
                  font_weight=700),
        pdk.Layer("ScatterplotLayer", unit_rows(units), id="units", get_position=["lon", "lat"],
                  get_fill_color="color", get_radius=35, radius_min_pixels=6, pickable=True,
                  stroked=True, get_line_color=[0, 0, 0], line_width_min_pixels=2),
        pdk.Layer("TextLayer", unit_rows(units), id="unit-labels", get_position=["lon", "lat"],
                  get_text="text", get_size=11, get_color=[30, 30, 30], get_pixel_offset=[0, 14]),
    ]
    return pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(**view),
                    map_provider="carto", map_style=pdk.map_styles.CARTO_LIGHT,
                    tooltip={"text": "{label}"})


LEGEND_HTML = (
    "<div style='font-size:0.85rem;line-height:1.7'>"
    + " ".join(f"<span style='color:rgb({','.join(map(str, s['color']))})'>&#9679;</span> {s['label']}&nbsp;&nbsp;"
               for s in UNIT_STYLE.values())
    + "<br><span style='color:rgb(220,20,20)'>&#11044;</span> Incident with criticals&nbsp;&nbsp;"
    "<span style='color:rgb(245,165,0)'>&#11044;</span> Incident, no criticals&nbsp;&nbsp;"
    "<span style='color:rgb(150,150,150)'>&#11044;</span> All victims at hospital&nbsp;&nbsp;"
    "<span style='color:rgb(20,140,90)'>&#9673;</span> Hospital (redder = fuller ER)&nbsp;&nbsp;"
    "- - - remaining route</div>"
)
