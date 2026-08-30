from __future__ import annotations

import hashlib
import io
import json
import math
import pickle
import zipfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, ClassVar
from urllib.request import Request, urlopen

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.unit
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.persistence import new_id
from app.modules.acquisition.infrastructure.models import (
    DiscoveredDatasetSourceORM,
    ExternalAnnotationLineageORM,
    ExternalProjectPackageORM,
    Ifc2DAnnotationLineageORM,
    Ifc2DRenderORM,
    LicenseAssessmentORM,
    UpstreamLicenseLineageORM,
)
from app.modules.documents.domain.ports import DocumentStorage
from app.modules.documents.infrastructure.models import (
    DocumentVersionORM,
    ProjectDocumentORM,
    SheetORM,
)
from app.modules.projects.infrastructure.models import ProjectORM
from app.modules.vision.infrastructure.models import (
    TrainingImageDerivativeORM,
    VisionAnnotationORM,
)

LICENSE_LINEAGE_VERSION = "UPSTREAM_LICENSE_LINEAGE_V1"
RESPLAN_IMPORTER_VERSION = "RESPLAN_ANNOTATION_IMPORT_V1"
IFC_2D_RENDERER_VERSION = "IFC_2D_GROUND_TRUTH_V1"
NORMALIZATION_VERSION = "NORMALIZED_COORDINATES_V1"
TRAINING_RIGHTS = {"OPEN_LICENSED", "LICENSED_BY_GURSAR", "INTERNAL_PROJECT"}
PERMISSIVE_LICENSES = {
    "MIT",
    "APACHE-2.0",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
    "CC0-1.0",
    "CC-BY-4.0",
    "ODC-BY-1.0",
}
NON_COMMERCIAL_LICENSES = {"CC-BY-NC-4.0", "RESEARCH-ONLY", "ACADEMIC-ONLY"}
SOURCE_TIERS = (
    "GURSAR_GOLD",
    "REAL_DRAWING_SILVER",
    "BIM_DERIVED_SILVER",
    "EXTERNAL_OBJECT_SILVER",
    "SYNTHETIC",
    "WEAK",
)


class RealDataRuleError(ValueError):
    pass


def canonical_license(value: str | None) -> str:
    normalized = (value or "UNKNOWN").strip().upper().replace("_", "-")
    aliases = {
        "CC BY 4.0": "CC-BY-4.0",
        "CC-BY": "CC-BY-4.0",
        "CC BY-NC 4.0": "CC-BY-NC-4.0",
        "CC-BY-NC": "CC-BY-NC-4.0",
        "APACHE 2.0": "APACHE-2.0",
        "UNKNOWN": "UNKNOWN",
        "": "UNKNOWN",
    }
    return aliases.get(normalized, normalized)


