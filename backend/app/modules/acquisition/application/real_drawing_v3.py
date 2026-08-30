from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from xml.etree import ElementTree as ET

import cv2
import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.unit
import numpy as np
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit
from app.modules.acquisition.application.real_data import (
    Ifc2DGroundTruthRenderer,
    RealDataRuleError,
    UpstreamLicenseLineageService,
    _bbox,
    _ifc_id,
    _valid_polygon,
    canonical_source_tier,
)
from app.modules.acquisition.infrastructure.models import (
    CrossModalAlignmentORM,
    CrossModalAnnotationLineageORM,
    CrossModalProjectLinkORM,
    DiscoveredDatasetSourceORM,
    ExternalProjectPackageORM,
    Ifc2DAnnotationLineageORM,
    Ifc2DRenderORM,
    IfcGeometryCacheORM,
    RealDrawingImportORM,
)
from app.modules.documents.infrastructure.models import DocumentVersionORM, SheetORM
from app.modules.vision.infrastructure.models import (
    DetectionConflictORM,
    VisionAnnotationORM,
)

REAL_DRAWING_IMPORT_VERSION = "REAL_DRAWING_ACQUISITION_V3"
IFC_HORIZONTAL_SLICE_VERSION = "IFC_HORIZONTAL_SLICE_V2"
IFC_GEOMETRY_SETTINGS_VERSION = "IFC_SECTION_GEOMETRY_V2_1"
CROSS_MODAL_ALIGNMENT_VERSION = "CROSS_MODAL_ALIGNMENT_V2"
CROSS_MODAL_PROJECTION_VERSION = "CROSS_MODAL_AUTO_ANNOTATION_V1"
SEMANTIC_CLASSES = {"WALL", "ROOM", "DOOR", "WINDOW", "COLUMN", "STAIR"}


