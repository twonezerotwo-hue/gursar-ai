from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class StructuralConnectionType(StrEnum):
    BEARS_ON = "BEARS_ON"
    MONOLITHIC = "MONOLITHIC"
    ANCHORED = "ANCHORED"
    TRANSFER = "TRANSFER"


class StructuralLoadType(StrEnum):
    DEAD = "DEAD"
    LIVE = "LIVE"
    WIND = "WIND"
    SEISMIC = "SEISMIC"
    SNOW = "SNOW"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class StructuralSupport:
    supported_element_id: str
    supporting_element_id: str
    connection_type: StructuralConnectionType
    load_path_enabled: bool = True


@dataclass(frozen=True, slots=True)
class StructuralLoad:
    key: str
    element_id: str
    load_type: StructuralLoadType
    magnitude_kn: Decimal
    factor: Decimal = Decimal("1")
    direction: str = "GRAVITY"
    source_reference: str | None = None


@dataclass(frozen=True, slots=True)
class StructuralGraphInput:
    project_id: str
    element_ids: tuple[str, ...]
    supports: tuple[StructuralSupport, ...]
    loads: tuple[StructuralLoad, ...] = ()
    reviewer: str = "SYSTEM_STRUCTURAL_ADMIN"


@dataclass(frozen=True, slots=True)
class StructuralMetric:
    code: str
    value: Decimal
    unit: str
    source: str


@dataclass(frozen=True, slots=True)
class StructuralIssue:
    code: str
    severity: str
    message: str
    evidence_element_ids: tuple[str, ...]
    evidence_relationship_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StructuralGraphAnalysis:
    analysis_id: str
    project_id: str
    reused: bool
    metrics: tuple[StructuralMetric, ...]
    issues: tuple[StructuralIssue, ...]
    assumptions: tuple[str, ...]
    element_ids: tuple[str, ...]
    relationship_ids: tuple[str, ...]
    load_ids: tuple[str, ...]
    analysis_version: str
    input_checksum: str
