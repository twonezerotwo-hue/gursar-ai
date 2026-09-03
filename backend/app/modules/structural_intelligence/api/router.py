from __future__ import annotations

from decimal import Decimal
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.security import require_admin_key
from app.modules.structural_intelligence.application.service import (
    StructuralGraphError,
    StructuralGraphService,
)
from app.modules.structural_intelligence.domain.models import (
    StructuralConnectionType,
    StructuralGraphAnalysis,
    StructuralGraphInput,
    StructuralLoad,
    StructuralLoadType,
    StructuralSupport,
)

router = APIRouter(
    prefix="/api/v1/intelligence",
    tags=["structural-intelligence"],
    dependencies=[Depends(require_admin_key)],
)


class StructuralSupportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_element_id: str = Field(min_length=1, max_length=36)
    supporting_element_id: str = Field(min_length=1, max_length=36)
    connection_type: StructuralConnectionType = StructuralConnectionType.MONOLITHIC
    load_path_enabled: bool = True


class StructuralLoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    element_id: str = Field(min_length=1, max_length=36)
    load_type: StructuralLoadType
    magnitude_kn: Decimal = Field(ge=0)
    factor: Decimal = Field(default=Decimal("1"), gt=0)
    direction: str = Field(default="GRAVITY", min_length=1, max_length=30)
    source_reference: str | None = Field(default=None, max_length=500)


class StructuralGraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_ids: list[str] = Field(min_length=1, max_length=20_000)
    supports: list[StructuralSupportRequest] = Field(default_factory=list, max_length=50_000)
    loads: list[StructuralLoadRequest] = Field(default_factory=list, max_length=50_000)
    reviewer: str = Field(default="SYSTEM_STRUCTURAL_ADMIN", min_length=1, max_length=200)


@router.post(
    "/projects/{project_id}/structural/load-path",
    status_code=status.HTTP_201_CREATED,
)
def create_load_path(
    project_id: str,
    payload: StructuralGraphRequest,
    session: Session = Depends(get_session),
) -> dict:
    data = StructuralGraphInput(
        project_id=project_id,
        element_ids=tuple(payload.element_ids),
        supports=tuple(StructuralSupport(**item.model_dump()) for item in payload.supports),
        loads=tuple(StructuralLoad(**item.model_dump()) for item in payload.loads),
        reviewer=payload.reviewer,
    )
    try:
        result = StructuralGraphService(session).build_and_analyze(data)
    except StructuralGraphError as exc:
        _raise_structural_error(exc)
    return _response(result)


@router.get("/projects/{project_id}/structural/load-path")
def get_load_path(
    project_id: str,
    session: Session = Depends(get_session),
) -> dict:
    try:
        result = StructuralGraphService(session).latest(project_id)
    except StructuralGraphError as exc:
        _raise_structural_error(exc)
    return _response(result)


def _raise_structural_error(exc: StructuralGraphError) -> NoReturn:
    code = str(exc)
    if code in {"PROJECT_NOT_FOUND", "STRUCTURAL_GRAPH_NOT_FOUND", "ELEMENT_NOT_FOUND"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=code) from exc
    if code == "STRUCTURAL_GRAPH_REVISION_REQUIRED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=code) from exc
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=code) from exc


def _response(result: StructuralGraphAnalysis) -> dict:
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
        "element_ids": list(result.element_ids),
        "relationship_ids": list(result.relationship_ids),
        "load_ids": list(result.load_ids),
    }
