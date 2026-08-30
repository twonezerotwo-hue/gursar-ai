from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import structlog
from ezdxf import units
from ezdxf.filemanagement import readfile
from ezdxf.lldxf.const import DXFError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit, append_outbox
from app.core.security import ReviewPrincipal
from app.modules.documents.infrastructure.models import DocumentVersionORM
from app.modules.project_graph.domain.models import ElementType, ReviewStatus, SourceType
from app.modules.project_graph.infrastructure.models import (
    BlockSemanticMappingORM,
    BuildingElementORM,
    DxfAnalysisORM,
    DxfUnitResolutionORM,
    LayerSemanticMappingORM,
    MeasurementEvidenceORM,
    MeasurementORM,
    TakeoffItemORM,
)
from app.modules.training.application.service import TrainingPipelineService

logger = structlog.get_logger(__name__)
DXF_ANALYSIS_VERSION = "DXF_INTELLIGENCE_V1"
SUPPORTED_ENTITY_TYPES = {
    "LINE",
    "LWPOLYLINE",
    "POLYLINE",
    "ARC",
    "CIRCLE",
    "ELLIPSE",
    "HATCH",
    "INSERT",
    "TEXT",
    "MTEXT",
    "DIMENSION",
}
UNIT_NAMES = {
    units.MM: "millimeter",
    units.CM: "centimeter",
    units.M: "meter",
    units.IN: "inch",
    units.FT: "foot",
}
UNIT_FACTORS = {
    "millimeter": 0.001,
    "centimeter": 0.01,
    "meter": 1.0,
    "inch": 0.0254,
    "foot": 0.3048,
}


class DxfIntelligenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SemanticResolution:
    element_type: ElementType
    confidence: float
    verified: bool
    source: str
    conflict: bool = False


@dataclass(frozen=True, slots=True)
class ExtractedGeometry:
    geometry_type: str
    raw: dict[str, Any]
    normalized: dict[str, Any]
    measurements: tuple[tuple[str, float, str, str], ...]
    valid: bool = True
    error: str | None = None


