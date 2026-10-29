"""pydeck layers for one playback moment. Positions come from the recorded routes.

Visual language: incidents are circles (red with criticals, amber without, grey when
everyone is at a hospital); hospitals are a fixed "H" sign with ER use in the label;
units are labelled badges in colours no other marker uses. Idle units are faded, and
units that share a spot with a hospital, an incident or each other sit beside it.
"""
from __future__ import annotations

import math
from collections import defaultdict

import pydeck as pdk

from sim.roads import haversine_m, position_on

UNIT_STYLE = {
    "ALS": {"hex": "#6A1B9A", "rgb": [106, 27, 154], "short": "ALS", "label": "ALS ambulance (paramedics, can carry criticals)"},
    "BLS": {"hex": "#1565C0", "rgb": [21, 101, 192], "short": "BLS", "label": "BLS ambulance (non-critical only)"},
    "FIRE": {"hex": "#37474F", "rgb": [55, 71, 79], "short": "FIRE", "label": "Fire team"},
    "RESCUE": {"hex": "#6D4C41", "rgb": [109, 76, 65], "short": "RES", "label": "Rescue team"},
    "HV": {"hex": "#558B2F", "rgb": [85, 139, 47], "short": "TV", "label": "Hospital transport vehicle"},
}
HOSPITAL_HEX = "#00796B"
HOSPITAL_RGB = [0, 121, 107]
INCIDENT_RED, INCIDENT_AMBER, INCIDENT_DONE = [211, 47, 47, 210], [255, 160, 0, 210], [158, 158, 158, 110]
LOAD_OK, LOAD_BUSY, LOAD_FULL = [46, 125, 50], [230, 120, 0], [198, 40, 40]
FREE_STATES = ("idle",)
BADGE_H = 20
IDLE_ALPHA = 140


# ----- badges -----

def unit_badge_text(unit: dict) -> str:
    if unit["kind"] == "HV":
        return unit["id"]
    return UNIT_STYLE[unit["kind"]]["short"] + unit["id"][len(unit["kind"]):]


def badge_width(text: str) -> int:
    """Approximate pixel width of a badge in deck.gl's default 12 px monospace font."""
    return 10 + 8 * len(text)


# ----- positions -----

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


def _spot(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 4), round(lon, 4))


