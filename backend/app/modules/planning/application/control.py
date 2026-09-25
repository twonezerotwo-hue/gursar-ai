from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit
from app.modules.planning.application.resource import ResourceLevelingEngine
from app.modules.planning.domain.models import PlanningPriority, ScheduleResult
from app.modules.planning.domain.resource import (
    DelayRootCause,
    ProgressVariance,
    ResourceCapacity,
    ResourceLevelingResult,
)
from app.modules.planning.infrastructure.models import (
    ScheduleActivityElementLinkORM,
    ScheduleBaselineORM,
    ScheduleDelayEventORM,
    ScheduleProgressUpdateORM,
)
from app.modules.project_graph.infrastructure.models import BuildingElementORM
from app.modules.projects.infrastructure.models import ProjectORM

SCHEDULE_CONTROL_VERSION = "SCHEDULE_CONTROL_V1"


class ScheduleControlError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BaselineCreationResult:
    baseline: ScheduleBaselineORM
    leveling: ResourceLevelingResult
    link_count: int
    reused: bool


class ScheduleControlService:
    """Immutable schedule baseline, actuals, delays and verified 4D links."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._leveler = ResourceLevelingEngine()

    def create_baseline(
        self,
        *,
        project_id: str,
        name: str,
        priority: PlanningPriority,
        schedule: ScheduleResult,
        capacities: tuple[ResourceCapacity, ...],
        created_by: str,
    ) -> BaselineCreationResult:
        self._require_project(project_id)
        activities = tuple(item.activity for item in schedule.activities)
        normalized_capacities = tuple(sorted(capacities, key=lambda item: (item.kind, item.code)))
        leveling = self._leveler.level(activities, normalized_capacities)
        snapshot = self._activity_snapshot(schedule, leveling)
        capacities_payload = [
            {"code": item.code, "kind": item.kind.value, "capacity": item.capacity}
            for item in normalized_capacities
        ]
        checksum = self._checksum(
            {
                "project_id": project_id,
                "name": name,
                "priority": priority.value,
                "activities": snapshot,
                "capacities": capacities_payload,
                "analysis_version": SCHEDULE_CONTROL_VERSION,
            }
        )
        existing = self._session.scalar(
            select(ScheduleBaselineORM).where(
                ScheduleBaselineORM.project_id == project_id,
                ScheduleBaselineORM.analysis_version == SCHEDULE_CONTROL_VERSION,
                ScheduleBaselineORM.input_checksum == checksum,
            )
        )
        if existing is not None:
            link_count = self._session.scalar(
                select(func.count(ScheduleActivityElementLinkORM.id)).where(
                    ScheduleActivityElementLinkORM.baseline_id == existing.id
                )
            )
            return BaselineCreationResult(existing, leveling, int(link_count or 0), True)

        current_version = self._session.scalar(
            select(func.max(ScheduleBaselineORM.version)).where(
                ScheduleBaselineORM.project_id == project_id
            )
        )
        row = ScheduleBaselineORM(
            project_id=project_id,
            name=name,
            version=int(current_version or 0) + 1,
            priority=priority.value,
            duration_days=leveling.duration_days,
            status="FROZEN",
            analysis_version=SCHEDULE_CONTROL_VERSION,
            input_checksum=checksum,
            activities_json=snapshot,
            capacities_json=capacities_payload,
            overloads_json=[self._overload_payload(item) for item in leveling.initial_overloads],
            assumptions_json=list(schedule.assumptions),
            created_by=created_by,
        )
        self._session.add(row)
        self._session.flush()
        link_count = self._link_verified_elements(row, schedule)
        append_audit(
            self._session,
            action="SCHEDULE_BASELINE_CREATED",
            aggregate_id=row.id,
            detail={
                "project_id": project_id,
                "version": row.version,
                "input_checksum": checksum,
                "duration_days": str(row.duration_days),
                "initial_overloads": len(leveling.initial_overloads),
                "shifted_activities": list(leveling.shifted_activity_ids),
                "element_links": link_count,
            },
            actor_type="ENGINEER",
            actor_id=created_by,
        )
        return BaselineCreationResult(row, leveling, link_count, False)

    def latest(self, project_id: str) -> ScheduleBaselineORM:
        self._require_project(project_id)
        row = self._session.scalar(
            select(ScheduleBaselineORM)
            .where(ScheduleBaselineORM.project_id == project_id)
            .order_by(ScheduleBaselineORM.version.desc())
        )
        if row is None:
            raise ScheduleControlError("SCHEDULE_BASELINE_NOT_FOUND")
        return row

    def add_progress(
        self,
        *,
        project_id: str,
        baseline_id: str,
        activity_id: str,
        observed_day: Decimal,
        actual_progress: Decimal,
        actual_start_day: Decimal | None,
        actual_finish_day: Decimal | None,
        forecast_finish_day: Decimal | None,
        source: str,
        verified: bool,
        notes: str | None,
    ) -> tuple[ScheduleProgressUpdateORM, ProgressVariance]:
        baseline = self._baseline(project_id, baseline_id)
        activity = self._activity(baseline, activity_id)
        if observed_day < 0 or not Decimal("0") <= actual_progress <= Decimal("1"):
            raise ScheduleControlError("INVALID_PROGRESS_VALUES")
        if (
            actual_start_day is not None
            and actual_finish_day is not None
            and actual_finish_day < actual_start_day
        ):
            raise ScheduleControlError("ACTUAL_FINISH_BEFORE_START")
        start = Decimal(str(activity["planned_start"]))
        finish = Decimal(str(activity["planned_finish"]))
        planned = self._planned_progress(start, finish, observed_day)
        forecast = forecast_finish_day if forecast_finish_day is not None else finish
        variance = ProgressVariance(
            activity_id=activity_id,
            observed_day=observed_day,
            planned_progress=planned,
            actual_progress=actual_progress,
            progress_variance=actual_progress - planned,
            planned_finish_day=finish,
            forecast_finish_day=forecast,
            finish_variance_days=forecast - finish,
        )
        row = ScheduleProgressUpdateORM(
            project_id=project_id,
            baseline_id=baseline_id,
            activity_id=activity_id,
            observed_day=observed_day,
            actual_progress=actual_progress,
            planned_progress=planned,
            progress_variance=variance.progress_variance,
            actual_start_day=actual_start_day,
            actual_finish_day=actual_finish_day,
            forecast_finish_day=forecast,
            finish_variance_days=variance.finish_variance_days,
            source=source,
            verified=verified,
            notes=notes,
        )
        self._session.add(row)
        self._session.flush()
        append_audit(
            self._session,
            action="SCHEDULE_PROGRESS_RECORDED",
            aggregate_id=row.id,
            detail={
                "baseline_id": baseline_id,
                "activity_id": activity_id,
                "progress_variance": str(variance.progress_variance),
                "finish_variance_days": str(variance.finish_variance_days),
                "verified": verified,
            },
            actor_type="ENGINEER" if verified else "SYSTEM",
        )
        return row, variance

    def add_delay(
        self,
        *,
        project_id: str,
        baseline_id: str,
        activity_id: str,
        root_cause: DelayRootCause,
        impact_days: Decimal,
        reason: str,
        verified: bool,
        created_by: str,
    ) -> ScheduleDelayEventORM:
        baseline = self._baseline(project_id, baseline_id)
        activity = self._activity(baseline, activity_id)
        if impact_days < 0:
            raise ScheduleControlError("DELAY_IMPACT_CANNOT_BE_NEGATIVE")
        total_float = Decimal(str(activity["total_float"]))
        project_impact = impact_days if activity["critical"] else max(
            Decimal("0"), impact_days - total_float
        )
        row = ScheduleDelayEventORM(
            project_id=project_id,
            baseline_id=baseline_id,
            activity_id=activity_id,
            root_cause=root_cause.value,
            impact_days=impact_days,
            project_impact_days=project_impact,
            reason=reason,
            verified=verified,
            created_by=created_by,
        )
        self._session.add(row)
        self._session.flush()
        append_audit(
            self._session,
            action="SCHEDULE_DELAY_RECORDED",
            aggregate_id=row.id,
            detail={
                "baseline_id": baseline_id,
                "activity_id": activity_id,
                "root_cause": root_cause.value,
                "impact_days": str(impact_days),
                "project_impact_days": str(project_impact),
                "verified": verified,
            },
            actor_type="ENGINEER" if verified else "SYSTEM",
            actor_id=created_by,
        )
        return row

    def _link_verified_elements(
        self,
        baseline: ScheduleBaselineORM,
        schedule: ScheduleResult,
    ) -> int:
        types = {
            element_type
            for item in schedule.activities
            for element_type in item.activity.element_types
        }
        if not types:
            return 0
        elements = self._session.scalars(
            select(BuildingElementORM).where(
                BuildingElementORM.project_id == baseline.project_id,
                BuildingElementORM.element_type.in_(types),
                BuildingElementORM.review_status.in_(("VERIFIED", "CORRECTED")),
            )
        ).all()
        activities_by_type: dict[str, list[str]] = {}
        for item in schedule.activities:
            for element_type in item.activity.element_types:
                activities_by_type.setdefault(element_type, []).append(item.activity.id)
        count = 0
        for element in elements:
            for activity_id in activities_by_type.get(element.element_type, []):
                self._session.add(
                    ScheduleActivityElementLinkORM(
                        project_id=baseline.project_id,
                        baseline_id=baseline.id,
                        activity_id=activity_id,
                        element_id=element.id,
                        link_method="VERIFIED_ELEMENT_TYPE",
                    )
                )
                count += 1
        self._session.flush()
        return count

    def _baseline(self, project_id: str, baseline_id: str) -> ScheduleBaselineORM:
        row = self._session.scalar(
            select(ScheduleBaselineORM).where(
                ScheduleBaselineORM.id == baseline_id,
                ScheduleBaselineORM.project_id == project_id,
            )
        )
        if row is None:
            raise ScheduleControlError("SCHEDULE_BASELINE_NOT_FOUND")
        return row

    @staticmethod
    def _activity(baseline: ScheduleBaselineORM, activity_id: str) -> dict[str, Any]:
        item = next(
            (row for row in baseline.activities_json if row["id"] == activity_id),
            None,
        )
        if item is None:
            raise ScheduleControlError("SCHEDULE_ACTIVITY_NOT_FOUND")
        return item

    def _require_project(self, project_id: str) -> None:
        if self._session.get(ProjectORM, project_id) is None:
            raise ScheduleControlError("PROJECT_NOT_FOUND")

    @staticmethod
    def _activity_snapshot(
        schedule: ScheduleResult,
        leveling: ResourceLevelingResult,
    ) -> list[dict[str, Any]]:
        calculated = {item.activity.id: item for item in schedule.activities}
        return [
            {
                "id": item.activity.id,
                "name": item.activity.name,
                "wbs_code": item.activity.wbs_code,
                "duration_days": str(item.activity.duration_days),
                "planned_start": str(item.planned_start),
                "planned_finish": str(item.planned_finish),
                "crew_code": item.activity.crew_code,
                "crew_size": item.activity.crew_size,
                "equipment_requirements": dict(item.activity.equipment_requirements),
                "element_types": list(item.activity.element_types),
                "total_float": str(calculated[item.activity.id].total_float),
                "critical": calculated[item.activity.id].critical,
                "shifted": item.activity.id in leveling.shifted_activity_ids,
            }
            for item in leveling.activities
        ]

    @staticmethod
    def _planned_progress(start: Decimal, finish: Decimal, observed: Decimal) -> Decimal:
        if observed <= start:
            return Decimal("0")
        if observed >= finish:
            return Decimal("1")
        if finish == start:
            return Decimal("1")
        return ((observed - start) / (finish - start)).quantize(Decimal("0.000001"))

    @staticmethod
    def _overload_payload(item: Any) -> dict[str, Any]:
        return {
            "code": item.code,
            "kind": item.kind.value,
            "start_day": str(item.start_day),
            "finish_day": str(item.finish_day),
            "demand": item.demand,
            "capacity": item.capacity,
            "activity_ids": list(item.activity_ids),
        }

    @staticmethod
    def _checksum(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
