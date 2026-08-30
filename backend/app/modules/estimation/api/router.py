from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.persistence import append_audit, append_outbox, new_id
from app.core.security import require_admin_key
from app.modules.estimation.api.schemas import BuildingEstimateRequest, CompletedProjectRequest
from app.modules.estimation.application.calibration import CalibrationEngine
from app.modules.estimation.application.service import (
    EstimationService,
    json_safe,
    serialize_result,
)
from app.modules.estimation.domain.models import (
    BuildingInput,
    CompletedProjectRecord,
    FootprintGeometry,
    ProjectAvailability,
    WallSystem,
)
from app.modules.estimation.infrastructure.registry import EstimationRegistry
from app.modules.estimation.infrastructure.repository import EstimationRepository

router = APIRouter(prefix="/api/v1/estimation", tags=["estimation"])
QUANTITY_ALIASES = {
    "concrete_m3": "concrete",
    "reinforcement_kg": "reinforcement",
    "steel_kg": "reinforcement",
    "formwork_m2": "formwork",
    "masonry_m3": "masonry",
    "facade_m2": "facade",
    "plaster_m2": "plaster",
    "paint_m2": "paint",
}


@router.post("/estimate")
def estimate(payload: BuildingEstimateRequest, session: Session = Depends(get_session)) -> dict:
    service = EstimationService(EstimationRepository(session), EstimationRegistry())
    result = service.estimate(
        _building_input(payload),
        project_quantities=_normalize_quantities(payload.project_quantities),
        conversation_id=payload.conversation_id,
    )
    return serialize_result(result)


@router.post(
    "/completed-projects",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_key)],
)
def add_completed_project(
    payload: CompletedProjectRequest,
    session: Session = Depends(get_session),
) -> dict:
    repository = EstimationRepository(session)
    values = payload.model_dump()
    values["actual_quantities"] = _normalize_quantities(values["actual_quantities"])
    values["estimated_quantities"] = _normalize_quantities(values["estimated_quantities"])
    record = repository.add_completed_project(CompletedProjectRecord(id=new_id(), **values))
    append_audit(
        session,
        action="COMPLETED_PROJECT_INGESTED",
        aggregate_id=record.id,
        detail={
            "project_id": record.project_id,
            "verified": record.verified,
            "calibration_eligible": record.verified,
        },
        actor_type="ADMIN",
    )
    append_outbox(
        session,
        event_type="completed_project.ingested.v1",
        aggregate_id=record.id,
        payload={"project_id": record.project_id, "verified": record.verified},
    )
    return {
        "id": record.id,
        "project_id": record.project_id,
        "verified": record.verified,
        "calibration_eligible": record.verified,
    }


@router.post(
    "/calibrations/propose",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_key)],
)
def propose_calibration(session: Session = Depends(get_session)) -> dict:
    repository = EstimationRepository(session)
    calibration = CalibrationEngine(repository, EstimationRegistry()).propose()
    append_audit(
        session,
        action="ESTIMATION_CALIBRATION_PROPOSED",
        aggregate_id=calibration.id,
        detail={"versions": calibration.estimation_model_version},
        actor_type="ADMIN",
    )
    return json_safe(asdict(calibration))


@router.post(
    "/calibrations/{calibration_id}/activate",
    dependencies=[Depends(require_admin_key)],
)
def activate_calibration(
    calibration_id: str,
    session: Session = Depends(get_session),
) -> dict:
    repository = EstimationRepository(session)
    calibration = repository.get_calibration(calibration_id)
    if calibration is None:
        raise HTTPException(status_code=404, detail="Calibration version not found")
    if calibration.status != "PROPOSED":
        raise HTTPException(status_code=409, detail="Only a proposed calibration can be activated")
    activated_at = datetime.now(UTC)
    repository.activate_calibration(calibration_id, activated_at)
    append_audit(
        session,
        action="ESTIMATION_CALIBRATION_ACTIVATED",
        aggregate_id=calibration_id,
        detail={"activated_at": activated_at.isoformat()},
        actor_type="ADMIN",
    )
    return {"id": calibration_id, "status": "ACTIVE", "activated_at": activated_at}


@router.get(
    "/metrics",
    dependencies=[Depends(require_admin_key)],
)
def calibration_metrics(session: Session = Depends(get_session)) -> dict:
    return CalibrationEngine(EstimationRepository(session), EstimationRegistry()).metrics()


def _building_input(payload: BuildingEstimateRequest) -> BuildingInput:
    data = payload.model_dump(exclude={"project_quantities", "conversation_id"})
    data["footprint_geometry"] = FootprintGeometry(**data["footprint_geometry"])
    data["wall_system"] = WallSystem(**data["wall_system"])
    data["project_availability"] = ProjectAvailability(**data["project_availability"])
    return BuildingInput(**data)


def _normalize_quantities[QuantityValue](
    quantities: dict[str, QuantityValue],
) -> dict[str, QuantityValue]:
    return {QUANTITY_ALIASES.get(code, code): value for code, value in quantities.items()}
