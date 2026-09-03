from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


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


class SpaceKind(StrEnum):
    ROOM = "ROOM"
    CIRCULATION = "CIRCULATION"
    CORE = "CORE"
    SERVICE = "SERVICE"
    ENTRANCE = "ENTRANCE"


class PrivacyLevel(StrEnum):
    PUBLIC = "PUBLIC"
    SEMI_PRIVATE = "SEMI_PRIVATE"
    PRIVATE = "PRIVATE"
    SERVICE = "SERVICE"


class ConnectionType(StrEnum):
    ADJACENT = "ADJACENT"
    DOOR = "DOOR"
    OPENING = "OPENING"
    VERTICAL = "VERTICAL"


@dataclass(frozen=True, slots=True)
class ArchitecturalSpace:
    key: str
    name: str
    function: str
    kind: SpaceKind
    privacy: PrivacyLevel
    boundary_m: tuple[tuple[float, float], ...]
    wet_area: bool = False
    daylight_required: bool = False
    has_exterior_opening: bool = False
    accessible_required: bool = False
    accessible: bool = True


@dataclass(frozen=True, slots=True)
class SpatialConnection:
    source_key: str
    target_key: str
    connection_type: ConnectionType
    accessible: bool = True


@dataclass(frozen=True, slots=True)
class ArchitecturalZone:
    key: str
    name: str
    zone_type: str
    member_space_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpatialGraphInput:
    project_id: str
    gross_area_m2: Decimal
    spaces: tuple[ArchitecturalSpace, ...]
    connections: tuple[SpatialConnection, ...]
    zones: tuple[ArchitecturalZone, ...] = ()
    building_id: str | None = None
    level_id: str | None = None
    reviewer: str = "SYSTEM_ARCHITECTURE_ADMIN"


@dataclass(frozen=True, slots=True)
class SpatialIssue:
    code: str
    severity: str
    message: str
    evidence_element_ids: tuple[str, ...]
    evidence_relationship_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SpatialGraphAnalysis:
    analysis_id: str
    project_id: str
    reused: bool
    metrics: tuple[ArchitectureMetric, ...]
    issues: tuple[SpatialIssue, ...]
    assumptions: tuple[str, ...]
    spaces: tuple[dict[str, object], ...]
    zones: tuple[dict[str, object], ...]
    connections: tuple[dict[str, object], ...]
    analysis_version: str
    input_checksum: str
