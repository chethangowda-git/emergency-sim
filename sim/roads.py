"""Road network: fastest routes, travel minutes and positions along a route (DESIGN 3.1).

The graph is a networkx DiGraph whose nodes carry `x` (lon) and `y` (lat) and whose
edges carry `travel_time` in seconds and, optionally, `geometry` as a WKT LineString.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
from shapely import wkt

LatLon = tuple[float, float]

DEFAULT_GRAPH = Path(__file__).resolve().parent.parent / "data" / "roads" / "network.graphml"


@dataclass(frozen=True)
class Route:
    """A trip's path. `cum` is minutes since departure at each point, stretched so the
    last point is reached exactly at `minutes`, the whole-minute travel time."""

    nodes: tuple[str, ...]
    points: tuple[LatLon, ...]
    cum: tuple[float, ...]
    node_idx: tuple[int, ...]
    seconds: float
    minutes: int

    def to_dict(self) -> dict:
        return {
            "nodes": list(self.nodes),
            "points": [[round(lat, 6), round(lon, 6)] for lat, lon in self.points],
            "cum": [round(c, 4) for c in self.cum],
            "minutes": self.minutes,
        }


def travel_minutes(seconds: float, factor: float) -> int:
    return max(1, math.ceil(seconds * factor / 60 - 1e-9))


def load_graph(path: Path = DEFAULT_GRAPH) -> nx.DiGraph:
    return nx.read_graphml(path)


def haversine_m(a: LatLon, b: LatLon) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class RoadNetwork:
    def __init__(self, graph: nx.DiGraph, factor: float = 0.85) -> None:
        self.graph = graph
        self.factor = factor
        self._paths: dict[tuple[str, str], tuple[list[str], float]] = {}
        self._routes: dict[tuple[str, str], Route] = {}

    def latlon(self, node: str) -> LatLon:
        d = self.graph.nodes[node]
        return (float(d["y"]), float(d["x"]))

    def sorted_nodes(self) -> list[str]:
        return sorted(self.graph.nodes)

    def path(self, origin: str, dest: str) -> tuple[list[str], float]:
        key = (origin, dest)
        if key not in self._paths:
            if origin == dest:
                self._paths[key] = ([origin], 0.0)
            else:
                _, nodes = nx.bidirectional_dijkstra(self.graph, origin, dest, weight="travel_time")
                self._paths[key] = (nodes, self._path_seconds(nodes))
        return self._paths[key]

    def minutes(self, origin: str, dest: str) -> int:
        return travel_minutes(self.path(origin, dest)[1], self.factor)

    def route(self, origin: str, dest: str) -> Route:
        key = (origin, dest)
        if key not in self._routes:
            nodes, _ = self.path(origin, dest)
            pts, secs, idx = self._trace(nodes)
            self._routes[key] = _make_route(nodes, pts, secs, idx, self.factor)
        return self._routes[key]

    def continue_route(self, route: Route, elapsed: float, dest: str) -> Route:
        """Route for a unit that is `elapsed` minutes into `route` and now heads to `dest`.

        It keeps driving to the next node on its current route, then takes the fastest
        path from there, so the drawn path stays continuous.
        """
        here, here_s, k = _locate(route, elapsed)
        if k is None:
            return self.route(route.nodes[-1], dest)
        nxt = route.nodes[k]
        to_next = _raw_seconds(route, route.node_idx[k]) - here_s
        nodes, _ = self.path(nxt, dest)
        pts, secs, idx = self._trace(nodes)
        pts = [here, *pts]
        secs = [0.0, *(s + to_next for s in secs)]
        idx = [i + 1 for i in idx]
        return _make_route(nodes, pts, secs, idx, self.factor)

    def minutes_from(self, route: Route, elapsed: float, dest: str) -> int:
        """Same answer as `continue_route(...).minutes`, without building the geometry."""
        _, here_s, k = _locate(route, elapsed)
        if k is None:
            return self.minutes(route.nodes[-1], dest)
        to_next = _raw_seconds(route, route.node_idx[k]) - here_s
        return travel_minutes(self.path(route.nodes[k], dest)[1] + to_next, self.factor)

    def _path_seconds(self, nodes: list[str]) -> float:
        """Summed edge by edge, in the same order as `_trace`, so both agree exactly."""
        total = 0.0
        for u, v in zip(nodes, nodes[1:]):
            total = total + float(self.graph.edges[u, v]["travel_time"])
        return total

    def _trace(self, nodes: list[str]) -> tuple[list[LatLon], list[float], list[int]]:
        pts = [self.latlon(nodes[0])]
        secs = [0.0]
        idx = [0]
        for u, v in zip(nodes, nodes[1:]):
            seg = self._edge_points(u, v)
            edge_s = float(self.graph.edges[u, v]["travel_time"])
            lens = [haversine_m(a, b) for a, b in zip(seg, seg[1:])]
            total = sum(lens) or 1.0
            start = secs[-1]
            run = 0.0
            for p, length in zip(seg[1:], lens):
                run += length
                pts.append(p)
                secs.append(start + edge_s * run / total)
            secs[-1] = start + edge_s
            idx.append(len(pts) - 1)
        return pts, secs, idx

    def _edge_points(self, u: str, v: str) -> list[LatLon]:
        a, b = self.latlon(u), self.latlon(v)
        geom = self.graph.edges[u, v].get("geometry")
        if not geom:
            return [a, b]
        pts = [(lat, lon) for lon, lat in wkt.loads(geom).coords]
        if haversine_m(pts[0], a) > haversine_m(pts[-1], a):
            pts.reverse()
        return [a, *pts[1:-1], b]


def _make_route(nodes, pts, secs, idx, factor) -> Route:
    total = secs[-1]
    mins = travel_minutes(total, factor)
    if total > 0:
        cum = tuple(s / total * mins for s in secs)
    else:
        pts = [pts[0], pts[0]]
        cum = (0.0, float(mins))
        idx = [0] * len(nodes)
    return Route(tuple(nodes), tuple(pts), cum, tuple(idx), total, mins)


def _raw_seconds(route: Route, point: int) -> float:
    if route.minutes == 0 or route.seconds == 0:
        return 0.0
    return route.cum[point] / route.minutes * route.seconds


def _locate(route: Route, elapsed: float) -> tuple[LatLon, float, int | None]:
    """Current point, raw seconds travelled, and index of the next node not yet reached."""
    here = position_on(route, elapsed)
    here_s = min(max(elapsed, 0.0), route.minutes) / route.minutes * route.seconds
    for k, p in enumerate(route.node_idx):
        if route.cum[p] > elapsed + 1e-9:
            return here, here_s, k
    return here, here_s, None


def position_on(route: Route, elapsed: float) -> LatLon:
    if elapsed <= 0:
        return route.points[0]
    if elapsed >= route.cum[-1]:
        return route.points[-1]
    i = bisect.bisect_right(route.cum, elapsed)
    c0, c1 = route.cum[i - 1], route.cum[i]
    (a_lat, a_lon), (b_lat, b_lon) = route.points[i - 1], route.points[i]
    f = (elapsed - c0) / (c1 - c0) if c1 > c0 else 1.0
    return (a_lat + (b_lat - a_lat) * f, a_lon + (b_lon - a_lon) * f)


def position_at(route: Route, depart_t: float, t: float) -> LatLon:
    """Position at simulated time `t` (may be fractional) for a trip that left at `depart_t`."""
    return position_on(route, t - depart_t)
