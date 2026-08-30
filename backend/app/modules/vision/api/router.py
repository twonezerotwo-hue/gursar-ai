from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.security import ReviewPrincipal, require_review_permission
from app.modules.documents.infrastructure.models import DocumentVersionORM
from app.modules.jobs.api.schemas import JobResponse
from app.modules.jobs.application.service import JobWorker
from app.modules.jobs.infrastructure.models import AnalysisJobORM
from app.modules.project_graph.application.dxf import DxfIntelligenceService
from app.modules.project_graph.application.service import ProjectGraphService
from app.modules.project_graph.application.vector_pdf import VectorPdfIntelligenceService
from app.modules.training.api.schemas import DatasetResponse
from app.modules.training.application.service import training_rules
from app.modules.training.infrastructure.models import ActiveLearningItemORM
from app.modules.vision.api.schemas import (
    AnnotationCreateRequest,
    AnnotationResponse,
    AnnotationReviewRequest,
    ArchivePreviewRequest,
    ArchiveResponse,
    BenchmarkRequest,
    ExportRequest,
    ModelRegisterRequest,
    RenderImageRequest,
    ShadowPredictionRequest,
    TrainingImageResponse,
    TrainingRequest,
)
from app.modules.vision.application.benchmark import GoldenVisionBenchmark
from app.modules.vision.application.inference import RasterVisionPipeline
from app.modules.vision.application.providers import (
    HeuristicVisionProvider,
    LocalVisionProvider,
    RemoteVisionProvider,
)
from app.modules.vision.application.service import (
    VisionDatasetService,
    VisionNotFoundError,
    VisionRuleError,
)
from app.modules.vision.infrastructure.models import (
    VisionBenchmarkRunORM,
    VisionModelVersionORM,
    VisionPredictionORM,
)

router = APIRouter(prefix="/api/v1", tags=["vision-dataset-factory"])


def _service(session: Session, request: Request) -> VisionDatasetService:
    return VisionDatasetService(session, request.app.state.document_storage)


def _vision_pipeline(session: Session, request: Request) -> RasterVisionPipeline:
    settings = request.app.state.settings
    if settings.vision_provider == "local":
        provider = LocalVisionProvider(
            model_name=settings.vision_model_name,
            model_version=settings.vision_model_version,
            device=settings.vision_device,
        )
    elif settings.vision_provider == "remote":
        provider = RemoteVisionProvider(
            model_name=settings.vision_model_name,
            model_version=settings.vision_model_version,
            device=settings.vision_device,
        )
    else:
        provider = HeuristicVisionProvider()
    return RasterVisionPipeline(
        session,
        request.app.state.document_storage,
        provider,
        render_dpi=min(settings.vision_render_dpi, settings.max_render_dpi),
        max_pages=settings.max_vision_pages_per_job,
        max_pixels=settings.max_image_pixels,
        low_confidence_threshold=settings.vision_low_confidence_threshold,
        fusion_iou_threshold=settings.vision_fusion_iou_threshold,
        fusion_distance_threshold=settings.vision_coordinate_distance_threshold,
    )


