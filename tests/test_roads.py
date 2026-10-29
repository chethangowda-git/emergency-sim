from __future__ import annotations

import pytest

from sim.roads import load_graph, position_at, RoadNetwork, travel_minutes


def test_travel_minutes_rounds_up_with_factor_and_minimum_one():
    assert travel_minutes(120, 0.85) == 2      # 102 s
    assert travel_minutes(60, 1.0) == 1
    assert travel_minutes(61, 1.0) == 2
    assert travel_minutes(10, 0.85) == 1
    assert travel_minutes(0, 0.85) == 1


def test_fastest_route_uses_travel_time(roads):
    route = roads.route("A", "E")
    assert route.nodes == ("A", "B", "C", "E")
    assert route.seconds == pytest.approx(240)
    assert route.minutes == 4                  # 240 * 0.85 = 204 s
    assert roads.minutes("A", "E") == 4


def test_route_is_cached(roads):
    assert roads.route("A", "C") is roads.route("A", "C")


def test_same_origin_and_destination_takes_one_minute(roads):
    route = roads.route("B", "B")
    assert route.minutes == 1
    assert position_at(route, 10, 10.5) == roads.latlon("B")


def test_geometry_follows_street_shape(roads):
    pts = roads.route("A", "B").points
    assert pts[0] == roads.latlon("A") and pts[-1] == roads.latlon("B")
    assert (42.002, -70.995) in pts
    back = roads.route("B", "A").points
    assert back[0] == roads.latlon("B") and (42.002, -70.995) in back


def test_cumulative_times_are_monotonic_and_end_at_minutes(roads):
    route = roads.route("A", "E")
    assert route.cum[0] == 0
    assert all(b >= a for a, b in zip(route.cum, route.cum[1:]))
    assert route.cum[-1] == pytest.approx(route.minutes)


def test_position_at_interpolates_fractional_minutes(roads):
    route = roads.route("B", "E")              # 120 s raw -> 2 min, B-C then C-E
    assert position_at(route, 5, 4) == roads.latlon("B")
    assert position_at(route, 5, 6) == pytest.approx(roads.latlon("C"))
    lat, lon = position_at(route, 5, 5.5)
    assert lat == pytest.approx(42.0) and lon == pytest.approx(-70.9875)
    assert position_at(route, 5, 99) == roads.latlon("E")


def test_continue_route_starts_where_the_unit_is(roads):
    route = roads.route("A", "C")              # 180 s -> 3 min
    here = position_at(route, 0, 1.0)
    new = roads.continue_route(route, 1.0, "E")
    assert new.points[0] == pytest.approx(here)
    assert new.nodes == ("B", "C", "E")
    assert new.points[-1] == roads.latlon("E")


def test_continue_route_after_arrival_starts_at_last_node(roads):
    route = roads.route("A", "B")
    new = roads.continue_route(route, 5.0, "C")
    assert new.nodes == ("B", "C")


def test_real_network_smoke():
    g = load_graph()
    net = RoadNetwork(g)
    nodes = net.sorted_nodes()
    route = net.route(nodes[0], nodes[len(nodes) // 2])
    assert route.minutes >= 1 and len(route.points) >= 2