class RealDrawingV3Error(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SvgSemanticCandidate:
    source_id: str
    class_name: str
    geometry_type: str
    geometry: dict[str, Any]
    label: str | None
    confidence: float


class SemanticSvgImporter:
    """Secure SVG geometry importer. Output remains review-only."""

    _class_tokens: ClassVar[dict[str, str]] = {
        "WALL": "WALL",
        "DUVAR": "WALL",
        "ROOM": "ROOM",
        "ODA": "ROOM",
        "MAHAL": "ROOM",
        "DOOR": "DOOR",
        "KAPI": "DOOR",
        "WINDOW": "WINDOW",
        "PENCERE": "WINDOW",
        "COLUMN": "COLUMN",
        "KOLON": "COLUMN",
        "STAIR": "STAIR",
        "MERDIVEN": "STAIR",
    }

    def __init__(self, session: Session) -> None:
        self._session = session

    def parse(self, content: bytes) -> tuple[list[SvgSemanticCandidate], dict[str, Any]]:
        if len(content) > 25 * 1024 * 1024 or b"<!ENTITY" in content.upper():
            raise RealDrawingV3Error("SVG_SECURITY_REJECTED")
        root = ET.fromstring(content)
        viewbox = _numbers(root.attrib.get("viewBox", ""))
        width = _length(root.attrib.get("width")) or (viewbox[2] if len(viewbox) == 4 else 1.0)
        height = _length(root.attrib.get("height")) or (viewbox[3] if len(viewbox) == 4 else 1.0)
        origin_x = viewbox[0] if len(viewbox) == 4 else 0.0
        origin_y = viewbox[1] if len(viewbox) == 4 else 0.0
        output: list[SvgSemanticCandidate] = []
        stats: Counter[str] = Counter()

        def visit(element: ET.Element, inherited: tuple[float, ...], inherited_label: str) -> None:
            matrix = _matrix_multiply(inherited, _parse_transform(element.attrib.get("transform")))
            raw_label = " ".join(
                value
                for value in (
                    inherited_label,
                    element.attrib.get("id", ""),
                    element.attrib.get("class", ""),
                    element.attrib.get("data-class", ""),
                    element.attrib.get("aria-label", ""),
                    element.attrib.get("label", ""),
                )
                if value
            )
            tag = element.tag.rsplit("}", 1)[-1].lower()
            geometry = _svg_geometry(tag, element.attrib)
            class_name = self._semantic_class(raw_label)
            if geometry and class_name:
                points = [_apply_matrix(point, matrix) for point in geometry]
                normalized = [
                    [(point[0] - origin_x) / width, (point[1] - origin_y) / height]
                    for point in points
                ]
                if len(normalized) >= 2 and all(
                    math.isfinite(value) for point in normalized for value in point
                ):
                    normalized = [[round(x, 8), round(y, 8)] for x, y in normalized]
                    bbox = _bbox(normalized)
                    if all(-0.001 <= value <= 1.001 for value in bbox):
                        output.append(
                            SvgSemanticCandidate(
                                source_id=element.attrib.get("id") or f"svg-{len(output)}",
                                class_name=class_name,
                                geometry_type="POLYGON" if len(normalized) >= 3 else "POLYLINE",
                                geometry={"polygon": normalized, "bbox": bbox, "svg_tag": tag},
                                label=raw_label or None,
                                confidence=0.88 if element.attrib.get("data-class") else 0.72,
                            )
                        )
                        stats[class_name] += 1
            for child in element:
                visit(child, matrix, raw_label)

        visit(root, _identity_matrix(), "")
        return output, {
            "width": width,
            "height": height,
            "candidate_count": len(output),
            "classes": dict(stats),
            "importer_version": REAL_DRAWING_IMPORT_VERSION,
        }

    def ingest(
        self,
        *,
        source_id: str,
        package_id: str,
        document_version_id: str,
        content: bytes,
        revision_family: str | None = None,
    ) -> dict[str, Any]:
        source = self._session.get(DiscoveredDatasetSourceORM, source_id)
        package = self._session.get(ExternalProjectPackageORM, package_id)
        version = self._session.get(DocumentVersionORM, document_version_id)
        lineage = UpstreamLicenseLineageService(self._session).latest(source_id)
        if (
            source is None
            or package is None
            or package.project_id is None
            or version is None
            or version.project_id != package.project_id
        ):
            raise RealDrawingV3Error("REAL_DRAWING_INPUT_INVALID")
        if lineage is None or not lineage.training_allowed:
            raise RealDrawingV3Error("UPSTREAM_LICENSE_LINEAGE_BLOCKED")
        existing = self._session.scalar(
            select(RealDrawingImportORM).where(
                RealDrawingImportORM.source_id == source_id,
                RealDrawingImportORM.document_version_id == document_version_id,
            )
        )
        if existing:
            count = self._session.scalar(
                select(func.count())
                .select_from(VisionAnnotationORM)
                .where(
                    VisionAnnotationORM.document_version_id == document_version_id,
                    VisionAnnotationORM.annotation_source == "DETERMINISTIC_VECTOR_CANDIDATE",
                )
            )
            return {
                "reused": True,
                "candidates": int(count or 0),
                "quality_score": existing.quality_score,
            }
        candidates, metadata = self.parse(content)
        sheet = self._session.scalar(
            select(SheetORM).where(
                SheetORM.document_version_id == version.id, SheetORM.page_number == 1
            )
        )
        if sheet is None:
            sheet = SheetORM(
                document_version_id=version.id,
                page_number=1,
                title="Semantic SVG architectural drawing",
                discipline="ARCHITECTURAL",
                drawing_type="PLAN",
                page_type="VECTOR",
                review_status="REVIEW_REQUIRED",
                width_points=metadata["width"],
                height_points=metadata["height"],
            )
            self._session.add(sheet)
            self._session.flush()
        factors = {
            "license_clarity": 20,
            "drawing_resolution": 10,
            "vector_availability": 20,
            "scale_availability": 0,
            "semantic_annotation_availability": 15 if candidates else 0,
            "project_completeness": 10,
            "revision_clarity": 5 if revision_family else 0,
            "paired_ifc_availability": 0,
        }
        # ExternalProjectPackage is intentionally reused; pairing is derived from its files.
        factors["paired_ifc_availability"] = (
            15
            if any(
                str(item.get("path", "")).lower().endswith(".ifc") for item in package.files_json
            )
            else 0
        )
        quality = min(100, sum(factors.values()))
        row = RealDrawingImportORM(
            source_id=source.id,
            project_package_id=package.id,
            project_id=package.project_id,
            document_version_id=version.id,
            source_family_id=source.source_family_id or source.id,
            drawing_format="SVG",
            revision_family=revision_family,
            license_lineage_id=lineage.id,
            vector_available=True,
            annotation_available=bool(candidates),
            paired_ifc_available=bool(factors["paired_ifc_availability"]),
            quality_score=quality,
            quality_factors_json=factors,
            training_eligibility="REVIEW_REQUIRED",
        )
        self._session.add(row)
        for item in candidates:
            self._session.add(
                VisionAnnotationORM(
                    project_id=package.project_id,
                    document_version_id=version.id,
                    sheet_id=sheet.id,
                    task_type=f"{item.class_name}_DETECTION",
                    class_name=item.class_name,
                    geometry_type=item.geometry_type,
                    geometry_normalized={**item.geometry, "source_label": item.label},
                    original_width=round(float(metadata["width"])),
                    original_height=round(float(metadata["height"])),
                    source_type="DETERMINISTIC_VECTOR_CANDIDATE",
                    annotation_source="DETERMINISTIC_VECTOR_CANDIDATE",
                    source_element_id=item.source_id[:36],
                    confidence=item.confidence,
                    review_status="REVIEW_REQUIRED",
                    training_allowed=False,
                    anonymized=True,
                    rights_status=package.rights_status,
                    quality_score=quality,
                )
            )
        self._session.flush()
        return {
            "reused": False,
            "candidates": len(candidates),
            "classes": metadata["classes"],
            "quality_score": quality,
            "source_type": "REAL_DRAWING_SOURCE",
        }

    @classmethod
    def _semantic_class(cls, value: str) -> str | None:
        normalized = _normalize_text(value)
        return next(
            (target for token, target in cls._class_tokens.items() if token in normalized), None
        )


class VectorSemanticCandidateExtractor:
    """Deterministic vector baselines; every result requires review."""

    def __init__(
        self, *, min_wall_thickness: float = 0.04, max_wall_thickness: float = 0.60
    ) -> None:
        self._min = min_wall_thickness
        self._max = max_wall_thickness

    def double_line_walls(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for index, first in enumerate(lines):
            a1, a2 = _line_points(first)
            for second in lines[index + 1 :]:
                b1, b2 = _line_points(second)
                va, vb = np.subtract(a2, a1), np.subtract(b2, b1)
                length_a, length_b = np.linalg.norm(va), np.linalg.norm(vb)
                if min(length_a, length_b) <= 1e-9:
                    continue
                parallel = abs(float(np.dot(va, vb) / (length_a * length_b)))
                distance = _point_line_distance(b1, a1, a2)
                overlap = _line_overlap_ratio(a1, a2, b1, b2)
                if parallel >= 0.995 and self._min <= distance <= self._max and overlap >= 0.70:
                    polygon = [a1, a2, b2, b1]
                    if _valid_polygon(polygon):
                        candidates.append(
                            {
                                "class_name": "WALL",
                                "geometry": {"polygon": polygon, "bbox": _bbox(polygon)},
                                "confidence": round(0.55 + 0.2 * overlap + 0.15 * parallel, 4),
                                "review_status": "REVIEW_REQUIRED",
                                "source_type": "DETERMINISTIC_VECTOR_CANDIDATE",
                                "features": {
                                    "parallelism": parallel,
                                    "distance_consistency": distance,
                                    "line_overlap": overlap,
                                },
                            }
                        )
        return candidates

    @staticmethod
    def rooms(
        closed_polygons: list[dict[str, Any]], texts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for polygon_item in closed_polygons:
            polygon = polygon_item.get("polygon", [])
            if not _valid_polygon(polygon):
                continue
            label = next(
                (text for text in texts if _point_in_polygon(text.get("point", [0, 0]), polygon)),
                None,
            )
            if label:
                result.append(
                    {
                        "class_name": "ROOM",
                        "geometry": {"polygon": polygon, "bbox": _bbox(polygon)},
                        "room_name": label.get("text"),
                        "room_number": label.get("room_number"),
                        "area_text": label.get("area_text"),
                        "confidence": 0.78,
                        "review_status": "REVIEW_REQUIRED",
                        "source_type": "DETERMINISTIC_VECTOR_CANDIDATE",
                    }
                )
        return result


class IfcHorizontalSliceRendererV2(Ifc2DGroundTruthRenderer):
    renderer_version = IFC_HORIZONTAL_SLICE_VERSION

    def render(
        self,
        *,
        source_id: str,
        package_id: str,
        document_version_id: str,
        path: Path,
        style: str = "clean_cad",
    ) -> dict[str, Any]:
        package = self._session.get(ExternalProjectPackageORM, package_id)
        version = self._session.get(DocumentVersionORM, document_version_id)
        lineage = UpstreamLicenseLineageService(self._session).latest(source_id)
        if package is None or package.project_id is None or version is None:
            raise RealDataRuleError("IFC_RENDER_INPUT_INVALID")
        if lineage is None or not lineage.training_allowed:
            raise RealDataRuleError("UPSTREAM_LICENSE_LINEAGE_BLOCKED")
        model = ifcopenshell.open(path)
        unit_scale = float(ifcopenshell.util.unit.calculate_unit_scale(model) or 1.0)
        opening_map = _opening_relationships(model)
        totals: Counter[str] = Counter()
        class_counts: Counter[str] = Counter()
        for storey_index, storey in enumerate(model.by_type("IfcBuildingStorey")):
            level_id = _ifc_id(storey)
            existing = self._session.scalar(
                select(Ifc2DRenderORM).where(
                    Ifc2DRenderORM.document_version_id == version.id,
                    Ifc2DRenderORM.level_external_id == level_id,
                    Ifc2DRenderORM.renderer_version == self.renderer_version,
                    Ifc2DRenderORM.render_style == style,
                )
            )
            if existing:
                totals["levels_reused"] += 1
                totals["annotations"] += existing.valid_geometry_count
                continue
            elevation = float(getattr(storey, "Elevation", 0.0) or 0.0) * unit_scale
            slice_z = elevation + self._slice_height
            features: list[dict[str, Any]] = []
            seen: set[str] = set()
            for ifc_class, class_name in self.supported.items():
                for element in model.by_type(ifc_class):
                    global_id = _ifc_id(element)
                    if global_id in seen:
                        continue
                    container = _storey_container(element)
                    if container is None or _ifc_id(container) != level_id:
                        continue
                    seen.add(global_id)
                    polygons, mode = self._geometry(
                        element=element,
                        version=version,
                        storey_id=level_id,
                        slice_z=slice_z,
                        unit_scale=unit_scale,
                    )
                    if not polygons:
                        totals["invalid_geometry"] += 1
                        if class_name == "ROOM":
                            totals["space_geometry_unavailable"] += 1
                        continue
                    polygon = max(polygons, key=lambda item: abs(_polygon_area(item)))
                    relation = opening_map.get(global_id, {})
                    features.append(
                        {
                            "global_id": global_id,
                            "ifc_class": str(element.is_a()),
                            "class": class_name,
                            "polygon_world_m": polygon,
                            "name": getattr(element, "Name", None),
                            "object_type": getattr(element, "ObjectType", None),
                            "geometry_extraction_mode": mode,
                            "host_wall_global_id": relation.get("host_wall_global_id"),
                            "opening_global_id": relation.get("opening_global_id"),
                            "opening_direction": _opening_direction(element),
                            "slice_height": slice_z,
                        }
                    )
                    totals[mode.lower()] += 1
                    if class_name == "ROOM":
                        totals["ifc_spaces"] += 1
            if not features:
                continue
            count, counts = self._persist_render(
                source_id=source_id,
                package=package,
                source_version=version,
                storey=storey,
                storey_index=storey_index,
                features=features,
                style=style,
            )
            totals["levels_rendered"] += 1
            totals["annotations"] += count
            class_counts.update(counts)
        door_ids = {_ifc_id(row) for row in model.by_type("IfcDoor")}
        window_ids = {_ifc_id(row) for row in model.by_type("IfcWindow")}
        return {
            "renderer_version": self.renderer_version,
            "ifc_storeys": len(model.by_type("IfcBuildingStorey")),
            "levels_rendered": totals["levels_rendered"],
            "levels_reused": totals["levels_reused"],
            "annotations": totals["annotations"],
            "exact_section_annotations": totals["exact_section"],
            "footprint_fallback_annotations": totals["footprint_fallback"],
            "convex_hull_fallback_annotations": totals["convex_hull_fallback"],
            "invalid_geometry": totals["invalid_geometry"],
            "ifc_spaces": totals["ifc_spaces"],
            "space_geometry_unavailable": totals["space_geometry_unavailable"],
            "door_host_relations": sum(1 for item in opening_map if item in door_ids),
            "window_host_relations": sum(1 for item in opening_map if item in window_ids),
            "class_distribution": dict(class_counts),
        }

    def _geometry(
        self,
        *,
        element: Any,
        version: DocumentVersionORM,
        storey_id: str,
        slice_z: float,
        unit_scale: float,
    ) -> tuple[list[list[list[float]]], str]:
        global_id = _ifc_id(element)
        cached = self._session.scalar(
            select(IfcGeometryCacheORM).where(
                IfcGeometryCacheORM.document_hash == version.sha256,
                IfcGeometryCacheORM.ifc_global_id == global_id,
                IfcGeometryCacheORM.storey_external_id == storey_id,
                IfcGeometryCacheORM.geometry_settings_version == IFC_GEOMETRY_SETTINGS_VERSION,
                IfcGeometryCacheORM.slice_height == slice_z,
            )
        )
        if cached:
            return cached.polygons_json, cached.extraction_mode
        polygons = _mesh_horizontal_sections(element, slice_z=slice_z, unit_scale=unit_scale)
        mode = "EXACT_SECTION"
        if not polygons:
            footprint = self._footprint(element, unit_scale=unit_scale)
            polygons = [footprint] if _valid_polygon(footprint) else []
            # The inherited fallback is explicitly a convex hull of projected mesh vertices.
            mode = "CONVEX_HULL_FALLBACK"
        geometry_hash = hashlib.sha256(
            json.dumps(polygons, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._session.add(
            IfcGeometryCacheORM(
                document_hash=version.sha256,
                ifc_global_id=global_id,
                storey_external_id=storey_id,
                geometry_settings_version=IFC_GEOMETRY_SETTINGS_VERSION,
                slice_height=slice_z,
                extraction_mode=mode,
                polygons_json=polygons,
                metadata_json={"ifc_class": element.is_a()},
                source_geometry_hash=geometry_hash,
            )
        )
        self._session.flush()
        return polygons, mode


class CrossModalAlignmentService:
    def __init__(
        self,
        session: Session,
        *,
        min_inliers: int = 3,
        min_inlier_ratio: float = 0.60,
        max_reprojection_error: float = 0.02,
        auto_silver_enabled: bool = False,
    ) -> None:
        self._session = session
        self._min_inliers = min_inliers
        self._min_ratio = min_inlier_ratio
        self._max_error = max_reprojection_error
        self._auto_silver = auto_silver_enabled

    def detect_pair(self, *, package_id: str) -> CrossModalProjectLinkORM:
        package = self._session.get(ExternalProjectPackageORM, package_id)
        if package is None or package.project_id is None:
            raise RealDrawingV3Error("PROJECT_PACKAGE_INVALID")
        versions = list(
            self._session.scalars(
                select(DocumentVersionORM).where(
                    DocumentVersionORM.project_id == package.project_id
                )
            )
        )
        ifc = next((row for row in versions if row.media_type == "application/x-step"), None)
        drawing = next(
            (
                row
                for row in versions
                if row.media_type
                in {"application/pdf", "image/svg+xml", "application/dxf", "image/png"}
            ),
            None,
        )
        if ifc is None or drawing is None:
            raise RealDrawingV3Error("PAIRED_DOCUMENTS_NOT_FOUND")
        existing = self._session.scalar(
            select(CrossModalProjectLinkORM).where(
                CrossModalProjectLinkORM.project_package_id == package.id,
                CrossModalProjectLinkORM.ifc_document_version_id == ifc.id,
                CrossModalProjectLinkORM.drawing_document_version_id == drawing.id,
            )
        )
        if existing:
            return existing
        link = CrossModalProjectLinkORM(
            project_package_id=package.id,
            ifc_document_version_id=ifc.id,
            drawing_document_version_id=drawing.id,
            level_mapping_json={"method": "PACKAGE_AND_DOCUMENT_METADATA"},
            alignment_status="PREDICTED",
            confidence=0.55,
        )
        self._session.add(link)
        self._session.flush()
        return link

    def align(
        self,
        *,
        link_id: str,
        storey_external_id: str,
        view_region_id: str | None,
        source_points: list[list[float]],
        target_points: list[list[float]],
        manual_verified: bool = False,
    ) -> CrossModalAlignmentORM:
        link = self._session.get(CrossModalProjectLinkORM, link_id)
        if link is None or len(source_points) != len(target_points) or len(source_points) < 3:
            raise RealDrawingV3Error("ALIGNMENT_INPUT_INVALID")
        source = np.asarray(source_points, dtype=np.float32)
        target = np.asarray(target_points, dtype=np.float32)
        matrix, mask = cv2.estimateAffinePartial2D(
            source,
            target,
            method=cv2.RANSAC,
            ransacReprojThreshold=self._max_error,
            maxIters=3000,
            confidence=0.99,
            refineIters=20,
        )
        if matrix is None or mask is None:
            raise RealDrawingV3Error("ALIGNMENT_ESTIMATION_FAILED")
        projected = cv2.transform(source.reshape(1, -1, 2), matrix)[0]
        errors = np.linalg.norm(projected - target, axis=1)
        inliers = mask.ravel().astype(bool)
        inlier_count = int(inliers.sum())
        ratio = inlier_count / len(source_points)
        mean_error = float(errors[inliers].mean()) if inlier_count else float("inf")
        median_error = float(np.median(errors[inliers])) if inlier_count else float("inf")
        scale = float(math.hypot(matrix[0, 0], matrix[0, 1]))
        rotation = float(math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])))
        passes = (
            inlier_count >= self._min_inliers
            and ratio >= self._min_ratio
            and mean_error <= self._max_error
        )
        status = "VERIFIED" if manual_verified and passes else "REVIEW_REQUIRED"
        confidence = max(0.0, min(0.99, ratio * max(0.0, 1 - mean_error / self._max_error)))
        existing = self._session.scalar(
            select(CrossModalAlignmentORM).where(
                CrossModalAlignmentORM.link_id == link.id,
                CrossModalAlignmentORM.ifc_storey_external_id == storey_external_id,
                CrossModalAlignmentORM.drawing_view_region_id == view_region_id,
                CrossModalAlignmentORM.alignment_version == CROSS_MODAL_ALIGNMENT_VERSION,
            )
        )
        if existing:
            return existing
        row = CrossModalAlignmentORM(
            link_id=link.id,
            project_id=self._project_id(link),
            ifc_storey_external_id=storey_external_id,
            drawing_view_region_id=view_region_id,
            method="MANUAL_ANCHORS_RANSAC" if manual_verified else "RANSAC_AFFINE_PARTIAL",
            transform_json={
                "matrix": matrix.tolist(),
                "reflection": bool(np.linalg.det(matrix[:, :2]) < 0),
            },
            metrics_json={
                "inlier_count": inlier_count,
                "inlier_ratio": ratio,
                "mean_reprojection_error": mean_error,
                "median_reprojection_error": median_error,
                "scale": scale,
                "rotation_degrees": rotation,
                "passes_thresholds": passes,
            },
            status=status,
            confidence=confidence,
            alignment_version=CROSS_MODAL_ALIGNMENT_VERSION,
        )
        self._session.add(row)
        link.alignment_status = status
        link.confidence = confidence
        self._session.flush()
        return row

    def project_annotations(self, *, alignment_id: str) -> dict[str, Any]:
        alignment = self._session.get(CrossModalAlignmentORM, alignment_id)
        if alignment is None or alignment.status != "VERIFIED":
            raise RealDrawingV3Error("VERIFIED_ALIGNMENT_REQUIRED")
        link = self._session.get(CrossModalProjectLinkORM, alignment.link_id)
        if link is None:
            raise RealDrawingV3Error("CROSS_MODAL_LINK_NOT_FOUND")
        sheet = self._session.scalar(
            select(SheetORM).where(SheetORM.document_version_id == link.drawing_document_version_id)
        )
        if sheet is None:
            raise RealDrawingV3Error("DRAWING_SHEET_NOT_FOUND")
        matrix = np.asarray(alignment.transform_json["matrix"], dtype=np.float64)
        ifc_annotations = list(
            self._session.scalars(
                select(VisionAnnotationORM)
                .join(
                    Ifc2DAnnotationLineageORM,
                    Ifc2DAnnotationLineageORM.annotation_id == VisionAnnotationORM.id,
                )
                .where(
                    Ifc2DAnnotationLineageORM.storey_external_id
                    == alignment.ifc_storey_external_id,
                    Ifc2DAnnotationLineageORM.renderer_version == IFC_HORIZONTAL_SLICE_VERSION,
                )
            )
        )
        created = conflicts = invalid = 0
        for source in ifc_annotations:
            existing = self._session.scalar(
                select(CrossModalAnnotationLineageORM).where(
                    CrossModalAnnotationLineageORM.alignment_id == alignment.id,
                    CrossModalAnnotationLineageORM.ifc_annotation_id == source.id,
                )
            )
            if existing:
                continue
            polygon = source.geometry_normalized.get("polygon", [])
            projected = _transform_polygon(polygon, matrix)
            clipped, was_clipped = _clip_polygon_unit(projected)
            if not _valid_polygon(clipped) or abs(_polygon_area(clipped)) < 1e-8:
                invalid += 1
                continue
            agreement, vector_id = self._vector_agreement(
                sheet_id=sheet.id, class_name=source.class_name, polygon=clipped
            )
            if agreement is not None and agreement < 0.15:
                self._session.add(
                    DetectionConflictORM(
                        project_id=alignment.project_id,
                        sheet_id=sheet.id,
                        source_a_type="IFC_HORIZONTAL_SLICE_V2",
                        source_a_id=source.id,
                        source_b_type="VECTOR_DRAWING",
                        source_b_id=vector_id or "unknown",
                        class_a=source.class_name,
                        class_b=source.class_name,
                        geometry_a_json={"polygon": clipped},
                        geometry_b_json={},
                        confidence=1 - agreement,
                    )
                )
                conflicts += 1
            auto_silver = bool(
                self._auto_silver
                and agreement is not None
                and agreement >= 0.85
                and not was_clipped
            )
            annotation = VisionAnnotationORM(
                project_id=alignment.project_id,
                document_version_id=link.drawing_document_version_id,
                sheet_id=sheet.id,
                view_region_id=alignment.drawing_view_region_id,
                task_type=f"{source.class_name}_DETECTION",
                class_name=source.class_name,
                geometry_type="POLYGON",
                geometry_normalized={
                    "polygon": clipped,
                    "bbox": _bbox(clipped),
                    "projection_version": CROSS_MODAL_PROJECTION_VERSION,
                },
                original_width=round(float(sheet.width_points)),
                original_height=round(float(sheet.height_points)),
                source_type="REAL_DRAWING_SILVER" if auto_silver else "CROSS_MODAL_AUTO_ANNOTATION",
                annotation_source="CROSS_MODAL_AUTO_ANNOTATION",
                source_element_id=source.source_element_id,
                confidence=min(0.99, alignment.confidence + 0.08 * float(agreement or 0)),
                review_status="VERIFIED" if auto_silver else "REVIEW_REQUIRED",
                training_allowed=auto_silver,
                anonymized=True,
                rights_status=source.rights_status,
                quality_score=92 if auto_silver else 75,
            )
            self._session.add(annotation)
            self._session.flush()
            self._session.add(
                CrossModalAnnotationLineageORM(
                    annotation_id=annotation.id,
                    alignment_id=alignment.id,
                    ifc_annotation_id=source.id,
                    ifc_global_id=source.source_element_id or "UNKNOWN",
                    vector_candidate_id=vector_id,
                    vector_agreement=agreement,
                    projection_json={"matrix": matrix.tolist()},
                    clipped=was_clipped,
                    auto_silver_eligible=auto_silver,
                )
            )
            created += 1
        self._session.flush()
        return {"created": created, "conflicts": conflicts, "invalid": invalid}

    def review_alignment(
        self, *, alignment_id: str, action: str, reviewer: str, reason: str | None = None
    ) -> CrossModalAlignmentORM:
        row = self._session.get(CrossModalAlignmentORM, alignment_id)
        if row is None:
            raise RealDrawingV3Error("ALIGNMENT_NOT_FOUND")
        if action not in {"ACCEPT", "REJECT"}:
            raise RealDrawingV3Error("ALIGNMENT_REVIEW_ACTION_INVALID")
        row.status = "VERIFIED" if action == "ACCEPT" else "REJECTED"
        row.reviewed_by = reviewer
        row.reviewed_at = datetime.now(UTC)
        row.review_reason = reason
        link = self._session.get(CrossModalProjectLinkORM, row.link_id)
        if link:
            link.alignment_status = row.status
        append_audit(
            self._session,
            action=f"CROSS_MODAL_ALIGNMENT_{action}",
            aggregate_id=row.id,
            actor_id=reviewer,
            detail={"reason": reason, "metrics": row.metrics_json},
        )
        self._session.flush()
        return row

    def _project_id(self, link: CrossModalProjectLinkORM) -> str:
        package = self._session.get(ExternalProjectPackageORM, link.project_package_id)
        if package is None or package.project_id is None:
            raise RealDrawingV3Error("PROJECT_PACKAGE_INVALID")
        return package.project_id

    def _vector_agreement(
        self, *, sheet_id: str, class_name: str, polygon: list[list[float]]
    ) -> tuple[float | None, str | None]:
        rows = list(
            self._session.scalars(
                select(VisionAnnotationORM).where(
                    VisionAnnotationORM.sheet_id == sheet_id,
                    VisionAnnotationORM.class_name == class_name,
                    VisionAnnotationORM.source_type == "DETERMINISTIC_VECTOR_CANDIDATE",
                )
            )
        )
        if not rows:
            return None, None
        target = _bbox(polygon)
        best = max(rows, key=lambda row: _bbox_iou(target, _annotation_bbox(row)))
        return _bbox_iou(target, _annotation_bbox(best)), best.id


def compare_renderer_versions(session: Session, *, document_version_id: str) -> dict[str, Any]:
    rows = list(
        session.execute(
            select(VisionAnnotationORM, Ifc2DAnnotationLineageORM)
            .join(
                Ifc2DAnnotationLineageORM,
                Ifc2DAnnotationLineageORM.annotation_id == VisionAnnotationORM.id,
            )
            .where(Ifc2DAnnotationLineageORM.source_document_version_id == document_version_id)
        )
    )
    grouped: defaultdict[str, dict[str, VisionAnnotationORM]] = defaultdict(dict)
    for annotation, lineage in rows:
        grouped[lineage.ifc_global_id][lineage.renderer_version] = annotation
    comparisons: list[dict[str, Any]] = []
    for global_id, versions in grouped.items():
        if "IFC_2D_GROUND_TRUTH_V1" not in versions or IFC_HORIZONTAL_SLICE_VERSION not in versions:
            continue
        first = _annotation_bbox(versions["IFC_2D_GROUND_TRUTH_V1"])
        second = _annotation_bbox(versions[IFC_HORIZONTAL_SLICE_VERSION])
        area_a = _bbox_area(first)
        area_b = _bbox_area(second)
        center_a = ((first[0] + first[2]) / 2, (first[1] + first[3]) / 2)
        center_b = ((second[0] + second[2]) / 2, (second[1] + second[3]) / 2)
        comparisons.append(
            {
                "ifc_global_id": global_id,
                "iou": _bbox_iou(first, second),
                "area_difference": abs(area_a - area_b),
                "centroid_distance": math.dist(center_a, center_b),
            }
        )
    return {
        "compared": len(comparisons),
        "mean_iou": sum(row["iou"] for row in comparisons) / max(1, len(comparisons)),
        "mean_area_difference": sum(row["area_difference"] for row in comparisons)
        / max(1, len(comparisons)),
        "mean_centroid_distance": sum(row["centroid_distance"] for row in comparisons)
        / max(1, len(comparisons)),
    }


def real_data_status_summary(session: Session) -> dict[str, Any]:
    """Deterministic V3 operational counters; no label promotion is performed here."""
    imports = list(session.scalars(select(RealDrawingImportORM)))
    links = list(session.scalars(select(CrossModalProjectLinkORM)))
    alignments = list(session.scalars(select(CrossModalAlignmentORM)))
    lineage_rows = list(session.scalars(select(CrossModalAnnotationLineageORM)))
    lineage_ids = {row.annotation_id for row in lineage_rows}
    cross_annotations = (
        list(
            session.scalars(
                select(VisionAnnotationORM).where(VisionAnnotationORM.id.in_(lineage_ids))
            )
        )
        if lineage_ids
        else []
    )
    annotations = list(session.scalars(select(VisionAnnotationORM)))
    tiers = Counter(canonical_source_tier(row.source_type) for row in annotations)
    return {
        "real_drawing_projects": len({row.project_id for row in imports}),
        "real_drawing_source_families": len({row.source_family_id for row in imports}),
        "paired_ifc_drawing_projects": len({row.project_package_id for row in links}),
        "alignment_attempts": len(alignments),
        "verified_alignments": sum(row.status == "VERIFIED" for row in alignments),
        "rejected_alignments": sum(row.status == "REJECTED" for row in alignments),
        "cross_modal_candidates": len(cross_annotations),
        "reviewed_candidates": sum(
            row.review_status in {"VERIFIED", "CORRECTED", "REJECTED", "SUPERSEDED"}
            for row in cross_annotations
        ),
        "source_distribution": {
            name: tiers.get(name, 0)
            for name in (
                "GURSAR_GOLD",
                "REAL_DRAWING_SILVER",
                "BIM_DERIVED_SILVER",
                "EXTERNAL_OBJECT_SILVER",
                "SYNTHETIC",
                "WEAK",
            )
        },
    }


def _identity_matrix() -> tuple[float, ...]:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _matrix_multiply(first: tuple[float, ...], second: tuple[float, ...]) -> tuple[float, ...]:
    a, b, c, d, e, f = first
    g, h, i, j, k, last = second
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * last + e,
        b * k + d * last + f,
    )


def _parse_transform(value: str | None) -> tuple[float, ...]:
    result = _identity_matrix()
    for name, raw in re.findall(r"([A-Za-z]+)\s*\(([^)]*)\)", value or ""):
        numbers = _numbers(raw)
        command = name.lower()
        matrix = _identity_matrix()
        if command == "matrix" and len(numbers) == 6:
            matrix = tuple(numbers)
        elif command == "translate" and numbers:
            matrix = (1, 0, 0, 1, numbers[0], numbers[1] if len(numbers) > 1 else 0)
        elif command == "scale" and numbers:
            matrix = (numbers[0], 0, 0, numbers[1] if len(numbers) > 1 else numbers[0], 0, 0)
        elif command == "rotate" and numbers:
            angle = math.radians(numbers[0])
            rotation = (math.cos(angle), math.sin(angle), -math.sin(angle), math.cos(angle), 0, 0)
            if len(numbers) >= 3:
                to_origin = (1, 0, 0, 1, -numbers[1], -numbers[2])
                back = (1, 0, 0, 1, numbers[1], numbers[2])
                matrix = _matrix_multiply(back, _matrix_multiply(rotation, to_origin))
            else:
                matrix = rotation
        result = _matrix_multiply(result, matrix)
    return result


def _apply_matrix(point: list[float], matrix: tuple[float, ...]) -> list[float]:
    x, y = point
    a, b, c, d, e, f = matrix
    return [a * x + c * y + e, b * x + d * y + f]


def _svg_geometry(tag: str, attrs: dict[str, str]) -> list[list[float]]:
    if tag == "rect":
        x, y = float(attrs.get("x", 0)), float(attrs.get("y", 0))
        width, height = float(attrs.get("width", 0)), float(attrs.get("height", 0))
        return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]
    if tag in {"polygon", "polyline"}:
        values = _numbers(attrs.get("points", ""))
        return [[values[index], values[index + 1]] for index in range(0, len(values) - 1, 2)]
    if tag == "line":
        return [
            [float(attrs.get("x1", 0)), float(attrs.get("y1", 0))],
            [float(attrs.get("x2", 0)), float(attrs.get("y2", 0))],
        ]
    if tag == "path":
        values = _numbers(attrs.get("d", ""))
        return [[values[index], values[index + 1]] for index in range(0, len(values) - 1, 2)]
    return []


