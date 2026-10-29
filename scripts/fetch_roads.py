"""One-time download of the road network and places for the area in config/area.json.

This is the only code in the project that touches the network. Its outputs,
data/roads/network.graphml and data/roads/places.json, are committed; the
simulator only reads them.

Run: python scripts/fetch_roads.py [--skip-stations]

--skip-stations saves places.json with no ambulance or fire stations (for when the
Overpass server is overloaded); units are then based at seeded random nodes (DESIGN 3.4).
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from pathlib import Path

import networkx as nx
import osmnx as ox
import requests

ROOT = Path(__file__).resolve().parent.parent
AREA_FILE = ROOT / "config" / "area.json"
OUT_DIR = ROOT / "data" / "roads"

HOSPITAL_NAMES = {
    "H1": "Central Trauma Center",
    "H2": "Northside General",
    "H3": "Eastside Community",
}

# Specialist or non-acute campuses that would not take emergency ambulances.
NON_ACUTE_WORDS = (
    "rehab", "eye", "ear", "children", "shriners", "dana", "psychiatr",
    "mental", "cancer", "hospice", "veteran", "spaulding", "lemuel",
)


def load_area() -> dict:
    return json.loads(AREA_FILE.read_text())


def bbox_tuple(area: dict) -> tuple[float, float, float, float]:
    b = area["bbox"]
    return (b["west"], b["south"], b["east"], b["north"])


def download_graph(area: dict) -> nx.MultiDiGraph:
    g = ox.graph_from_bbox(bbox_tuple(area), network_type="drive")
    g = ox.truncate.largest_component(g, strongly=True)
    g = ox.add_edge_speeds(
        g, hwy_speeds=area["default_speeds_kph"], fallback=area["fallback_speed_kph"]
    )
    return ox.add_edge_travel_times(g)


def to_lean_digraph(g: nx.MultiDiGraph, area_name: str) -> nx.DiGraph:
    """Keep the fastest of any parallel edges and only the attributes the simulator reads."""
    lean = nx.DiGraph(name=area_name)
    for n in sorted(g.nodes):
        d = g.nodes[n]
        lean.add_node(str(n), x=float(d["x"]), y=float(d["y"]))
    for u, v, d in sorted(g.edges(data=True), key=lambda e: (e[0], e[1], e[2]["travel_time"])):
        u_s, v_s = str(u), str(v)
        if lean.has_edge(u_s, v_s):
            continue
        attrs = {"travel_time": float(d["travel_time"]), "length": float(d["length"])}
        if "geometry" in d:
            attrs["geometry"] = d["geometry"].wkt
        lean.add_edge(u_s, v_s, **attrs)
    return lean


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_node(g: nx.DiGraph, lat: float, lon: float) -> str:
    """Snapping only: picks the closest graph node, not a travel time."""
    return min(g.nodes, key=lambda n: (haversine_m(lat, lon, g.nodes[n]["y"], g.nodes[n]["x"]), n))


def features_with_retry(area: dict, tags: dict, attempts: int = 7):
    """The public Overpass server is often at capacity; back off and retry."""
    wait = 15
    for attempt in range(1, attempts + 1):
        try:
            return ox.features_from_bbox(bbox_tuple(area), tags=tags)
        except (ox._errors.ResponseStatusCodeError, requests.exceptions.ConnectionError):
            if attempt == attempts:
                raise
            print(f"  Overpass busy, retrying in {wait}s ...")
            time.sleep(wait)
            wait *= 2


def fetch_points(area: dict, tags: dict) -> list[dict]:
    try:
        gdf = features_with_retry(area, tags)
    except ox._errors.InsufficientResponseError:
        return []
    points = []
    for idx, row in gdf.iterrows():
        pt = row.geometry.representative_point()
        points.append({
            "osm_id": f"{idx[0]}/{idx[1]}",
            "name": str(row.get("name") or ""),
            "emergency": str(row.get("emergency") or ""),
            "lat": pt.y,
            "lon": pt.x,
        })
    return sorted(points, key=lambda p: p["osm_id"])


def snap_unique(g: nx.DiGraph, points: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for p in points:
        node = nearest_node(g, p["lat"], p["lon"])
        if node in seen:
            continue
        seen.add(node)
        out.append({**p, "node": node})
    return out


def hospital_candidates(g: nx.DiGraph, area: dict) -> list[dict]:
    raw = fetch_points(area, {"amenity": "hospital"})
    named = [p for p in raw if p["name"]]
    acute = [p for p in named if not any(w in p["name"].lower() for w in NON_ACUTE_WORDS)]
    with_er = [p for p in acute if p["emergency"] == "yes"]
    pool = with_er if len(with_er) >= 3 else acute
    by_name: dict[str, dict] = {}
    for p in pool:
        by_name.setdefault(p["name"], p)
    return snap_unique(g, list(by_name.values()))


def pairwise_times(g: nx.DiGraph, nodes: list[str]) -> dict[tuple[str, str], float]:
    times = {}
    for a in nodes:
        dist = nx.single_source_dijkstra_path_length(g, a, weight="travel_time")
        for b in nodes:
            times[(a, b)] = dist[b]
    return times


def choose_spread(g: nx.DiGraph, cands: list[dict]) -> list[dict]:
    """Pick the 3 hospitals whose closest pair is as far apart by road as possible."""
    times = pairwise_times(g, [c["node"] for c in cands])

    def closest_pair(trio: tuple[dict, ...]) -> float:
        return min(
            min(times[(a["node"], b["node"])], times[(b["node"], a["node"])])
            for a, b in itertools.combinations(trio, 2)
        )

    return list(max(itertools.combinations(cands, 3), key=closest_pair))


def label_hospitals(g: nx.DiGraph, trio: list[dict]) -> list[dict]:
    """H1 is the most central by road (lowest mean time to and from every node); H2 the
    northernmost of the rest."""
    rev = g.reverse(copy=False)

    def mean_time(node: str) -> float:
        out = nx.single_source_dijkstra_path_length(g, node, weight="travel_time")
        back = nx.single_source_dijkstra_path_length(rev, node, weight="travel_time")
        return (sum(out.values()) + sum(back.values())) / (len(out) + len(back))

    h1 = min(trio, key=lambda h: (mean_time(h["node"]), h["node"]))
    rest = sorted((h for h in trio if h is not h1), key=lambda h: -h["lat"])
    out = []
    for hid, h in zip(("H1", "H2", "H3"), [h1, *rest]):
        node = h["node"]
        out.append({"id": hid, "name": HOSPITAL_NAMES[hid], "node": node,
                    "lat": g.nodes[node]["y"], "lon": g.nodes[node]["x"]})
    return out


def stations(g: nx.DiGraph, area: dict, tags: dict, prefix: str) -> list[dict]:
    snapped = snap_unique(g, fetch_points(area, tags))
    snapped.sort(key=lambda p: (-g.nodes[p["node"]]["y"], g.nodes[p["node"]]["x"]))
    return [
        {"id": f"{prefix}{i}", "node": p["node"],
         "lat": g.nodes[p["node"]]["y"], "lon": g.nodes[p["node"]]["x"]}
        for i, p in enumerate(snapped, start=1)
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-stations", action="store_true",
                        help="save no ambulance or fire stations; units get seeded random bases")
    args = parser.parse_args()
    area = load_area()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading drivable roads for {area['name']} ...")
    g = to_lean_digraph(download_graph(area), area["name"])
    print(f"  {g.number_of_nodes()} nodes, {g.number_of_edges()} edges")
    nx.write_graphml(g, OUT_DIR / "network.graphml")

    cands = hospital_candidates(g, area)
    print(f"  {len(cands)} candidate hospitals: {sorted(c['name'] for c in cands)}")
    if len(cands) < 3:
        raise SystemExit("Fewer than 3 hospitals in this area; choose a larger area.")
    hospitals = label_hospitals(g, choose_spread(g, cands))
    places = {
        "area": area["name"],
        "hospitals": hospitals,
        "ambulance_stations": [] if args.skip_stations else
        stations(g, area, {"emergency": "ambulance_station"}, "AS"),
        "fire_stations": [] if args.skip_stations else stations(g, area, {"amenity": "fire_station"}, "FS"),
    }
    (OUT_DIR / "places.json").write_text(json.dumps(places, indent=2) + "\n")
    print(f"  hospitals at nodes {[h['node'] for h in hospitals]}")
    print(f"  {len(places['ambulance_stations'])} ambulance stations, "
          f"{len(places['fire_stations'])} fire stations")


if __name__ == "__main__":
    main()
