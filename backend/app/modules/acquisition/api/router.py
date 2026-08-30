from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.security import ReviewPrincipal, require_review_permission
from app.modules.acquisition.api.schemas import (
    BootstrapRequest,
    ClassMappingRequest,
    DiscoveryRequest,
    DownloadRequest,
    IngestRequest,
    LicenseReviewRequest,
    RealDataJobRequest,
    SyntheticGenerateRequest,
)
from app.modules.acquisition.application.real_data import (
    Ifc2DGroundTruthRenderer,
    ResPlanAnnotationImporter,
    UpstreamLicenseLineageService,
    load_resplan_from_storage,
)
from app.modules.acquisition.application.real_drawing_v3 import (
    IFC_HORIZONTAL_SLICE_VERSION,
    IfcHorizontalSliceRendererV2,
    real_data_status_summary,
)
from app.modules.acquisition.application.service import (
    ACQUISITION_VERSION,
    ARCH_CLASSES,
    MAPPING_VERSION,
    AcquisitionCoordinator,
    AcquisitionRuleError,
    AutonomousAcquisitionOrchestrator,
    DatasetDownloadManager,
    DatasetReadinessEngine,
    ExternalProjectPackageBuilder,
    GitHubDiscoveryProvider,
    HuggingFaceDiscoveryProvider,
    IfcArchiveImporter,
    StaticSeedSourceProvider,
    SyntheticProjectFactory,
    acquisition_status_summary,
    load_acquisition_rules,
    manual_license_review,
)
from app.modules.acquisition.infrastructure.models import (
    DiscoveredDatasetSourceORM,
    DownloadManifestORM,
    ExternalClassMappingORM,
    LicenseAssessmentORM,
)
from app.modules.documents.infrastructure.models import DocumentVersionORM
from app.modules.jobs.application.service import DatabaseJobQueueProvider, JobWorker
from app.modules.jobs.infrastructure.models import AnalysisJobORM
from app.modules.projects.infrastructure.models import ProjectORM

router = APIRouter(prefix="/api/v1/training", tags=["autonomous-data-acquisition"])


def _system_project(session: Session) -> ProjectORM:
    project = session.scalar(
        select(ProjectORM).where(ProjectORM.name == "GURSAR Dataset Acquisition")
    )
    if project is None:
        project = ProjectORM(name="GURSAR Dataset Acquisition", locale="tr", status="ACTIVE")
        session.add(project)
        session.flush()
    return project


def _providers(payload: DiscoveryRequest) -> list:
    rules = load_acquisition_rules()
    providers: list = [StaticSeedSourceProvider(rules["seed_sources"])]
    if payload.use_remote_providers:
        providers.extend([GitHubDiscoveryProvider(), HuggingFaceDiscoveryProvider()])
    return providers


def _bootstrap_providers(payload: DiscoveryRequest) -> list:
    rules = load_acquisition_rules()
    providers: list = [
        StaticSeedSourceProvider(
            list(rules["seed_sources"])
            + list(rules["bootstrap_sources"])
            + list(rules.get("real_data_sources", []))
        )
    ]
    if payload.use_remote_providers:
        providers.extend([GitHubDiscoveryProvider(), HuggingFaceDiscoveryProvider()])
    return providers


