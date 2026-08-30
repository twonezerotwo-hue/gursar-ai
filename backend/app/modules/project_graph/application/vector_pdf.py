from __future__ import annotations

import io
import math
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import pdfplumber
import structlog
from rapidocr import RapidOCR
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit, append_outbox
from app.core.security import ReviewPrincipal
from app.modules.documents.infrastructure.models import DocumentVersionORM, SheetORM
from app.modules.project_graph.domain.models import ElementType, ReviewStatus, SourceType
from app.modules.project_graph.infrastructure.models import (
    BuildingElementORM,
    ExtractedPdfTextORM,
    GeometryArtifactORM,
    MeasurementEvidenceORM,
    MeasurementORM,
    ScaleCalibrationORM,
    SheetMetadataRevisionORM,
    VectorPdfAnalysisORM,
    ViewRegionORM,
)
from app.modules.training.application.service import TrainingPipelineService
from app.modules.vision.infrastructure.models import VisionAnnotationORM

logger = structlog.get_logger(__name__)
VECTOR_PDF_ANALYSIS_VERSION = "VECTOR_PDF_INTELLIGENCE_V1"
PDF_POINT_TO_METER = 0.0254 / 72.0
SCALE_PATTERN = re.compile(
    r"(?:(?:ÖLÇEK|OLCEK|SCALE)\s*[:=]?\s*)?1\s*[:/]\s*(10|20|25|50|100|200)\b",
    re.IGNORECASE,
)
SHEET_NUMBER_PATTERN = re.compile(r"\b(?:M[İI]M|STA|[AMSEP])\s*[- ]?\s*\d{1,3}\b", re.IGNORECASE)
REVISION_PATTERN = re.compile(r"\b(?:REV(?:[İI]ZYON)?\s*[: ]?\s*|R)(\d{1,3})\b", re.IGNORECASE)


class VectorPdfIntelligenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TextItem:
    text: str
    bbox_points: tuple[float, float, float, float]
    bbox_normalized: tuple[float, float, float, float]
    font_size: float | None
    font_name: str | None
    rotation: int
    confidence: float
    source: str


@dataclass(frozen=True, slots=True)
class RegionCandidate:
    bbox: tuple[float, float, float, float]
    region_type: str
    confidence: float
    scale: int | None
    scale_confidence: float
    scale_status: str
    source_text_ids: tuple[str, ...] = ()


