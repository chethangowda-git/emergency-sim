"""Seeded scenario: incidents, victims, walk-in schedule, hospitals and fleet (DESIGN 3.3 to 3.7)."""
from __future__ import annotations

import random

from sim.config import SimConfig
from sim.models import Hospital, Incident, Scenario, Staff, UnitSpec, Vehicle, Victim, WalkIn
from sim.roads import RoadNetwork
from sim.rules import SEVERITY_RANK, care_need_for, requires_for

DESCRIPTIONS = {
    "traffic_accident": ["two-car collision at an intersection", "car struck a cyclist",
                         "rollover on a main road", "delivery van hit a parked car"],
    "medical_emergency": ["cardiac arrest in an office", "elderly person fell at home",
                          "severe allergic reaction at a school", "person collapsed at a gym"],
    "building_fire": ["apartment fire with heavy smoke", "kitchen fire in a restaurant",
                      "fire in a row house", "basement fire in a shop"],
    "structural_collapse": ["scaffolding collapse at a construction site",
                            "partial collapse of an old building", "parking garage ramp collapsed"],
}
SURGE_DESCRIPTIONS = {
    "bus_fire": "school bus on fire, heavy smoke, many passengers",
    "traffic_accident": "multi-vehicle pileup at rush hour",
}


def generate(seed: int, cfg: SimConfig, roads: RoadNetwork, places: dict) -> Scenario:
    rng = random.Random(seed)
    nodes = roads.sorted_nodes()
    hospitals = build_hospitals(cfg, places)
    incidents, victims = make_incidents(rng, cfg, roads, nodes)
    walk_ins = make_walk_ins(rng, cfg, hospitals)
    units = make_fleet(rng, cfg, places, nodes)
    return Scenario(seed, incidents, victims, walk_ins, hospitals, units)


def weighted(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def build_hospitals(cfg: SimConfig, places: dict) -> list[Hospital]:
    located = {h["id"]: h for h in places["hospitals"]}
    out = []
    for spec in cfg.hospitals:
        loc = located[spec.id]
        doctors = [Staff(f"{spec.id}-D{k}", spec.id, "doctor", ("general", s), cfg.doctor_max_patients)
                   for k, s in enumerate(spec.doctor_specialties, start=1)]
        nurses = [Staff(f"{spec.id}-N{k}", spec.id, "nurse", ("general",), cfg.nurse_max_patients)
                  for k in range(1, spec.nurses + 1)]
        vehicles = [Vehicle(f"{spec.id}-TV{k}", spec.id, loc["node"]) for k in range(1, spec.vehicles + 1)]
        out.append(Hospital(spec.id, spec.name, loc["node"], loc["lat"], loc["lon"], spec.capabilities,
                            spec.er_slots, spec.ward_beds, doctors, nurses, vehicles))
    return out


def raw_incident(rng: random.Random, cfg: SimConfig, t: int, kind: str, n_victims: int,
                 description: str, surge: bool, node: str) -> dict:
    severities = [weighted(rng, cfg.severity_mix) for _ in range(n_victims)]
    return {"t": t, "type": kind, "severities": severities, "description": description,
            "surge": surge, "node": node}


def make_incidents(rng: random.Random, cfg: SimConfig, roads: RoadNetwork,
                   nodes: list[str]) -> tuple[list[Incident], list[Victim]]:
    raws = []
    for _ in range(cfg.n_incidents - 1):
        t = rng.randrange(cfg.incident_window)
        kind = weighted(rng, cfg.incident_type_weights)
        n = rng.randint(*cfg.normal_victims)
        raws.append(raw_incident(rng, cfg, t, kind, n, rng.choice(DESCRIPTIONS[kind]), False, rng.choice(nodes)))
    t = rng.randint(*cfg.surge_window)
    kind = rng.choice(sorted(SURGE_DESCRIPTIONS))
    n = rng.randint(*cfg.surge_victims)
    raws.append(raw_incident(rng, cfg, t, kind, n, SURGE_DESCRIPTIONS[kind], True, rng.choice(nodes)))

    order = sorted(range(len(raws)), key=lambda i: (raws[i]["t"], i))
    incidents, victims = [], []
    for k, i in enumerate(order, start=1):
        inc, vics = materialize(cfg, roads, f"I{k:02d}", raws[i])
        incidents.append(inc)
        victims.extend(vics)
    return incidents, victims


def materialize(cfg: SimConfig, roads: RoadNetwork, iid: str, raw: dict) -> tuple[Incident, list[Victim]]:
    order = sorted(range(len(raw["severities"])), key=lambda j: (SEVERITY_RANK[raw["severities"][j]], j))
    victims = []
    for k, j in enumerate(order, start=1):
        sev = raw["severities"][j]
        victims.append(Victim(f"{iid}-V{k}", iid, sev, care_need_for(raw["type"], sev),
                              raw["t"], raw["t"] + cfg.deadline[sev]))
    lat, lon = roads.latlon(raw["node"])
    inc = Incident(iid, raw["type"], raw["node"], lat, lon, raw["t"], requires_for(raw["type"]),
                   raw["description"], [v.id for v in victims], raw["surge"])
    return inc, victims


def make_walk_ins(rng: random.Random, cfg: SimConfig, hospitals: list[Hospital]) -> list[WalkIn]:
    raws = []
    for h in sorted(hospitals, key=lambda h: h.id):
        for t in range(cfg.horizon):
            if rng.random() < 1 / cfg.walk_in_mean_gap:
                sev = weighted(rng, cfg.walk_in_mix)
                need = "general" if sev == "minor" else rng.choice(sorted(h.capabilities))
                raws.append((t, h.id, sev, need))
    raws.sort(key=lambda r: (r[0], r[1]))
    return [WalkIn(f"W{k:02d}", hid, t, sev, need) for k, (t, hid, sev, need) in enumerate(raws, start=1)]


def make_fleet(rng: random.Random, cfg: SimConfig, places: dict, nodes: list[str]) -> list[UnitSpec]:
    hospitals = sorted(places["hospitals"], key=lambda h: h["id"])
    fire = sorted(places.get("fire_stations", []), key=lambda s: s["id"])
    stations = sorted(places.get("ambulance_stations", []), key=lambda s: s["id"]) + fire
    units = [UnitSpec(f"ALS{i}", "ALS", hospitals[(i - 1) % len(hospitals)]["id"],
                      hospitals[(i - 1) % len(hospitals)]["node"])
             for i in range(1, cfg.fleet["ALS"] + 1)]
    units += bases(rng, "BLS", cfg.fleet["BLS"], stations, nodes)
    units += bases(rng, "FIRE", cfg.fleet["FIRE"], fire, nodes)
    units += bases(rng, "RESCUE", cfg.fleet["RESCUE"], fire, nodes)
    return units


def bases(rng: random.Random, kind: str, count: int, pool: list[dict], nodes: list[str]) -> list[UnitSpec]:
    """Distinct real stations while they last, then seeded random nodes."""
    picks = rng.sample(pool, min(count, len(pool)))
    out = [UnitSpec(f"{kind}{i}", kind, p["id"], p["node"]) for i, p in enumerate(picks, start=1)]
    for i in range(len(out) + 1, count + 1):
        out.append(UnitSpec(f"{kind}{i}", kind, "street", rng.choice(nodes)))
    return out
