from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.modules.planning.domain.models import Activity


class ResourceKind(StrEnum):
    WORKFORCE = "WORKFORCE"
    EQUIPMENT = "EQUIPMENT"


class DelayRootCause(StrEnum):
    MATERIAL = "MATERIAL"
    LABOR = "LABOR"
    EQUIPMENT = "EQUIPMENT"
    DESIGN = "DESIGN"
    WEATHER = "WEATHER"
    CLIENT = "CLIENT"
    SUBCONTRACTOR = "SUBCONTRACTOR"
    APPROVAL = "APPROVAL"
    QUALITY = "QUALITY"
    SAFETY = "SAFETY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ResourceCapacity:
    code: str
    kind: ResourceKind
    capacity: int


@dataclass(frozen=True, slots=True)
class ResourceOverload:
    code: str
    kind: ResourceKind
    start_day: Decimal
    finish_day: Decimal
    demand: int
    capacity: int
    activity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LeveledActivity:
    activity: Activity
    planned_start: Decimal
    planned_finish: Decimal


@dataclass(frozen=True, slots=True)
class ResourceLevelingResult:
    activities: tuple[LeveledActivity, ...]
    duration_days: Decimal
    initial_overloads: tuple[ResourceOverload, ...]
    remaining_overloads: tuple[ResourceOverload, ...]
    shifted_activity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProgressVariance:
    activity_id: str
    observed_day: Decimal
    planned_progress: Decimal
    actual_progress: Decimal
    progress_variance: Decimal
    planned_finish_day: Decimal
    forecast_finish_day: Decimal
    finish_variance_days: Decimal
