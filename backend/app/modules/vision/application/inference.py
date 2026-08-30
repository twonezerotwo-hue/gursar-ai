from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pdfplumber
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.documents.domain.ports import DocumentStorage
from app.modules.documents.infrastructure.models import DocumentVersionORM, SheetORM
from app.modules.jobs.infrastructure.models import AnalysisJobORM
from app.modules.project_graph.domain.models import ElementType, ReviewStatus, SourceType
from app.modules.project_graph.infrastructure.models import BuildingElementORM
from app.modules.training.application.service import TrainingPipelineService
from app.modules.vision.application.fusion import DetectionCandidate, DetectionFusionEngine
from app.modules.vision.application.providers import VisionInferenceProvider
from app.modules.vision.application.service import VISION_CLASSES, VisionDatasetService
from app.modules.vision.infrastructure.models import DetectionConflictORM, VisionAnnotationORM

VISION_ANALYSIS_VERSION = "VISION_INTELLIGENCE_V1"
FUSION_ANALYSIS_VERSION = "VECTOR_VISION_FUSION_V1"


class RasterVisionPipeline:
    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        provider: VisionInferenceProvider,
        *,
        render_dpi: int,
        max_pages: int,
        max_pixels: int,
        low_confidence_threshold: float,
        fusion_iou_threshold: float,
        fusion_distance_threshold: float,
    ) -> None:
        self._session = session
        self._storage = storage
        self._provider = provider
        self._render_dpi = render_dpi
        self._max_pages = max_pages
        self._max_pixels = max_pixels
        self._low_confidence_threshold = low_confidence_threshold
        self._fusion = DetectionFusionEngine(
            iou_threshold=fusion_iou_threshold,
            distance_threshold=fusion_distance_threshold,
        )

    def execute(self, job: AnalysisJobORM) -> dict[str, Any]:
        if not job.document_version_id:
            raise ValueError("Document version required")
        version = self._session.get(DocumentVersionORM, job.document_version_id)
        if version is None or version.project_id != job.project_id:
            raise ValueError("Document version not found")
        path = self._storage.path_for_read(storage_key=version.storage_key)
        modes = set(job.payload_json.get("analysis_modes", ["RASTER_VISION"]))
        stages: dict[str, float] = {}
        prediction_count = conflict_count = fused_count = page_count = 0
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) > self._max_pages:
                raise ValueError("MAX_VISION_PAGES_PER_JOB_EXCEEDED")
            for page_number, page in enumerate(pdf.pages, start=1):
                self._refresh(job)
                if job.status == "CANCELLED":
                    break
                page_count += 1
                job.current_stage = "RENDERING"
                job.progress = min(90, int((page_number - 1) / max(1, len(pdf.pages)) * 100))
                started = time.monotonic()
                image = page.to_image(resolution=self._render_dpi).original.convert("RGB")
                stages["render_duration"] = (
                    stages.get("render_duration", 0) + time.monotonic() - started
                )
                if image.width * image.height > self._max_pixels:
                    raise ValueError("MAX_IMAGE_PIXELS_EXCEEDED")
                sheet = self._sheet(version.id, page_number)
                page_type = self._page_type(page)
                use_vision = page_type == "RASTER" or (
                    page_type == "HYBRID" and "RASTER_VISION" in modes
                )
                if page_type == "VECTOR" and "RASTER_VISION" not in modes:
                    continue
                job.current_stage = "VISION_INFERENCE"
                started = time.monotonic()
                detections = self._detect(image) if use_vision or "RASTER_VISION" in modes else []
                stages["vision_duration"] = (
                    stages.get("vision_duration", 0) + time.monotonic() - started
                )
                vision_candidates = self._persist_vision_candidates(job, sheet, detections)
                prediction_count += len(vision_candidates)
                structured = self._structured_candidates(sheet.id)
                job.current_stage = "FUSION"
                started = time.monotonic()
                fused, conflicts = self._fusion.fuse(structured, vision_candidates)
                stages["fusion_duration"] = (
                    stages.get("fusion_duration", 0) + time.monotonic() - started
                )
                conflict_count += self._persist_conflicts(job.project_id, sheet.id, conflicts)
                fused_count += self._persist_fused(job, sheet, fused)
                self._session.flush()
        job.current_stage = "PROJECT_GRAPH_WRITE"
        job.progress = 95
        return {
            "analysis_version": VISION_ANALYSIS_VERSION,
            "fusion_version": FUSION_ANALYSIS_VERSION,
            "page_count": page_count,
            "prediction_count": prediction_count,
            "fused_candidate_count": fused_count,
            "conflict_count": conflict_count,
            "timings": {key: round(value, 6) for key, value in stages.items()},
            "quantity_generated": False,
        }

    def _detect(self, image: Image.Image) -> list[dict[str, Any]]:
        return [
            *self._provider.detect_title_block(image),
            *self._provider.detect_view_regions(image),
            *self._provider.detect_elements(image, VISION_CLASSES),
        ]

    def _persist_vision_candidates(
        self, job: AnalysisJobORM, sheet: SheetORM, detections: list[dict[str, Any]]
    ) -> list[DetectionCandidate]:
        service = VisionDatasetService(self._session, self._storage)
        training = TrainingPipelineService(self._session)
        results: list[DetectionCandidate] = []
        for index, detection in enumerate(detections):
            class_name = str(detection["class_name"])
            task = self._task_for_class(class_name)
            existing = self._session.scalar(
                select(VisionAnnotationORM).where(
                    VisionAnnotationORM.document_version_id == job.document_version_id,
                    VisionAnnotationORM.sheet_id == sheet.id,
                    VisionAnnotationORM.source_element_id == f"{job.id}:{index}",
                )
            )
            if existing is None:
                existing = service.create_annotation(
                    project_id=job.project_id,
                    document_version_id=str(job.document_version_id),
                    sheet_id=sheet.id,
                    view_region_id=None,
                    image_derivative_id=None,
                    task_type=task,
                    class_name=class_name,
                    geometry_type=str(detection["geometry_type"]),
                    geometry_normalized=dict(detection["geometry"]),
                    source_type="VISION_MODEL",
                    source_element_id=f"{job.id}:{index}",
                    confidence=float(detection["confidence"]),
                    review_status="REVIEW_REQUIRED",
                    principal=None,
                )
            if existing.confidence < self._low_confidence_threshold:
                training.enqueue_active_learning(
                    project_id=job.project_id,
                    source_reference=f"VISION_ANNOTATION:{existing.id}",
                    prediction={
                        "class_name": existing.class_name,
                        "geometry": existing.geometry_normalized,
                    },
                    confidence=existing.confidence,
                    reason="LOW_CONFIDENCE",
                )
            results.append(
                DetectionCandidate(
                    source_id=existing.id,
                    source_type="VISION",
                    class_name=existing.class_name,
                    geometry=existing.geometry_normalized,
                    confidence=existing.confidence,
                )
            )
        return results

    def _structured_candidates(self, sheet_id: str) -> list[DetectionCandidate]:
        rows = self._session.scalars(
            select(BuildingElementORM).where(BuildingElementORM.source_sheet_id == sheet_id)
        ).all()
        results = []
        for row in rows:
            source_type = (
                row.source_type if row.source_type in {"IFC", "DXF", "VECTOR_PDF"} else "HEURISTIC"
            )
            results.append(
                DetectionCandidate(
                    source_id=row.id,
                    source_type=source_type,
                    class_name=row.element_type,
                    geometry=row.geometry_json,
                    confidence=float(row.confidence),
                )
            )
        return results

    def _persist_conflicts(
        self,
        project_id: str,
        sheet_id: str,
        conflicts: list[tuple[DetectionCandidate, DetectionCandidate]],
    ) -> int:
        training = TrainingPipelineService(self._session)
        for first, second in conflicts:
            row = DetectionConflictORM(
                project_id=project_id,
                sheet_id=sheet_id,
                source_a_type=first.source_type,
                source_a_id=first.source_id,
                source_b_type=second.source_type,
                source_b_id=second.source_id,
                class_a=first.class_name,
                class_b=second.class_name,
                geometry_a_json=first.geometry,
                geometry_b_json=second.geometry,
                confidence=min(first.confidence, second.confidence),
            )
            self._session.add(row)
            self._session.flush()
            training.enqueue_active_learning(
                project_id=project_id,
                source_reference=f"DETECTION_CONFLICT:{row.id}",
                prediction={"structured": first.class_name, "vision": second.class_name},
                confidence=min(first.confidence, second.confidence),
                reason="MODEL_DISAGREEMENT",
            )
        return len(conflicts)

    def _persist_fused(self, job: AnalysisJobORM, sheet: SheetORM, fused: list[Any]) -> int:
        count = 0
        allowed_elements = {item.value for item in ElementType}
        for index, candidate in enumerate(fused):
            if candidate.class_name not in allowed_elements:
                continue
            external_id = f"fusion:{job.id}:{sheet.page_number}:{index}"
            exists = self._session.scalar(
                select(BuildingElementORM).where(
                    BuildingElementORM.source_document_id == job.document_version_id,
                    BuildingElementORM.source_external_id == external_id,
                )
            )
            if exists:
                continue
            self._session.add(
                BuildingElementORM(
                    project_id=job.project_id,
                    discipline=sheet.discipline,
                    element_type=candidate.class_name,
                    geometry_type="BBOX",
                    geometry_json=candidate.geometry,
                    properties_json={
                        "fused_sources": list(candidate.source_types),
                        "source_ids": list(candidate.source_ids),
                        "agreement": candidate.agreement,
                        "conflict": candidate.conflict,
                        "raw_model_confidence": candidate.confidence,
                        "calibrated_confidence": None,
                    },
                    source_type=SourceType.VISION.value,
                    source_document_id=job.document_version_id,
                    source_sheet_id=sheet.id,
                    source_external_id=external_id,
                    source_coordinates={"page": sheet.page_number},
                    extraction_method="VECTOR_VISION_FUSION",
                    extraction_model=self._provider.model_name,
                    extraction_version=FUSION_ANALYSIS_VERSION,
                    confidence=Decimal(str(candidate.confidence)),
                    review_status=ReviewStatus.REVIEW_REQUIRED.value,
                    revision=1,
                )
            )
            count += 1
        return count

    def _sheet(self, document_version_id: str, page_number: int) -> SheetORM:
        row = self._session.scalar(
            select(SheetORM).where(
                SheetORM.document_version_id == document_version_id,
                SheetORM.page_number == page_number,
            )
        )
        if row is None:
            raise ValueError("Sheet not found")
        return row

    @staticmethod
    def _page_type(page: Any) -> str:
        vectors = len(page.lines) + len(page.rects) + len(page.curves)
        images = len(page.images)
        if vectors and images:
            return "HYBRID"
        if vectors:
            return "VECTOR"
        if images:
            return "RASTER"
        return "UNKNOWN"

    @staticmethod
    def _task_for_class(class_name: str) -> str:
        if class_name == "TITLE_BLOCK":
            return "TITLE_BLOCK_DETECTION"
        if class_name.endswith("_REGION"):
            return "VIEW_REGION_DETECTION"
        return "ELEMENT_DETECTION"

    def _refresh(self, job: AnalysisJobORM) -> None:
        self._session.refresh(job)
