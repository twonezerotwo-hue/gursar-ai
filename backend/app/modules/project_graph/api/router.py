from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.security import ReviewPrincipal, require_review_permission
from app.modules.acquisition.application.real_drawing_v3 import (
    CrossModalAlignmentService,
    RealDrawingV3Error,
)
from app.modules.acquisition.application.service import load_acquisition_rules
from app.modules.acquisition.infrastructure.models import (
    CrossModalAlignmentORM,
    CrossModalAnnotationLineageORM,
    ExternalProjectPackageORM,
)
from app.modules.documents.infrastructure.models import DocumentVersionORM, SheetORM
from app.modules.documents.infrastructure.repository import DocumentRepository
from app.modules.project_graph.api.schemas import (
    AnalyzeProjectRequest,
    BlockMappingRequest,
    BuildingElementResponse,
    CrossModalAlignRequest,
    CrossModalReviewRequest,
    DxfAnalysisResponse,
    DxfUnitRequest,
    DxfUnitResponse,
    EvidenceResponse,
    ExtractedPdfTextResponse,
    LayerMappingRequest,
    ManualMeasurementRequest,
    ManualMeasurementResponse,
    ProjectGraphResponse,
    ReviewDecisionResponse,
    ReviewRequest,
    ScaleCalibrationRequest,
    ScaleCalibrationResponse,
    SemanticMappingResponse,
    SheetMetadataCorrectionRequest,
    SheetMetadataRevisionResponse,
    TakeoffEvidenceResponse,
    TakeoffItemResponse,
    VectorPdfAnalysisResponse,
    ViewRegionRequest,
    ViewRegionResponse,
)
from app.modules.project_graph.application.dxf import (
    DxfIntelligenceError,
    DxfIntelligenceService,
)
from app.modules.project_graph.application.review import (
    ExpertReviewService,
    InvalidReviewError,
    ReviewNotFoundError,
)
from app.modules.project_graph.application.service import (
    InvalidMeasurementError,
    ProjectDocumentVersionError,
    ProjectGraphNotFoundError,
    ProjectGraphService,
)
from app.modules.project_graph.application.vector_pdf import (
    VectorPdfIntelligenceError,
    VectorPdfIntelligenceService,
)
from app.modules.project_graph.domain.models import MeasurementType, ReviewAction, ScaleMethod
from app.modules.project_graph.infrastructure.models import ExtractedPdfTextORM
from app.modules.vision.infrastructure.models import VisionAnnotationORM

router = APIRouter(prefix="/api/v1", tags=["project-intelligence"])


def _cross_modal_service(session: Session) -> CrossModalAlignmentService:
    rules = load_acquisition_rules()["real_data"]
    return CrossModalAlignmentService(
        session,
        min_inliers=int(rules["alignment_min_inliers"]),
        min_inlier_ratio=float(rules["alignment_min_inlier_ratio"]),
        max_reprojection_error=float(rules["alignment_max_reprojection_error"]),
        auto_silver_enabled=bool(rules.get("auto_silver_enabled", False)),
    )


def _vector_pdf_service(session: Session, request: Request) -> VectorPdfIntelligenceService:
    settings = request.app.state.settings
    return VectorPdfIntelligenceService(
        session,
        max_pages=settings.vector_pdf_max_pages,
        max_vector_objects_per_page=settings.vector_pdf_max_objects_per_page,
        max_text_objects=settings.vector_pdf_max_text_objects,
        max_view_regions=settings.vector_pdf_max_view_regions,
        timeout_seconds=settings.vector_pdf_analysis_timeout_seconds,
    )


@router.post(
    "/projects/{project_id}/documents/{document_version_id}/analyze-vector-pdf",
    response_model=VectorPdfAnalysisResponse,
    status_code=status.HTTP_201_CREATED,
)
def analyze_vector_pdf(
    project_id: str,
    document_version_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> VectorPdfAnalysisResponse:
    version = DocumentRepository(session).get_version(document_version_id)
    if version is None or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="PDF belge sürümü bulunamadı.")
    try:
        path = request.app.state.document_storage.path_for_read(storage_key=version.storage_key)
        row, reused = _vector_pdf_service(session, request).analyze(
            project_id=project_id, document_version_id=document_version_id, path=path
        )
    except VectorPdfIntelligenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return VectorPdfAnalysisResponse.model_validate(row).model_copy(update={"reused": reused})


