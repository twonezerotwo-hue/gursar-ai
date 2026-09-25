from __future__ import annotations

from decimal import Decimal
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.persistence import append_audit
from app.core.security import require_admin_key
from app.modules.architecture_intelligence.application.service import ArchitectureAnalyzer
from app.modules.architecture_intelligence.application.spatial import (
    ArchitectureSpatialGraphService,
    SpatialGraphError,
)
from app.modules.architecture_intelligence.domain.models import (
    ArchitecturalSpace,
    ArchitecturalZone,
    ArchitectureInput,
    ConnectionType,
    PrivacyLevel,
    SpaceKind,
    SpatialConnection,
    SpatialGraphAnalysis,
    SpatialGraphInput,
)
from app.modules.planning.application.control import (
    BaselineCreationResult,
    ScheduleControlError,
    ScheduleControlService,
)
from app.modules.planning.application.productivity import (
    CrewPlanningService,
    ProductivityPlanningError,
)
from app.modules.planning.application.resource import ResourcePlanningError
from app.modules.planning.application.service import ConstructionSchedulingService
from app.modules.planning.domain.models import (
    CrewPlan,
    PlanningPriority,
    PlanningRequest,
    ProductivityRecord,
    ProductivitySourceTier,
)
from app.modules.planning.domain.resource import DelayRootCause, ResourceCapacity, ResourceKind
from app.modules.planning.infrastructure.models import ScheduleBaselineORM
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


class ProductivityRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_code: str = Field(min_length=1, max_length=120)
    work_type: str | None = Field(default=None, max_length=160)
    unit: str = Field(min_length=1, max_length=30)
    output_per_crew_day: Decimal = Field(gt=0)
    crew_size: int = Field(gt=0, le=1_000)
    crew_composition: dict[str, int] = Field(default_factory=dict)
    sample_quantity: Decimal | None = Field(default=None, gt=0)
    sample_duration_days: Decimal | None = Field(default=None, gt=0)
    productive_hours: Decimal | None = Field(default=None, gt=0)
    project_type: str = Field(default="RESIDENTIAL", min_length=1, max_length=60)
    location: str | None = Field(default=None, max_length=240)
    floor: str | None = Field(default=None, max_length=100)
    conditions: str = Field(default="normal", min_length=1, max_length=500)
    verified: bool = False
    confidence: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    source_tier: ProductivitySourceTier = ProductivitySourceTier.HEURISTIC
    source: str = Field(min_length=1, max_length=300)


class CrewPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_code: str = Field(min_length=1, max_length=120)
    quantity: Decimal = Field(gt=0)
    unit: str = Field(min_length=1, max_length=30)
    balanced_duration_days: Decimal = Field(gt=0)
    project_type: str = Field(default="RESIDENTIAL", min_length=1, max_length=60)
    location: str | None = Field(default=None, max_length=240)
    max_workers: int | None = Field(default=None, gt=0, le=100_000)


class ResourceCapacityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=120)
    kind: ResourceKind
    capacity: int = Field(gt=0, le=100_000)


class ScheduleBaselineRequest(ScheduleRequest):
    name: str = Field(default="Master Baseline", min_length=1, max_length=240)
    location: str | None = Field(default=None, max_length=240)
    resource_capacities: list[ResourceCapacityRequest] = Field(
        default_factory=list, max_length=1_000
    )
    created_by: str = Field(default="SCHEDULE_ADMIN", min_length=1, max_length=200)


class ProgressUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_id: str = Field(min_length=1, max_length=80)
    observed_day: Decimal = Field(ge=0)
    actual_progress: Decimal = Field(ge=0, le=1)
    actual_start_day: Decimal | None = Field(default=None, ge=0)
    actual_finish_day: Decimal | None = Field(default=None, ge=0)
    forecast_finish_day: Decimal | None = Field(default=None, ge=0)
    source: str = Field(min_length=1, max_length=300)
    verified: bool = False
    notes: str | None = Field(default=None, max_length=2_000)


class DelayEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_id: str = Field(min_length=1, max_length=80)
    root_cause: DelayRootCause
    impact_days: Decimal = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2_000)
    verified: bool = False
    created_by: str = Field(default="SCHEDULE_ADMIN", min_length=1, max_length=200)


class ArchitecturalSpaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=300)
    function: str = Field(min_length=1, max_length=200)
    kind: SpaceKind = SpaceKind.ROOM
    privacy: PrivacyLevel = PrivacyLevel.SEMI_PRIVATE
    boundary_m: list[tuple[float, float]] = Field(min_length=3, max_length=5_000)
    wet_area: bool = False
    daylight_required: bool = False
    has_exterior_opening: bool = False
    accessible_required: bool = False
    accessible: bool = True


class SpatialConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1, max_length=160)
    target_key: str = Field(min_length=1, max_length=160)
    connection_type: ConnectionType = ConnectionType.ADJACENT
    accessible: bool = True


class ArchitecturalZoneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=300)
    zone_type: str = Field(min_length=1, max_length=50)
    member_space_keys: list[str] = Field(min_length=1)


class SpatialGraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gross_area_m2: Decimal = Field(gt=0)
    building_id: str | None = None
    level_id: str | None = None
    reviewer: str = Field(default="SYSTEM_ARCHITECTURE_ADMIN", min_length=1, max_length=200)
    spaces: list[ArchitecturalSpaceRequest] = Field(min_length=1, max_length=5_000)
    connections: list[SpatialConnectionRequest] = Field(default_factory=list, max_length=20_000)
    zones: list[ArchitecturalZoneRequest] = Field(default_factory=list, max_length=1_000)


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


@router.post(
    "/projects/{project_id}/architecture/spatial-graph",
    status_code=status.HTTP_201_CREATED,
)
def create_spatial_graph(
    project_id: str,
    payload: SpatialGraphRequest,
    session: Session = Depends(get_session),
) -> dict:
    data = SpatialGraphInput(
        project_id=project_id,
        gross_area_m2=payload.gross_area_m2,
        building_id=payload.building_id,
        level_id=payload.level_id,
        reviewer=payload.reviewer,
        spaces=tuple(
            ArchitecturalSpace(
                **space.model_dump(exclude={"boundary_m"}),
                boundary_m=tuple(space.boundary_m),
            )
            for space in payload.spaces
        ),
        connections=tuple(SpatialConnection(**item.model_dump()) for item in payload.connections),
        zones=tuple(
            ArchitecturalZone(
                **zone.model_dump(exclude={"member_space_keys"}),
                member_space_keys=tuple(zone.member_space_keys),
            )
            for zone in payload.zones
        ),
    )
    try:
        result = ArchitectureSpatialGraphService(session).build_and_analyze(data)
    except SpatialGraphError as exc:
        _raise_spatial_error(exc)
    return _spatial_response(result)


@router.get("/projects/{project_id}/architecture/spatial-graph")
def get_spatial_graph(
    project_id: str,
    session: Session = Depends(get_session),
) -> dict:
    try:
        result = ArchitectureSpatialGraphService(session).latest(project_id)
    except SpatialGraphError as exc:
        _raise_spatial_error(exc)
    return _spatial_response(result)


def _raise_spatial_error(exc: SpatialGraphError) -> NoReturn:
    code = str(exc)
    if code in {"PROJECT_NOT_FOUND", "SPATIAL_GRAPH_NOT_FOUND"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=code) from exc
    if code == "SPATIAL_GRAPH_REVISION_REQUIRED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=code) from exc
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=code) from exc


def _spatial_response(result: SpatialGraphAnalysis) -> dict:
    return {
        "analysis_id": result.analysis_id,
        "project_id": result.project_id,
        "analysis_version": result.analysis_version,
        "input_checksum": result.input_checksum,
        "reused": result.reused,
        "metrics": [
            {
                "code": item.code,
                "value": item.value,
                "unit": item.unit,
                "source": item.source,
            }
            for item in result.metrics
        ],
        "issues": [
            {
                "code": item.code,
                "severity": item.severity,
                "message": item.message,
                "evidence_element_ids": list(item.evidence_element_ids),
                "evidence_relationship_ids": list(item.evidence_relationship_ids),
            }
            for item in result.issues
        ],
        "assumptions": list(result.assumptions),
        "spaces": list(result.spaces),
        "zones": list(result.zones),
        "connections": list(result.connections),
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
        "critical_paths": [list(path) for path in result.critical_paths],
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
                "free_float": item.free_float,
                "critical": item.critical,
                "crew_size": item.activity.crew_size,
            }
            for item in result.activities
        ],
    }