class DxfIntelligenceService:
    def __init__(
        self,
        session: Session,
        *,
        max_entities: int = 250_000,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._session = session
        self._max_entities = max_entities
        self._timeout_seconds = timeout_seconds

    def create_layer_mapping(
        self, *, project_id: str, values: dict[str, Any], principal: ReviewPrincipal
    ) -> LayerSemanticMappingORM:
        self._require_global_admin(values["scope"], principal)
        self._validate_mapping(
            values["normalized_element_type"], values["scope"], project_id, values
        )
        row = LayerSemanticMappingORM(
            **values,
            project_id=project_id if values["scope"] == "PROJECT" else None,
            confidence=Decimal("1"),
            verified=True,
            created_by=principal.reviewer_id,
            reviewed_by=principal.reviewer_id,
        )
        self._session.add(row)
        self._mapping_audit(project_id, "DXF_LAYER_MAPPING_VERIFIED", row.raw_layer_name, principal)
        self._session.flush()
        return row

    def create_block_mapping(
        self, *, project_id: str, values: dict[str, Any], principal: ReviewPrincipal
    ) -> BlockSemanticMappingORM:
        self._require_global_admin(values["scope"], principal)
        self._validate_mapping(
            values["normalized_element_type"], values["scope"], project_id, values
        )
        row = BlockSemanticMappingORM(
            **values,
            project_id=project_id if values["scope"] == "PROJECT" else None,
            confidence=Decimal("1"),
            verified=True,
            created_by=principal.reviewer_id,
            reviewed_by=principal.reviewer_id,
        )
        self._session.add(row)
        self._mapping_audit(project_id, "DXF_BLOCK_MAPPING_VERIFIED", row.block_name, principal)
        self._session.flush()
        return row

    def set_unit(
        self,
        *,
        project_id: str,
        document_version_id: str,
        unit: str,
        reason: str,
        principal: ReviewPrincipal,
    ) -> DxfUnitResolutionORM:
        self._require_dxf(project_id, document_version_id)
        if unit not in UNIT_FACTORS:
            raise DxfIntelligenceError("Desteklenmeyen DXF birimi")
        revision = (
            int(
                self._session.scalar(
                    select(func.count(DxfUnitResolutionORM.id)).where(
                        DxfUnitResolutionORM.document_version_id == document_version_id
                    )
                )
                or 0
            )
            + 1
        )
        row = DxfUnitResolutionORM(
            project_id=project_id,
            document_version_id=document_version_id,
            unit=unit,
            reason=reason,
            reviewer=principal.reviewer_id,
            reviewer_role=principal.role,
            revision=revision,
        )
        self._session.add(row)
        append_audit(
            self._session,
            action="SET_DXF_UNIT",
            aggregate_id=project_id,
            actor_type=principal.role,
            actor_id=principal.reviewer_id,
            detail={
                "document_version_id": document_version_id,
                "unit": unit,
                "reason": reason,
                "revision": revision,
            },
        )
        self._session.flush()
        return row

    def analyze(
        self, *, project_id: str, document_version_id: str, path: Path
    ) -> tuple[DxfAnalysisORM, bool]:
        version = self._require_dxf(project_id, document_version_id)
        existing = self._session.scalar(
            select(DxfAnalysisORM).where(
                DxfAnalysisORM.document_version_id == document_version_id,
                DxfAnalysisORM.analysis_version == DXF_ANALYSIS_VERSION,
            )
        )
        if existing:
            return existing, True
        started = time.monotonic()
        try:
            document = readfile(path)
        except (DXFError, OSError, ValueError) as exc:
            raise DxfIntelligenceError("INVALID_DXF") from exc
        modelspace = document.modelspace()
        entity_count = len(modelspace)
        if entity_count > self._max_entities:
            raise DxfIntelligenceError("DXF_ENTITY_LIMIT_EXCEEDED")
        dimension_conflict = self._dimension_conflict(modelspace)
        raw_unit, unit_status, factor, unit_confidence, unit_method = self._resolve_unit(
            document, document_version_id, dimension_conflict=dimension_conflict
        )
        semantic_counts: Counter[str] = Counter()
        measurement_counts: Counter[str] = Counter()
        review_required = 0
        grouped: dict[tuple[str, str, str], list[MeasurementORM]] = defaultdict(list)
        training = TrainingPipelineService(self._session)
        if unit_status == "CONFLICT":
            training.enqueue_active_learning(
                project_id=project_id,
                source_reference=f"DXF_UNIT:{document_version_id}",
                prediction={
                    "raw_unit": UNIT_NAMES.get(int(document.units)),
                    "resolved_unit": raw_unit,
                },
                confidence=unit_confidence,
                reason="UNIT_CONFLICT",
            )
        for entity in modelspace:
            if time.monotonic() - started > self._timeout_seconds:
                raise DxfIntelligenceError("DXF_ANALYSIS_TIMEOUT")
            entity_type = entity.dxftype()
            if entity_type not in SUPPORTED_ENTITY_TYPES:
                continue
            handle = str(entity.dxf.get("handle", ""))
            layer = str(entity.dxf.get("layer", "0"))
            block_name = str(entity.dxf.name) if entity_type == "INSERT" else None
            semantic = self._resolve_semantic(project_id, layer, block_name)
            geometry = self._extract_geometry(entity, factor)
            if entity_type in {"TEXT", "MTEXT", "DIMENSION"} and not geometry.measurements:
                continue
            status = (
                ReviewStatus.VERIFIED.value
                if semantic.verified
                else ReviewStatus.REVIEW_REQUIRED.value
            )
            if not semantic.verified:
                review_required += 1
            semantic_counts[semantic.element_type.value] += 1
            element = BuildingElementORM(
                project_id=project_id,
                discipline="UNKNOWN",
                element_type=semantic.element_type.value,
                name=block_name or layer,
                geometry_type=geometry.geometry_type,
                geometry_json=geometry.normalized if factor else geometry.raw,
                properties_json={
                    "raw_geometry": geometry.raw,
                    "layer": layer,
                    "block_name": block_name,
                    "semantic_source": semantic.source,
                    "semantic_verified": semantic.verified,
                    "geometry_valid": geometry.valid,
                    "geometry_error": geometry.error,
                },
                source_type=SourceType.DXF.value,
                source_document_id=document_version_id,
                source_external_id=handle,
                source_coordinates={
                    "entity_handle": handle,
                    "layer_name": layer,
                    "block_name": block_name,
                },
                extraction_method="DXF_VECTOR_GEOMETRY",
                extraction_model="ezdxf",
                extraction_version=DXF_ANALYSIS_VERSION,
                confidence=Decimal(str(semantic.confidence)),
                review_status=status,
                revision=1,
            )
            self._session.add(element)
            self._session.flush()
            if semantic.element_type is ElementType.UNKNOWN or semantic.conflict:
                reason = "SEMANTIC_MAPPING_CONFLICT" if semantic.conflict else "UNKNOWN_CLASS"
                training.enqueue_active_learning(
                    project_id=project_id,
                    source_reference=f"DXF:{document_version_id}:{handle}",
                    prediction={
                        "element_type": semantic.element_type.value,
                        "layer": layer,
                        "block_name": block_name,
                    },
                    confidence=semantic.confidence,
                    reason=reason,
                )
            if factor is None or not geometry.valid:
                continue
            for measurement_type, value, unit, method in geometry.measurements:
                measurement = MeasurementORM(
                    project_id=project_id,
                    element_id=element.id,
                    measurement_type=measurement_type,
                    measurement_purpose=(
                        "ENGINEERING_QUANTITY" if semantic.verified else "RAW_GEOMETRY"
                    ),
                    geometry_json=geometry.normalized,
                    raw_value=Decimal(
                        str(value / self._measurement_factor(factor, measurement_type))
                    ),
                    unit=unit,
                    calculated_value=Decimal(str(round(value, 6))),
                    source_document_id=document_version_id,
                    method=method,
                    algorithm_version=DXF_ANALYSIS_VERSION,
                    confidence=Decimal("0.99"),
                    review_status=status,
                    revision=1,
                )
                self._session.add(measurement)
                self._session.flush()
                self._session.add(
                    MeasurementEvidenceORM(
                        measurement_id=measurement.id,
                        source_coordinates={
                            "document_version_id": document_version_id,
                            "entity_handle": handle,
                            "entity_type": entity_type,
                            "layer_name": layer,
                            "block_name": block_name,
                        },
                        geometry_json={
                            "raw_geometry": geometry.raw,
                            "normalized_geometry": geometry.normalized,
                        },
                        scale_json={
                            "raw_unit": raw_unit,
                            "normalized_unit": "meter",
                            "unit_resolution_method": unit_method,
                            "factor": factor,
                        },
                        algorithm=method,
                        model_version=DXF_ANALYSIS_VERSION,
                        review_status=status,
                    )
                )
                measurement_counts[measurement_type.lower()] += 1
                if semantic.verified:
                    grouped[(semantic.element_type.value, measurement_type, unit)].append(
                        measurement
                    )
        for (element_type, measurement_type, unit), rows in grouped.items():
            self._session.add(
                TakeoffItemORM(
                    project_id=project_id,
                    service_code=f"dxf.{element_type.lower()}.{measurement_type.lower()}",
                    description=f"{element_type} DXF geometric {measurement_type.lower()}",
                    quantity=sum((row.calculated_value for row in rows), Decimal("0")),
                    unit=unit,
                    measurement_ids=[row.id for row in rows],
                    calculation_rule="SUM(VERIFIED_DXF_GEOMETRY)",
                    calculation_rule_version=DXF_ANALYSIS_VERSION,
                    quantity_source="VERIFIED_DXF_GEOMETRY",
                    review_status=ReviewStatus.VERIFIED.value,
                    revision=1,
                )
            )
        recognized = sum(count for key, count in semantic_counts.items() if key != "UNKNOWN")
        analysis = DxfAnalysisORM(
            project_id=project_id,
            document_version_id=version.id,
            analysis_version=DXF_ANALYSIS_VERSION,
            raw_unit=raw_unit,
            normalized_unit="meter" if factor else None,
            unit_status=unit_status,
            unit_confidence=Decimal(str(unit_confidence)),
            entity_count=entity_count,
            recognized_count=recognized,
            unknown_count=semantic_counts["UNKNOWN"],
            measurement_count=sum(measurement_counts.values()),
            summary_json={
                "layers": len(document.layers),
                "semantic_elements": dict(semantic_counts),
                "measurements": dict(measurement_counts),
                "review_required": review_required,
                "label": "DXF GEOMETRIC MEASUREMENT",
            },
        )
        self._session.add(analysis)
        append_audit(
            self._session,
            action="DXF_PROJECT_GRAPH_EXTRACTED",
            aggregate_id=project_id,
            detail={
                "document_version_id": document_version_id,
                "analysis_version": DXF_ANALYSIS_VERSION,
                "entity_count": entity_count,
                "measurement_count": analysis.measurement_count,
            },
        )
        append_outbox(
            self._session,
            event_type="project.graph.updated.v1",
            aggregate_id=project_id,
            payload={"source": "DXF", "document_version_id": document_version_id},
        )
        self._session.flush()
        logger.info(
            "dxf_analysis_completed",
            project_id=project_id,
            document_version_id=document_version_id,
            analysis_version=DXF_ANALYSIS_VERSION,
            entity_count=entity_count,
            recognized_count=recognized,
            unknown_count=analysis.unknown_count,
            measurement_count=analysis.measurement_count,
            duration=round(time.monotonic() - started, 4),
            errors=0,
        )
        return analysis, False

    def list_layers(self, project_id: str) -> list[LayerSemanticMappingORM]:
        return list(
            self._session.scalars(
                select(LayerSemanticMappingORM)
                .where(
                    (LayerSemanticMappingORM.project_id == project_id)
                    | (LayerSemanticMappingORM.project_id.is_(None))
                )
                .order_by(LayerSemanticMappingORM.raw_layer_name)
            ).all()
        )

    def list_blocks(self, project_id: str) -> list[BlockSemanticMappingORM]:
        return list(
            self._session.scalars(
                select(BlockSemanticMappingORM)
                .where(
                    (BlockSemanticMappingORM.project_id == project_id)
                    | (BlockSemanticMappingORM.project_id.is_(None))
                )
                .order_by(BlockSemanticMappingORM.block_name)
            ).all()
        )

    def _resolve_unit(
        self,
        document: Any,
        document_version_id: str,
        *,
        dimension_conflict: bool,
    ) -> tuple[str, str, float | None, float, str]:
        override = self._session.scalar(
            select(DxfUnitResolutionORM)
            .where(DxfUnitResolutionORM.document_version_id == document_version_id)
            .order_by(DxfUnitResolutionORM.revision.desc())
            .limit(1)
        )
        metadata_unit = UNIT_NAMES.get(int(document.units))
        if override:
            return override.unit, "VERIFIED", UNIT_FACTORS[override.unit], 1.0, "EXPERT_OVERRIDE"
        if metadata_unit:
            if dimension_conflict:
                return metadata_unit, "CONFLICT", None, 0.25, "$INSUNITS_DIMENSION_CONFLICT"
            return metadata_unit, "VERIFIED", UNIT_FACTORS[metadata_unit], 1.0, "$INSUNITS"
        return "unknown", "UNKNOWN", None, 0.0, "$INSUNITS_MISSING"

    @staticmethod
    def _dimension_conflict(modelspace: Any) -> bool:
        numeric = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")
        for entity in modelspace.query("DIMENSION"):
            text = str(entity.dxf.get("text", "<>"))
            match = numeric.match(text)
            if not match:
                continue
            stated = float(match.group(1).replace(",", "."))
            measured = float(entity.get_measurement())
            if stated > 0 and measured > 0:
                ratio = measured / stated
                if ratio < 0.98 or ratio > 1.02:
                    return True
        return False

    def _resolve_semantic(
        self, project_id: str, layer: str, block_name: str | None
    ) -> SemanticResolution:
        if block_name:
            rows = self._session.scalars(
                select(BlockSemanticMappingORM).where(
                    func.upper(BlockSemanticMappingORM.block_name) == block_name.upper(),
                    BlockSemanticMappingORM.verified.is_(True),
                )
            ).all()
            result = self._select_mapping(rows, project_id)
            if result:
                return result
        rows = self._session.scalars(
            select(LayerSemanticMappingORM).where(
                func.upper(LayerSemanticMappingORM.raw_layer_name) == layer.upper(),
                LayerSemanticMappingORM.verified.is_(True),
            )
        ).all()
        result = self._select_mapping(rows, project_id)
        if result:
            return result
        return self._heuristic(block_name or layer)

    @staticmethod
    def _select_mapping(rows: Iterable[Any], project_id: str) -> SemanticResolution | None:
        ranked: dict[int, list[Any]] = defaultdict(list)
        for row in rows:
            rank = (
                4
                if row.scope == "PROJECT" and row.project_id == project_id
                else 3
                if row.scope == "OFFICE_PROFILE"
                else 2
                if row.scope == "GLOBAL"
                else 0
            )
            if rank:
                ranked[rank].append(row)
        if not ranked:
            return None
        selected = ranked[max(ranked)]
        types = {row.normalized_element_type for row in selected}
        if len(types) != 1:
            return SemanticResolution(
                ElementType.UNKNOWN, 0.0, False, "SEMANTIC_MAPPING_CONFLICT", True
            )
        return SemanticResolution(
            ElementType(next(iter(types))), float(selected[0].confidence), True, selected[0].scope
        )

    @staticmethod
    def _heuristic(value: str) -> SemanticResolution:
        normalized = re.sub(
            r"[^A-Z0-9]+", "_", value.upper().translate(str.maketrans("ÇĞİÖŞÜ", "CGIOSU"))
        )
        patterns = (
            ("DUVAR|WALL", ElementType.WALL),
            ("KOLON|COLUMN|S_COL", ElementType.COLUMN),
            ("KIRIS|BEAM", ElementType.BEAM),
            ("KAPI|DOOR", ElementType.DOOR),
            ("PENC|WINDOW", ElementType.WINDOW),
            ("MERDIVEN|STAIR", ElementType.STAIR),
            ("ODA|ROOM", ElementType.ROOM),
        )
        for pattern, element_type in patterns:
            if re.search(pattern, normalized):
                return SemanticResolution(element_type, 0.65, False, "HEURISTIC")
        return SemanticResolution(ElementType.UNKNOWN, 0.0, False, "UNKNOWN")

    def _extract_geometry(self, entity: Any, factor: float | None) -> ExtractedGeometry:
        kind = entity.dxftype()
        scale = factor or 1.0
        if kind == "LINE":
            points = [self._xy(entity.dxf.start), self._xy(entity.dxf.end)]
            return self._points_geometry(kind, points, False, scale)
        if kind in {"LWPOLYLINE", "POLYLINE"}:
            points = [
                self._xy(point)
                for point in (entity.get_points("xy") if kind == "LWPOLYLINE" else entity.points())
            ]
            return self._points_geometry("POLYLINE", points, bool(entity.is_closed), scale)
        if kind == "ARC":
            radius = float(entity.dxf.radius)
            angle = math.radians(
                (float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360
            )
            value = radius * angle * scale
            raw = {
                "center": self._xy(entity.dxf.center),
                "radius": radius,
                "start_angle": float(entity.dxf.start_angle),
                "end_angle": float(entity.dxf.end_angle),
            }
            return ExtractedGeometry(
                "ARC",
                raw,
                {**raw, "center": self._scaled(raw["center"], scale), "radius": radius * scale},
                (("LENGTH", value, "m", "DXF_LINE_GEOMETRY"),),
            )
        if kind == "CIRCLE":
            radius = float(entity.dxf.radius)
            raw = {"center": self._xy(entity.dxf.center), "radius": radius}
            nr = radius * scale
            return ExtractedGeometry(
                "CIRCLE",
                raw,
                {"center": self._scaled(raw["center"], scale), "radius": nr},
                (
                    ("LENGTH", 2 * math.pi * nr, "m", "DXF_LINE_GEOMETRY"),
                    ("AREA", math.pi * nr * nr, "m2", "DXF_POLYLINE_GEOMETRY"),
                ),
            )
        if kind == "ELLIPSE":
            major = math.dist((0, 0), self._xy(entity.dxf.major_axis))
            minor = major * float(entity.dxf.ratio)
            a = major * scale
            b = minor * scale
            perimeter = math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))
            raw = {
                "center": self._xy(entity.dxf.center),
                "major_radius": major,
                "minor_radius": minor,
            }
            return ExtractedGeometry(
                "ELLIPSE",
                raw,
                {
                    "center": self._scaled(raw["center"], scale),
                    "major_radius": a,
                    "minor_radius": b,
                },
                (
                    ("LENGTH", perimeter, "m", "DXF_LINE_GEOMETRY"),
                    ("AREA", math.pi * a * b, "m2", "DXF_POLYLINE_GEOMETRY"),
                ),
            )
        if kind == "HATCH":
            points = []
            for path in entity.paths:
                if hasattr(path, "vertices"):
                    points = [self._xy(vertex[:2]) for vertex in path.vertices]
                    break
            return self._points_geometry("HATCH", points, True, scale, area_only=True)
        if kind == "INSERT":
            raw = {
                "insertion_point": self._xy(entity.dxf.insert),
                "rotation": float(entity.dxf.get("rotation", 0)),
                "xscale": float(entity.dxf.get("xscale", 1)),
                "yscale": float(entity.dxf.get("yscale", 1)),
                "zscale": float(entity.dxf.get("zscale", 1)),
                "attributes": {attrib.dxf.tag: attrib.dxf.text for attrib in entity.attribs},
            }
            return ExtractedGeometry(
                "POINT",
                raw,
                {**raw, "insertion_point": self._scaled(raw["insertion_point"], scale)},
                (("COUNT", 1.0, "adet", "DXF_BLOCK_COUNT"),),
            )
        if kind in {"TEXT", "MTEXT"}:
            text = entity.plain_text() if kind == "MTEXT" else str(entity.dxf.text)
            raw = {
                "text": text,
                "insertion_point": self._xy(entity.dxf.insert),
                "rotation": float(entity.dxf.get("rotation", 0)),
                "height": float(entity.dxf.get("height", entity.dxf.get("char_height", 0))),
                "style": str(entity.dxf.get("style", "")),
            }
            return ExtractedGeometry(
                "TEXT",
                raw,
                {**raw, "insertion_point": self._scaled(raw["insertion_point"], scale)},
                (),
            )
        if kind == "DIMENSION":
            value = float(entity.get_measurement()) * scale
            raw = {
                "dimension_text": str(entity.dxf.get("text", "<>")),
                "measured_value": value / scale,
            }
            return ExtractedGeometry(
                "DIMENSION",
                raw,
                {**raw, "measured_value": value},
                (("LENGTH", value, "m", "DXF_DIMENSION"),),
            )
        return ExtractedGeometry(kind, {}, {}, ())

    def _points_geometry(
        self,
        kind: str,
        points: list[tuple[float, float]],
        closed: bool,
        scale: float,
        *,
        area_only: bool = False,
    ) -> ExtractedGeometry:
        normalized = [self._scaled(point, scale) for point in points]
        raw = {"geometry_type": kind, "coordinates": points, "closed": closed}
        norm = {"geometry_type": kind, "coordinates": normalized, "closed": closed}
        if len(points) < (3 if closed else 2):
            return ExtractedGeometry(kind, raw, norm, (), False, "GEOMETRY_INVALID")
        measures: list[tuple[str, float, str, str]] = []
        if not area_only:
            pairs = list(pairwise(normalized))
            if closed:
                pairs.append((normalized[-1], normalized[0]))
            measures.append(
                (
                    "LENGTH",
                    sum(math.dist(a, b) for a, b in pairs),
                    "m",
                    "DXF_POLYLINE_GEOMETRY" if kind == "POLYLINE" else "DXF_LINE_GEOMETRY",
                )
            )
        if closed:
            if self._self_intersects(normalized):
                return ExtractedGeometry(
                    kind, raw, norm, tuple(measures), False, "GEOMETRY_INVALID"
                )
            area = (
                abs(
                    sum(
                        a[0] * b[1] - b[0] * a[1]
                        for a, b in zip(normalized, normalized[1:] + normalized[:1], strict=True)
                    )
                )
                / 2
            )
            if area <= 1e-12:
                return ExtractedGeometry(
                    kind, raw, norm, tuple(measures), False, "GEOMETRY_INVALID"
                )
            measures.append(
                (
                    "AREA",
                    area,
                    "m2",
                    "DXF_HATCH_GEOMETRY" if kind == "HATCH" else "DXF_POLYLINE_GEOMETRY",
                )
            )
        return ExtractedGeometry(kind, raw, norm, tuple(measures))

    @staticmethod
    def _self_intersects(points: list[tuple[float, float]]) -> bool:
        def orient(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

        edges = list(zip(points, points[1:] + points[:1], strict=True))
        for i, (a, b) in enumerate(edges):
            for j, (c, d) in enumerate(edges):
                if abs(i - j) <= 1 or {i, j} == {0, len(edges) - 1}:
                    continue
                if orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0:
                    return True
        return False

    @staticmethod
    def _xy(point: Any) -> tuple[float, float]:
        return float(point[0]), float(point[1])

    @staticmethod
    def _scaled(point: tuple[float, float], scale: float) -> tuple[float, float]:
        return point[0] * scale, point[1] * scale

    @staticmethod
    def _measurement_factor(factor: float, measurement_type: str) -> float:
        return factor ** {"LENGTH": 1, "AREA": 2, "VOLUME": 3, "COUNT": 0}[measurement_type]

    def _require_dxf(self, project_id: str, document_version_id: str) -> DocumentVersionORM:
        row = self._session.get(DocumentVersionORM, document_version_id)
        if row is None or row.project_id != project_id:
            raise DxfIntelligenceError("DXF document version not found in project")
        if row.media_type != "image/vnd.dxf":
            raise DxfIntelligenceError("Document version is not DXF")
        return row

    @staticmethod
    def _validate_mapping(
        element_type: str, scope: str, project_id: str, values: dict[str, Any]
    ) -> None:
        del project_id
        ElementType(element_type)
        if scope not in {"PROJECT", "OFFICE_PROFILE", "GLOBAL"}:
            raise DxfIntelligenceError("Geçersiz mapping scope")
        if scope == "OFFICE_PROFILE" and not values.get("office_profile"):
            raise DxfIntelligenceError("office_profile gerekli")

    def _mapping_audit(
        self, project_id: str, action: str, source_name: str, principal: ReviewPrincipal
    ) -> None:
        append_audit(
            self._session,
            action=action,
            aggregate_id=project_id,
            actor_type=principal.role,
            actor_id=principal.reviewer_id,
            detail={"source_name": source_name},
        )

    @staticmethod
    def _require_global_admin(scope: str, principal: ReviewPrincipal) -> None:
        if scope == "GLOBAL" and principal.role != "ADMIN":
            raise DxfIntelligenceError("GLOBAL mapping için ADMIN rolü gerekli")