def badge_offsets(units: list[dict], landmarks: set[tuple[float, float]]) -> dict[str, list[int]]:
    """Pixel offsets so badges never cover a hospital, an incident or each other."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for u in units:
        groups[_spot(u["lat"], u["lon"])].append(u)
    out = {}
    for spot, members in groups.items():
        if len(members) == 1 and spot not in landmarks:
            out[members[0]["id"]] = [0, 0]
            continue
        # Beside the marker and stacked downward, clear of the name label above it.
        for i, u in enumerate(sorted(members, key=lambda m: m["id"])):
            dx = 40 + badge_width(unit_badge_text(u)) / 2
            out[u["id"]] = [int(dx), i * (BADGE_H + 2)]
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


# ----- rows -----

def incident_rows(minute: dict) -> list[dict]:
    rows = []
    for inc in minute["incidents"]:
        open_v = [v for v in inc["victims"] if v["status"] in ("waiting", "assigned", "in_transit", "at_door")]
        crit = any(v["original_severity"] == "critical" for v in open_v)
        color = INCIDENT_RED if crit else INCIDENT_AMBER if open_v else INCIDENT_DONE
        waiting = sum(1 for v in inc["victims"] if v["status"] in ("waiting", "assigned"))
        rows.append({"lon": inc["lon"], "lat": inc["lat"], "color": color,
                     "radius": 50 + 25 * len(inc["victims"]),
                     "count": str(waiting) if waiting else "",
                     "label": f"{inc['id']} {inc['type'].replace('_', ' ')}: {inc['description']}\n"
                              f"{len(inc['victims'])} victims, {waiting} still at the scene"
                              + ("" if inc["cleared"] else f"\nWaiting for {inc['requires']} clearance")})
    return rows


def load_color(h: dict) -> list[int]:
    load = h["er_used"] / h["er_slots"]
    return LOAD_FULL if load >= 1 else LOAD_BUSY if load >= 0.6 else LOAD_OK


def hospital_rows(minute: dict) -> list[dict]:
    return [{"lon": h["lon"], "lat": h["lat"], "sign": "H",
             "name": f"{h['id']} {h['name']}",
             "er": f"ER {h['er_used']}/{h['er_slots']}" + (f" (+{h['reserved']})" if h["reserved"] else ""),
             "er_color": load_color(h),
             "label": f"{h['id']} {h['name']} (hospital)\nER {h['er_used']}/{h['er_slots']} in use, "
                      f"{h['reserved']} reserved, {h['waiting_for_staff']} waiting for staff\n"
                      f"Ward {h['ward_used']}/{h['ward_beds']}, boarding {h['boarding']}"}
            for h in minute["hospitals"]]


def unit_rows(units: list[dict], offsets: dict[str, list[int]]) -> list[dict]:
    rows = []
    for u in units:
        style = UNIT_STYLE[u["kind"]]
        job = u["incident"] or u["hospital"]
        alpha = IDLE_ALPHA if u["state"] in FREE_STATES else 255
        rows.append({"lon": u["lon"], "lat": u["lat"], "badge": unit_badge_text(u),
                     "bg": style["rgb"] + [alpha], "fg": [255, 255, 255, alpha],
                     "offset": offsets[u["id"]],
                     "label": f"{u['id']}: {style['label']}\n{u['state'].replace('_', ' ')}"
                              + (f" ({job})" if job else "")
                              + (f"\ncarrying {', '.join(u['victims'])}" if u["victims"] else "")})
    return rows


# ----- deck -----

# Streamlit's deck.gl renderer drops text drawn with a custom font family, weight or outline,
# and mis-sizes anything given explicit "pixels" units, so every layer below sticks to the
# default font and to metre sizes clamped in pixels.

def _badge_layer(rows: list[dict], layer_id: str, size: int, padding: list[int], **props) -> pdk.Layer:
    """Text in a rounded, filled box: readable at any zoom and needs no images."""
    return pdk.Layer("TextLayer", rows, id=layer_id, get_position=["lon", "lat"], get_size=size,
                     background=True, background_padding=padding, background_border_radius=5,
                     pickable=True, **props)


def _label_layer(rows: list[dict], layer_id: str, text: str, color, offset: list[int]) -> pdk.Layer:
    return _badge_layer(rows, layer_id, 12, [4, 2], get_text=text, get_color=color,
                        get_background_color=[255, 255, 255, 235], get_pixel_offset=offset)


def build_deck(run: dict, t: float, view: dict) -> pdk.Deck:
    minute = run["timeline"][min(int(t), len(run["timeline"]) - 1)]
    units = units_at(run, t)
    incidents = incident_rows(minute)
    hospitals = hospital_rows(minute)
    landmarks = {_spot(r["lat"], r["lon"]) for r in incidents + hospitals}
    rows = unit_rows(units, badge_offsets(units, landmarks))
    paths = [{"path": piece, "color": UNIT_STYLE[u["kind"]]["rgb"] + [210]}
             for u in units if u["path"] for piece in dashed(u["path"])]
    layers = [
        pdk.Layer("PathLayer", paths, id="routes", get_path="path", get_color="color",
                  width_min_pixels=3, width_max_pixels=4),
        pdk.Layer("ScatterplotLayer", incidents, id="incidents", get_position=["lon", "lat"],
                  get_fill_color="color", get_radius="radius", radius_min_pixels=8, radius_max_pixels=22,
                  pickable=True, stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=2),
        pdk.Layer("TextLayer", incidents, id="incident-counts", get_position=["lon", "lat"], get_text="count",
                  get_color=[255, 255, 255], get_size=13),
        _badge_layer(hospitals, "hospitals", 16, [6, 3], get_text="sign", get_color=[255, 255, 255],
                     get_background_color=HOSPITAL_RGB + [255]),
        _label_layer(hospitals, "hospital-names", "name", HOSPITAL_RGB, [0, -24]),
        _label_layer(hospitals, "hospital-er", "er", "er_color", [0, 24]),
        _badge_layer(rows, "units", 12, [5, 3], get_text="badge", get_color="fg",
                     get_background_color="bg", get_pixel_offset="offset"),
    ]
    return pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(**view),
                    map_provider="carto", map_style=pdk.map_styles.CARTO_LIGHT,
                    tooltip={"text": "{label}"})


def _badge(text: str, fill: str, color: str = "white", border: str = "white", opacity: float = 1.0) -> str:
    return (f"<span style='display:inline-block;padding:0 5px;border-radius:5px;background:{fill};"
            f"color:{color};border:2px solid {border};box-shadow:0 0 0 1px #ccc;font:12px Monaco,Menlo,monospace;"
            f"line-height:15px;vertical-align:middle;opacity:{opacity}'>{text}</span>")


def _dot(rgb: list[int]) -> str:
    return (f"<span style='display:inline-block;width:13px;height:13px;border-radius:50%;"
            f"background:rgb({rgb[0]},{rgb[1]},{rgb[2]});border:2px solid white;"
            f"box-shadow:0 0 0 1px #bbb;vertical-align:middle'></span>")


def legend_html() -> str:
    units = "&nbsp;&nbsp; ".join(
        f"{_badge('H1-TV1' if k == 'HV' else s['short'] + '1', s['hex'])} {s['label']}" for k, s in UNIT_STYLE.items())
    return (
        "<div style='font-size:0.85rem;line-height:2.1'>"
        f"{units}<br>"
        f"{_badge('H', HOSPITAL_HEX, border=HOSPITAL_HEX)} Hospital: ER in use "
        f"<b style='color:rgb(46,125,50)'>under 60%</b> / <b style='color:rgb(230,120,0)'>60 to 99%</b> / "
        f"<b style='color:rgb(198,40,40)'>full</b>, (+n) = reserved for incoming ambulances<br>"
        f"{_dot(INCIDENT_RED)} Incident with criticals &nbsp; {_dot(INCIDENT_AMBER)} Incident, no criticals &nbsp; "
        f"{_dot(INCIDENT_DONE)} Everyone at hospital (number = victims still at the scene)<br>"
        f"{_badge('ALS2', UNIT_STYLE['ALS']['hex'], opacity=0.45)} faded = idle at base &nbsp;·&nbsp; "
        "dashed line = route still to drive &nbsp;·&nbsp; hover any marker for details</div>"
    )