def _numbers(value: str) -> list[float]:
    return [float(item) for item in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", value)]


def _length(value: str | None) -> float | None:
    values = _numbers(value or "")
    return values[0] if values else None


def _normalize_text(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", value.upper().translate(str.maketrans("ÇĞİÖŞÜ", "CGIOSU")))


def _mesh_horizontal_sections(
    element: Any, *, slice_z: float, unit_scale: float
) -> list[list[list[float]]]:
    try:
        settings = ifcopenshell.geom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
        shape: Any = ifcopenshell.geom.create_shape(settings, element)
        vertices = list(shape.geometry.verts)
        faces = list(shape.geometry.faces)
    except (RuntimeError, ValueError, TypeError, AttributeError):
        return []
    points3d = [
        (
            float(vertices[index]) * unit_scale,
            float(vertices[index + 1]) * unit_scale,
            float(vertices[index + 2]) * unit_scale,
        )
        for index in range(0, len(vertices), 3)
    ]
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for index in range(0, len(faces), 3):
        triangle = [points3d[faces[index + offset]] for offset in range(3)]
        intersections: list[tuple[float, float]] = []
        for offset in range(3):
            first, second = triangle[offset], triangle[(offset + 1) % 3]
            z1, z2 = first[2] - slice_z, second[2] - slice_z
            if abs(z1) <= 1e-7 and abs(z2) <= 1e-7:
                intersections.extend([(first[0], first[1]), (second[0], second[1])])
            elif z1 * z2 <= 0 and abs(first[2] - second[2]) > 1e-9:
                ratio = (slice_z - first[2]) / (second[2] - first[2])
                if -1e-7 <= ratio <= 1 + 1e-7:
                    intersections.append(
                        (
                            first[0] + ratio * (second[0] - first[0]),
                            first[1] + ratio * (second[1] - first[1]),
                        )
                    )
        unique = list(dict.fromkeys(_point_key(point) for point in intersections))
        if len(unique) == 2 and unique[0] != unique[1]:
            segments.append((unique[0], unique[1]))
    return _segments_to_polygons(segments)


def _segments_to_polygons(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> list[list[list[float]]]:
    if segments:
        merged = unary_union([LineString([first, second]) for first, second in segments])
        robust = [
            [[round(float(x), 6), round(float(y), 6)] for x, y in polygon.exterior.coords[:-1]]
            for polygon in polygonize(merged)
            if polygon.is_valid and polygon.area > 1e-9
        ]
        if robust:
            return robust
    adjacency: defaultdict[tuple[float, float], list[tuple[float, float]]] = defaultdict(list)
    edges: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for first, second in segments:
        edge = _edge_key(first, second)
        if edge in edges:
            continue
        edges.add(edge)
        adjacency[first].append(second)
        adjacency[second].append(first)
    polygons: list[list[list[float]]] = []
    while edges:
        first_edge = next(iter(edges))
        start, current = first_edge
        path = [start, current]
        edges.remove(first_edge)
        previous = start
        for _ in range(len(adjacency) + 2):
            options = [
                item
                for item in adjacency[current]
                if _edge_key(current, item) in edges and item != previous
            ]
            if not options:
                break
            next_point = min(options)
            edges.remove(_edge_key(current, next_point))
            if next_point == start:
                polygon = [[x, y] for x, y in path]
                if _valid_polygon(polygon):
                    polygons.append(polygon)
                break
            path.append(next_point)
            previous, current = current, next_point
    return polygons


def _opening_relationships(model: Any) -> dict[str, dict[str, str]]:
    opening_hosts = {
        _ifc_id(relation.RelatedOpeningElement): _ifc_id(relation.RelatingBuildingElement)
        for relation in model.by_type("IfcRelVoidsElement")
    }
    output: dict[str, dict[str, str]] = {}
    for relation in model.by_type("IfcRelFillsElement"):
        element_id = _ifc_id(relation.RelatedBuildingElement)
        opening_id = _ifc_id(relation.RelatingOpeningElement)
        host_id = opening_hosts.get(opening_id)
        if host_id:
            output[element_id] = {
                "opening_global_id": opening_id,
                "host_wall_global_id": host_id,
            }
    return output


def _opening_direction(element: Any) -> float | None:
    try:
        placement = element.ObjectPlacement.RelativePlacement
        direction = placement.RefDirection
        if direction is None:
            return None
        ratios = direction.DirectionRatios
        return round(math.degrees(math.atan2(float(ratios[1]), float(ratios[0]))), 6)
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def _storey_container(element: Any) -> Any | None:
    container = ifcopenshell.util.element.get_container(
        element, should_get_direct=False, ifc_class="IfcBuildingStorey"
    )
    if container is not None:
        return container
    for relation in getattr(element, "Decomposes", ()):
        parent = getattr(relation, "RelatingObject", None)
        if parent is not None and parent.is_a("IfcBuildingStorey"):
            return parent
    return None


def _point_key(point: tuple[float, float]) -> tuple[float, float]:
    return round(point[0], 6), round(point[1], 6)


def _edge_key(
    first: tuple[float, float], second: tuple[float, float]
) -> tuple[tuple[float, float], tuple[float, float]]:
    return (first, second) if first <= second else (second, first)


def _polygon_area(polygon: list[list[float]]) -> float:
    return 0.5 * sum(
        polygon[index][0] * polygon[(index + 1) % len(polygon)][1]
        - polygon[(index + 1) % len(polygon)][0] * polygon[index][1]
        for index in range(len(polygon))
    )


def _line_points(row: dict[str, Any]) -> tuple[list[float], list[float]]:
    return [float(value) for value in row["start"]], [float(value) for value in row["end"]]


def _point_line_distance(point: list[float], start: list[float], end: list[float]) -> float:
    vector = np.subtract(end, start)
    cross = vector[0] * (point[1] - start[1]) - vector[1] * (point[0] - start[0])
    return float(abs(cross) / max(np.linalg.norm(vector), 1e-9))


def _line_overlap_ratio(
    a1: list[float], a2: list[float], b1: list[float], b2: list[float]
) -> float:
    axis = 0 if abs(a2[0] - a1[0]) >= abs(a2[1] - a1[1]) else 1
    first = sorted((a1[axis], a2[axis]))
    second = sorted((b1[axis], b2[axis]))
    overlap = max(0.0, min(first[1], second[1]) - max(first[0], second[0]))
    return overlap / max(1e-9, min(first[1] - first[0], second[1] - second[0]))


def _point_in_polygon(point: list[float], polygon: list[list[float]]) -> bool:
    x, y = float(point[0]), float(point[1])
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if (current[1] > y) != (previous[1] > y) and x < (
            (previous[0] - current[0]) * (y - current[1]) / max(previous[1] - current[1], 1e-12)
            + current[0]
        ):
            inside = not inside
        previous = current
    return inside


def _transform_polygon(polygon: list[list[float]], matrix: Any) -> list[list[float]]:
    points = np.asarray(polygon, dtype=np.float64)
    projected = cv2.transform(points.reshape(1, -1, 2), matrix)[0]
    return [[round(float(x), 8), round(float(y), 8)] for x, y in projected]


def _clip_polygon_unit(polygon: list[list[float]]) -> tuple[list[list[float]], bool]:
    clipped = [[min(1.0, max(0.0, x)), min(1.0, max(0.0, y))] for x, y in polygon]
    return clipped, clipped != polygon


def _annotation_bbox(row: VisionAnnotationORM) -> list[float]:
    value = row.geometry_normalized.get("bbox")
    if value and len(value) == 4:
        return [float(item) for item in value]
    polygon = row.geometry_normalized.get("polygon", [])
    return _bbox(polygon) if polygon else [0, 0, 0, 0]


def _bbox_area(value: list[float]) -> float:
    return max(0.0, value[2] - value[0]) * max(0.0, value[3] - value[1])


def _bbox_iou(first: list[float], second: list[float]) -> float:
    intersection = _bbox_area(
        [
            max(first[0], second[0]),
            max(first[1], second[1]),
            min(first[2], second[2]),
            min(first[3], second[3]),
        ]
    )
    union = _bbox_area(first) + _bbox_area(second) - intersection
    return intersection / union if union else 0.0