@router.post(
    "/projects/{project_id}/training-images",
    response_model=TrainingImageResponse,
    status_code=status.HTTP_201_CREATED,
)
def render_training_image(
    project_id: str,
    payload: RenderImageRequest,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> TrainingImageResponse:
    try:
        row, reused = _service(session, request).render_training_image(
            project_id=project_id, **payload.model_dump()
        )
    except VisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VisionRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TrainingImageResponse.model_validate(row).model_copy(update={"reused": reused})


@router.post(
    "/projects/{project_id}/training/annotations",
    response_model=AnnotationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_annotation(
    project_id: str,
    payload: AnnotationCreateRequest,
    request: Request,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> AnnotationResponse:
    try:
        row = _service(session, request).create_annotation(
            project_id=project_id, principal=principal, **payload.model_dump()
        )
    except (VisionRuleError, VisionNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AnnotationResponse.model_validate(row)


@router.post(
    "/training/annotations/{annotation_id}/review",
    response_model=AnnotationResponse,
)
def review_annotation(
    annotation_id: str,
    payload: AnnotationReviewRequest,
    request: Request,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> AnnotationResponse:
    try:
        row = _service(session, request).review_annotation(
            annotation_id=annotation_id,
            action=payload.action,
            correction=payload.correction,
            principal=principal,
        )
    except VisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VisionRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AnnotationResponse.model_validate(row)


@router.post("/training/datasets/{dataset_id}/build")
def build_dataset(
    dataset_id: str,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return _service(session, request).build_dataset(dataset_id=dataset_id)
    except (VisionRuleError, VisionNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/training/datasets/{dataset_id}/freeze", response_model=DatasetResponse)
def freeze_dataset(
    dataset_id: str,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> DatasetResponse:
    try:
        row = _service(session, request).freeze_vision_dataset(dataset_id)
    except (VisionRuleError, VisionNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DatasetResponse.model_validate(row)


@router.get("/training/datasets/{dataset_id}/quality")
def dataset_quality(
    dataset_id: str, request: Request, session: Session = Depends(get_session)
) -> dict[str, object]:
    return _service(session, request).quality_report(dataset_id)


@router.post("/training/datasets/{dataset_id}/exports", status_code=status.HTTP_202_ACCEPTED)
def enqueue_export(
    dataset_id: str,
    payload: ExportRequest,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        export, job, reused = _service(session, request).enqueue_export(
            dataset_id=dataset_id,
            project_id=payload.project_id,
            export_format=payload.export_format,
        )
    except VisionRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"export_id": export.id, "job_id": job.id, "status": job.status, "reused": reused}


@router.post("/jobs/{job_id}/run", response_model=JobResponse)
def run_job(
    job_id: str,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> JobResponse:
    target = session.get(AnalysisJobORM, job_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if target.status != "QUEUED":
        return JobResponse.model_validate(target)
    service = _service(session, request)
    pipeline = _vision_pipeline(session, request)

    def vector_handler(job: AnalysisJobORM) -> dict[str, object]:
        version = session.get(DocumentVersionORM, job.document_version_id)
        if version is None:
            raise ValueError("Document not found")
        path = request.app.state.document_storage.path_for_read(storage_key=version.storage_key)
        row, reused = VectorPdfIntelligenceService(session).analyze(
            project_id=job.project_id, document_version_id=version.id, path=path
        )
        return {"analysis_id": row.id, "reused": reused}

    def dxf_handler(job: AnalysisJobORM) -> dict[str, object]:
        version = session.get(DocumentVersionORM, job.document_version_id)
        if version is None:
            raise ValueError("Document not found")
        path = request.app.state.document_storage.path_for_read(storage_key=version.storage_key)
        row, reused = DxfIntelligenceService(session).analyze(
            project_id=job.project_id, document_version_id=version.id, path=path
        )
        return {"analysis_id": row.id, "reused": reused}

    def ifc_handler(job: AnalysisJobORM) -> dict[str, object]:
        version = session.get(DocumentVersionORM, job.document_version_id)
        if version is None:
            raise ValueError("Document not found")
        path = request.app.state.document_storage.path_for_read(storage_key=version.storage_key)
        result = ProjectGraphService(session).analyze_ifc(
            project_id=job.project_id, document_version_id=version.id, path=path
        )
        return {"result": result}

    worker = JobWorker(
        session,
        handlers={
            "DATASET_EXPORT": service.execute_coco_export,
            "RASTER_VISION": pipeline.execute,
            "HYBRID_ANALYSIS": pipeline.execute,
            "VECTOR_PDF": vector_handler,
            "DXF": dxf_handler,
            "IFC": ifc_handler,
        },
        worker_version="gursar-db-worker-v1",
    )
    row = worker.run_one(job_id=job_id)
    if row is None:
        raise HTTPException(status_code=409, detail="Job could not be claimed")
    return JobResponse.model_validate(row)


@router.get("/training/readiness")
def readiness(request: Request, session: Session = Depends(get_session)) -> dict[str, object]:
    return _service(session, request).readiness()


@router.get("/training/vision/active-learning")
def list_active_learning(
    project_id: str | None = None,
    reason: str | None = None,
    item_status: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    query = select(ActiveLearningItemORM)
    if project_id:
        query = query.where(ActiveLearningItemORM.project_id == project_id)
    if reason:
        query = query.where(ActiveLearningItemORM.reason == reason)
    if item_status:
        query = query.where(ActiveLearningItemORM.status == item_status)
    rows = session.scalars(query.order_by(ActiveLearningItemORM.priority.desc()))
    return [
        {
            "id": row.id,
            "project_id": row.project_id,
            "reason": row.reason,
            "priority": row.priority,
            "status": row.status,
            "confidence": row.confidence,
        }
        for row in rows
    ]


@router.post("/models/vision/train")
def request_training(
    payload: TrainingRequest,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return _service(session, request).request_training(**payload.model_dump())
    except VisionRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/models/vision/{model_version_id}/benchmark")
def run_benchmark(
    model_version_id: str,
    payload: BenchmarkRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        row = GoldenVisionBenchmark(session, training_rules()["vision_benchmark_targets"]).evaluate(
            model_version_id=model_version_id,
            dataset_version_id=payload.dataset_version_id,
            predictions=payload.predictions,
            golden_version=payload.golden_version,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "id": row.id,
        "golden_set_version": row.golden_set_version,
        "metrics": row.metrics_json,
        "passed": row.passed,
        "auto_activated": False,
    }


@router.post("/models/vision", status_code=status.HTTP_201_CREATED)
def register_vision_model(
    payload: ModelRegisterRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    row = VisionModelVersionORM(
        model_name=payload.model_name,
        model_family=payload.model_family,
        version=payload.version,
        task=payload.task,
        dataset_version_id=payload.dataset_version_id,
        classes_json=payload.classes,
        training_config_json={},
        metrics_json={},
        status="REGISTERED",
        framework=payload.framework,
        device=payload.device,
        artifact_storage_key=payload.artifact_storage_key,
        artifact_checksum=payload.artifact_checksum,
    )
    session.add(row)
    session.flush()
    return {"id": row.id, "status": row.status, "auto_activated": False}


@router.get("/models/vision")
def list_vision_models(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    rows = session.scalars(
        select(VisionModelVersionORM).order_by(VisionModelVersionORM.created_at.desc())
    )
    return [
        {
            "id": row.id,
            "name": row.model_name,
            "version": row.version,
            "task": row.task,
            "status": row.status,
            "metrics": row.metrics_json,
        }
        for row in rows
    ]


@router.post("/models/vision/{model_version_id}/approve")
def approve_vision_model(
    model_version_id: str,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    row = session.get(VisionModelVersionORM, model_version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Model version not found")
    benchmark = session.scalar(
        select(VisionBenchmarkRunORM)
        .where(
            VisionBenchmarkRunORM.model_version_id == model_version_id,
            VisionBenchmarkRunORM.passed.is_(True),
        )
        .order_by(VisionBenchmarkRunORM.completed_at.desc())
    )
    if benchmark is None:
        raise HTTPException(status_code=409, detail="Passing golden benchmark required")
    row.status = "APPROVED"
    row.approved_by = principal.reviewer_id
    session.flush()
    return {"id": row.id, "status": row.status, "active": False}


@router.post("/projects/{project_id}/vision-analysis", status_code=status.HTTP_201_CREATED)
def create_shadow_prediction(
    project_id: str,
    payload: ShadowPredictionRequest,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        row = _service(session, request).create_shadow_prediction(
            project_id=project_id,
            document_version_id=payload.document_version_id,
            sheet_id=payload.sheet_id,
            model_version_id=payload.model_version_id,
            class_name=payload.class_name,
            confidence=payload.confidence,
            geometry=payload.geometry_normalized,
            structured_class=payload.structured_verified_class,
        )
    except VisionRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": row.id, "status": row.review_status, "takeoff_changed": False}


@router.get("/projects/{project_id}/vision/candidates")
def list_vision_candidates(
    project_id: str,
    review_status: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    query = select(VisionPredictionORM).where(VisionPredictionORM.project_id == project_id)
    if review_status:
        query = query.where(VisionPredictionORM.review_status == review_status)
    rows = session.scalars(query.order_by(VisionPredictionORM.created_at.desc()))
    return [
        {
            "id": row.id,
            "class_name": row.class_name,
            "geometry": row.geometry_normalized,
            "raw_model_confidence": row.raw_model_confidence,
            "calibrated_confidence": row.calibrated_confidence,
            "review_status": row.review_status,
            "source": "VISION",
            "label": "AI önerisi · İnceleme gerekli",
        }
        for row in rows
    ]


@router.post(
    "/projects/{project_id}/archive-imports/preview",
    response_model=ArchiveResponse,
    status_code=status.HTTP_201_CREATED,
)
def archive_preview(
    project_id: str,
    payload: ArchivePreviewRequest,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> ArchiveResponse:
    try:
        row = _service(session, request).create_archive_preview(
            project_id=project_id,
            manifest=payload.manifest,
            document_version_ids=payload.document_version_ids,
        )
    except VisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ArchiveResponse.model_validate(row)


@router.post("/archive-imports/{archive_id}/approve", response_model=ArchiveResponse)
def approve_archive(
    archive_id: str,
    request: Request,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> ArchiveResponse:
    try:
        row = _service(session, request).approve_archive(archive_id, principal)
    except VisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VisionRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ArchiveResponse.model_validate(row)