class UpstreamLicenseLineageService:
    """Resolves the effective license without LLM or permissive fall-through."""

    _attribution: ClassVar[set[str]] = {"CC-BY-4.0", "ODC-BY-1.0"}
    _notice: ClassVar[set[str]] = PERMISSIVE_LICENSES - {"CC0-1.0"}

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        source_id: str,
        declared_license: str | None,
        parent_source_id: str | None = None,
        upstream_license: str | None = None,
        derived_from: str | None = None,
        upstream_url: str | None = None,
        lineage_verified: bool = False,
    ) -> UpstreamLicenseLineageORM:
        source = self._session.get(DiscoveredDatasetSourceORM, source_id)
        if source is None:
            raise RealDataRuleError("SOURCE_NOT_FOUND")
        parent = None
        if parent_source_id:
            parent = self._session.scalar(
                select(UpstreamLicenseLineageORM)
                .where(UpstreamLicenseLineageORM.source_id == parent_source_id)
                .order_by(UpstreamLicenseLineageORM.created_at.desc())
            )
            if parent is None:
                raise RealDataRuleError("UPSTREAM_LINEAGE_MISSING")
        declared = canonical_license(declared_license)
        upstream = canonical_license(parent.effective_license if parent else upstream_license)
        root_source_id = parent.root_source_id if parent else source_id
        decision = self._resolve(
            declared=declared,
            upstream=upstream,
            derived=bool(parent_source_id or derived_from or upstream_url),
            lineage_verified=lineage_verified,
        )
        existing = self._session.scalar(
            select(UpstreamLicenseLineageORM).where(
                UpstreamLicenseLineageORM.source_id == source_id,
                UpstreamLicenseLineageORM.parent_source_id == parent_source_id,
                UpstreamLicenseLineageORM.lineage_version == LICENSE_LINEAGE_VERSION,
            )
        )
        if existing:
            return existing
        row = UpstreamLicenseLineageORM(
            source_id=source_id,
            parent_source_id=parent_source_id,
            root_source_id=root_source_id,
            declared_license=declared,
            upstream_license=upstream if upstream != "UNKNOWN" else None,
            effective_license=decision["effective_license"],
            derived_from=derived_from,
            upstream_url=upstream_url,
            commercial_use_allowed=decision["commercial_use_allowed"],
            training_allowed=decision["training_allowed"],
            redistribution_allowed=decision["redistribution_allowed"],
            lineage_verified=lineage_verified and decision["conflict_code"] is None,
            manual_review_required=decision["manual_review_required"],
            conflict_code=decision["conflict_code"],
            decision_json=decision,
            lineage_version=LICENSE_LINEAGE_VERSION,
        )
        self._session.add(row)
        source.license_status = (
            "DERIVED_DATASET_LICENSE_CONFLICT"
            if decision["conflict_code"]
            else ("OPEN_LICENSED" if decision["training_allowed"] else "LICENSE_UNKNOWN")
        )
        if decision["conflict_code"]:
            source.status = "REVIEW_REQUIRED"
        self._session.flush()
        return row

    def latest(self, source_id: str) -> UpstreamLicenseLineageORM | None:
        return self._session.scalar(
            select(UpstreamLicenseLineageORM)
            .where(UpstreamLicenseLineageORM.source_id == source_id)
            .order_by(UpstreamLicenseLineageORM.created_at.desc())
        )

    @classmethod
    def _resolve(
        cls, *, declared: str, upstream: str, derived: bool, lineage_verified: bool
    ) -> dict[str, Any]:
        conflict: str | None = None
        effective = declared
        if declared in NON_COMMERCIAL_LICENSES:
            conflict = "DERIVED_DATASET_LICENSE_CONFLICT" if derived else "NON_COMMERCIAL_LICENSE"
            effective = declared
        elif declared not in PERMISSIVE_LICENSES:
            conflict = "DERIVED_DATASET_LICENSE_CONFLICT" if derived else "LICENSE_UNKNOWN"
            effective = "UNKNOWN"
        if derived:
            if upstream == "UNKNOWN":
                conflict = "DERIVED_DATASET_LICENSE_CONFLICT"
                effective = "UNKNOWN"
            elif not lineage_verified or (
                upstream in NON_COMMERCIAL_LICENSES
                or upstream not in PERMISSIVE_LICENSES
                or (upstream in cls._attribution and declared not in cls._attribution)
                or (upstream in cls._notice and declared == "CC0-1.0")
                or (upstream == "APACHE-2.0" and declared != "APACHE-2.0")
            ):
                conflict = "DERIVED_DATASET_LICENSE_CONFLICT"
                effective = upstream
            elif upstream in cls._attribution:
                effective = upstream
        allowed = conflict is None and effective in PERMISSIVE_LICENSES and lineage_verified
        return {
            "effective_license": effective,
            "commercial_use_allowed": allowed,
            "training_allowed": allowed,
            "redistribution_allowed": allowed,
            "manual_review_required": not allowed,
            "conflict_code": conflict,
            "lineage_verified": lineage_verified,
        }


def canonical_source_tier(source_type: str) -> str:
    aliases = {
        "GURSAR_EXPERT_VERIFIED": "GURSAR_GOLD",
        "EXPERT_MANUAL_ANNOTATION": "GURSAR_GOLD",
        "EXPERT_CORRECTION": "GURSAR_GOLD",
        "CROSS_MODAL_REVIEWED": "REAL_DRAWING_SILVER",
        "REAL_DRAWING_SILVER": "REAL_DRAWING_SILVER",
        "BIM_DERIVED_SILVER": "BIM_DERIVED_SILVER",
        "EXTERNAL_GROUND_TRUTH": "EXTERNAL_OBJECT_SILVER",
        "EXTERNAL_OBJECT_SILVER": "EXTERNAL_OBJECT_SILVER",
        "SYNTHETIC_GROUND_TRUTH": "SYNTHETIC",
        "SYNTHETIC": "SYNTHETIC",
    }
    return aliases.get(source_type, "WEAK")


class _RestrictedResPlanUnpickler(pickle.Unpickler):
    """Allows only the data constructors used by the official geometry release."""

    _allowed: ClassVar[dict[str, set[str]]] = {
        "builtins": {"dict", "list", "tuple", "set", "frozenset", "slice"},
        "collections": {"defaultdict"},
        "numpy": {"dtype", "ndarray"},
        "numpy.core.multiarray": {"_reconstruct", "scalar"},
        "numpy._core.multiarray": {"_reconstruct", "scalar"},
        "shapely.io": {"from_wkb"},
        "shapely.geometry.polygon": {"Polygon"},
        "shapely.geometry.linestring": {"LineString"},
        "shapely.geometry.point": {"Point"},
        "shapely.geometry.multipolygon": {"MultiPolygon"},
        "shapely.geometry.multilinestring": {"MultiLineString"},
        "shapely.geometry.collection": {"GeometryCollection"},
    }

    def find_class(self, module: str, name: str) -> Any:
        if name in self._allowed.get(module, set()):
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"blocked pickle global: {module}.{name}")


