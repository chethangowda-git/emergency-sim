from __future__ import annotations

import networkx as nx
import pytest

from sim.roads import RoadNetwork


def tiny_graph() -> nx.DiGraph:
    """Five nodes on a small square plus a side street.

        A --120s-- B --60s-- C
        |                    |
       300s                 60s
        |                    |
        D -------600s------- E

    A-B has a bent street shape. All edges are two-way.
    """
    g = nx.DiGraph()
    coords = {"A": (42.000, -71.000), "B": (42.000, -70.990), "C": (42.000, -70.985),
              "D": (41.990, -71.000), "E": (41.990, -70.985)}
    for n, (lat, lon) in coords.items():
        g.add_node(n, y=lat, x=lon)
    edges = [("A", "B", 120.0), ("B", "C", 60.0), ("A", "D", 300.0),
             ("C", "E", 60.0), ("D", "E", 600.0)]
    for u, v, secs in edges:
        g.add_edge(u, v, travel_time=secs, length=secs * 10)
        g.add_edge(v, u, travel_time=secs, length=secs * 10)
    bend = "LINESTRING (-71.000 42.000, -70.995 42.002, -70.990 42.000)"
    g.edges["A", "B"]["geometry"] = bend
    g.edges["B", "A"]["geometry"] = bend
    return g


@pytest.fixture
def roads() -> RoadNetwork:
    return RoadNetwork(tiny_graph(), factor=0.85)