@router.post("/productivity-records", status_code=status.HTTP_201_CREATED)
def create_productivity_record(
    payload: ProductivityRecordRequest,
    session: Session = Depends(get_session),
) -> dict:
    record = ProductivityRecord(
        **payload.model_dump(exclude={"crew_composition"}),
        crew_composition=tuple(sorted(payload.crew_composition.items())),
    )
    try:
        row = ProductivityRepository(session).add_version(record)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    append_audit(
        session,
        action="PRODUCTIVITY_RECORD_VERSION_CREATED",
        aggregate_id=row.id,
        detail={
            "service_code": row.service_code,
            "source_tier": row.source_tier,
            "version": row.version,
            "verified": row.verified,
        },
        actor_type="ADMIN",
        actor_id="ADMIN_API",
    )
    return {"id": row.id, "version": row.version, **_productivity_response(record)}


@router.get("/productivity-records")
def list_productivity_records(
    project_type: str = Query(default="RESIDENTIAL", min_length=1, max_length=60),
    location: str | None = Query(default=None, max_length=240),
    session: Session = Depends(get_session),
) -> dict:
    records = ProductivityRepository(session).active_catalog(project_type, location)
    return {
        "records": [_productivity_response(item) for item in records.values()]
    }


@router.post("/productivity/crew-plan")
def create_crew_plan(
    payload: CrewPlanRequest,
    session: Session = Depends(get_session),
) -> dict:
    record = ProductivityRepository(session).active_catalog(
        payload.project_type, payload.location
    ).get(payload.service_code)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PRODUCTIVITY_RECORD_NOT_FOUND",
        )
    try:
        scenarios = CrewPlanningService().scenarios(
            record,
            quantity=payload.quantity,
            unit=payload.unit,
            balanced_duration_days=payload.balanced_duration_days,
            max_workers=payload.max_workers,
        )
    except ProductivityPlanningError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return {
        "productivity": _productivity_response(record),
        "scenarios": [_crew_plan_response(item) for item in scenarios],
    }


def _productivity_response(item: ProductivityRecord) -> dict:
    return {
        "service_code": item.service_code,
        "work_type": item.work_type,
        "unit": item.unit,
        "output_per_crew_day": item.output_per_crew_day,
        "crew_size": item.crew_size,
        "crew_composition": dict(item.crew_composition),
        "sample_quantity": item.sample_quantity,
        "sample_duration_days": item.sample_duration_days,
        "productive_hours": item.productive_hours,
        "project_type": item.project_type,
        "location": item.location,
        "floor": item.floor,
        "conditions": item.conditions,
        "verified": item.verified,
        "confidence": item.confidence,
        "source_tier": item.source_tier.value,
        "source": item.source,
    }


def _crew_plan_response(item: CrewPlan) -> dict:
    return {
        "priority": item.priority.value,
        "service_code": item.service_code,
        "quantity": item.quantity,
        "unit": item.unit,
        "target_duration_days": item.target_duration_days,
        "crew_days": item.crew_days,
        "required_crews": item.required_crews,
        "required_workers": item.required_workers,
        "planned_duration_days": item.planned_duration_days,
        "daily_output": item.daily_output,
        "crew_composition": dict(item.crew_composition),
        "total_composition": dict(item.total_composition),
        "source_tier": item.source_tier.value,
        "source": item.source,
        "confidence": item.confidence,
        "verified": item.verified,
        "risks": list(item.risks),
        "assumptions": list(item.assumptions),
    }


@router.post(
    "/projects/{project_id}/schedule/baselines",
    status_code=status.HTTP_201_CREATED,
)
def create_schedule_baseline(
    project_id: str,
    payload: ScheduleBaselineRequest,
    session: Session = Depends(get_session),
) -> dict:
    catalog = ProductivityRepository(session).active_catalog(
        payload.project_type, payload.location
    )
    schedule = ConstructionSchedulingService(productivity=catalog or None).create(
        PlanningRequest(
            gross_area_m2=payload.gross_area_m2,
            floor_count=payload.floor_count,
            stage=payload.stage,
            project_type=payload.project_type,
            priority=payload.priority,
            available_crew=payload.available_crew,
        )
    )
    capacities = tuple(
        ResourceCapacity(**item.model_dump()) for item in payload.resource_capacities
    )
    try:
        result = ScheduleControlService(session).create_baseline(
            project_id=project_id,
            name=payload.name,
            priority=payload.priority,
            schedule=schedule,
            capacities=capacities,
            created_by=payload.created_by,
        )
    except (ScheduleControlError, ResourcePlanningError) as exc:
        _raise_schedule_control(exc)
    return _baseline_response(result)


