from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class PlanningPriority(StrEnum):
    FAST = "FAST"
    ECONOMIC = "ECONOMIC"
    BALANCED = "BALANCED"


class DependencyType(StrEnum):
    FINISH_START = "FS"
    START_START = "SS"
    FINISH_FINISH = "FF"
    START_FINISH = "SF"


@dataclass(frozen=True, slots=True)
class ActivityDependency:
    predecessor_id: str
    relationship: DependencyType = DependencyType.FINISH_START
    lag_days: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class Activity:
    id: str
    name: str
    wbs_code: str
    duration_days: Decimal
    crew_code: str
    crew_size: int
    dependencies: tuple[ActivityDependency, ...] = ()
    element_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WbsNode:
    code: str
    name: str
    parent_code: str | None = None


@dataclass(frozen=True, slots=True)
class WorkBreakdownStructure:
    nodes: tuple[WbsNode, ...]


@dataclass(frozen=True, slots=True)
class ScheduledActivity:
    activity: Activity
    early_start: Decimal
    early_finish: Decimal
    late_start: Decimal
    late_finish: Decimal
    total_float: Decimal
    critical: bool
    free_float: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class ProductivityRecord:
    service_code: str
    unit: str
    output_per_crew_day: Decimal
    crew_size: int
    project_type: str
    conditions: str
    verified: bool
    source: str


@dataclass(frozen=True, slots=True)
class PlanningRequest:
    gross_area_m2: Decimal
    floor_count: int
    stage: str
    project_type: str = "RESIDENTIAL"
    priority: PlanningPriority = PlanningPriority.BALANCED
    available_crew: int | None = None


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    activities: tuple[ScheduledActivity, ...]
    duration_days: Decimal
    peak_crew: int
    critical_path: tuple[str, ...]
    critical_paths: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    assumptions: tuple[str, ...] = field(default_factory=tuple)