@router.get("/projects/{project_id}/documents/{document_version_id}/pages/{page_number}/render")
def render_pdf_page(
    project_id: str,
    document_version_id: str,
    page_number: int,
    request: Request,
    resolution: int | None = Query(default=None, ge=72, le=300),
    session: Session = Depends(get_session),
) -> Response:
    version = DocumentRepository(session).get_version(document_version_id)
    if version is None or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="PDF belge sürümü bulunamadı.")
    try:
        path = request.app.state.document_storage.path_for_read(storage_key=version.storage_key)
        content = _vector_pdf_service(session, request).render_page(
            project_id=project_id,
            document_version_id=document_version_id,
            path=path,
            page_number=page_number,
            resolution=resolution or request.app.state.settings.pdf_render_resolution,
        )
    except VectorPdfIntelligenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=content, media_type="image/png")


@router.get("/projects/{project_id}/documents/{document_version_id}/pages/{page_number}/overlays")
def pdf_page_overlays(
    project_id: str,
    document_version_id: str,
    page_number: int,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return _vector_pdf_service(session, request).overlays(
            project_id=project_id,
            document_version_id=document_version_id,
            page_number=page_number,
        )
    except VectorPdfIntelligenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/documents/{document_version_id}/vector-pdf/texts",
    response_model=list[ExtractedPdfTextResponse],
)
def vector_pdf_texts(
    project_id: str,
    document_version_id: str,
    page: int | None = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> list[ExtractedPdfTextResponse]:
    version = session.get(DocumentVersionORM, document_version_id)
    if version is None or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="PDF belge sürümü bulunamadı.")
    statement = select(ExtractedPdfTextORM).where(
        ExtractedPdfTextORM.document_version_id == document_version_id
    )
    if page is not None:
        statement = statement.where(ExtractedPdfTextORM.page_number == page)
    rows = session.scalars(statement.order_by(ExtractedPdfTextORM.page_number)).all()
    return [ExtractedPdfTextResponse.model_validate(row) for row in rows]