@router.get("/projects/{project_id}/schedule/baselines/latest")
def get_latest_schedule_baseline(
    project_id: str,
    session: Session = Depends(get_session),
) -> dict:
    try:
        baseline = ScheduleControlService(session).latest(project_id)
    except ScheduleControlError as exc:
        _raise_schedule_control(exc)
    return _baseline_row_response(baseline)


@router.post(
    "/projects/{project_id}/schedule/baselines/{baseline_id}/progress",
    status_code=status.HTTP_201_CREATED,
)
def record_schedule_progress(
    project_id: str,
    baseline_id: str,
    payload: ProgressUpdateRequest,
    session: Session = Depends(get_session),
) -> dict:
    try:
        row, variance = ScheduleControlService(session).add_progress(
            project_id=project_id,
            baseline_id=baseline_id,
            **payload.model_dump(),
        )
    except ScheduleControlError as exc:
        _raise_schedule_control(exc)
    return {
        "id": row.id,
        "baseline_id": row.baseline_id,
        "activity_id": variance.activity_id,
        "observed_day": variance.observed_day,
        "planned_progress": variance.planned_progress,
        "actual_progress": variance.actual_progress,
        "progress_variance": variance.progress_variance,
        "planned_finish_day": variance.planned_finish_day,
        "forecast_finish_day": variance.forecast_finish_day,
        "finish_variance_days": variance.finish_variance_days,
        "verified": row.verified,
    }


@router.post(
    "/projects/{project_id}/schedule/baselines/{baseline_id}/delays",
    status_code=status.HTTP_201_CREATED,
)
def record_schedule_delay(
    project_id: str,
    baseline_id: str,
    payload: DelayEventRequest,
    session: Session = Depends(get_session),
) -> dict:
    try:
        row = ScheduleControlService(session).add_delay(
            project_id=project_id,
            baseline_id=baseline_id,
            **payload.model_dump(),
        )
    except ScheduleControlError as exc:
        _raise_schedule_control(exc)
    return {
        "id": row.id,
        "baseline_id": row.baseline_id,
        "activity_id": row.activity_id,
        "root_cause": row.root_cause,
        "impact_days": row.impact_days,
        "project_impact_days": row.project_impact_days,
        "verified": row.verified,
    }


def _raise_schedule_control(exc: ValueError) -> NoReturn:
    code = str(exc)
    if code in {
        "PROJECT_NOT_FOUND",
        "SCHEDULE_BASELINE_NOT_FOUND",
        "SCHEDULE_ACTIVITY_NOT_FOUND",
    }:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=code) from exc
    if code.startswith("RESOURCE_CAPACITY_INFEASIBLE"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=code) from exc
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=code) from exc


def _baseline_response(result: BaselineCreationResult) -> dict:
    return {
        **_baseline_row_response(result.baseline),
        "reused": result.reused,
        "initial_overloads": [
            {
                "code": item.code,
                "kind": item.kind.value,
                "start_day": item.start_day,
                "finish_day": item.finish_day,
                "demand": item.demand,
                "capacity": item.capacity,
                "activity_ids": list(item.activity_ids),
            }
            for item in result.leveling.initial_overloads
        ],
        "remaining_overloads": len(result.leveling.remaining_overloads),
        "shifted_activity_ids": list(result.leveling.shifted_activity_ids),
        "building_element_link_count": result.link_count,
    }


def _baseline_row_response(row: ScheduleBaselineORM) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "name": row.name,
        "version": row.version,
        "priority": row.priority,
        "duration_days": row.duration_days,
        "status": row.status,
        "analysis_version": row.analysis_version,
        "input_checksum": row.input_checksum,
        "activities": row.activities_json,
        "resource_capacities": row.capacities_json,
        "assumptions": row.assumptions_json,
    }
