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

from dashboard.names import hospital_label
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
TRAIL_MINUTES = 1.5   # a moving unit leaves a fading trail of the last 1.5 simulated minutes


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
        out.append({**u, "lat": lat, "lon": lon, "path": [[lat, lon], *ahead],
                    "trail": trail(trip, elapsed, t, (lat, lon))})
    return out


def trail(trip: dict, elapsed: float, t: float, here: tuple[float, float]) -> dict:
    """The route driven in the last TRAIL_MINUTES as [lon, lat] points with their minute, ending here."""
    behind = [(c, p) for p, c in zip(trip["points"], trip["cum"]) if c <= elapsed]
    keep = [i for i, (c, _) in enumerate(behind) if c >= elapsed - TRAIL_MINUTES]
    start = max(0, (keep[0] if keep else len(behind)) - 1)        # one point before, already faded out
    pts = behind[start:]
    return {"path": [[p[1], p[0]] for _, p in pts] + [[here[1], here[0]]],
            "times": [trip["depart_t"] + c for c, _ in pts] + [t]}


# ----- label placement: nothing sits on top of anything else -----

LABEL_CHAR_PX = 7.4     # deck.gl's default 12 px monospace font
GAP = 3


def to_px(lat: float, lon: float, zoom: float) -> tuple[float, float]:
    """Web Mercator pixel position at this zoom (only differences between points matter)."""
    scale = 256 * 2 ** zoom
    siny = math.sin(math.radians(lat))
    return ((lon + 180) / 360 * scale, (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * scale)


def text_box(x: float, y: float, text: str, dy: float = 0, h: float = 18) -> tuple[float, float, float, float]:
    w = LABEL_CHAR_PX * len(text) + 10
    return (x - w / 2, y + dy - h / 2, x + w / 2, y + dy + h / 2)


def overlaps(a, b) -> bool:
    return a[0] < b[2] + GAP and b[0] < a[2] + GAP and a[1] < b[3] + GAP and b[1] < a[3] + GAP


def fixed_boxes(hospitals: list[dict], incidents: list[dict], zoom: float) -> list[tuple]:
    """Hospital sign, name and ER labels, and incident circles, which never move."""
    boxes = []
    for h in hospitals:
        x, y = to_px(h["lat"], h["lon"], zoom)
        boxes += [(x - 12, y - 12, x + 12, y + 12), text_box(x, y, h["name"], -24), text_box(x, y, h["er"], 24)]
    m_per_px = 156543.0 * math.cos(math.radians(42.36)) / 2 ** zoom
    for r in incidents:
        x, y = to_px(r["lat"], r["lon"], zoom)
        rad = min(22, max(8, r["radius"] / m_per_px)) + 2
        boxes.append((x - rad, y - rad, x + rad, y + rad))
    return boxes


def candidates(w: float) -> list[tuple[float, float]]:
    """Offsets tried in order: on the spot, then beside it (right, left), stacking outwards."""
    side = w / 2 + 16
    out = [(0, 0)]
    for dy in (0, 22, -22, 44, -44, 66, -66, 88, 110):
        out += [(side, dy), (-side, dy)]
    return out + [(0, dy) for dy in (30, -30, 52, -52)]


def badge_offsets(units: list[dict], hospitals: list[dict], incidents: list[dict], zoom: float) -> dict[str, list[int]]:
    """Pixel offsets so unit badges never cover a hospital, its labels, an incident or each other."""
    placed = fixed_boxes(hospitals, incidents, zoom)
    out = {}
    for u in sorted(units, key=lambda u: (u["state"] in FREE_STATES, u["id"])):   # moving units first
        x, y = to_px(u["lat"], u["lon"], zoom)
        w = badge_width(unit_badge_text(u))
        for dx, dy in candidates(w):
            box = (x + dx - w / 2, y + dy - BADGE_H / 2, x + dx + w / 2, y + dy + BADGE_H / 2)
            if not any(overlaps(box, b) for b in placed):
                break
        placed.append(box)
        out[u["id"]] = [int(dx), int(dy)]
    return out


def parked_vehicles(units: list[dict], minute: dict) -> dict[str, list[str]]:
    """Hospital vehicles idle at their own hospital: shown in the hospital's tooltip, not as badges."""
    nodes = {h["id"]: h["node"] for h in minute["hospitals"]}
    out: dict[str, list[str]] = defaultdict(list)
    for u in units:
        if u["kind"] == "HV" and u["state"] in FREE_STATES and u.get("owner") and u["node"] == nodes.get(u["owner"]):
            out[u["owner"]].append(u["id"])
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
        rows.append({"id": inc["id"], "lon": inc["lon"], "lat": inc["lat"], "color": color,
                     "radius": 50 + 25 * len(inc["victims"]),
                     "count": str(waiting) if waiting else "",
                     "label": f"{inc['id']} {inc['type'].replace('_', ' ')}: {inc['description']}\n"
                              f"{len(inc['victims'])} victims, {waiting} still at the scene"
                              + ("" if inc["cleared"] else f"\nWaiting for {inc['requires']} clearance")})
    return rows


def load_color(h: dict) -> list[int]:
    load = h["er_used"] / h["er_slots"]
    return LOAD_FULL if load >= 1 else LOAD_BUSY if load >= 0.6 else LOAD_OK


def walk_ins_in_er(h: dict) -> int:
    return sum(1 for p in h.get("patients", []) if p["walk_in"])


def hospital_rows(minute: dict, parked: dict[str, list[str]] | None = None) -> list[dict]:
    parked = parked or {}
    return [{"lon": h["lon"], "lat": h["lat"], "sign": "H",
             "name": hospital_label(h),
             "er": f"ER {h['er_used']}/{h['er_slots']}" + (f" (+{h['reserved']})" if h["reserved"] else ""),
             "er_color": load_color(h),
             "label": f"{hospital_label(h)} (hospital)\nER {h['er_used']}/{h['er_slots']} in use, "
                      f"{h['reserved']} reserved, {h['waiting_for_staff']} waiting for staff\n"
                      f"Ward {h['ward_used']}/{h['ward_beds']}, boarding {h['boarding']}\n"
                      f"Walk-in patients in the ER: {walk_ins_in_er(h)}"
                      + (f"\nVehicles parked here: {', '.join(parked[h['id']])}" if parked.get(h["id"]) else "")}
            for h in minute["hospitals"]]


def unit_rows(units: list[dict], offsets: dict[str, list[int]]) -> list[dict]:
    rows = []
    for u in units:
        style = UNIT_STYLE[u["kind"]]
        job = u["incident"] or u["hospital"]
        alpha = IDLE_ALPHA if u["state"] in FREE_STATES else 255
        rows.append({"id": u["id"], "incident": u["incident"], "lon": u["lon"], "lat": u["lat"],
                     "badge": unit_badge_text(u),
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


def spawn_layers(points: list[dict], picked: dict | None) -> list[pdk.Layer]:
    """Clickable road intersections for placing an incident, and a ring around the chosen one."""
    layers = [pdk.Layer("ScatterplotLayer", points, id="spawn-points", get_position=["lon", "lat"],
                        get_fill_color=[211, 47, 47, 90], get_radius=12, radius_min_pixels=3,
                        radius_max_pixels=6, pickable=True, auto_highlight=True,
                        highlight_color=[211, 47, 47, 255])]
    if picked:
        layers.append(pdk.Layer("ScatterplotLayer", [picked], id="spawn-picked", get_position=["lon", "lat"],
                                get_fill_color=[211, 47, 47, 60], get_line_color=[211, 47, 47, 255],
                                stroked=True, line_width_min_pixels=3, get_radius=60, radius_min_pixels=12))
    return layers


def focus_layers(units: list[dict], incidents: list[dict], focus: str | None) -> list[pdk.Layer]:
    """Highlight one incident with its units and their routes (a card or map click chose it)."""
    if not focus:
        return []
    mine = [u for u in units if u["incident"] == focus or any(v.startswith(focus + "-") for v in u["victims"])]
    spots = [r for r in incidents if r["id"] == focus] + [{"lon": u["lon"], "lat": u["lat"]} for u in mine]
    routes = [{"path": [[p[1], p[0]] for p in u["path"]]} for u in mine if u["path"]]
    return [pdk.Layer("PathLayer", routes, id="focus-routes", get_path="path", get_color=[255, 196, 0, 200],
                      width_min_pixels=7),
            pdk.Layer("ScatterplotLayer", spots, id="focus", get_position=["lon", "lat"], get_fill_color=[255, 196, 0, 40],
                      get_line_color=[255, 196, 0, 255], stroked=True, line_width_min_pixels=3, get_radius=90,
                      radius_min_pixels=16, radius_max_pixels=30)]


def build_deck(run: dict, t: float, view: dict, extra: list[pdk.Layer] = (), focus: str | None = None) -> pdk.Deck:
    minute = run["timeline"][min(int(t), len(run["timeline"]) - 1)]
    units = units_at(run, t)
    parked = parked_vehicles(units, minute)
    shown = [u for u in units if u["id"] not in {v for vs in parked.values() for v in vs}]
    incidents = incident_rows(minute)
    hospitals = hospital_rows(minute, parked)
    rows = unit_rows(shown, badge_offsets(shown, hospitals, incidents, view.get("zoom", 12.6)))
    paths = [{"path": piece, "color": UNIT_STYLE[u["kind"]]["rgb"] + [210]}
             for u in units if u["path"] for piece in dashed(u["path"])]
    trails = [{**u["trail"], "color": UNIT_STYLE[u["kind"]]["rgb"]} for u in units if u.get("trail")]
    layers = [
        *focus_layers(units, incidents, focus),
        pdk.Layer("PathLayer", paths, id="routes", get_path="path", get_color="color",
                  width_min_pixels=3, width_max_pixels=4),
        pdk.Layer("TripsLayer", trails, id="trails", get_path="path", get_timestamps="times", get_color="color",
                  opacity=0.85, width_min_pixels=6, rounded=True, trail_length=TRAIL_MINUTES, current_time=t),
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
        *extra,
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
        f"<b style='color:rgb(198,40,40)'>full</b>, (+n) = reserved for incoming ambulances, "
        "walk-in patients = people who came in on their own and are in the ER now<br>"
        f"{_dot(INCIDENT_RED)} Incident with criticals &nbsp; {_dot(INCIDENT_AMBER)} Incident, no criticals &nbsp; "
        f"{_dot(INCIDENT_DONE)} Everyone at hospital (number = victims still at the scene)<br>"
        f"{_badge('ALS2', UNIT_STYLE['ALS']['hex'], opacity=0.45)} faded = idle at base &nbsp;·&nbsp; "
        "dashed line = route still to drive &nbsp;·&nbsp; hover any marker for details</div>"
    )


def legend_compact_html() -> str:
    """One line under the map (DESIGN 13.1); hover any marker for details."""
    units = " ".join(_badge("TV" if k == "HV" else s["short"], s["hex"]) for k, s in UNIT_STYLE.items())
    return ("<div class='legend'>"
            f"{units} units · {_badge('H', HOSPITAL_HEX, border=HOSPITAL_HEX)} hospital (ER "
            "<b style='color:rgb(46,125,50)'>ok</b>/<b style='color:rgb(230,120,0)'>busy</b>/"
            "<b style='color:rgb(198,40,40)'>full</b>) · "
            f"{_dot(INCIDENT_RED)} criticals {_dot(INCIDENT_AMBER)} others {_dot(INCIDENT_DONE)} done · "
            "faded = idle · dashed = route ahead · hover for details</div>")
