from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ArchitectureInput:
    gross_area_m2: Decimal
    net_area_m2: Decimal | None = None
    circulation_area_m2: Decimal | None = None
    core_area_m2: Decimal | None = None
    facade_area_m2: Decimal | None = None
    opening_area_m2: Decimal | None = None
    room_count: int | None = None
    unreachable_room_count: int = 0
    invalid_room_boundary_count: int = 0
    door_collision_count: int = 0
    building_type: str = "RESIDENTIAL"


@dataclass(frozen=True, slots=True)
class ArchitectureMetric:
    code: str
    value: Decimal
    unit: str
    source: str


@dataclass(frozen=True, slots=True)
class ArchitectureIssue:
    code: str
    severity: str
    message: str
    evidence_count: int


@dataclass(frozen=True, slots=True)
class ArchitectureAnalysis:
    metrics: tuple[ArchitectureMetric, ...]
    issues: tuple[ArchitectureIssue, ...]
    assumptions: tuple[str, ...]
