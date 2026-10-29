"""World entities (DESIGN 3.2 to 3.8). Plain dataclasses; the World mutates them."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from sim.roads import Route

AMBULANCE_KINDS = ("ALS", "BLS")
TEAM_KINDS = ("FIRE", "RESCUE")
VEHICLE_KIND = "HV"
TEAM_FOR_REQUIREMENT = {"fire": "FIRE", "rescue": "RESCUE"}


@dataclass
class Victim:
    id: str
    incident_id: str | None
    severity: str
    care_need: str
    reported_at: int
    deadline: int | None
    original_severity: str = ""
    walk_in: bool = False
    status: str = "waiting"
    unit: str | None = None
    hospital: str | None = None
    reservation: str | None = None
    picked_up_at: int | None = None
    delivered_at: int | None = None
    treatment_started_at: int | None = None
    transferable_at: int | None = None
    transfer_deadline: int | None = None
    ready_at: int | None = None
    released_at: int | None = None
    doctor: str | None = None
    nurse: str | None = None
    treatment_minutes: int | None = None
    treatment_remaining: int | None = None
    transfers: int = 0

    def __post_init__(self) -> None:
        if not self.original_severity:
            self.original_severity = self.severity


@dataclass
class Incident:
    id: str
    type: str
    node: str
    lat: float
    lon: float
    reported_at: int
    requires: str | None
    description: str
    victim_ids: list[str]
    surge: bool = False
    team: str | None = None
    team_arrived_at: int | None = None
    cleared_at: int | None = None


@dataclass
class WalkIn:
    id: str
    hospital: str
    t: int
    severity: str
    care_need: str


@dataclass
class Staff:
    id: str
    hospital: str
    kind: str
    skills: tuple[str, ...]
    max_patients: int
    patients: list[str] = field(default_factory=list)
    home: str = ""
    lent_until: int | None = None

    def __post_init__(self) -> None:
        if not self.home:
            self.home = self.hospital

    @property
    def free(self) -> int:
        return self.max_patients - len(self.patients)


@dataclass
class Vehicle:
    id: str
    hospital: str
    node: str


@dataclass
class Hospital:
    id: str
    name: str
    node: str
    lat: float
    lon: float
    capabilities: tuple[str, ...]
    er_slots: int
    ward_beds: int
    doctors: list[Staff]
    nurses: list[Staff]
    vehicles: list[Vehicle]
    er: list[str] = field(default_factory=list)
    ward: list[str] = field(default_factory=list)


@dataclass
class UnitSpec:
    id: str
    kind: str
    base: str
    base_node: str


@dataclass
class Trip:
    id: str
    unit: str
    purpose: str
    route: Route
    depart_t: int
    dest_node: str
    ended_at: int | None = None

    @property
    def arrive_t(self) -> int:
        return self.depart_t + self.route.minutes

    def to_dict(self) -> dict:
        return {"id": self.id, "unit": self.unit, "purpose": self.purpose,
                "depart_t": self.depart_t, "arrive_t": self.arrive_t,
                "ended_at": self.ended_at, "dest_node": self.dest_node, **self.route.to_dict()}


@dataclass
class Unit:
    id: str
    kind: str
    base: str
    base_node: str
    node: str
    state: str = "idle"
    incident: str | None = None
    hospital: str | None = None
    victims: list[str] = field(default_factory=list)
    trip: Trip | None = None
    on_scene_since: int | None = None
    handover_until: int | None = None
    owner: str | None = None
    lent_until: int | None = None
    prep_until: int | None = None
    redirects: int = 0
    reroutes: int = 0

    @property
    def is_ambulance(self) -> bool:
        return self.kind in AMBULANCE_KINDS

    @property
    def is_vehicle(self) -> bool:
        return self.kind == VEHICLE_KIND


@dataclass
class Reservation:
    """Firm: one victim, one ER slot. Hold: a slot kept free, for a named expected critical
    (`victim` set) or general (`victim` None), until `expires_at`."""

    id: str
    hospital: str
    unit: str | None
    victim: str | None
    created_at: int
    kind: str = "firm"
    status: str = "active"
    expires_at: int | None = None
    ended_at: int | None = None


@dataclass
class Scenario:
    seed: int
    incidents: list[Incident]
    victims: list[Victim]
    walk_ins: list[WalkIn]
    hospitals: list[Hospital]
    units: list[UnitSpec]

    def to_dict(self) -> dict:
        return asdict(self)
