from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.security import require_admin_key
from app.modules.architecture_intelligence.application.service import ArchitectureAnalyzer
from app.modules.architecture_intelligence.domain.models import ArchitectureInput
from app.modules.planning.application.service import ConstructionSchedulingService
from app.modules.planning.domain.models import PlanningPriority, PlanningRequest
from app.modules.planning.infrastructure.repository import ProductivityRepository

router = APIRouter(
    prefix="/api/v1/intelligence",
    tags=["construction-intelligence"],
    dependencies=[Depends(require_admin_key)],
)


class ArchitectureAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gross_area_m2: Decimal = Field(gt=0)
    net_area_m2: Decimal | None = Field(default=None, gt=0)
    circulation_area_m2: Decimal | None = Field(default=None, ge=0)
    core_area_m2: Decimal | None = Field(default=None, ge=0)
    facade_area_m2: Decimal | None = Field(default=None, gt=0)
    opening_area_m2: Decimal | None = Field(default=None, ge=0)
    room_count: int | None = Field(default=None, ge=0)
    unreachable_room_count: int = Field(default=0, ge=0)
    invalid_room_boundary_count: int = Field(default=0, ge=0)
    door_collision_count: int = Field(default=0, ge=0)
    building_type: str = "RESIDENTIAL"


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gross_area_m2: Decimal = Field(gt=0)
    floor_count: int = Field(gt=0, le=200)
    stage: str = "FOUNDATION_COMPLETED"
    project_type: str = "RESIDENTIAL"
    priority: PlanningPriority = PlanningPriority.BALANCED
    available_crew: int | None = Field(default=None, gt=0)


@router.post("/architecture/analyze")
def analyze_architecture(payload: ArchitectureAnalysisRequest) -> dict:
    result = ArchitectureAnalyzer().analyze(ArchitectureInput(**payload.model_dump()))
    return {
        "metrics": [
            {
                "code": metric.code,
                "value": metric.value,
                "unit": metric.unit,
                "source": metric.source,
            }
            for metric in result.metrics
        ],
        "issues": [
            {
                "code": issue.code,
                "severity": issue.severity,
                "message": issue.message,
                "evidence_count": issue.evidence_count,
            }
            for issue in result.issues
        ],
        "assumptions": list(result.assumptions),
    }


@router.post("/schedule")
def create_schedule(
    payload: ScheduleRequest,
    session: Session = Depends(get_session),
) -> dict:
    catalog = ProductivityRepository(session).active_catalog(payload.project_type)
    result = ConstructionSchedulingService(productivity=catalog or None).create(
        PlanningRequest(**payload.model_dump())
    )
    return {
        "duration_days": result.duration_days,
        "peak_crew": result.peak_crew,
        "critical_path": list(result.critical_path),
        "assumptions": list(result.assumptions),
        "activities": [
            {
                "id": item.activity.id,
                "wbs_code": item.activity.wbs_code,
                "name": item.activity.name,
                "duration_days": item.activity.duration_days,
                "early_start": item.early_start,
                "early_finish": item.early_finish,
                "late_start": item.late_start,
                "late_finish": item.late_finish,
                "total_float": item.total_float,
                "critical": item.critical,
                "crew_size": item.activity.crew_size,
            }
            for item in result.activities
        ],
    }


@router.get("/productivity-records")
def list_productivity_records(session: Session = Depends(get_session)) -> dict:
    records = ProductivityRepository(session).active_catalog()
    return {
        "records": [
            {
                "service_code": item.service_code,
                "unit": item.unit,
                "output_per_crew_day": item.output_per_crew_day,
                "crew_size": item.crew_size,
                "project_type": item.project_type,
                "conditions": item.conditions,
                "verified": item.verified,
                "source": item.source,
            }
            for item in records.values()
        ]
    }
