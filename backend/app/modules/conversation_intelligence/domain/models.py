from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ConstructionIntent(StrEnum):
    GENERAL_CONSTRUCTION = "GENERAL_CONSTRUCTION"
    NEW_BUILD = "NEW_BUILD"
    RENOVATION = "RENOVATION"
    ARCHITECTURE = "ARCHITECTURE"
    STRUCTURAL = "STRUCTURAL"
    MEP = "MEP"
    QUANTITY = "QUANTITY"
    COST = "COST"
    QUOTE = "QUOTE"
    SITE_MANAGEMENT = "SITE_MANAGEMENT"
    SITE_SCHEDULE = "SITE_SCHEDULE"
    CREW_PLANNING = "CREW_PLANNING"
    SAFETY = "SAFETY"
    QUALITY = "QUALITY"
    REGULATION = "REGULATION"
    CONTRACT = "CONTRACT"
    DOCUMENT_ANALYSIS = "DOCUMENT_ANALYSIS"
    IFC = "IFC"
    DXF = "DXF"
    PDF = "PDF"
    CONTACT = "CONTACT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProjectContext:
    conversation_id: str
    project_type: str | None = None
    footprint_m2: Decimal | None = None
    gross_area_m2: Decimal | None = None
    floor_count: int | None = None
    structural_system: str | None = None
    stage: str | None = None
    location: str | None = None
    goal: str | None = None
    available_crew: int | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    version: int = 1


@dataclass(frozen=True, slots=True)
class RoutedTurn:
    intent: ConstructionIntent
    confidence: float
    updates: dict[str, Any]
    assumptions: tuple[str, ...] = ()
