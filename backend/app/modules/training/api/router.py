from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.security import ReviewPrincipal, require_review_permission
from app.modules.training.api.schemas import (
    ActiveLearningRequest,
    ActiveLearningResponse,
    AnonymizedDerivativeResponse,
    AnonymizeRequest,
    DatasetCreateRequest,
    DatasetMembershipRequest,
    DatasetMembershipResponse,
    DatasetResponse,
    ManifestRequest,
    ManifestResponse,
    TrainingSampleRequest,
    TrainingSampleResponse,
)
from app.modules.training.application.service import (
    TrainingNotFoundError,
    TrainingPipelineService,
    TrainingRuleError,
)

router = APIRouter(prefix="/api/v1", tags=["training-pipeline"])


@router.put("/projects/{project_id}/training-manifest", response_model=ManifestResponse)
def upsert_manifest(
    project_id: str,
    payload: ManifestRequest,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> ManifestResponse:
    try:
        row = TrainingPipelineService(session).upsert_manifest(
            project_id=project_id,
            values=payload.model_dump(),
            principal=principal,
        )
    except TrainingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ManifestResponse.model_validate(row)


@router.post(
    "/projects/{project_id}/training/anonymize/{document_version_id}",
    response_model=AnonymizedDerivativeResponse,
    status_code=status.HTTP_201_CREATED,
)
def anonymize(
    project_id: str,
    document_version_id: str,
    payload: AnonymizeRequest,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> AnonymizedDerivativeResponse:
    try:
        row = TrainingPipelineService(session).anonymize(
            project_id=project_id,
            document_version_id=document_version_id,
            metadata=payload.metadata,
            principal=principal,
        )
    except TrainingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AnonymizedDerivativeResponse.model_validate(row)


@router.post(
    "/projects/{project_id}/training-samples",
    response_model=TrainingSampleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sample(
    project_id: str,
    payload: TrainingSampleRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> TrainingSampleResponse:
    try:
        row = TrainingPipelineService(session).create_sample(
            project_id=project_id,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            task_type=payload.task_type,
            annotation=payload.annotation_json,
        )
    except TrainingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TrainingSampleResponse.model_validate(row)


@router.post(
    "/active-learning",
    response_model=ActiveLearningResponse,
    status_code=status.HTTP_201_CREATED,
)
def enqueue_active_learning(
    payload: ActiveLearningRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> ActiveLearningResponse:
    row = TrainingPipelineService(session).enqueue_active_learning(**payload.model_dump())
    return ActiveLearningResponse.model_validate(row)


@router.post("/datasets", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
def create_dataset(
    payload: DatasetCreateRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> DatasetResponse:
    row = TrainingPipelineService(session).create_dataset(**payload.model_dump())
    return DatasetResponse.model_validate(row)


@router.post(
    "/datasets/{dataset_id}/samples",
    response_model=DatasetMembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_dataset_sample(
    dataset_id: str,
    payload: DatasetMembershipRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> DatasetMembershipResponse:
    try:
        row = TrainingPipelineService(session).add_sample(
            dataset_id=dataset_id, sample_id=payload.sample_id
        )
    except TrainingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingRuleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DatasetMembershipResponse.model_validate(row)


@router.post("/datasets/{dataset_id}/freeze", response_model=DatasetResponse)
def freeze_dataset(
    dataset_id: str,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> DatasetResponse:
    try:
        row = TrainingPipelineService(session).freeze_dataset(dataset_id)
    except TrainingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingRuleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DatasetResponse.model_validate(row)