class VectorPdfIntelligenceService:
    def __init__(
        self,
        session: Session,
        *,
        max_pages: int = 100,
        max_vector_objects_per_page: int = 50_000,
        max_text_objects: int = 100_000,
        max_view_regions: int = 1000,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._session = session
        self._max_pages = max_pages
        self._max_vector_objects_per_page = max_vector_objects_per_page
        self._max_text_objects = max_text_objects
        self._max_view_regions = max_view_regions
        self._timeout_seconds = timeout_seconds
        self._ocr: RapidOCR | None = None

    def analyze(
        self, *, project_id: str, document_version_id: str, path: Path
    ) -> tuple[VectorPdfAnalysisORM, bool]:
        version = self._require_pdf(project_id, document_version_id)
        existing = self._session.scalar(
            select(VectorPdfAnalysisORM).where(
                VectorPdfAnalysisORM.document_version_id == document_version_id,
                VectorPdfAnalysisORM.analysis_version == VECTOR_PDF_ANALYSIS_VERSION,
            )
        )
        if existing:
            return existing, True
        started = time.monotonic()
        analysis = VectorPdfAnalysisORM(
            project_id=project_id,
            document_version_id=document_version_id,
            analysis_version=VECTOR_PDF_ANALYSIS_VERSION,
            page_count=0,
            vector_page_count=0,
            raster_page_count=0,
            hybrid_page_count=0,
            text_object_count=0,
            vector_object_count=0,
            view_region_count=0,
            measurement_count=0,
            review_required_count=0,
            summary_json={},
        )
        self._session.add(analysis)
        self._session.flush()
        sheets = {
            row.page_number: row
            for row in self._session.scalars(
                select(SheetORM).where(SheetORM.document_version_id == document_version_id)
            ).all()
        }
        page_summaries: list[dict[str, Any]] = []
        page_types: Counter[str] = Counter()
        total_texts = total_vectors = total_regions = total_measurements = total_review = 0
        training = TrainingPipelineService(self._session)
        try:
            with pdfplumber.open(path) as document:
                analysis.page_count = len(document.pages)
                if len(document.pages) > self._max_pages:
                    raise VectorPdfIntelligenceError("VECTOR_PDF_PAGE_LIMIT_EXCEEDED")
                for page_number, page in enumerate(document.pages, start=1):
                    self._check_timeout(started)
                    sheet = sheets.get(page_number)
                    if sheet is None:
                        raise VectorPdfIntelligenceError("Sheet metadata is missing")
                    vector_count = sum(
                        len(page.objects.get(kind, ())) for kind in ("line", "rect", "curve")
                    )
                    if vector_count > self._max_vector_objects_per_page:
                        raise VectorPdfIntelligenceError("VECTOR_PDF_OBJECT_LIMIT_EXCEEDED")
                    native_items = self._native_text(page)
                    ocr_items: list[TextItem] = []
                    if self._native_text_insufficient(native_items):
                        ocr_items = self._ocr_text(page)
                    text_items = self._deduplicate_text([*native_items, *ocr_items])
                    total_texts += len(text_items)
                    if total_texts > self._max_text_objects:
                        raise VectorPdfIntelligenceError("VECTOR_PDF_TEXT_LIMIT_EXCEEDED")
                    page_type, page_confidence = self._classify_page(
                        native_count=len(native_items),
                        ocr_count=len(ocr_items),
                        vector_count=vector_count,
                        image_count=len(page.images),
                    )
                    page_types[page_type] += 1
                    text_rows = self._persist_texts(
                        analysis=analysis,
                        version=version,
                        sheet=sheet,
                        page_number=page_number,
                        items=text_items,
                    )
                    lines = self._text_lines(text_rows)
                    metadata = self._extract_metadata(lines)
                    discipline, discipline_confidence = self._classify_discipline(lines)
                    drawing_type, drawing_confidence = self._classify_drawing_type(lines)
                    title_block = self._detect_title_block(text_rows)
                    regions = self._detect_regions(
                        text_rows=text_rows,
                        lines=lines,
                        title_block=title_block,
                        drawing_type=drawing_type,
                    )
                    if total_regions + len(regions) > self._max_view_regions:
                        raise VectorPdfIntelligenceError("VECTOR_PDF_REGION_LIMIT_EXCEEDED")
                    region_rows = self._persist_regions(sheet, regions)
                    total_regions += len(region_rows)
                    self._update_sheet(
                        sheet=sheet,
                        page=page,
                        page_type=page_type,
                        page_confidence=page_confidence,
                        metadata=metadata,
                        discipline=discipline,
                        discipline_confidence=discipline_confidence,
                        drawing_type=drawing_type,
                        drawing_confidence=drawing_confidence,
                        title_block=title_block,
                    )
                    measurement_count, candidate_count = self._extract_geometry(
                        project_id=project_id,
                        version=version,
                        sheet=sheet,
                        page=page,
                        regions=region_rows,
                        text_rows=text_rows,
                    )
                    total_measurements += measurement_count
                    review_items = candidate_count
                    if discipline == "UNKNOWN":
                        review_items += 1
                        training.enqueue_active_learning(
                            project_id=project_id,
                            source_reference=f"PDF_SHEET:{sheet.id}:DISCIPLINE",
                            prediction={"discipline": discipline, "page": page_number},
                            confidence=discipline_confidence,
                            reason="UNKNOWN_DISCIPLINE",
                        )
                    if drawing_type == "UNKNOWN":
                        review_items += 1
                        training.enqueue_active_learning(
                            project_id=project_id,
                            source_reference=f"PDF_SHEET:{sheet.id}:DRAWING_TYPE",
                            prediction={"drawing_type": drawing_type, "page": page_number},
                            confidence=drawing_confidence,
                            reason="UNKNOWN_DRAWING_TYPE",
                        )
                    if title_block.confidence < 0.7:
                        review_items += 1
                        training.enqueue_active_learning(
                            project_id=project_id,
                            source_reference=f"PDF_SHEET:{sheet.id}:TITLE_BLOCK",
                            prediction={"bbox": title_block.bbox},
                            confidence=title_block.confidence,
                            reason="TITLE_BLOCK_LOW_CONFIDENCE",
                        )
                    if not any(
                        region.scale_status in {"INFERRED", "VERIFIED"} for region in region_rows
                    ):
                        review_items += 1
                        training.enqueue_active_learning(
                            project_id=project_id,
                            source_reference=f"PDF_SHEET:{sheet.id}:SCALE",
                            prediction={"scale": None},
                            confidence=0.0,
                            reason="SCALE_UNKNOWN",
                        )
                    total_review += review_items
                    total_vectors += vector_count
                    page_summaries.append(
                        {
                            "sheet_id": sheet.id,
                            "page": page_number,
                            "page_type": page_type,
                            "sheet_number": sheet.sheet_number,
                            "sheet_name": sheet.sheet_name,
                            "discipline": sheet.discipline,
                            "drawing_type": sheet.drawing_type,
                            "revision": sheet.revision,
                            "view_regions": len(region_rows),
                            "vector_objects": vector_count,
                            "text_objects": len(text_rows),
                            "measurements": measurement_count,
                            "candidates": candidate_count,
                            "ocr_used": bool(ocr_items),
                        }
                    )
        except VectorPdfIntelligenceError:
            raise
        except Exception as exc:
            raise VectorPdfIntelligenceError("INVALID_VECTOR_PDF") from exc
        analysis.vector_page_count = page_types["VECTOR"]
        analysis.raster_page_count = page_types["RASTER"]
        analysis.hybrid_page_count = page_types["HYBRID"]
        analysis.text_object_count = total_texts
        analysis.vector_object_count = total_vectors
        analysis.view_region_count = total_regions
        analysis.measurement_count = total_measurements
        analysis.review_required_count = total_review
        analysis.summary_json = {"pages": page_summaries, "label": "AUTOMATED VECTOR PDF CANDIDATE"}
        append_audit(
            self._session,
            action="VECTOR_PDF_PROJECT_GRAPH_EXTRACTED",
            aggregate_id=project_id,
            detail={
                "document_version_id": document_version_id,
                "analysis_version": VECTOR_PDF_ANALYSIS_VERSION,
                "pages": analysis.page_count,
                "vectors": total_vectors,
                "measurements": total_measurements,
            },
        )
        append_outbox(
            self._session,
            event_type="project.graph.updated.v1",
            aggregate_id=project_id,
            payload={"source": "VECTOR_PDF", "document_version_id": document_version_id},
        )
        self._session.flush()
        logger.info(
            "vector_pdf_analysis_completed",
            project_id=project_id,
            document_version_id=document_version_id,
            analysis_version=VECTOR_PDF_ANALYSIS_VERSION,
            page_count=analysis.page_count,
            vector_object_count=total_vectors,
            text_object_count=total_texts,
            measurement_count=total_measurements,
            duration=round(time.monotonic() - started, 4),
        )
        return analysis, False

    def correct_sheet_metadata(
        self,
        *,
        project_id: str,
        sheet_id: str,
        corrections: dict[str, Any],
        task_type: str,
        reason: str,
        principal: ReviewPrincipal,
    ) -> SheetMetadataRevisionORM:
        sheet = self._session.get(SheetORM, sheet_id)
        if sheet is None:
            raise VectorPdfIntelligenceError("Sheet not found")
        version = self._session.get(DocumentVersionORM, sheet.document_version_id)
        if version is None or version.project_id != project_id:
            raise VectorPdfIntelligenceError("Sheet does not belong to project")
        allowed = {
            "sheet_number",
            "sheet_name",
            "revision",
            "discipline",
            "drawing_type",
            "level_name",
            "title_block_bbox",
            "scale",
        }
        if not corrections or set(corrections) - allowed:
            raise VectorPdfIntelligenceError("Unsupported sheet correction fields")
        previous = self._session.scalar(
            select(SheetMetadataRevisionORM)
            .where(SheetMetadataRevisionORM.sheet_id == sheet_id)
            .order_by(SheetMetadataRevisionORM.revision.desc())
            .limit(1)
        )
        original = self._sheet_snapshot(sheet)
        row = SheetMetadataRevisionORM(
            project_id=project_id,
            document_version_id=version.id,
            sheet_id=sheet.id,
            revision=(previous.revision + 1) if previous else 1,
            previous_revision_id=previous.id if previous else None,
            original_prediction_json=original,
            corrected_metadata_json=corrections,
            task_type=task_type,
            reviewer=principal.reviewer_id,
            reviewer_role=principal.role,
            reason=reason,
            review_status="VERIFIED",
        )
        self._session.add(row)
        for key, value in corrections.items():
            if key in {"title_block_bbox", "scale"}:
                continue
            setattr(sheet, key, value)
        sheet.review_status = "CORRECTED"
        sheet.metadata_json = {
            **sheet.metadata_json,
            "verified_correction": corrections,
            "metadata_revision": row.revision,
        }
        TrainingPipelineService(self._session).mark_reviewed(
            f"PDF_SHEET:{sheet.id}:{self._task_reference(task_type)}"
        )
        append_audit(
            self._session,
            action="PDF_SHEET_METADATA_CORRECTED",
            aggregate_id=project_id,
            actor_type=principal.role,
            actor_id=principal.reviewer_id,
            detail={"sheet_id": sheet.id, "revision": row.revision, "corrections": corrections},
        )
        self._session.flush()
        return row

    def render_page(
        self,
        *,
        project_id: str,
        document_version_id: str,
        path: Path,
        page_number: int,
        resolution: int,
    ) -> bytes:
        self._require_pdf(project_id, document_version_id)
        if page_number < 1:
            raise VectorPdfIntelligenceError("Invalid page number")
        with pdfplumber.open(path) as document:
            if page_number > len(document.pages):
                raise VectorPdfIntelligenceError("Page not found")
            image = document.pages[page_number - 1].to_image(resolution=resolution).original
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue()

    def overlays(
        self, *, project_id: str, document_version_id: str, page_number: int
    ) -> dict[str, Any]:
        version = self._require_pdf(project_id, document_version_id)
        sheet = self._session.scalar(
            select(SheetORM).where(
                SheetORM.document_version_id == version.id, SheetORM.page_number == page_number
            )
        )
        if sheet is None:
            raise VectorPdfIntelligenceError("Sheet not found")
        regions = self._session.scalars(
            select(ViewRegionORM).where(ViewRegionORM.sheet_id == sheet.id)
        ).all()
        elements = self._session.scalars(
            select(BuildingElementORM).where(BuildingElementORM.source_sheet_id == sheet.id)
        ).all()
        measurements = self._session.scalars(
            select(MeasurementORM).where(MeasurementORM.source_sheet_id == sheet.id)
        ).all()
        vision_candidates = self._session.scalars(
            select(VisionAnnotationORM).where(VisionAnnotationORM.sheet_id == sheet.id)
        ).all()
        return {
            "sheet_id": sheet.id,
            "page": page_number,
            "view_regions": [
                {
                    "id": row.id,
                    "type": row.view_type,
                    "bbox": row.bbox_normalized,
                    "scale": row.scale,
                    "scale_calibration_id": row.metadata_json.get("scale_calibration_id"),
                    "status": row.review_status,
                }
                for row in regions
            ],
            "elements": [
                {
                    "id": row.id,
                    "type": row.element_type,
                    "geometry": row.geometry_json,
                    "status": row.review_status,
                }
                for row in elements
            ],
            "measurements": [
                {
                    "id": row.id,
                    "type": row.measurement_type,
                    "geometry": row.geometry_json,
                    "value": row.calculated_value,
                    "unit": row.unit,
                    "status": row.review_status,
                }
                for row in measurements
            ],
            "vision_candidates": [
                {
                    "id": row.id,
                    "class": row.class_name,
                    "geometry": row.geometry_normalized,
                    "confidence": row.confidence,
                    "source": row.source_type,
                    "status": row.review_status,
                    "label": "AI önerisi · İnceleme gerekli",
                }
                for row in vision_candidates
            ],
        }

    def _native_text(self, page: Any) -> list[TextItem]:
        width, height = float(page.width), float(page.height)
        words = page.extract_words(
            x_tolerance=2,
            y_tolerance=2,
            keep_blank_chars=False,
            extra_attrs=["fontname", "size"],
        )
        return [
            TextItem(
                text=str(word["text"]),
                bbox_points=(
                    float(word["x0"]),
                    float(word["top"]),
                    float(word["x1"]),
                    float(word["bottom"]),
                ),
                bbox_normalized=(
                    float(word["x0"]) / width,
                    float(word["top"]) / height,
                    float(word["x1"]) / width,
                    float(word["bottom"]) / height,
                ),
                font_size=float(word.get("size") or 0) or None,
                font_name=str(word.get("fontname") or "") or None,
                rotation=0,
                confidence=1.0,
                source="PDF_TEXT",
            )
            for word in words
            if str(word.get("text", "")).strip()
        ]

    def _ocr_text(self, page: Any) -> list[TextItem]:
        resolution = 144
        image = np.asarray(page.to_image(resolution=resolution).original.convert("RGB"))
        if self._ocr is None:
            self._ocr = RapidOCR()
        result = cast(Any, self._ocr(cv2.cvtColor(image, cv2.COLOR_RGB2BGR)))
        boxes: list[Any] = list(getattr(result, "boxes", None) or [])
        texts: list[Any] = list(getattr(result, "txts", None) or [])
        scores: list[Any] = list(getattr(result, "scores", None) or [])
        height_px, width_px = image.shape[:2]
        point_factor = 72.0 / resolution
        items: list[TextItem] = []
        for box, text, score in zip(boxes, texts, scores, strict=True):
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            items.append(
                TextItem(
                    text=str(text),
                    bbox_points=(
                        x0 * point_factor,
                        y0 * point_factor,
                        x1 * point_factor,
                        y1 * point_factor,
                    ),
                    bbox_normalized=(x0 / width_px, y0 / height_px, x1 / width_px, y1 / height_px),
                    font_size=None,
                    font_name=None,
                    rotation=0,
                    confidence=float(score),
                    source="OCR",
                )
            )
        return items

    @staticmethod
    def _native_text_insufficient(items: list[TextItem]) -> bool:
        return len(items) < 3 or sum(len(item.text) for item in items) < 12

    @staticmethod
    def _deduplicate_text(items: list[TextItem]) -> list[TextItem]:
        selected: list[TextItem] = []
        for item in sorted(items, key=lambda row: row.source != "PDF_TEXT"):
            normalized = VectorPdfIntelligenceService._fold(item.text)
            duplicate = any(
                VectorPdfIntelligenceService._fold(other.text) == normalized
                and VectorPdfIntelligenceService._iou(item.bbox_normalized, other.bbox_normalized)
                > 0.6
                for other in selected
            )
            if not duplicate:
                selected.append(item)
        return selected

    def _persist_texts(
        self,
        *,
        analysis: VectorPdfAnalysisORM,
        version: DocumentVersionORM,
        sheet: SheetORM,
        page_number: int,
        items: list[TextItem],
    ) -> list[ExtractedPdfTextORM]:
        rows = [
            ExtractedPdfTextORM(
                analysis_id=analysis.id,
                document_version_id=version.id,
                sheet_id=sheet.id,
                page_number=page_number,
                text=item.text,
                bbox_points=list(item.bbox_points),
                bbox_normalized=list(item.bbox_normalized),
                font_size=Decimal(str(item.font_size)) if item.font_size else None,
                font_name=item.font_name,
                rotation=item.rotation,
                confidence=Decimal(str(item.confidence)),
                source=item.source,
            )
            for item in items
        ]
        self._session.add_all(rows)
        self._session.flush()
        return rows

    @staticmethod
    def _text_lines(rows: list[ExtractedPdfTextORM]) -> list[dict[str, Any]]:
        buckets: dict[int, list[ExtractedPdfTextORM]] = defaultdict(list)
        for row in rows:
            buckets[round(row.bbox_normalized[1] * 300)].append(row)
        lines = []
        for _, words in sorted(buckets.items()):
            ordered = sorted(words, key=lambda row: row.bbox_normalized[0])
            bbox = VectorPdfIntelligenceService._union_bbox(
                [VectorPdfIntelligenceService._bbox_tuple(row.bbox_normalized) for row in ordered]
            )
            lines.append(
                {
                    "text": " ".join(row.text for row in ordered),
                    "bbox": bbox,
                    "ids": [row.id for row in ordered],
                }
            )
        return lines

    def _extract_metadata(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        joined = "\n".join(line["text"] for line in lines)
        sheet_match = SHEET_NUMBER_PATTERN.search(joined)
        revision_match = REVISION_PATTERN.search(joined)
        scales = self._scale_candidates(lines)
        title_line = next(
            (line for line in lines if self._classify_drawing_type([line])[0] != "UNKNOWN"),
            None,
        )
        return {
            "sheet_number": self._field(
                sheet_match.group(0).replace(" ", "") if sheet_match else None,
                0.96 if sheet_match else 0.0,
                self._line_bbox(lines, sheet_match.group(0)) if sheet_match else None,
            ),
            "sheet_name": self._field(
                title_line["text"] if title_line else None,
                0.85 if title_line else 0.0,
                title_line["bbox"] if title_line else None,
            ),
            "revision": self._field(
                revision_match.group(1) if revision_match else None,
                0.92 if revision_match else 0.0,
                self._line_bbox(lines, revision_match.group(0)) if revision_match else None,
            ),
            "scale": self._field(
                scales[0][0] if len(scales) == 1 else None,
                scales[0][2] if len(scales) == 1 else 0.0,
                scales[0][1] if len(scales) == 1 else None,
            ),
        }

    def _classify_discipline(self, lines: list[dict[str, Any]]) -> tuple[str, float]:
        text = self._fold(" ".join(line["text"] for line in lines))
        groups = {
            "ARCHITECTURAL": ("MIMARI", "ARCHITECTURAL", "KAT PLANI", "KESIT", "GORUNUS"),
            "STRUCTURAL": (
                "STATIK",
                "BETONARME",
                "KALIP PLANI",
                "DONATI",
                "KOLON",
                "KIRIS",
                "PERDE",
            ),
            "MECHANICAL": ("MEKANIK", "HAVALANDIRMA", "SIHHI TESISAT", "ISITMA", "SPRINKLER"),
            "ELECTRICAL": ("ELEKTRIK", "AYDINLATMA", "PRIZ", "KUVVET", "ZAYIF AKIM"),
            "FIRE": ("YANGIN", "FIRE"),
            "LANDSCAPE": ("PEYZAJ", "LANDSCAPE"),
            "GEOTECHNICAL": ("JEOTEKNIK", "ZEMIN ETUD", "GEOTECHNICAL"),
        }
        scores = {key: sum(marker in text for marker in markers) for key, markers in groups.items()}
        best, count = max(scores.items(), key=lambda item: item[1])
        if count == 0:
            return "UNKNOWN", 0.0
        tied = sum(value == count for value in scores.values()) > 1
        return ("UNKNOWN", 0.45) if tied else (best, min(0.99, 0.72 + count * 0.09))

    def _classify_drawing_type(self, lines: list[dict[str, Any]]) -> tuple[str, float]:
        text = self._fold(" ".join(line["text"] for line in lines))
        patterns = (
            ("SECTION", ("KESIT", "SECTION")),
            ("ELEVATION", ("GORUNUS", "ELEVATION")),
            ("DETAIL", ("DETAY", "DETAIL")),
            ("SCHEDULE", ("CIZELGE", "LISTE", "SCHEDULE")),
            ("SCHEMATIC", ("SEMA", "SCHEMATIC")),
            ("PLAN", ("PLAN", "PLANI")),
        )
        for value, markers in patterns:
            if any(marker in text for marker in markers):
                return value, 0.9
        return "UNKNOWN", 0.0

    @dataclass(frozen=True, slots=True)
    class _TitleBlock:
        bbox: tuple[float, float, float, float]
        confidence: float

    def _detect_title_block(self, rows: list[ExtractedPdfTextORM]) -> _TitleBlock:
        candidates = [
            row for row in rows if row.bbox_normalized[0] >= 0.55 and row.bbox_normalized[1] >= 0.55
        ]
        if len(candidates) >= 3:
            bbox = self._union_bbox([self._bbox_tuple(row.bbox_normalized) for row in candidates])
            keywords = sum(
                any(key in self._fold(row.text) for key in ("OLCEK", "REV", "PROJE", "PAFTA"))
                for row in candidates
            )
            return self._TitleBlock(
                bbox=bbox, confidence=min(0.98, 0.65 + 0.04 * len(candidates) + 0.05 * keywords)
            )
        return self._TitleBlock(bbox=(0.65, 0.65, 1.0, 1.0), confidence=0.35)

    def _detect_regions(
        self,
        *,
        text_rows: list[ExtractedPdfTextORM],
        lines: list[dict[str, Any]],
        title_block: _TitleBlock,
        drawing_type: str,
    ) -> list[RegionCandidate]:
        del text_rows
        scales = self._scale_candidates(lines)
        regions: list[RegionCandidate] = []
        if scales:
            for denominator, text_bbox, confidence, text, ids in scales:
                folded = self._fold(text)
                region_type = self._region_type(folded, drawing_type)
                center_x = (text_bbox[0] + text_bbox[2]) / 2
                center_y = (text_bbox[1] + text_bbox[3]) / 2
                if len(scales) == 1:
                    bbox = (0.0, 0.0, 1.0, max(0.55, title_block.bbox[1]))
                elif max(item[1][0] for item in scales) - min(item[1][0] for item in scales) > 0.25:
                    bbox = (
                        (0.0, 0.0, 0.5, title_block.bbox[1])
                        if center_x < 0.5
                        else (0.5, 0.0, 1.0, title_block.bbox[1])
                    )
                else:
                    bbox = (
                        (0.0, 0.0, 1.0, 0.5)
                        if center_y < 0.5
                        else (0.0, 0.5, 1.0, title_block.bbox[1])
                    )
                regions.append(
                    RegionCandidate(
                        bbox, region_type, 0.82, denominator, confidence, "INFERRED", tuple(ids)
                    )
                )
        else:
            regions.append(
                RegionCandidate(
                    (0.0, 0.0, 1.0, title_block.bbox[1]),
                    drawing_type if drawing_type != "UNKNOWN" else "UNKNOWN",
                    0.45,
                    None,
                    0.0,
                    "UNKNOWN",
                )
            )
        regions.append(
            RegionCandidate(
                title_block.bbox, "TITLE_BLOCK", title_block.confidence, None, 0.0, "UNKNOWN"
            )
        )
        return regions

    def _persist_regions(
        self, sheet: SheetORM, regions: list[RegionCandidate]
    ) -> list[ViewRegionORM]:
        rows: list[ViewRegionORM] = []
        for region in regions:
            row = ViewRegionORM(
                sheet_id=sheet.id,
                bbox_normalized=list(region.bbox),
                view_type=region.region_type,
                scale=Decimal(region.scale) if region.scale else None,
                scale_confidence=Decimal(str(region.scale_confidence)),
                confidence=Decimal(str(region.confidence)),
                review_status="REVIEW_REQUIRED",
                scale_status=region.scale_status,
                source_type="VECTOR_PDF",
                metadata_json={"source_text_ids": list(region.source_text_ids)},
            )
            self._session.add(row)
            self._session.flush()
            if region.scale:
                ratio = PDF_POINT_TO_METER * region.scale
                calibration = ScaleCalibrationORM(
                    view_region_id=row.id,
                    method="TITLEBLOCK",
                    known_distance=Decimal(str(ratio)),
                    pixel_distance=Decimal("1"),
                    drawing_units="PDF_POINT",
                    scale_ratio=Decimal(str(ratio)),
                    confidence=Decimal(str(region.scale_confidence)),
                    verified=False,
                )
                self._session.add(calibration)
                self._session.flush()
                row.metadata_json = {**row.metadata_json, "scale_calibration_id": calibration.id}
            rows.append(row)
        return rows

    def _update_sheet(
        self,
        *,
        sheet: SheetORM,
        page: Any,
        page_type: str,
        page_confidence: float,
        metadata: dict[str, Any],
        discipline: str,
        discipline_confidence: float,
        drawing_type: str,
        drawing_confidence: float,
        title_block: _TitleBlock,
    ) -> None:
        sheet.page_type = page_type
        sheet.page_type_confidence = Decimal(str(page_confidence))
        sheet.rotation = int(getattr(page, "rotation", 0) or 0)
        sheet.orientation = "LANDSCAPE" if float(page.width) > float(page.height) else "PORTRAIT"
        sheet.standard_size = self._standard_size(float(page.width), float(page.height))
        sheet.sheet_number = metadata["sheet_number"]["value"]
        sheet.sheet_name = metadata["sheet_name"]["value"]
        sheet.title = sheet.sheet_name
        sheet.revision = metadata["revision"]["value"]
        sheet.discipline = discipline
        sheet.discipline_confidence = Decimal(str(discipline_confidence))
        sheet.discipline_method = "DETERMINISTIC_KEYWORDS"
        sheet.drawing_type = drawing_type
        sheet.drawing_type_confidence = Decimal(str(drawing_confidence))
        sheet.drawing_type_method = "DETERMINISTIC_KEYWORDS"
        sheet.classification_confidence = Decimal(
            str(min(discipline_confidence, drawing_confidence))
        )
        sheet.review_status = "REVIEW_REQUIRED"
        sheet.metadata_json = {
            **metadata,
            "title_block": {"bbox": list(title_block.bbox), "confidence": title_block.confidence},
        }

    def _extract_geometry(
        self,
        *,
        project_id: str,
        version: DocumentVersionORM,
        sheet: SheetORM,
        page: Any,
        regions: list[ViewRegionORM],
        text_rows: list[ExtractedPdfTextORM],
    ) -> tuple[int, int]:
        width, height = float(page.width), float(page.height)
        measurement_count = candidate_count = 0
        room_words = [
            row
            for row in text_rows
            if any(name in self._fold(row.text) for name in ("SALON", "MUTFAK", "ODA", "WC", "HOL"))
        ]
        objects: list[tuple[str, dict[str, Any]]] = []
        objects.extend(("LINE", item) for item in page.lines)
        objects.extend(("RECTANGLE", item) for item in page.rects)
        objects.extend(("CURVE", item) for item in page.curves)
        for index, (geometry_type, item) in enumerate(objects):
            raw, normalized, measures = self._pdf_geometry(geometry_type, item, width, height)
            if not measures:
                continue
            bbox = self._geometry_bbox(normalized)
            region = self._region_for_bbox(regions, bbox)
            artifact = GeometryArtifactORM(
                project_id=project_id,
                document_version_id=version.id,
                sheet_id=sheet.id,
                view_region_id=region.id if region else None,
                source_external_id=f"pdf:p{sheet.page_number}:{geometry_type.lower()}:{index}",
                geometry_type=geometry_type,
                raw_geometry_json=raw,
                normalized_geometry_json=normalized,
                source_type="VECTOR_PDF",
                artifact_stage="RAW_GEOMETRY",
                semantic_class=None,
                confidence=Decimal("0.99"),
                review_status="PREDICTED",
                analysis_version=VECTOR_PDF_ANALYSIS_VERSION,
                metadata_json={"page_number": sheet.page_number, "bbox": list(bbox)},
            )
            self._session.add(artifact)
            if region is None or region.scale_status not in {"INFERRED", "VERIFIED"}:
                continue
            calibration_id = region.metadata_json.get("scale_calibration_id")
            calibration = (
                self._session.get(ScaleCalibrationORM, calibration_id) if calibration_id else None
            )
            if calibration is None:
                continue
            element: BuildingElementORM | None = None
            room_text = next(
                (word for word in room_words if self._contains(bbox, word.bbox_normalized)), None
            )
            if geometry_type == "RECTANGLE" and room_text:
                element = BuildingElementORM(
                    project_id=project_id,
                    discipline=sheet.discipline,
                    element_type=ElementType.ROOM.value,
                    name=room_text.text,
                    geometry_type="POLYGON",
                    geometry_json=normalized,
                    properties_json={
                        "text_id": room_text.id,
                        "semantic_source": "ROOM_TEXT_INSIDE_CLOSED_PATH",
                        "semantic_confidence": 0.78,
                        "geometry_confidence": 0.99,
                    },
                    source_type=SourceType.VECTOR_PDF.value,
                    source_document_id=version.id,
                    source_sheet_id=sheet.id,
                    source_view_region_id=region.id,
                    source_external_id=f"pdf:p{sheet.page_number}:rect:{index}",
                    source_coordinates={"page": sheet.page_number, "bbox_normalized": list(bbox)},
                    extraction_method="VECTOR_PDF_ROOM_CANDIDATE",
                    extraction_model="pdfplumber",
                    extraction_version=VECTOR_PDF_ANALYSIS_VERSION,
                    confidence=Decimal("0.78"),
                    review_status=ReviewStatus.REVIEW_REQUIRED.value,
                    revision=1,
                )
                self._session.add(element)
                self._session.flush()
                artifact.artifact_stage = "SEMANTIC_CANDIDATE"
                artifact.semantic_class = "ROOM"
                artifact.review_status = ReviewStatus.REVIEW_REQUIRED.value
                candidate_count += 1
                TrainingPipelineService(self._session).enqueue_active_learning(
                    project_id=project_id,
                    source_reference=f"VECTOR_PDF:{version.id}:{sheet.page_number}:{index}",
                    prediction={"element_type": "ROOM", "text": room_text.text, "bbox": bbox},
                    confidence=0.78,
                    reason="SEMANTIC_ELEMENT_LOW_CONFIDENCE",
                )
            for measurement_type, raw_value in measures:
                dimension = 2 if measurement_type == "AREA" else 1
                calculated = raw_value * float(calibration.scale_ratio) ** dimension
                measurement = MeasurementORM(
                    project_id=project_id,
                    element_id=element.id if element else None,
                    measurement_type=measurement_type,
                    measurement_purpose=("ENGINEERING_QUANTITY" if element else "RAW_GEOMETRY"),
                    geometry_json=normalized,
                    raw_value=Decimal(str(round(raw_value, 6))),
                    unit="m2" if measurement_type == "AREA" else "m",
                    scale_calibration_id=calibration.id,
                    calculated_value=Decimal(str(round(calculated, 6))),
                    source_document_id=version.id,
                    source_sheet_id=sheet.id,
                    source_view_region_id=region.id,
                    method="VECTOR_PDF_POLYGON"
                    if measurement_type == "AREA"
                    else "VECTOR_PDF_LINE",
                    algorithm_version=VECTOR_PDF_ANALYSIS_VERSION,
                    confidence=Decimal("0.90"),
                    review_status=ReviewStatus.REVIEW_REQUIRED.value,
                    revision=1,
                )
                self._session.add(measurement)
                self._session.flush()
                self._session.add(
                    MeasurementEvidenceORM(
                        measurement_id=measurement.id,
                        source_coordinates={
                            "document_version_id": version.id,
                            "sheet_id": sheet.id,
                            "page_number": sheet.page_number,
                            "raw_pdf_geometry": raw,
                            "normalized_bbox": list(bbox),
                        },
                        geometry_json={"raw_pdf_geometry": raw, "normalized_geometry": normalized},
                        page_number=sheet.page_number,
                        view_region_id=region.id,
                        scale_json={
                            "scale_calibration_id": calibration.id,
                            "scale_denominator": float(region.scale or 0),
                            "scale_status": region.scale_status,
                            "meters_per_pdf_point": float(calibration.scale_ratio),
                        },
                        thumbnail_reference=self._thumbnail_reference(
                            version.id, sheet.page_number, bbox
                        ),
                        algorithm=measurement.method,
                        model_version=VECTOR_PDF_ANALYSIS_VERSION,
                        review_status=ReviewStatus.REVIEW_REQUIRED.value,
                    )
                )
                measurement_count += 1
        return measurement_count, candidate_count

    def _pdf_geometry(
        self, geometry_type: str, item: dict[str, Any], width: float, height: float
    ) -> tuple[dict[str, Any], dict[str, Any], list[tuple[str, float]]]:
        if geometry_type == "LINE":
            points = [
                (float(item["x0"]), float(item["y0"])),
                (float(item["x1"]), float(item["y1"])),
            ]
            raw = {"geometry_type": "LINE", "points": points}
            normalized = {
                "geometry_type": "LINE",
                "points": [(x / width, 1 - y / height) for x, y in points],
            }
            return raw, normalized, [("LENGTH", math.dist(*points))]
        if geometry_type == "RECTANGLE":
            x0, x1, y0, y1 = map(float, (item["x0"], item["x1"], item["y0"], item["y1"]))
            points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            raw = {"geometry_type": "POLYGON", "points": points, "closed": True}
            normalized = {
                "geometry_type": "POLYGON",
                "points": [(x / width, 1 - y / height) for x, y in points],
                "closed": True,
            }
            return (
                raw,
                normalized,
                [
                    ("LENGTH", 2 * (abs(x1 - x0) + abs(y1 - y0))),
                    ("AREA", abs(x1 - x0) * abs(y1 - y0)),
                ],
            )
        points_data = item.get("pts") or ()
        points = [(float(point[0]), float(point[1])) for point in points_data]
        if len(points) < 2:
            return {}, {}, []
        raw = {"geometry_type": "POLYLINE", "points": points, "closed": False}
        normalized = {
            "geometry_type": "POLYLINE",
            "points": [(x / width, 1 - y / height) for x, y in points],
            "closed": False,
        }
        return (
            raw,
            normalized,
            [("LENGTH", sum(math.dist(a, b) for a, b in pairwise(points)))],
        )

    @staticmethod
    def _thumbnail_reference(
        document_version_id: str,
        page_number: int,
        bbox: tuple[float, float, float, float],
    ) -> str:
        encoded_bbox = ",".join(str(round(value, 5)) for value in bbox)
        return f"pdf://{document_version_id}/pages/{page_number}#bbox={encoded_bbox}"

    @staticmethod
    def _bbox_tuple(values: list[float]) -> tuple[float, float, float, float]:
        return (float(values[0]), float(values[1]), float(values[2]), float(values[3]))

    def _scale_candidates(
        self, lines: list[dict[str, Any]]
    ) -> list[tuple[int, tuple[float, float, float, float], float, str, list[str]]]:
        candidates = []
        for line in lines:
            for match in SCALE_PATTERN.finditer(line["text"]):
                candidates.append(
                    (int(match.group(1)), tuple(line["bbox"]), 0.94, line["text"], line["ids"])
                )
        return candidates

    @staticmethod
    def _classify_page(
        *, native_count: int, ocr_count: int, vector_count: int, image_count: int
    ) -> tuple[str, float]:
        if vector_count > 0 and native_count > 0 and image_count == 0:
            return "VECTOR", 0.97
        if vector_count > 0 and (image_count > 0 or ocr_count > 0):
            return "HYBRID", 0.88
        if vector_count == 0 and (image_count > 0 or ocr_count > 0):
            return "RASTER", 0.9
        return "UNKNOWN", 0.2

    @staticmethod
    def _region_type(text: str, fallback: str) -> str:
        if "DETAIL" in text or "DETAY" in text:
            return "DETAIL"
        if "SECTION" in text or "KESIT" in text:
            return "SECTION"
        if "ELEVATION" in text or "GORUNUS" in text:
            return "ELEVATION"
        if "PLAN" in text:
            return "PLAN"
        return fallback if fallback != "UNKNOWN" else "UNKNOWN"

    @staticmethod
    def _standard_size(width: float, height: float) -> str:
        short, long = sorted((width, height))
        standards = {
            "A4": (595, 842),
            "A3": (842, 1191),
            "A2": (1191, 1684),
            "A1": (1684, 2384),
            "A0": (2384, 3370),
        }
        for name, (expected_short, expected_long) in standards.items():
            if (
                abs(short - expected_short) / expected_short < 0.04
                and abs(long - expected_long) / expected_long < 0.04
            ):
                return name
        return "CUSTOM"

    @staticmethod
    def _geometry_bbox(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
        points = geometry.get("points", ())
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        return min(xs), min(ys), max(xs), max(ys)

    @staticmethod
    def _region_for_bbox(
        regions: list[ViewRegionORM], bbox: tuple[float, float, float, float]
    ) -> ViewRegionORM | None:
        center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        return next(
            (
                row
                for row in regions
                if row.view_type != "TITLE_BLOCK"
                and row.bbox_normalized[0] <= center[0] <= row.bbox_normalized[2]
                and row.bbox_normalized[1] <= center[1] <= row.bbox_normalized[3]
            ),
            None,
        )

    @staticmethod
    def _contains(container: tuple[float, float, float, float], inner: list[float]) -> bool:
        center = ((inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2)
        return (
            container[0] <= center[0] <= container[2] and container[1] <= center[1] <= container[3]
        )

    @staticmethod
    def _field(value: Any, confidence: float, source_bbox: Any) -> dict[str, Any]:
        return {"value": value, "confidence": confidence, "source_bbox": source_bbox}

    @staticmethod
    def _line_bbox(lines: list[dict[str, Any]], text: str) -> Any:
        return next((line["bbox"] for line in lines if text in line["text"]), None)

    @staticmethod
    def _union_bbox(
        boxes: Iterable[tuple[float, float, float, float]],
    ) -> tuple[float, float, float, float]:
        values = list(boxes)
        return (
            min(box[0] for box in values),
            min(box[1] for box in values),
            max(box[2] for box in values),
            max(box[3] for box in values),
        )

    @staticmethod
    def _iou(
        left: tuple[float, float, float, float], right: tuple[float, float, float, float]
    ) -> float:
        x0, y0 = max(left[0], right[0]), max(left[1], right[1])
        x1, y1 = min(left[2], right[2]), min(left[3], right[3])
        intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
        right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
        return intersection / max(left_area + right_area - intersection, 1e-12)

    @staticmethod
    def _fold(value: str) -> str:
        return re.sub(
            r"\s+", " ", value.upper().translate(str.maketrans("ÇĞİÖŞÜ", "CGIOSU"))
        ).strip()

    @staticmethod
    def _sheet_snapshot(sheet: SheetORM) -> dict[str, Any]:
        return {
            key: getattr(sheet, key)
            for key in (
                "sheet_number",
                "sheet_name",
                "revision",
                "discipline",
                "drawing_type",
                "level_name",
                "metadata_json",
            )
        }

    @staticmethod
    def _task_reference(task_type: str) -> str:
        return {
            "SHEET_CLASSIFICATION": "DISCIPLINE",
            "TITLE_BLOCK": "TITLE_BLOCK",
            "SCALE": "SCALE",
        }.get(task_type, task_type)

    def _require_pdf(self, project_id: str, document_version_id: str) -> DocumentVersionORM:
        row = self._session.get(DocumentVersionORM, document_version_id)
        if row is None or row.project_id != project_id:
            raise VectorPdfIntelligenceError("PDF document version not found in project")
        if row.media_type != "application/pdf":
            raise VectorPdfIntelligenceError("Document version is not PDF")
        return row

    def _check_timeout(self, started: float) -> None:
        if time.monotonic() - started > self._timeout_seconds:
            raise VectorPdfIntelligenceError("VECTOR_PDF_ANALYSIS_TIMEOUT")