@router.post(
    "/projects/{project_id}/sheets/{sheet_id}/metadata-corrections",
    response_model=SheetMetadataRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def correct_pdf_sheet_metadata(
    project_id: str,
    sheet_id: str,
    payload: SheetMetadataCorrectionRequest,
    request: Request,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> SheetMetadataRevisionResponse:
    try:
        row = _vector_pdf_service(session, request).correct_sheet_metadata(
            project_id=project_id,
            sheet_id=sheet_id,
            corrections=payload.corrections,
            task_type=payload.task_type,
            reason=payload.reason,
            principal=principal,
        )
    except VectorPdfIntelligenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SheetMetadataRevisionResponse.model_validate(row)


@router.get("/projects/{project_id}/sheets")
def search_project_sheets(
    project_id: str,
    q: str | None = Query(default=None, max_length=120),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    statement = (
        select(SheetORM)
        .join(DocumentVersionORM, DocumentVersionORM.id == SheetORM.document_version_id)
        .where(DocumentVersionORM.project_id == project_id)
    )
    if q:
        value = f"%{q}%"
        statement = statement.where(
            or_(
                SheetORM.sheet_number.ilike(value),
                SheetORM.sheet_name.ilike(value),
                SheetORM.level_name.ilike(value),
                SheetORM.discipline.ilike(value),
            )
        )
    return [
        {
            "id": row.id,
            "page_number": row.page_number,
            "sheet_number": row.sheet_number,
            "sheet_name": row.sheet_name,
            "revision": row.revision,
            "discipline": row.discipline,
            "drawing_type": row.drawing_type,
            "level_name": row.level_name,
            "page_type": row.page_type,
            "review_status": row.review_status,
        }
        for row in session.scalars(statement.order_by(SheetORM.page_number)).all()
    ]


@router.post(
    "/projects/{project_id}/documents/{document_version_id}/analyze-dxf",
    response_model=DxfAnalysisResponse,
    status_code=status.HTTP_201_CREATED,
)
def analyze_dxf(
    project_id: str,
    document_version_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> DxfAnalysisResponse:
    version = DocumentRepository(session).get_version(document_version_id)
    if version is None or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="DXF belge sürümü bulunamadı.")
    try:
        path = request.app.state.document_storage.path_for_read(storage_key=version.storage_key)
        row, reused = DxfIntelligenceService(
            session,
            max_entities=request.app.state.settings.dxf_max_entities,
            timeout_seconds=request.app.state.settings.dxf_analysis_timeout_seconds,
        ).analyze(project_id=project_id, document_version_id=document_version_id, path=path)
    except DxfIntelligenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = DxfAnalysisResponse.model_validate(row)
    return response.model_copy(update={"reused": reused})


@router.post(
    "/projects/{project_id}/dxf/layer-mappings",
    response_model=SemanticMappingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_dxf_layer_mapping(
    project_id: str,
    payload: LayerMappingRequest,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> SemanticMappingResponse:
    try:
        row = DxfIntelligenceService(session).create_layer_mapping(
            project_id=project_id, values=payload.model_dump(), principal=principal
        )
    except (DxfIntelligenceError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SemanticMappingResponse.model_validate(row)


@router.post(
    "/projects/{project_id}/dxf/block-mappings",
    response_model=SemanticMappingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_dxf_block_mapping(
    project_id: str,
    payload: BlockMappingRequest,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> SemanticMappingResponse:
    try:
        row = DxfIntelligenceService(session).create_block_mapping(
            project_id=project_id, values=payload.model_dump(), principal=principal
        )
    except (DxfIntelligenceError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SemanticMappingResponse.model_validate(row)


@router.post(
    "/projects/{project_id}/documents/{document_version_id}/dxf-unit",
    response_model=DxfUnitResponse,
    status_code=status.HTTP_201_CREATED,
)
def set_dxf_unit(
    project_id: str,
    document_version_id: str,
    payload: DxfUnitRequest,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> DxfUnitResponse:
    try:
        row = DxfIntelligenceService(session).set_unit(
            project_id=project_id,
            document_version_id=document_version_id,
            unit=payload.unit,
            reason=payload.reason,
            principal=principal,
        )
    except DxfIntelligenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DxfUnitResponse.model_validate(row)


@router.get("/projects/{project_id}/dxf/layers", response_model=list[SemanticMappingResponse])
def list_dxf_layer_mappings(
    project_id: str, session: Session = Depends(get_session)
) -> list[SemanticMappingResponse]:
    return [
        SemanticMappingResponse.model_validate(row)
        for row in DxfIntelligenceService(session).list_layers(project_id)
    ]


@router.get("/projects/{project_id}/dxf/blocks", response_model=list[SemanticMappingResponse])
def list_dxf_block_mappings(
    project_id: str, session: Session = Depends(get_session)
) -> list[SemanticMappingResponse]:
    return [
        SemanticMappingResponse.model_validate(row)
        for row in DxfIntelligenceService(session).list_blocks(project_id)
    ]


@router.post(
    "/projects/{project_id}/analysis",
    response_model=ProjectGraphResponse,
    status_code=status.HTTP_201_CREATED,
)
def analyze_project(
    project_id: str,
    payload: AnalyzeProjectRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> ProjectGraphResponse:
    version = DocumentRepository(session).get_version(payload.document_version_id)
    if version is None or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="Belge sürümü bu projede bulunamadı.")
    try:
        path = request.app.state.document_storage.path_for_read(storage_key=version.storage_key)
        result = ProjectGraphService(session).analyze_ifc(
            project_id=project_id,
            document_version_id=version.id,
            path=path,
        )
    except (ProjectDocumentVersionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProjectGraphNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Proje bulunamadı.") from exc
    return ProjectGraphResponse.model_validate(result)


@router.get("/projects/{project_id}/graph", response_model=ProjectGraphResponse)
def get_project_graph(
    project_id: str,
    session: Session = Depends(get_session),
) -> ProjectGraphResponse:
    try:
        return ProjectGraphResponse.model_validate(ProjectGraphService(session).graph(project_id))
    except ProjectGraphNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Proje bulunamadı.") from exc


@router.get("/projects/{project_id}/elements", response_model=list[BuildingElementResponse])
def list_project_elements(
    project_id: str,
    session: Session = Depends(get_session),
) -> list[BuildingElementResponse]:
    try:
        rows = ProjectGraphService(session).elements(project_id)
    except ProjectGraphNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Proje bulunamadı.") from exc
    return [BuildingElementResponse.model_validate(row) for row in rows]


@router.get("/projects/{project_id}/takeoff", response_model=list[TakeoffItemResponse])
def list_project_takeoff(
    project_id: str,
    session: Session = Depends(get_session),
) -> list[TakeoffItemResponse]:
    try:
        rows = ProjectGraphService(session).takeoff(project_id)
    except ProjectGraphNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Proje bulunamadı.") from exc
    return [TakeoffItemResponse.model_validate(row) for row in rows]


@router.get("/takeoff/{item_id}/evidence", response_model=TakeoffEvidenceResponse)
def get_takeoff_evidence(
    item_id: str,
    session: Session = Depends(get_session),
) -> TakeoffEvidenceResponse:
    try:
        item, evidence = ProjectGraphService(session).evidence(item_id)
    except ProjectGraphNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Metraj kalemi bulunamadı.") from exc
    return TakeoffEvidenceResponse(
        item=TakeoffItemResponse.model_validate(item),
        evidence=[EvidenceResponse.model_validate(row) for row in evidence],
    )


@router.post(
    "/projects/{project_id}/view-regions",
    response_model=ViewRegionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_view_region(
    project_id: str,
    payload: ViewRegionRequest,
    session: Session = Depends(get_session),
) -> ViewRegionResponse:
    try:
        row = ProjectGraphService(session).create_view_region(
            project_id=project_id,
            sheet_id=payload.sheet_id,
            bbox_normalized=payload.bbox_normalized,
            view_type=payload.view_type,
            level_name=payload.level_name,
        )
    except (ProjectGraphNotFoundError, InvalidMeasurementError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ViewRegionResponse.model_validate(row)


@router.post(
    "/projects/{project_id}/scale-calibrations",
    response_model=ScaleCalibrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_scale_calibration(
    project_id: str,
    payload: ScaleCalibrationRequest,
    session: Session = Depends(get_session),
) -> ScaleCalibrationResponse:
    try:
        row = ProjectGraphService(session).create_calibration(
            project_id=project_id,
            view_region_id=payload.view_region_id,
            method=ScaleMethod(payload.method),
            known_distance=payload.known_distance,
            pixel_distance=payload.pixel_distance,
            drawing_units=payload.drawing_units,
            confidence=payload.confidence,
            verified=payload.verified,
            reviewer=payload.reviewer,
        )
    except (ValueError, ProjectGraphNotFoundError, InvalidMeasurementError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ScaleCalibrationResponse.model_validate(row)


@router.post(
    "/projects/{project_id}/measurements",
    response_model=ManualMeasurementResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_manual_measurement(
    project_id: str,
    payload: ManualMeasurementRequest,
    session: Session = Depends(get_session),
) -> ManualMeasurementResponse:
    try:
        measurement, evidence, takeoff = ProjectGraphService(session).create_manual_measurement(
            project_id=project_id,
            measurement_type=MeasurementType(payload.measurement_type),
            geometry=payload.geometry,
            unit=payload.unit,
            service_code=payload.service_code,
            description=payload.description,
            view_region_id=payload.view_region_id,
            scale_calibration_id=payload.scale_calibration_id,
        )
    except (ValueError, ProjectGraphNotFoundError, InvalidMeasurementError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ManualMeasurementResponse(
        measurement_id=measurement.id,
        evidence_id=evidence.id,
        takeoff_item=TakeoffItemResponse.model_validate(takeoff),
        calculated_value=measurement.calculated_value,
        unit=measurement.unit,
        quantity_source=takeoff.quantity_source,
    )


@router.post(
    "/review/elements/{element_id}",
    response_model=ReviewDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def review_element(
    element_id: str,
    payload: ReviewRequest,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> ReviewDecisionResponse:
    try:
        decision = ExpertReviewService(session).review_element(
            element_id=element_id,
            action=ReviewAction(payload.action),
            principal=principal,
            reason=payload.reason,
            correction=payload.correction,
        )
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Yapı elemanı bulunamadı.") from exc
    except (ValueError, InvalidReviewError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ReviewDecisionResponse.model_validate(decision)


@router.post(
    "/review/measurements/{measurement_id}",
    response_model=ReviewDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def review_measurement(
    measurement_id: str,
    payload: ReviewRequest,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> ReviewDecisionResponse:
    try:
        decision = ExpertReviewService(session).review_measurement(
            measurement_id=measurement_id,
            action=ReviewAction(payload.action),
            principal=principal,
            reason=payload.reason,
            correction=payload.correction,
        )
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Ölçüm bulunamadı.") from exc
    except (ValueError, InvalidReviewError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ReviewDecisionResponse.model_validate(decision)


@router.get("/review/{subject_type}/{subject_id}", response_model=list[ReviewDecisionResponse])
def review_history(
    subject_type: str,
    subject_id: str,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> list[ReviewDecisionResponse]:
    normalized = subject_type.strip().upper()
    if normalized not in {"BUILDING_ELEMENT", "MEASUREMENT"}:
        raise HTTPException(status_code=422, detail="Geçersiz review subject type.")
    rows = ExpertReviewService(session).history(subject_type=normalized, subject_id=subject_id)
    return [ReviewDecisionResponse.model_validate(row) for row in rows]


@router.post("/projects/{project_id}/cross-modal/detect", status_code=status.HTTP_201_CREATED)
def detect_cross_modal_pair(
    project_id: str,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    package = session.scalar(
        select(ExternalProjectPackageORM).where(ExternalProjectPackageORM.project_id == project_id)
    )
    if package is None:
        raise HTTPException(status_code=404, detail="Project package not found")
    try:
        row = _cross_modal_service(session).detect_pair(package_id=package.id)
    except RealDrawingV3Error as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "link_id": row.id,
        "status": row.alignment_status,
        "confidence": row.confidence,
        "ifc_document_version_id": row.ifc_document_version_id,
        "drawing_document_version_id": row.drawing_document_version_id,
    }


@router.post("/projects/{project_id}/cross-modal/align", status_code=status.HTTP_201_CREATED)
def align_cross_modal_pair(
    project_id: str,
    payload: CrossModalAlignRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if len(payload.source_points) != len(payload.target_points):
        raise HTTPException(status_code=422, detail="Anchor counts must match")
    try:
        row = _cross_modal_service(session).align(
            link_id=payload.link_id,
            storey_external_id=payload.storey_external_id,
            view_region_id=payload.view_region_id,
            source_points=payload.source_points,
            target_points=payload.target_points,
            manual_verified=payload.manual_verified,
        )
    except RealDrawingV3Error as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row.project_id != project_id:
        raise HTTPException(status_code=404, detail="Alignment not found for project")
    return {
        "alignment_id": row.id,
        "status": row.status,
        "confidence": row.confidence,
        "transform": row.transform_json,
        "metrics": row.metrics_json,
    }


@router.get("/projects/{project_id}/cross-modal/alignments")
def list_cross_modal_alignments(
    project_id: str,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    rows = session.scalars(
        select(CrossModalAlignmentORM).where(CrossModalAlignmentORM.project_id == project_id)
    ).all()
    return [
        {
            "alignment_id": row.id,
            "link_id": row.link_id,
            "storey_external_id": row.ifc_storey_external_id,
            "view_region_id": row.drawing_view_region_id,
            "status": row.status,
            "confidence": row.confidence,
            "transform": row.transform_json,
            "metrics": row.metrics_json,
        }
        for row in rows
    ]


@router.post("/cross-modal/alignments/{alignment_id}/review")
def review_cross_modal_alignment(
    alignment_id: str,
    payload: CrossModalReviewRequest,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        service = _cross_modal_service(session)
        row = service.review_alignment(
            alignment_id=alignment_id,
            action=payload.action,
            reviewer=principal.reviewer_id,
            reason=payload.reason,
        )
        projected = (
            service.project_annotations(alignment_id=row.id) if row.status == "VERIFIED" else None
        )
    except RealDrawingV3Error as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "alignment_id": row.id,
        "status": row.status,
        "reviewer": row.reviewed_by,
        "projection": projected,
    }


@router.get("/cross-modal/candidates")
def list_cross_modal_candidates(
    project_id: str | None = None,
    review_status: str | None = None,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    statement = select(VisionAnnotationORM, CrossModalAnnotationLineageORM).join(
        CrossModalAnnotationLineageORM,
        CrossModalAnnotationLineageORM.annotation_id == VisionAnnotationORM.id,
    )
    if project_id:
        statement = statement.where(VisionAnnotationORM.project_id == project_id)
    if review_status:
        statement = statement.where(VisionAnnotationORM.review_status == review_status)
    return [
        {
            "annotation_id": annotation.id,
            "project_id": annotation.project_id,
            "class_name": annotation.class_name,
            "geometry": annotation.geometry_normalized,
            "confidence": annotation.confidence,
            "review_status": annotation.review_status,
            "alignment_id": lineage.alignment_id,
            "ifc_global_id": lineage.ifc_global_id,
            "vector_agreement": lineage.vector_agreement,
            "clipped": lineage.clipped,
        }
        for annotation, lineage in session.execute(statement)
    ]
