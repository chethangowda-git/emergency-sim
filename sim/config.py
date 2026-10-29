"""All simulator tunables (DESIGN section 3 and 3.11)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AREA_FILE = ROOT / "config" / "area.json"
PLACES_FILE = ROOT / "data" / "roads" / "places.json"

SEVERITIES = ("critical", "serious", "minor")


@dataclass(frozen=True)
class HospitalSpec:
    id: str
    name: str
    capabilities: tuple[str, ...]
    er_slots: int
    ward_beds: int
    doctor_specialties: tuple[str, ...]
    nurses: int
    vehicles: int = 1


DEFAULT_HOSPITALS = (
    HospitalSpec("H1", "Central Trauma Center", ("trauma", "burns", "general"), 6, 10,
                 ("trauma", "trauma", "burns"), 3),
    HospitalSpec("H2", "Northside General", ("trauma", "general"), 4, 6, ("trauma", "trauma"), 2),
    HospitalSpec("H3", "Eastside Community", ("burns", "general"), 4, 6, ("burns", "burns"), 2),
)


@dataclass(frozen=True)
class SimConfig:
    horizon: int = 360
    incident_window: int = 120
    emergency_factor: float = 0.85

    deadline: dict[str, int] = field(default_factory=lambda: {"critical": 60, "serious": 120, "minor": 240})
    treatment: dict[str, int] = field(default_factory=lambda: {"critical": 90, "serious": 60, "minor": 30})
    stabilize_after: int = 20
    transfer_deadline: int = 120
    release_delay: int = 30

    clearance_minutes: int = 10
    on_scene_minutes: int = 5
    handover_minutes: int = 5
    hold_minutes: int = 15
    transfer_prep_minutes: int = 5

    n_incidents: int = 12
    normal_victims: tuple[int, int] = (1, 4)
    surge_victims: tuple[int, int] = (6, 10)
    surge_window: tuple[int, int] = (30, 90)
    incident_type_weights: dict[str, float] = field(default_factory=lambda: {
        "traffic_accident": 0.35, "medical_emergency": 0.35,
        "building_fire": 0.20, "structural_collapse": 0.10,
    })
    severity_mix: dict[str, float] = field(default_factory=lambda: {"critical": 0.25, "serious": 0.35, "minor": 0.40})

    walk_in_mean_gap: int = 40
    walk_in_mix: dict[str, float] = field(default_factory=lambda: {"critical": 0.20, "serious": 0.40, "minor": 0.40})

    fleet: dict[str, int] = field(default_factory=lambda: {"ALS": 3, "BLS": 3, "FIRE": 2, "RESCUE": 1})
    doctor_max_patients: int = 2
    nurse_max_patients: int = 3
    hospitals: tuple[HospitalSpec, ...] = DEFAULT_HOSPITALS

    def to_dict(self) -> dict:
        return asdict(self)


def load_area(path: Path = AREA_FILE) -> dict:
    return json.loads(path.read_text())


def load_places(path: Path = PLACES_FILE) -> dict:
    return json.loads(path.read_text())


def default_config() -> SimConfig:
    return SimConfig(emergency_factor=float(load_area()["emergency_factor"]))
