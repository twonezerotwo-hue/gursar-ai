from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.conversation_intelligence.domain.models import ProjectContext
from app.modules.conversation_intelligence.infrastructure.models import (
    ConversationPlanORM,
    ConversationProjectContextORM,
)
from app.modules.planning.domain.models import PlanningPriority, ScheduleResult


class ProjectContextRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, conversation_id: str) -> ProjectContext:
        row = self._session.scalar(
            select(ConversationProjectContextORM).where(
                ConversationProjectContextORM.conversation_id == conversation_id
            )
        )
        return self._to_domain(row) if row else ProjectContext(conversation_id=conversation_id)

    def merge(self, conversation_id: str, updates: dict[str, Any]) -> ProjectContext:
        row = self._session.scalar(
            select(ConversationProjectContextORM).where(
                ConversationProjectContextORM.conversation_id == conversation_id
            )
        )
        if row is None:
            row = ConversationProjectContextORM(conversation_id=conversation_id)
            self._session.add(row)
        elif updates:
            row.version += 1
        for key in (
            "project_type",
            "footprint_m2",
            "gross_area_m2",
            "floor_count",
            "structural_system",
            "stage",
            "location",
            "goal",
            "available_crew",
        ):
            value = updates.get(key)
            if value is not None:
                setattr(row, key, value)
        facts = dict(row.facts_json or {})
        facts.update(updates.get("facts", {}))
        row.facts_json = facts
        self._session.flush()
        return self._to_domain(row)

    def save_plan(
        self,
        conversation_id: str,
        context_version: int,
        priority: PlanningPriority,
        result: ScheduleResult,
    ) -> str:
        row = ConversationPlanORM(
            conversation_id=conversation_id,
            context_version=context_version,
            priority=priority.value,
            duration_days=result.duration_days,
            peak_crew=result.peak_crew,
            schedule_json=[
                {
                    "id": item.activity.id,
                    "wbs_code": item.activity.wbs_code,
                    "name": item.activity.name,
                    "duration_days": str(item.activity.duration_days),
                    "early_start": str(item.early_start),
                    "early_finish": str(item.early_finish),
                    "crew_code": item.activity.crew_code,
                    "crew_size": item.activity.crew_size,
                    "critical": item.critical,
                    "element_types": list(item.activity.element_types),
                }
                for item in result.activities
            ],
            assumptions_json=list(result.assumptions),
        )
        self._session.add(row)
        self._session.flush()
        return row.id

    @staticmethod
    def _to_domain(row: ConversationProjectContextORM) -> ProjectContext:
        return ProjectContext(
            conversation_id=row.conversation_id,
            project_type=row.project_type,
            footprint_m2=row.footprint_m2,
            gross_area_m2=row.gross_area_m2,
            floor_count=row.floor_count,
            structural_system=row.structural_system,
            stage=row.stage,
            location=row.location,
            goal=row.goal,
            available_crew=row.available_crew,
            facts=row.facts_json or {},
            version=row.version,
        )