class ResPlanAnnotationImporter:
    room_keys = ("living", "bedroom", "kitchen", "bathroom", "corridor", "storage")

    def __init__(self, session: Session, storage: DocumentStorage) -> None:
        self._session = session
        self._storage = storage

    @staticmethod
    def load_archive(path: Path) -> list[dict[str, Any]]:
        if not zipfile.is_zipfile(path):
            raise RealDataRuleError("INVALID_RESPLAN_ARCHIVE")
        with zipfile.ZipFile(path) as archive:
            candidates = [name for name in archive.namelist() if name.lower().endswith(".pkl")]
            if len(candidates) != 1:
                raise RealDataRuleError("RESPLAN_PICKLE_MISSING")
            info = archive.getinfo(candidates[0])
            if info.file_size > 2 * 1024**3:
                raise RealDataRuleError("RESPLAN_ARCHIVE_TOO_LARGE")
            with archive.open(info) as stream:
                payload = _RestrictedResPlanUnpickler(stream).load()
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise RealDataRuleError("RESPLAN_SCHEMA_MISMATCH")
        return payload

    def import_records(
        self,
        *,
        source_id: str,
        records: list[dict[str, Any]],
        limit: int = 1000,
        annotation_file: str = "ResPlan.pkl",
    ) -> dict[str, Any]:
        source = self._session.get(DiscoveredDatasetSourceORM, source_id)
        if source is None:
            raise RealDataRuleError("SOURCE_NOT_FOUND")
        lineage = UpstreamLicenseLineageService(self._session).latest(source_id)
        if lineage is None or not lineage.training_allowed:
            raise RealDataRuleError("UPSTREAM_LICENSE_LINEAGE_BLOCKED")
        imported = reused = rejected = annotation_count = invalid_geometry = duplicates = 0
        class_counts: Counter[str] = Counter()
        seen_hashes: set[str] = set()
        for index, plan in enumerate(records[:limit]):
            extracted = self._extract_plan(plan)
            if not extracted:
                rejected += 1
                continue
            plan_hash = hashlib.sha256(
                json.dumps(extracted, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if plan_hash in seen_hashes:
                duplicates += 1
                continue
            seen_hashes.add(plan_hash)
            external_id = str(plan.get("id") or plan.get("plan_id") or f"resplan-{index:06d}")
            package = self._session.scalar(
                select(ExternalProjectPackageORM).where(
                    ExternalProjectPackageORM.source_id == source_id,
                    ExternalProjectPackageORM.external_project_id == external_id,
                )
            )
            if package is not None:
                reused += 1
                continue
            try:
                package, count, counts = self._persist_plan(
                    source=source,
                    lineage=lineage,
                    external_id=external_id,
                    extracted=extracted,
                    annotation_file=annotation_file,
                    plan_hash=plan_hash,
                )
            except (ValueError, OSError):
                invalid_geometry += 1
                rejected += 1
                continue
            package.ingestion_status = "INGESTED"
            imported += 1
            annotation_count += count
            class_counts.update(counts)
        valid_ratio = imported / max(1, imported + rejected)
        duplicate_ratio = duplicates / max(1, len(records[:limit]))
        return {
            "projects_downloaded": min(limit, len(records)),
            "projects_imported": imported,
            "projects_valid": imported,
            "projects_rejected": rejected,
            "projects_reused": reused,
            "annotations_imported": annotation_count,
            "class_distribution": dict(class_counts),
            "duplicate_ratio": round(duplicate_ratio, 6),
            "invalid_geometry_ratio": round(invalid_geometry / max(1, len(records[:limit])), 6),
            "valid_ratio": round(valid_ratio, 6),
            "quality_score": round(100 * valid_ratio * (1 - duplicate_ratio)),
            "pilot_accepted": valid_ratio >= 0.90,
            "source_tier": "REAL_DRAWING_SILVER",
        }

    @classmethod
    def _extract_plan(cls, plan: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        mapping = {"wall": "WALL", "door": "DOOR", "front_door": "DOOR", "window": "WINDOW"}
        for key, target in mapping.items():
            for coordinates in _geometry_parts(plan.get(key)):
                if _valid_polygon(coordinates):
                    result.append({"class": target, "coordinates": coordinates, "room_type": None})
        for room_type in cls.room_keys:
            for coordinates in _geometry_parts(plan.get(room_type)):
                if _valid_polygon(coordinates):
                    result.append(
                        {"class": "ROOM", "coordinates": coordinates, "room_type": room_type}
                    )
        return result

    def _persist_plan(
        self,
        *,
        source: DiscoveredDatasetSourceORM,
        lineage: UpstreamLicenseLineageORM,
        external_id: str,
        extracted: list[dict[str, Any]],
        annotation_file: str,
        plan_hash: str,
    ) -> tuple[ExternalProjectPackageORM, int, Counter[str]]:
        bounds = _bounds([item["coordinates"] for item in extracted])
        normalized = [
            {
                **item,
                "normalized": _normalize_polygon(item["coordinates"], bounds),
                "bbox": _bbox(_normalize_polygon(item["coordinates"], bounds)),
            }
            for item in extracted
        ]
        image = _render_polygons(normalized, size=1024)
        image_buffer = io.BytesIO()
        image.save(image_buffer, "PNG", optimize=True)
        image_bytes = image_buffer.getvalue()
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        image_key = f"training-derivatives/resplan/{plan_hash}.png"
        annotation_key = f"training-derivatives/resplan/{plan_hash}.json"
        annotation_bytes = json.dumps(
            {"version": RESPLAN_IMPORTER_VERSION, "annotations": normalized}, sort_keys=True
        ).encode()
        self._storage.save(storage_key=image_key, source=io.BytesIO(image_bytes))
        self._storage.save(storage_key=annotation_key, source=io.BytesIO(annotation_bytes))
        project = ProjectORM(name=f"ResPlan {external_id}"[:300], locale="tr", status="ACTIVE")
        self._session.add(project)
        self._session.flush()
        package = ExternalProjectPackageORM(
            source_id=source.id,
            external_project_id=external_id,
            project_id=project.id,
            building_type="residential",
            floors=1,
            discipline_availability_json=["ARCHITECTURAL"],
            files_json=[{"storage_key": image_key, "sha256": image_hash}],
            annotation_files_json=[{"storage_key": annotation_key}],
            project_quality_score=92,
            training_eligibility="ELIGIBLE",
            rights_status="OPEN_LICENSED",
            ingestion_status="IMPORTING",
            completeness_json={
                "source_tier": "REAL_DRAWING_SILVER",
                "source_family_id": source.source_family_id,
                "license_lineage_id": lineage.id,
                "metric_coordinates": True,
            },
        )
        self._session.add(package)
        self._session.flush()
        document = ProjectDocumentORM(project_id=project.id, original_name=f"{external_id}.png")
        self._session.add(document)
        self._session.flush()
        version_id = new_id()
        self._session.add(
            DocumentVersionORM(
                id=version_id,
                document_id=document.id,
                project_id=project.id,
                version=1,
                sha256=image_hash,
                size_bytes=len(image_bytes),
                media_type="image/png",
                storage_key=image_key,
                page_count=1,
                status="READY",
            )
        )
        sheet = SheetORM(
            document_version_id=version_id,
            page_number=1,
            title="ResPlan vector floor plan",
            discipline="ARCHITECTURAL",
            drawing_type="PLAN",
            page_type="VECTOR",
            review_status="VERIFIED",
            width_points=1024,
            height_points=1024,
        )
        self._session.add(sheet)
        self._session.flush()
        derivative = TrainingImageDerivativeORM(
            project_id=project.id,
            document_version_id=version_id,
            sheet_id=sheet.id,
            page_number=1,
            render_profile="RESPLAN_VECTOR_V1",
            render_version=RESPLAN_IMPORTER_VERSION,
            width=1024,
            height=1024,
            dpi=144,
            storage_key=image_key,
            checksum_sha256=image_hash,
            perceptual_hash=image_hash[:16],
            rights_status="OPEN_LICENSED",
            anonymization_status="COMPLETED",
        )
        self._session.add(derivative)
        self._session.flush()
        counts: Counter[str] = Counter()
        for order, item in enumerate(normalized):
            polygon = item["normalized"]
            annotation = VisionAnnotationORM(
                project_id=project.id,
                document_version_id=version_id,
                sheet_id=sheet.id,
                image_derivative_id=derivative.id,
                task_type=f"{item['class']}_DETECTION",
                class_name=item["class"],
                geometry_type="POLYGON",
                geometry_normalized={
                    "polygon": polygon,
                    "bbox": item["bbox"],
                    "room_type": item["room_type"],
                    "metric_coordinates": item["coordinates"],
                },
                original_width=1024,
                original_height=1024,
                source_type="REAL_DRAWING_SILVER",
                annotation_source="EXTERNAL_GROUND_TRUTH",
                source_element_id=f"{external_id}:{order}",
                confidence=0.96,
                review_status="VERIFIED",
                reviewer="resplan-dataset-adapter",
                training_allowed=True,
                anonymized=True,
                rights_status="OPEN_LICENSED",
                quality_score=92,
            )
            self._session.add(annotation)
            self._session.flush()
            self._session.add(
                ExternalAnnotationLineageORM(
                    annotation_id=annotation.id,
                    source_id=source.id,
                    project_package_id=package.id,
                    original_file=image_key,
                    annotation_file=annotation_file,
                    external_class=str(item["room_type"] or item["class"]).lower(),
                    importer_version=RESPLAN_IMPORTER_VERSION,
                    mapping_version="GURSAR_ARCH_ONTOLOGY_V1",
                    normalization_version=NORMALIZATION_VERSION,
                    quality_tier="REAL_DRAWING_SILVER",
                )
            )
            counts[item["class"]] += 1
        self._session.flush()
        return package, sum(counts.values()), counts


class Ifc2DGroundTruthRenderer:
    renderer_version = IFC_2D_RENDERER_VERSION
    supported: ClassVar[dict[str, str]] = {
        "IfcWall": "WALL",
        "IfcDoor": "DOOR",
        "IfcWindow": "WINDOW",
        "IfcColumn": "COLUMN",
        "IfcStair": "STAIR",
        "IfcStairFlight": "STAIR",
        "IfcSpace": "ROOM",
    }

    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        *,
        render_size: int = 1536,
        margin_ratio: float = 0.04,
        line_width: int = 2,
        slice_height: float = 1.2,
        projection_mode: str = "FOOTPRINT",
    ) -> None:
        self._session = session
        self._storage = storage
        self._size = render_size
        self._margin = margin_ratio
        self._line_width = line_width
        self._slice_height = slice_height
        self._projection_mode = projection_mode

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
        storeys = list(model.by_type("IfcBuildingStorey"))
        rendered = reused = annotations = invalid = 0
        class_counts: Counter[str] = Counter()
        for storey_index, storey in enumerate(storeys):
            level_id = _ifc_id(storey)
            existing = self._session.scalar(
                select(Ifc2DRenderORM).where(
                    Ifc2DRenderORM.document_version_id == document_version_id,
                    Ifc2DRenderORM.level_external_id == level_id,
                    Ifc2DRenderORM.renderer_version == self.renderer_version,
                    Ifc2DRenderORM.render_style == style,
                )
            )
            if existing:
                reused += 1
                annotations += existing.valid_geometry_count
                continue
            features: list[dict[str, Any]] = []
            seen: set[str] = set()
            for ifc_class, class_name in self.supported.items():
                for element in model.by_type(ifc_class):
                    global_id = _ifc_id(element)
                    if global_id in seen:
                        continue
                    container = ifcopenshell.util.element.get_container(
                        element, should_get_direct=False, ifc_class="IfcBuildingStorey"
                    )
                    if container is None or _ifc_id(container) != level_id:
                        continue
                    seen.add(global_id)
                    polygon = self._footprint(element, unit_scale=unit_scale)
                    if not _valid_polygon(polygon):
                        invalid += 1
                        continue
                    features.append(
                        {
                            "global_id": global_id,
                            "ifc_class": str(element.is_a()),
                            "class": class_name,
                            "polygon_world_m": polygon,
                            "name": getattr(element, "Name", None),
                            "object_type": getattr(element, "ObjectType", None),
                        }
                    )
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
            rendered += 1
            annotations += count
            class_counts.update(counts)
        return {
            "ifc_levels": len(storeys),
            "levels_rendered": rendered,
            "levels_reused": reused,
            "plan_images": rendered,
            "annotations": annotations,
            "invalid_geometry": invalid,
            "class_distribution": dict(class_counts),
            "source_tier": "BIM_DERIVED_SILVER",
            "renderer_version": self.renderer_version,
        }

    @staticmethod
    def _footprint(element: Any, *, unit_scale: float) -> list[list[float]]:
        try:
            settings = ifcopenshell.geom.settings()
            settings.set(settings.USE_WORLD_COORDS, True)
            shape: Any = ifcopenshell.geom.create_shape(settings, element)
            vertices = list(shape.geometry.verts)
            points = [
                (float(vertices[i]) * unit_scale, float(vertices[i + 1]) * unit_scale)
                for i in range(0, len(vertices), 3)
            ]
            return [[x, y] for x, y in _convex_hull(points)]
        except (RuntimeError, ValueError, TypeError, AttributeError):
            return []

    def _persist_render(
        self,
        *,
        source_id: str,
        package: ExternalProjectPackageORM,
        source_version: DocumentVersionORM,
        storey: Any,
        storey_index: int,
        features: list[dict[str, Any]],
        style: str,
    ) -> tuple[int, Counter[str]]:
        bounds = _bounds([item["polygon_world_m"] for item in features])
        normalized = [
            {
                **item,
                "polygon": _normalize_polygon(item["polygon_world_m"], bounds, self._margin),
            }
            for item in features
        ]
        for item in normalized:
            item["bbox"] = _bbox(item["polygon"])
        image = _render_ifc_features(
            normalized, size=self._size, style=style, line_width=self._line_width
        )
        image_buffer = io.BytesIO()
        image.save(image_buffer, "PNG", optimize=True)
        image_bytes = image_buffer.getvalue()
        level_id = _ifc_id(storey)
        render_config = {
            "slice_height": self._slice_height,
            "projection_mode": self._projection_mode,
            "render_size": self._size,
            "margin_ratio": self._margin,
            "line_width": self._line_width,
            "style": style,
            "world_bounds_m": bounds,
        }
        annotation_payload = {
            "renderer_version": self.renderer_version,
            "source_document_version_id": source_version.id,
            "storey_global_id": level_id,
            "projection": render_config,
            "annotations": normalized,
        }
        annotation_bytes = json.dumps(annotation_payload, sort_keys=True).encode()
        family = f"IFC:{source_version.sha256}"
        style_key = style.replace("_", "-")
        version_key = "v2" if self.renderer_version == "IFC_HORIZONTAL_SLICE_V2" else "v1"
        base = (
            f"training-derivatives/ifc2d/{source_version.sha256}/"
            f"{level_id}/{version_key}/{style_key}"
        )
        image_key = f"{base}.png"
        annotation_key = f"{base}.json"
        image_checksum = hashlib.sha256(image_bytes).hexdigest()
        annotation_checksum = hashlib.sha256(annotation_bytes).hexdigest()
        self._storage.save(storage_key=image_key, source=io.BytesIO(image_bytes))
        self._storage.save(storage_key=annotation_key, source=io.BytesIO(annotation_bytes))
        render = Ifc2DRenderORM(
            source_id=source_id,
            project_package_id=package.id,
            project_id=package.project_id,
            document_version_id=source_version.id,
            level_external_id=level_id,
            level_name=str(getattr(storey, "Name", None) or f"Storey {storey_index}"),
            level_elevation=(
                float(storey.Elevation) if getattr(storey, "Elevation", None) is not None else None
            ),
            project_family_id=family,
            render_style=style,
            renderer_version=self.renderer_version,
            projection_json=render_config,
            image_storage_key=image_key,
            annotation_storage_key=annotation_key,
            image_checksum=image_checksum,
            annotation_checksum=annotation_checksum,
            width=self._size,
            height=self._size,
            element_count=len(features),
            valid_geometry_count=len(normalized),
            quality_score=95,
            status="COMPLETED",
        )
        self._session.add(render)
        self._session.flush()
        render_version = self._session.scalar(
            select(DocumentVersionORM).where(
                DocumentVersionORM.project_id == package.project_id,
                DocumentVersionORM.sha256 == image_checksum,
            )
        )
        if render_version is None:
            document = ProjectDocumentORM(
                project_id=package.project_id,
                original_name=(
                    f"{source_version.sha256[:12]}-{level_id}-{version_key}-{style}.png"
                )[:255],
            )
            self._session.add(document)
            self._session.flush()
            render_version = DocumentVersionORM(
                id=new_id(),
                document_id=document.id,
                project_id=package.project_id,
                version=1,
                sha256=image_checksum,
                size_bytes=len(image_bytes),
                media_type="image/png",
                storage_key=image_key,
                page_count=1,
                status="READY",
            )
            self._session.add(render_version)
            self._session.flush()
        sheet = self._session.scalar(
            select(SheetORM).where(
                SheetORM.document_version_id == render_version.id,
                SheetORM.page_number == 1,
            )
        )
        if sheet is None:
            sheet = SheetORM(
                document_version_id=render_version.id,
                page_number=1,
                title=render.level_name,
                sheet_number=f"IFC-{storey_index:03d}",
                sheet_name=f"IFC 2D {render.level_name}",
                discipline="ARCHITECTURAL",
                drawing_type="PLAN",
                page_type="VECTOR",
                review_status="VERIFIED",
                width_points=self._size,
                height_points=self._size,
            )
            self._session.add(sheet)
            self._session.flush()
        profile = f"IFC_2D_{style.upper()}"
        derivative = self._session.scalar(
            select(TrainingImageDerivativeORM).where(
                TrainingImageDerivativeORM.document_version_id == render_version.id,
                TrainingImageDerivativeORM.page_number == 1,
                TrainingImageDerivativeORM.render_profile == profile,
            )
        )
        if derivative is None:
            derivative = self._session.scalar(
                select(TrainingImageDerivativeORM).where(
                    TrainingImageDerivativeORM.storage_key == render_version.storage_key
                )
            )
        if derivative is None:
            derivative = TrainingImageDerivativeORM(
                project_id=package.project_id,
                document_version_id=render_version.id,
                sheet_id=sheet.id,
                page_number=1,
                render_profile=profile,
                render_version=self.renderer_version,
                width=self._size,
                height=self._size,
                dpi=144,
                storage_key=render_version.storage_key,
                checksum_sha256=image_checksum,
                perceptual_hash=image_checksum[:16],
                rights_status=package.rights_status,
                anonymization_status="COMPLETED",
            )
            self._session.add(derivative)
            self._session.flush()
        counts: Counter[str] = Counter()
        for item in normalized:
            geometry_hash = hashlib.sha256(
                json.dumps(item["polygon_world_m"], separators=(",", ":")).encode()
            ).hexdigest()
            annotation = VisionAnnotationORM(
                project_id=package.project_id,
                document_version_id=render_version.id,
                sheet_id=sheet.id,
                image_derivative_id=derivative.id,
                task_type=f"{item['class']}_DETECTION",
                class_name=item["class"],
                geometry_type="POLYGON",
                geometry_normalized={
                    "polygon": item["polygon"],
                    "bbox": item["bbox"],
                    "world_polygon_m": item["polygon_world_m"],
                    "ifc_class": item["ifc_class"],
                    "storey_global_id": level_id,
                    "geometry_extraction_mode": item.get(
                        "geometry_extraction_mode", "FOOTPRINT_FALLBACK"
                    ),
                    "host_wall_global_id": item.get("host_wall_global_id"),
                    "opening_global_id": item.get("opening_global_id"),
                    "opening_direction": item.get("opening_direction"),
                },
                original_width=self._size,
                original_height=self._size,
                source_type="BIM_DERIVED_SILVER",
                annotation_source="IFC_DERIVED",
                source_element_id=item["global_id"],
                confidence=1.0,
                review_status="VERIFIED",
                reviewer="ifc-2d-deterministic-renderer",
                training_allowed=True,
                anonymized=True,
                rights_status=package.rights_status,
                quality_score=95,
            )
            self._session.add(annotation)
            self._session.flush()
            self._session.add(
                Ifc2DAnnotationLineageORM(
                    annotation_id=annotation.id,
                    render_id=render.id,
                    source_document_version_id=source_version.id,
                    ifc_global_id=item["global_id"],
                    ifc_class=item["ifc_class"],
                    storey_external_id=level_id,
                    source_geometry_hash=geometry_hash,
                    projection_parameters_json=render_config,
                    renderer_version=self.renderer_version,
                    quality_tier="BIM_DERIVED_SILVER",
                )
            )
            counts[item["class"]] += 1
        self._session.flush()
        return sum(counts.values()), counts


def _geometry_parts(value: Any) -> list[list[list[float]]]:
    if value is None:
        return []
    if isinstance(value, dict):
        geometry_type = value.get("type")
        coordinates = value.get("coordinates")
        if geometry_type == "Polygon" and isinstance(coordinates, list) and coordinates:
            return [_coordinate_list(coordinates[0])]
        if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
            return [_coordinate_list(item[0]) for item in coordinates if item]
    geometries = getattr(value, "geoms", None)
    if geometries is not None:
        output: list[list[list[float]]] = []
        for item in geometries:
            output.extend(_geometry_parts(item))
        return output
    exterior = getattr(value, "exterior", None)
    if exterior is not None:
        return [_coordinate_list(list(exterior.coords))]
    coords = getattr(value, "coords", None)
    if coords is not None:
        points = _coordinate_list(list(coords))
        if len(points) >= 2:
            width = 0.02 * max(1.0, math.dist(points[0], points[-1]))
            x1, y1 = points[0]
            x2, y2 = points[-1]
            return [
                [
                    [x1 - width, y1 - width],
                    [x2 + width, y2 - width],
                    [x2 + width, y2 + width],
                    [x1 - width, y1 + width],
                ]
            ]
    if isinstance(value, list) and value and isinstance(value[0], (list, tuple)):
        return [_coordinate_list(value)]
    return []


def _coordinate_list(value: Any) -> list[list[float]]:
    return [[float(item[0]), float(item[1])] for item in value]


def _signed_area(polygon: list[list[float]]) -> float:
    return 0.5 * sum(
        polygon[index][0] * polygon[(index + 1) % len(polygon)][1]
        - polygon[(index + 1) % len(polygon)][0] * polygon[index][1]
        for index in range(len(polygon))
    )


def _valid_polygon(polygon: list[list[float]]) -> bool:
    if len(polygon) < 3 or any(not math.isfinite(value) for point in polygon for value in point):
        return False
    if abs(_signed_area(polygon)) <= 1e-9:
        return False
    segments = [
        (polygon[index], polygon[(index + 1) % len(polygon)]) for index in range(len(polygon))
    ]
    for index, first in enumerate(segments):
        for other_index, second in enumerate(segments[index + 1 :], start=index + 1):
            adjacent = other_index in {index, index + 1}
            closes_ring = index == 0 and other_index == len(segments) - 1
            if adjacent or closes_ring:
                continue
            if _segments_intersect(first[0], first[1], second[0], second[1]):
                return False
    return True


def _segments_intersect(a: list[float], b: list[float], c: list[float], d: list[float]) -> bool:
    def cross(p: list[float], q: list[float], r: list[float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0


def _bounds(polygons: list[list[list[float]]]) -> list[float]:
    points = [point for polygon in polygons for point in polygon]
    if not points:
        raise ValueError("empty geometry")
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _normalize_polygon(
    polygon: list[list[float]], bounds: list[float], margin: float = 0.02
) -> list[list[float]]:
    x1, y1, x2, y2 = bounds
    width = max(x2 - x1, 1e-9)
    height = max(y2 - y1, 1e-9)
    scale = (1 - 2 * margin) / max(width, height)
    x_pad = (1 - width * scale) / 2
    y_pad = (1 - height * scale) / 2
    return [
        [round(x_pad + (x - x1) * scale, 8), round(y_pad + (y - y1) * scale, 8)] for x, y in polygon
    ]


def _bbox(polygon: list[list[float]]) -> list[float]:
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return [min(xs), min(ys), max(xs), max(ys)]


def _render_polygons(items: list[dict[str, Any]], *, size: int) -> Image.Image:
    image = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(image)
    colors = {"ROOM": 235, "WALL": 25, "DOOR": 100, "WINDOW": 160}
    for item in sorted(items, key=lambda row: (row["class"] != "ROOM", row["class"])):
        points = [
            (round(x * (size - 1)), round((1 - y) * (size - 1))) for x, y in item["normalized"]
        ]
        draw.polygon(points, fill=colors.get(item["class"], 200), outline=0)
    return image.convert("RGB")


def _render_ifc_features(
    items: list[dict[str, Any]], *, size: int, style: str, line_width: int
) -> Image.Image:
    background = 255
    image = Image.new("L", (size, size), background)
    draw = ImageDraw.Draw(image)
    palette = {
        "WALL": 25,
        "ROOM": 238,
        "DOOR": 105,
        "WINDOW": 155,
        "COLUMN": 55,
        "STAIR": 185,
    }
    width = max(1, line_width + (1 if style == "thick_lines" else 0))

    def order(row: dict[str, Any]) -> tuple[bool, str, str]:
        return row["class"] != "ROOM", str(row["class"]), str(row["global_id"])

    for item in sorted(items, key=order):
        points = [(round(x * (size - 1)), round((1 - y) * (size - 1))) for x, y in item["polygon"]]
        fill = palette.get(item["class"], 210)
        if style == "thin_lines":
            fill = min(245, fill + 25)
        draw.polygon(points, fill=fill, outline=0, width=width)
    return image.convert("RGB")


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) < 3:
        return []

    def cross(
        origin: tuple[float, float], first: tuple[float, float], second: tuple[float, float]
    ) -> float:
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
            second[0] - origin[0]
        )

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _ifc_id(entity: Any) -> str:
    return str(getattr(entity, "GlobalId", None) or f"ifc-id:{entity.id()}")


def load_resplan_from_storage(storage: DocumentStorage, storage_key: str) -> list[dict[str, Any]]:
    return ResPlanAnnotationImporter.load_archive(storage.path_for_read(storage_key=storage_key))


def curated_gni_ifc_archive(
    url: str, *, limit: int = 20, timeout: float = 120, max_download_bytes: int = 1024**3
) -> Iterable[bytes]:
    """Uses HTTP ranges so the 20-file pilot does not download the complete 503 MB archive."""

    source_stream = _HttpRangeReader(url, timeout=timeout, max_bytes=max_download_bytes)
    output = io.BytesIO()
    with (
        zipfile.ZipFile(source_stream) as source,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as destination,
    ):
        names = sorted(
            item.filename
            for item in source.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".ifc")
        )[:limit]
        if not names:
            raise RealDataRuleError("GNI_IFC_FILES_MISSING")
        for name in names:
            with source.open(name) as input_stream, destination.open(name, "w") as output_stream:
                while chunk := input_stream.read(4 * 1024 * 1024):
                    output_stream.write(chunk)
    yield output.getvalue()


class _HttpRangeReader(io.RawIOBase):
    def __init__(self, url: str, *, timeout: float, max_bytes: int) -> None:
        super().__init__()
        self._url = url
        self._timeout = timeout
        request = Request(url, method="HEAD", headers={"User-Agent": "GURSAR-AI/1"})
        with urlopen(request, timeout=timeout) as response:
            self._size = int(response.headers.get("Content-Length", "0"))
        if self._size <= 0 or self._size > max_bytes:
            raise RealDataRuleError("GNI_ARCHIVE_SIZE_INVALID")
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._position + offset
        elif whence == io.SEEK_END:
            position = self._size + offset
        else:
            raise ValueError("invalid whence")
        self._position = max(0, min(self._size, position))
        return self._position

    def read(self, size: int = -1) -> bytes:
        if self._position >= self._size:
            return b""
        end = self._size - 1 if size < 0 else min(self._size - 1, self._position + size - 1)
        request = Request(
            self._url,
            headers={
                "User-Agent": "GURSAR-AI/1",
                "Range": f"bytes={self._position}-{end}",
            },
        )
        with urlopen(request, timeout=self._timeout) as response:
            data = response.read()
        self._position += len(data)
        return data


def source_license_snapshot(session: Session, source_id: str) -> dict[str, Any]:
    assessment = session.scalar(
        select(LicenseAssessmentORM)
        .where(LicenseAssessmentORM.source_id == source_id)
        .order_by(LicenseAssessmentORM.checked_at.desc())
    )
    lineage = UpstreamLicenseLineageService(session).latest(source_id)
    return {
        "assessment_id": assessment.id if assessment else None,
        "rights_status": assessment.rights_status if assessment else "LICENSE_UNKNOWN",
        "lineage_id": lineage.id if lineage else None,
        "effective_license": lineage.effective_license if lineage else "UNKNOWN",
        "training_allowed": bool(lineage and lineage.training_allowed),
        "conflict_code": lineage.conflict_code if lineage else "UPSTREAM_LINEAGE_MISSING",
    }
