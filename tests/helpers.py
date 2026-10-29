"""Hand-made scenarios on the tiny fixture graph (see conftest.tiny_graph).

Travel minutes at factor 0.85: A-B 2, B-C 1, A-C 3, C-E 1, A-E 4, A-D 5, D-B 6, D-C 7.
"""
from __future__ import annotations

from sim.config import SimConfig
from sim.models import Hospital, Incident, Scenario, Staff, UnitSpec, Vehicle, Victim, WalkIn
from sim.roads import RoadNetwork
from sim.rules import requires_for
from sim.world import World
from tests.conftest import tiny_graph

CFG = SimConfig(horizon=400, emergency_factor=0.85)


def hospital(hid: str, node: str, caps=("trauma", "burns", "general"), slots=2, beds=2,
             doctors=("trauma",), nurses=1, doctor_max=2, nurse_max=3) -> Hospital:
    ds = [Staff(f"{hid}-D{k}", hid, "doctor", ("general", s), doctor_max) for k, s in enumerate(doctors, 1)]
    ns = [Staff(f"{hid}-N{k}", hid, "nurse", ("general",), nurse_max) for k in range(1, nurses + 1)]
    roads = RoadNetwork(tiny_graph())
    lat, lon = roads.latlon(node)
    return Hospital(hid, f"Test {hid}", node, lat, lon, tuple(caps), slots, beds, ds, ns,
                    [Vehicle(f"{hid}-TV1", hid, node)])


def incident(iid: str, node: str, t: int, victims: list[tuple[str, str]], kind="traffic_accident"):
    roads = RoadNetwork(tiny_graph())
    lat, lon = roads.latlon(node)
    vs = [Victim(f"{iid}-V{k}", iid, sev, need, t, t + CFG.deadline[sev])
          for k, (sev, need) in enumerate(victims, 1)]
    inc = Incident(iid, kind, node, lat, lon, t, requires_for(kind), "test", [v.id for v in vs])
    return inc, vs


def make_world(incidents=(), hospitals=None, units=None, walk_ins=(), cfg: SimConfig = CFG) -> World:
    hospitals = hospitals if hospitals is not None else [hospital("H1", "C"), hospital("H2", "E", caps=("general",))]
    units = units if units is not None else [
        UnitSpec("ALS1", "ALS", "A", "A"), UnitSpec("BLS1", "BLS", "A", "A"),
        UnitSpec("FIRE1", "FIRE", "D", "D"), UnitSpec("RESCUE1", "RESCUE", "D", "D")]
    incs = [i for i, _ in incidents]
    vics = [v for _, vs in incidents for v in vs]
    sc = Scenario(1, incs, vics, list(walk_ins), hospitals, units)
    return World(sc, RoadNetwork(tiny_graph(), cfg.emergency_factor), cfg)


def walk_in(wid: str, hid: str, t: int, sev="serious", need="general") -> WalkIn:
    return WalkIn(wid, hid, t, sev, need)


def run_until(world: World, t: int, actions_at: dict | None = None) -> list[dict]:
    """Advance to minute t, applying {minute: [actions]} before each step. Returns all events."""
    actions_at = actions_at or {}
    if world.t == 0 and not world.events:
        world.start()
    while world.t < t:
        world.apply(actions_at.get(world.t, []))
        world.step()
    world.apply(actions_at.get(world.t, []))
    return world.events


def of_type(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]