@router.post("/acquisition/discover", status_code=status.HTTP_202_ACCEPTED)
def discover(
    payload: DiscoveryRequest,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    rules = load_acquisition_rules()
    project = _system_project(session)
    queries = payload.queries or list(rules["search_terms"])
    operation_digest = hashlib.sha256(json.dumps(queries, sort_keys=True).encode()).hexdigest()[:16]
    row, reused = DatabaseJobQueueProvider(session).enqueue(
        job_type="AUTONOMOUS_DATA_ACQUISITION",
        project_id=project.id,
        document_version_id=None,
        operation_version=f"{ACQUISITION_VERSION}:{operation_digest}:{int(payload.dry_run)}",
        payload={
            "queries": queries,
            "dry_run": payload.dry_run,
            "use_remote_providers": payload.use_remote_providers,
        },
    )
    row.max_retries = request.app.state.settings.analysis_job_max_retries
    return {"job_id": row.id, "status": row.status, "reused": reused}


@router.post("/acquisition/bootstrap", status_code=status.HTTP_202_ACCEPTED)
def bootstrap_acquisition(
    payload: BootstrapRequest,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if payload.mode not in {"DISCOVERY_ONLY", "SAFE_ACQUIRE", "FULL_BOOTSTRAP"}:
        raise HTTPException(status_code=422, detail="Invalid acquisition mode")
    project = _system_project(session)
    row, reused = DatabaseJobQueueProvider(session).enqueue(
        job_type="AUTONOMOUS_DATA_ACQUISITION",
        project_id=project.id,
        document_version_id=None,
        operation_version=f"AUTONOMOUS_BOOTSTRAP_V1:{payload.mode}",
        payload={"mode": payload.mode, "use_remote_providers": payload.use_remote_providers},
    )
    row.max_retries = request.app.state.settings.analysis_job_max_retries
    return {"job_id": row.id, "status": row.status, "reused": reused, "mode": payload.mode}


@router.post("/acquisition/real-data/jobs", status_code=status.HTTP_202_ACCEPTED)
def create_real_data_job(
    payload: RealDataJobRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    allowed = {"RESPLAN_IMPORT", "BIM_DATASET_IMPORT", "IFC_2D_RENDER", "REAL_DATA_READINESS"}
    if payload.job_type not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported real-data job type")
    if payload.job_type != "REAL_DATA_READINESS" and not payload.source_id:
        raise HTTPException(status_code=422, detail="source_id is required")
    if payload.job_type in {"BIM_DATASET_IMPORT", "IFC_2D_RENDER"} and not payload.package_id:
        raise HTTPException(status_code=422, detail="package_id is required")
    if payload.job_type == "IFC_2D_RENDER" and not payload.document_version_id:
        raise HTTPException(status_code=422, detail="document_version_id is required")
    if payload.job_type == "RESPLAN_IMPORT" and not payload.storage_key:
        raise HTTPException(status_code=422, detail="storage_key is required")
    project = _system_project(session)
    operation = hashlib.sha256(
        json.dumps(payload.model_dump(), sort_keys=True).encode()
    ).hexdigest()[:20]
    row, reused = DatabaseJobQueueProvider(session).enqueue(
        job_type=payload.job_type,
        project_id=project.id,
        document_version_id=payload.document_version_id,
        operation_version=f"REAL_DATA_EXPANSION_V2:{operation}",
        payload=payload.model_dump(),
    )
    return {"job_id": row.id, "status": row.status, "reused": reused}


@router.post("/acquisition/jobs/{job_id}/run")
def run_acquisition_job(
    job_id: str,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    target = session.get(AnalysisJobORM, job_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Job not found")
    rules = load_acquisition_rules()

    def acquisition_handler(job: AnalysisJobORM) -> dict[str, object]:
        payload = job.payload_json
        if "mode" in payload:
            discovery_request = DiscoveryRequest(
                use_remote_providers=bool(payload.get("use_remote_providers", False))
            )
            return AutonomousAcquisitionOrchestrator(
                session,
                request.app.state.document_storage,
                storage_root=request.app.state.settings.document_storage_root,
                providers=_bootstrap_providers(discovery_request),
            ).execute(mode=str(payload["mode"]), job=job)
        discovery_request = DiscoveryRequest.model_validate(payload)
        return AcquisitionCoordinator(session, _providers(discovery_request)).execute(
            queries=list(payload["queries"]),
            max_sources=int(rules["max_sources_per_run"]),
            dry_run=bool(payload.get("dry_run", False)),
            job_id=job.id,
        )

    def synthetic_handler(job: AnalysisJobORM) -> dict[str, object]:
        payload = job.payload_json
        return SyntheticProjectFactory(session, request.app.state.document_storage).generate(
            count=int(payload["count"]),
            seed=int(payload["seed"]),
            building_type=str(payload["building_type"]),
            floors_min=int(payload["floors_min"]),
            floors_max=int(payload["floors_max"]),
        )

    def real_data_handler(job: AnalysisJobORM) -> dict[str, object]:
        payload = job.payload_json
        if job.job_type == "REAL_DATA_READINESS":
            return DatasetReadinessEngine(session, rules["readiness"]).evaluate()
        source_id = str(payload["source_id"])
        if job.job_type == "RESPLAN_IMPORT":
            records = load_resplan_from_storage(
                request.app.state.document_storage, str(payload["storage_key"])
            )
            return ResPlanAnnotationImporter(
                session, request.app.state.document_storage
            ).import_records(
                source_id=source_id,
                records=records,
                limit=int(payload["limit"]),
            )
        if job.job_type == "BIM_DATASET_IMPORT":
            return IfcArchiveImporter(session, request.app.state.document_storage).ingest(
                source_id=source_id,
                package_id=str(payload["package_id"]),
                limit=int(payload["limit"]),
                offset=int(payload.get("offset", 0)),
            )
        version = session.get(DocumentVersionORM, str(payload["document_version_id"]))
        if version is None:
            raise AcquisitionRuleError("DOCUMENT_VERSION_NOT_FOUND")
        path = request.app.state.document_storage.path_for_read(storage_key=version.storage_key)
        real_rules = rules["real_data"]
        renderer_class = (
            IfcHorizontalSliceRendererV2
            if payload.get("renderer_version") == IFC_HORIZONTAL_SLICE_VERSION
            else Ifc2DGroundTruthRenderer
        )
        return renderer_class(
            session,
            request.app.state.document_storage,
            render_size=int(real_rules["ifc_plan_render_scale"]),
            margin_ratio=float(real_rules["ifc_plan_margin"]),
            line_width=int(real_rules["ifc_plan_line_width"]),
            slice_height=float(real_rules["ifc_plan_slice_height"]),
            projection_mode=str(real_rules["ifc_plan_projection_mode"]),
        ).render(
            source_id=source_id,
            package_id=str(payload["package_id"]),
            document_version_id=version.id,
            path=path,
            style=str(payload.get("render_style", "clean_cad")),
        )

    worker = JobWorker(
        session,
        handlers={
            "AUTONOMOUS_DATA_ACQUISITION": acquisition_handler,
            "SYNTHETIC_PROJECT_GENERATION": synthetic_handler,
            "RESPLAN_IMPORT": real_data_handler,
            "BIM_DATASET_IMPORT": real_data_handler,
            "IFC_2D_RENDER": real_data_handler,
            "REAL_DATA_READINESS": real_data_handler,
        },
        worker_version="gursar-acquisition-worker-v1",
    )
    row = worker.run_one(job_id=job_id)
    if row is None:
        raise HTTPException(status_code=409, detail="Job could not be claimed")
    return {
        "job_id": row.id,
        "status": row.status,
        "progress": row.progress,
        "result": row.result_json,
        "error": row.error_code,
    }


@router.get("/sources/discovered")
def list_sources(
    source_status: str | None = None,
    license_status: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    query = select(DiscoveredDatasetSourceORM)
    if source_status:
        query = query.where(DiscoveredDatasetSourceORM.status == source_status)
    if license_status:
        query = query.where(DiscoveredDatasetSourceORM.license_status == license_status)
    rows = session.scalars(query.order_by(DiscoveredDatasetSourceORM.discovered_at.desc()))
    return [_source(row) for row in rows]


@router.get("/sources/{source_id}")
def get_source(source_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    row = session.get(DiscoveredDatasetSourceORM, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    assessments = list(
        session.scalars(
            select(LicenseAssessmentORM)
            .where(LicenseAssessmentORM.source_id == source_id)
            .order_by(LicenseAssessmentORM.checked_at.desc())
        )
    )
    return {
        **_source(row),
        "license_history": [
            {
                "rights_status": item.rights_status,
                "license_name": item.license_name,
                "training_allowed": item.training_use_allowed,
                "checked_at": item.checked_at,
            }
            for item in assessments
        ],
        "upstream_lineage": (
            {
                "effective_license": lineage.effective_license,
                "training_allowed": lineage.training_allowed,
                "lineage_verified": lineage.lineage_verified,
                "manual_review_required": lineage.manual_review_required,
                "conflict_code": lineage.conflict_code,
                "upstream_url": lineage.upstream_url,
            }
            if (lineage := UpstreamLicenseLineageService(session).latest(source_id))
            else None
        ),
    }


@router.post("/sources/{source_id}/license-review")
def license_review(
    source_id: str,
    payload: LicenseReviewRequest,
    principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        row = manual_license_review(
            session, source_id=source_id, reviewer=principal.reviewer_id, **payload.model_dump()
        )
    except AcquisitionRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "source_id": source_id,
        "rights_status": row.rights_status,
        "training_allowed": row.training_use_allowed,
    }


@router.post("/sources/{source_id}/approve")
def approve_source(
    source_id: str,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    row = session.get(DiscoveredDatasetSourceORM, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if row.license_status not in {"OPEN_LICENSED", "LICENSED_BY_GURSAR", "INTERNAL_PROJECT"}:
        raise HTTPException(status_code=409, detail="Training-compatible license required")
    lineage = UpstreamLicenseLineageService(session).latest(source_id)
    if lineage is not None and not lineage.training_allowed:
        raise HTTPException(status_code=409, detail="Verified upstream license lineage required")
    row.status = "APPROVED"
    return {"id": row.id, "status": row.status}


@router.post("/sources/{source_id}/reject")
def reject_source(
    source_id: str,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    row = session.get(DiscoveredDatasetSourceORM, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    row.status = "REJECTED"
    return {"id": row.id, "status": row.status}


@router.post("/sources/{source_id}/download")
def download_source(
    source_id: str,
    payload: DownloadRequest,
    request: Request,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    settings = request.app.state.settings
    try:
        row, result = DatasetDownloadManager(
            session,
            request.app.state.document_storage,
            max_single_file_bytes=settings.acquisition_max_single_file_mb * 1024 * 1024,
            max_archive_files=settings.acquisition_max_archive_file_count,
            max_total_bytes=settings.acquisition_max_total_download_bytes,
            allowed_domains=set(settings.acquisition_allowed_domains),
            timeout=settings.acquisition_download_timeout_seconds,
            max_storage_bytes=int(settings.acquisition_max_storage_gb * 1024**3),
        ).download(source_id, dry_run=payload.dry_run)
    except AcquisitionRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"manifest_id": row.id if row else None, **result}


@router.post("/sources/{source_id}/ingest")
def ingest_source(
    source_id: str,
    payload: IngestRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    manifest = session.get(DownloadManifestORM, payload.manifest_id)
    if manifest is None or manifest.source_id != source_id:
        raise HTTPException(status_code=404, detail="Manifest not found")
    return ExternalProjectPackageBuilder(session).build(
        payload.manifest_id, dry_run=payload.dry_run
    )


@router.post("/sources/{source_id}/class-mappings", status_code=status.HTTP_201_CREATED)
def create_class_mapping(
    source_id: str,
    payload: ClassMappingRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if session.get(DiscoveredDatasetSourceORM, source_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if payload.gursar_class not in ARCH_CLASSES:
        raise HTTPException(status_code=422, detail="Unsupported GURSAR class")
    existing = session.scalar(
        select(ExternalClassMappingORM).where(
            ExternalClassMappingORM.source_id == source_id,
            ExternalClassMappingORM.external_class == payload.external_class,
            ExternalClassMappingORM.mapping_version == MAPPING_VERSION,
        )
    )
    if existing:
        return {"id": existing.id, "reused": True, "verified": existing.verified}
    row = ExternalClassMappingORM(
        source_id=source_id,
        external_class=payload.external_class,
        gursar_class=payload.gursar_class,
        metadata_json=payload.metadata,
        mapping_confidence=payload.confidence,
        mapping_method="MANUAL",
        verified=payload.verified,
        mapping_version=MAPPING_VERSION,
    )
    session.add(row)
    session.flush()
    return {"id": row.id, "reused": False, "verified": row.verified}


@router.get("/vision/readiness")
def readiness(session: Session = Depends(get_session)) -> dict[str, object]:
    return DatasetReadinessEngine(session, load_acquisition_rules()["readiness"]).evaluate()


@router.get("/real-data/status")
def real_data_status(session: Session = Depends(get_session)) -> dict[str, object]:
    rules = load_acquisition_rules()
    return {
        **real_data_status_summary(session),
        "readiness": DatasetReadinessEngine(session, rules["readiness"]).evaluate(),
    }


@router.get("/acquisition/status")
def status_summary(session: Session = Depends(get_session)) -> dict[str, object]:
    return {
        **acquisition_status_summary(session),
        "readiness": DatasetReadinessEngine(
            session, load_acquisition_rules()["readiness"]
        ).evaluate(),
    }


@router.post("/synthetic/projects/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_synthetic(
    payload: SyntheticGenerateRequest,
    _principal: ReviewPrincipal = Depends(require_review_permission),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = _system_project(session)
    minimum = int(payload.floors.get("min", 1))
    maximum = int(payload.floors.get("max", 8))
    if minimum < 1 or maximum < minimum:
        raise HTTPException(status_code=422, detail="Invalid floor range")
    row, reused = DatabaseJobQueueProvider(session).enqueue(
        job_type="SYNTHETIC_PROJECT_GENERATION",
        project_id=project.id,
        document_version_id=None,
        operation_version=f"GURSAR_SYNTHETIC_ARCH_V1:{payload.seed}:{payload.count}:{minimum}:{maximum}:{payload.building_type}",
        payload={
            "count": payload.count,
            "seed": payload.seed,
            "building_type": payload.building_type,
            "floors_min": minimum,
            "floors_max": maximum,
        },
    )
    return {"job_id": row.id, "status": row.status, "reused": reused}


def _source(row: DiscoveredDatasetSourceORM) -> dict[str, object]:
    return {
        "id": row.id,
        "provider": row.provider,
        "name": row.name,
        "source_url": row.source_url,
        "formats": row.detected_formats_json,
        "license_status": row.license_status,
        "quality_score": row.quality_score,
        "relevance_score": row.relevance_score,
        "status": row.status,
    }
