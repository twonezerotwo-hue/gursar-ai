from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.security import ReviewPrincipal, require_review_permission
from app.modules.documents.infrastructure.models import DocumentVersionORM
from app.modules.jobs.api.schemas import JobCreateRequest, JobResponse
from app.modules.jobs.application.service import DatabaseJobQueueProvider, JobWorker
from app.modules.jobs.infrastructure.models import AnalysisJobORM
from app.modules.projects.infrastructure.models import ProjectORM

router = APIRouter(prefix="/api/v1/jobs", tags=["background-jobs"])


class ProjectAnalysisJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_version_id: str
    analysis_modes: list[str]


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def enqueue_job(
    payload: JobCreateRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> JobResponse:
    if session.get(ProjectORM, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        row, reused = DatabaseJobQueueProvider(session).enqueue(
            job_type=payload.job_type,
            project_id=payload.project_id,
            document_version_id=payload.document_version_id,
            operation_version=payload.operation_version,
            payload=payload.payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JobResponse.model_validate(row).model_copy(update={"reused": reused})


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, session: Session = Depends(get_session)) -> JobResponse:
    row = session.get(AnalysisJobORM, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse.model_validate(row)


project_analysis_router = APIRouter(prefix="/api/v1", tags=["background-jobs"])


@project_analysis_router.post(
    "/projects/{project_id}/analysis/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_project_analysis_job(
    project_id: str,
    payload: ProjectAnalysisJobRequest,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> JobResponse:
    version = session.get(DocumentVersionORM, payload.document_version_id)
    if version is None or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="Document version not found")
    modes = set(payload.analysis_modes)
    allowed = {"VECTOR_PDF", "RASTER_VISION", "HYBRID_ANALYSIS", "IFC", "DXF"}
    if not modes or not modes.issubset(allowed):
        raise HTTPException(status_code=422, detail="Unsupported analysis mode")
    job_type = (
        "HYBRID_ANALYSIS" if len(modes) > 1 or "HYBRID_ANALYSIS" in modes else next(iter(modes))
    )
    operation_version = (
        f"{request.app.state.settings.vision_model_version}:{'+'.join(sorted(modes))}"
    )
    row, reused = DatabaseJobQueueProvider(session).enqueue(
        job_type=job_type,
        project_id=project_id,
        document_version_id=version.id,
        operation_version=operation_version,
        payload={"analysis_modes": sorted(modes)},
    )
    row.max_retries = request.app.state.settings.analysis_job_max_retries
    return JobResponse.model_validate(row).model_copy(update={"reused": reused})


@project_analysis_router.get("/analysis/jobs/{job_id}", response_model=JobResponse)
def analysis_job_status(job_id: str, session: Session = Depends(get_session)) -> JobResponse:
    return get_job(job_id, session)


@project_analysis_router.post("/analysis/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_analysis_job(
    job_id: str,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> JobResponse:
    try:
        row = JobWorker(session, handlers={}).cancel(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    return JobResponse.model_validate(row)


@project_analysis_router.post("/analysis/jobs/{job_id}/retry", response_model=JobResponse)
def retry_analysis_job(
    job_id: str,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> JobResponse:
    try:
        row = JobWorker(session, handlers={}).retry_failed(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JobResponse.model_validate(row)
