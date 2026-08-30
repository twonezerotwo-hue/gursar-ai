from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections import Counter, defaultdict
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol, cast

import pdfplumber
from PIL import Image, ImageDraw
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit, utc_now
from app.core.security import ReviewPrincipal
from app.modules.documents.domain.ports import DocumentStorage
from app.modules.documents.infrastructure.models import DocumentVersionORM, SheetORM
from app.modules.jobs.application.service import DatabaseJobQueueProvider
from app.modules.jobs.infrastructure.models import AnalysisJobORM
from app.modules.projects.infrastructure.models import ProjectORM
from app.modules.training.application.service import (
    TrainingNotFoundError,
    TrainingPipelineService,
    TrainingRuleError,
    training_rules,
)
from app.modules.training.infrastructure.models import (
    AnonymizedDerivativeORM,
    DatasetVersionORM,
    TrainingProjectManifestORM,
)
from app.modules.vision.infrastructure.models import (
    ProjectArchiveImportORM,
    TrainingImageDerivativeORM,
    VisionAnnotationORM,
    VisionDatasetExportORM,
    VisionDatasetItemORM,
    VisionModelVersionORM,
    VisionPredictionORM,
)

VISION_CLASSES = {"WALL", "DOOR", "WINDOW", "COLUMN", "ROOM", "STAIR"}
HELPER_CLASSES = {"TEXT", "DIMENSION", "TITLE_BLOCK"}
REGION_CLASSES = {"PLAN_REGION", "SECTION_REGION", "ELEVATION_REGION", "DETAIL_REGION"}
STRUCTURAL_V2_CLASSES = {"COLUMN", "SHEAR_WALL", "BEAM", "SLAB", "AXIS", "FOUNDATION"}
TASK_TYPES = {
    "SHEET_CLASSIFICATION",
    "TITLE_BLOCK_DETECTION",
    "VIEW_REGION_DETECTION",
    "ROOM_DETECTION",
    "WALL_DETECTION",
    "DOOR_DETECTION",
    "WINDOW_DETECTION",
    "COLUMN_DETECTION",
    "STAIR_DETECTION",
    "ELEMENT_DETECTION",
    "ELEMENT_SEGMENTATION",
}
GEOMETRY_TYPES = {"BBOX", "POLYGON", "POLYLINE", "POINT", "MASK"}
VERIFIED_STATUSES = {"VERIFIED", "CORRECTED"}
TRAINING_IMAGE_VERSION = "TRAINING_RENDER_V1"


class VisionRuleError(ValueError):
    pass


class VisionNotFoundError(LookupError):
    pass


class VisionModelProvider(Protocol):
    provider_name: str

    def train(self, dataset: DatasetVersionORM, config: dict[str, Any]) -> dict[str, Any]: ...

    def evaluate(self, model: VisionModelVersionORM) -> dict[str, Any]: ...

    def predict(self, image_path: Path, model: VisionModelVersionORM) -> list[dict[str, Any]]: ...


class DatasetNotReadyProvider:
    provider_name = "dataset-readiness-gate"

    def train(self, dataset: DatasetVersionORM, config: dict[str, Any]) -> dict[str, Any]:
        del dataset, config
        raise VisionRuleError("DATASET_NOT_READY")

    def evaluate(self, model: VisionModelVersionORM) -> dict[str, Any]:
        del model
        raise VisionRuleError("MODEL_NOT_TRAINED")


class DatasetLeakageValidator:
    def __init__(self, session: Session) -> None:
        self._session = session

    def validate(self, items: list[VisionDatasetItemORM]) -> dict[str, Any]:
        leaks: list[dict[str, Any]] = []
        project_splits: dict[str, set[str]] = defaultdict(set)
        hash_splits: dict[str, set[str]] = defaultdict(set)
        revision_splits: dict[str, set[str]] = defaultdict(set)
        crop_splits: dict[str, set[str]] = defaultdict(set)
        for item in items:
            project_splits[item.project_id].add(item.dataset_split)
            annotation = self._session.get(VisionAnnotationORM, item.annotation_id)
            image = self._session.get(TrainingImageDerivativeORM, item.image_derivative_id)
            if annotation:
                if annotation.source_type == "GOLDEN_BENCHMARK":
                    leaks.append(
                        {
                            "type": "GOLDEN_BENCHMARK",
                            "key": annotation.id,
                            "splits": [item.dataset_split],
                        }
                    )
                version = self._session.get(DocumentVersionORM, annotation.document_version_id)
                if version:
                    hash_splits[version.sha256].add(item.dataset_split)
                    revision_splits[version.document_id].add(item.dataset_split)
            if image:
                crop_splits[image.checksum_sha256].add(item.dataset_split)
        for leak_type, mapping in (
            ("PROJECT", project_splits),
            ("DOCUMENT_HASH", hash_splits),
            ("REVISION_FAMILY", revision_splits),
            ("DUPLICATE_CROP", crop_splits),
        ):
            leaks.extend(
                {"type": leak_type, "key": key, "splits": sorted(splits)}
                for key, splits in mapping.items()
                if len(splits) > 1
            )
        return {"valid": not leaks, "leaks": leaks}

    def predict(self, image_path: Path, model: VisionModelVersionORM) -> list[dict[str, Any]]:
        del image_path, model
        raise VisionRuleError("MODEL_NOT_TRAINED")


class VisionDatasetService:
    def __init__(self, session: Session, storage: DocumentStorage) -> None:
        self._session = session
        self._storage = storage
        self._rules = training_rules()

    def render_training_image(
        self,
        *,
        project_id: str,
        document_version_id: str,
        page_number: int,
        dpi: int,
        redact_title_block: bool,
    ) -> tuple[TrainingImageDerivativeORM, bool]:
        version = self._document(project_id, document_version_id)
        manifest = self._eligible_manifest(project_id)
        derivative = self._anonymized_derivative(document_version_id)
        sheet = self._session.scalar(
            select(SheetORM).where(
                SheetORM.document_version_id == document_version_id,
                SheetORM.page_number == page_number,
            )
        )
        if sheet is None:
            raise VisionNotFoundError("Sheet not found")
        profile = f"{TRAINING_IMAGE_VERSION}:{dpi}:redact={int(redact_title_block)}"
        existing = self._session.scalar(
            select(TrainingImageDerivativeORM).where(
                TrainingImageDerivativeORM.document_version_id == document_version_id,
                TrainingImageDerivativeORM.page_number == page_number,
                TrainingImageDerivativeORM.render_profile == profile,
            )
        )
        if existing:
            return existing, True
        path = self._storage.path_for_read(storage_key=version.storage_key)
        with pdfplumber.open(path) as pdf:
            if page_number < 1 or page_number > len(pdf.pages):
                raise VisionRuleError("Invalid page")
            image = pdf.pages[page_number - 1].to_image(resolution=dpi).original.convert("RGB")
        redaction_version = None
        if redact_title_block:
            title = (sheet.metadata_json or {}).get("title_block", {})
            bbox = title.get("bbox")
            if bbox and len(bbox) == 4:
                draw = ImageDraw.Draw(image)
                draw.rectangle(
                    (
                        int(bbox[0] * image.width),
                        int(bbox[1] * image.height),
                        int(bbox[2] * image.width),
                        int(bbox[3] * image.height),
                    ),
                    fill="black",
                )
                redaction_version = "TITLE_BLOCK_REDACTION_V1"
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        content = output.getvalue()
        checksum = hashlib.sha256(content).hexdigest()
        storage_key = f"training-images/{checksum[:2]}/{checksum}.png"
        self._storage.save(storage_key=storage_key, source=io.BytesIO(content))
        row = TrainingImageDerivativeORM(
            project_id=project_id,
            document_version_id=document_version_id,
            sheet_id=sheet.id,
            page_number=page_number,
            render_profile=profile,
            render_version=TRAINING_IMAGE_VERSION,
            width=image.width,
            height=image.height,
            dpi=dpi,
            storage_key=storage_key,
            checksum_sha256=checksum,
            perceptual_hash=self._perceptual_hash(image),
            rights_status=manifest.rights_status,
            anonymization_status=derivative.status,
            redaction_version=redaction_version,
        )
        self._session.add(row)
        self._session.flush()
        return row, False

    def create_annotation(
        self,
        *,
        project_id: str,
        document_version_id: str,
        sheet_id: str,
        view_region_id: str | None,
        image_derivative_id: str | None,
        task_type: str,
        class_name: str,
        geometry_type: str,
        geometry_normalized: dict[str, Any],
        source_type: str,
        source_element_id: str | None,
        confidence: float,
        review_status: str,
        principal: ReviewPrincipal | None,
    ) -> VisionAnnotationORM:
        self._document(project_id, document_version_id)
        sheet = self._session.get(SheetORM, sheet_id)
        if sheet is None or sheet.document_version_id != document_version_id:
            raise VisionRuleError("Sheet does not belong to document")
        if task_type not in TASK_TYPES or geometry_type not in GEOMETRY_TYPES:
            raise VisionRuleError("Unsupported annotation task or geometry")
        supported_classes = VISION_CLASSES | HELPER_CLASSES | REGION_CLASSES | STRUCTURAL_V2_CLASSES
        if class_name not in supported_classes:
            raise VisionRuleError("Unsupported class")
        self._validate_geometry(geometry_normalized)
        automated = source_type in {"VISION_MODEL", "AUTO_REGISTRATION", "HEURISTIC"}
        if automated and review_status in VERIFIED_STATUSES:
            raise VisionRuleError("AUTO_ANNOTATION_CANDIDATE cannot be ground truth")
        if review_status in VERIFIED_STATUSES and principal is None:
            raise VisionRuleError("Verified annotation requires reviewer")
        image = (
            self._session.get(TrainingImageDerivativeORM, image_derivative_id)
            if image_derivative_id
            else None
        )
        width = image.width if image else int(float(sheet.width_points))
        height = image.height if image else int(float(sheet.height_points))
        manifest = self._session.scalar(
            select(TrainingProjectManifestORM).where(
                TrainingProjectManifestORM.project_id == project_id
            )
        )
        anonymized = bool(image and image.anonymization_status == "COMPLETED")
        annotation_source = {
            "EXPERT_MANUAL_ANNOTATION": "MANUAL",
            "EXPERT_CORRECTION": "REVIEW_CORRECTION",
            "VECTOR_PDF": "VECTOR_DERIVED",
            "IFC": "IFC_DERIVED",
            "VISION_MODEL": "VERIFIED_MODEL_PREDICTION",
        }.get(source_type, source_type)
        quality_score = self._quality_score(
            review_status=review_status,
            source_type=source_type,
            geometry=geometry_normalized,
            rights_status=manifest.rights_status if manifest else "UNKNOWN",
            anonymized=anonymized,
        )
        row = VisionAnnotationORM(
            project_id=project_id,
            document_version_id=document_version_id,
            sheet_id=sheet_id,
            view_region_id=view_region_id,
            image_derivative_id=image_derivative_id,
            task_type=task_type,
            class_name=class_name,
            geometry_type=geometry_type,
            geometry_normalized=geometry_normalized,
            original_width=width,
            original_height=height,
            source_type=source_type,
            annotation_source=annotation_source,
            source_element_id=source_element_id,
            confidence=confidence,
            review_status=review_status,
            reviewer=principal.reviewer_id if principal else None,
            revision=1,
            training_allowed=review_status in VERIFIED_STATUSES and not automated,
            anonymized=anonymized,
            rights_status=manifest.rights_status if manifest else "UNKNOWN",
            quality_score=quality_score,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def review_annotation(
        self,
        *,
        annotation_id: str,
        action: str,
        principal: ReviewPrincipal,
        correction: dict[str, Any] | None,
    ) -> VisionAnnotationORM:
        source = self._session.get(VisionAnnotationORM, annotation_id)
        if source is None:
            raise VisionNotFoundError(annotation_id)
        if action == "ACCEPT":
            source.review_status = "VERIFIED"
            source.reviewer = principal.reviewer_id
            source.training_allowed = True
            if source.source_type == "CROSS_MODAL_AUTO_ANNOTATION":
                source.source_type = "GURSAR_EXPERT_VERIFIED"
                source.annotation_source = "EXPERT_ACCEPTED_CROSS_MODAL"
                source.confidence = 1.0
                source.quality_score = 100
            result = source
        elif action == "REJECT":
            source.review_status = "REJECTED"
            source.reviewer = principal.reviewer_id
            source.training_allowed = False
            result = source
        elif action == "CORRECT":
            if not correction:
                raise VisionRuleError("Correction required")
            geometry = correction.get("geometry_normalized", source.geometry_normalized)
            self._validate_geometry(geometry)
            result = VisionAnnotationORM(
                project_id=source.project_id,
                document_version_id=source.document_version_id,
                sheet_id=source.sheet_id,
                view_region_id=source.view_region_id,
                image_derivative_id=source.image_derivative_id,
                task_type=source.task_type,
                class_name=correction.get("class_name", source.class_name),
                geometry_type=correction.get("geometry_type", source.geometry_type),
                geometry_normalized=geometry,
                original_width=source.original_width,
                original_height=source.original_height,
                source_type="EXPERT_CORRECTION",
                annotation_source="REVIEW_CORRECTION",
                source_element_id=source.source_element_id,
                confidence=1.0,
                review_status="CORRECTED",
                reviewer=principal.reviewer_id,
                revision=source.revision + 1,
                previous_annotation_id=source.id,
                training_allowed=True,
                anonymized=source.anonymized,
                rights_status=source.rights_status,
                quality_score=min(100, source.quality_score + 10),
            )
            source.review_status = "SUPERSEDED"
            source.training_allowed = False
            self._session.add(result)
            self._session.flush()
            training = TrainingPipelineService(self._session)
            training.mark_reviewed(f"VISION_ANNOTATION:{source.id}")
            training.enqueue_active_learning(
                project_id=source.project_id,
                source_reference=f"VISION_ANNOTATION:{source.id}",
                prediction={
                    "class_name": source.class_name,
                    "geometry": source.geometry_normalized,
                },
                confidence=source.confidence,
                reason="HUMAN_CORRECTION",
            )
        else:
            raise VisionRuleError("Invalid review action")
        append_audit(
            self._session,
            action=f"VISION_ANNOTATION_{action}",
            aggregate_id=source.id,
            actor_type=principal.role,
            actor_id=principal.reviewer_id,
            detail={"result_id": result.id, "revision": result.revision},
        )
        if result.training_allowed and result.anonymized:
            with suppress(TrainingRuleError, TrainingNotFoundError):
                TrainingPipelineService(self._session).create_sample(
                    project_id=result.project_id,
                    subject_type="VISION_ANNOTATION",
                    subject_id=result.id,
                    task_type=result.task_type,
                    annotation={
                        "class_name": result.class_name,
                        "geometry_type": result.geometry_type,
                        "geometry_normalized": result.geometry_normalized,
                        "factory_version": "VISION_DATASET_FACTORY_V1",
                    },
                )
        self._session.flush()
        return result

    def build_dataset(self, *, dataset_id: str) -> dict[str, Any]:
        dataset = self._session.get(DatasetVersionORM, dataset_id)
        if dataset is None:
            raise VisionNotFoundError(dataset_id)
        if dataset.status != "DRAFT":
            raise VisionRuleError("Frozen dataset cannot change")
        annotations = self._session.scalars(
            select(VisionAnnotationORM).where(
                VisionAnnotationORM.review_status.in_(VERIFIED_STATUSES),
                VisionAnnotationORM.training_allowed.is_(True),
                VisionAnnotationORM.anonymized.is_(True),
                VisionAnnotationORM.rights_status.in_(self._rules["allowed_rights"]),
                VisionAnnotationORM.image_derivative_id.is_not(None),
            )
        ).all()
        added = 0
        for annotation in annotations:
            manifest = self._manifest(annotation.project_id)
            if not self._manifest_training_eligible(manifest):
                continue
            exists = self._session.scalar(
                select(VisionDatasetItemORM).where(
                    VisionDatasetItemORM.dataset_version_id == dataset.id,
                    VisionDatasetItemORM.annotation_id == annotation.id,
                )
            )
            if exists:
                continue
            self._session.add(
                VisionDatasetItemORM(
                    dataset_version_id=dataset.id,
                    annotation_id=annotation.id,
                    image_derivative_id=annotation.image_derivative_id,
                    project_id=annotation.project_id,
                    dataset_split=self._project_split(annotation.project_id),
                )
            )
            added += 1
        self._session.flush()
        return {"dataset_id": dataset.id, "added": added, **self.quality_report(dataset.id)}

    def freeze_vision_dataset(self, dataset_id: str) -> DatasetVersionORM:
        dataset = self._session.get(DatasetVersionORM, dataset_id)
        if dataset is None:
            raise VisionNotFoundError(dataset_id)
        if dataset.status != "DRAFT":
            raise VisionRuleError("Only DRAFT dataset can be frozen")
        report = self.quality_report(dataset_id)
        items = self._items(dataset_id)
        leakage = DatasetLeakageValidator(self._session).validate(items)
        if not leakage["valid"]:
            raise VisionRuleError("DATASET_LEAKAGE_DETECTED")
        if float(report["synthetic_ratio"]) > float(
            self._rules["vision_dataset"]["maximum_synthetic_ratio"]
        ):
            raise VisionRuleError("SYNTHETIC_RATIO_EXCEEDED")
        immutable = sorted(
            (item.annotation_id, item.image_derivative_id, item.dataset_split) for item in items
        )
        dataset.project_count = report["project_count"]
        dataset.sheet_count = report["sheet_count"]
        dataset.sample_count = report["annotation_count"]
        dataset.class_distribution_json = report["class_distribution"]
        dataset.rights_summary_json = report["rights_summary"]
        dataset.task_types_json = report["task_distribution"]
        dataset.checksum = hashlib.sha256(json.dumps(immutable).encode()).hexdigest()
        dataset.status = "FROZEN"
        self._session.flush()
        return dataset

    def quality_report(self, dataset_id: str) -> dict[str, Any]:
        items = self._items(dataset_id)
        annotations = [self._session.get(VisionAnnotationORM, item.annotation_id) for item in items]
        valid = [row for row in annotations if row is not None]
        images = {item.image_derivative_id for item in items}
        projects = {item.project_id for item in items}
        sheets = {row.sheet_id for row in valid}
        splits = Counter(item.dataset_split for item in items)
        classes = Counter(row.class_name for row in valid)
        image_rows = [
            row
            for image_id in images
            if (row := self._session.get(TrainingImageDerivativeORM, image_id)) is not None
        ]
        rights = Counter(row.rights_status for row in image_rows)
        source_distribution = Counter(row.source_type for row in valid)
        synthetic_count = source_distribution.get("SYNTHETIC_GROUND_TRUTH", 0)
        synthetic_ratio = round(synthetic_count / len(valid), 4) if valid else 0
        source_dominance = max(source_distribution.values(), default=0) / max(1, len(valid))
        corrected = sum(row.review_status == "CORRECTED" for row in valid)
        rare_threshold = int(self._rules["vision_dataset"]["rare_class_threshold"])
        return {
            "project_count": len(projects),
            "sheet_count": len(sheets),
            "image_count": len(images),
            "annotation_count": len(valid),
            "class_distribution": dict(classes),
            "task_distribution": dict(Counter(row.task_type for row in valid)),
            "verified_ratio": round((len(valid) - corrected) / len(valid), 4) if valid else 0,
            "corrected_ratio": round(corrected / len(valid), 4) if valid else 0,
            "rights_summary": dict(rights),
            "source_distribution": dict(source_distribution),
            "synthetic_ratio": synthetic_ratio,
            "source_balance_warnings": (
                ["SINGLE_SOURCE_DOMINANCE"] if source_dominance > 0.70 else []
            ),
            "split_project_counts": {
                key: len({item.project_id for item in items if item.dataset_split == key})
                for key in ("TRAIN", "VALIDATION", "TEST")
            },
            "item_split_counts": dict(splits),
            "rare_classes": {
                name: count for name, count in classes.items() if count < rare_threshold
            },
            "leakage": DatasetLeakageValidator(self._session).validate(items),
        }

    def enqueue_export(
        self, *, dataset_id: str, project_id: str, export_format: str = "COCO"
    ) -> tuple[VisionDatasetExportORM, AnalysisJobORM, bool]:
        dataset = self._session.get(DatasetVersionORM, dataset_id)
        if dataset is None or dataset.status != "FROZEN":
            raise VisionRuleError("Frozen dataset required")
        if export_format not in {"COCO", "GURSAR_NATIVE_JSON"}:
            raise VisionRuleError("Unsupported export format")
        job, reused = DatabaseJobQueueProvider(self._session).enqueue(
            job_type="DATASET_EXPORT",
            project_id=project_id,
            document_version_id=None,
            operation_version=f"{export_format}_V1:{dataset.id}:{dataset.checksum}",
            payload={"dataset_id": dataset.id, "format": export_format},
        )
        export = self._session.scalar(
            select(VisionDatasetExportORM).where(VisionDatasetExportORM.job_id == job.id)
        )
        if export is None:
            export = VisionDatasetExportORM(
                dataset_version_id=dataset.id,
                job_id=job.id,
                export_format=export_format,
                status="QUEUED",
            )
            self._session.add(export)
            self._session.flush()
        return export, job, reused

    def execute_coco_export(self, job: AnalysisJobORM) -> dict[str, Any]:
        dataset_id = str(job.payload_json["dataset_id"])
        export_format = str(job.payload_json.get("format", "COCO"))
        dataset = self._session.get(DatasetVersionORM, dataset_id)
        if dataset is None or dataset.status != "FROZEN":
            raise VisionRuleError("Frozen dataset required")
        items = self._items(dataset_id)
        annotations = [self._session.get(VisionAnnotationORM, item.annotation_id) for item in items]
        valid = [row for row in annotations if row is not None]
        class_names = sorted({row.class_name for row in valid})
        categories = {name: index + 1 for index, name in enumerate(class_names)}
        image_ids: dict[str, int] = {}
        images: list[dict[str, Any]] = []
        coco_annotations: list[dict[str, Any]] = []
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for item, annotation in zip(items, valid, strict=False):
                image = self._session.get(TrainingImageDerivativeORM, item.image_derivative_id)
                if image is None:
                    continue
                if image.id not in image_ids:
                    image_ids[image.id] = len(image_ids) + 1
                    images.append(
                        {
                            "id": image_ids[image.id],
                            "file_name": f"images/{image.checksum_sha256}.png",
                            "width": image.width,
                            "height": image.height,
                            "project_id": image.project_id,
                            "document_version_id": image.document_version_id,
                            "sheet_id": image.sheet_id,
                            "render_checksum": image.checksum_sha256,
                            "split": item.dataset_split.lower(),
                        }
                    )
                    target.writestr(
                        f"images/{image.checksum_sha256}.png",
                        self._storage.path_for_read(storage_key=image.storage_key).read_bytes(),
                    )
                coco_annotations.append(
                    self._coco_annotation(
                        annotation, len(coco_annotations) + 1, image_ids[image.id], categories
                    )
                )
            payload = {
                "images": images,
                "annotations": coco_annotations,
                "categories": [
                    {"id": category_id, "name": name} for name, category_id in categories.items()
                ],
            }
            manifest = {
                "dataset_id": dataset.id,
                "dataset_checksum": dataset.checksum,
                "format": export_format,
                "schema_version": "VISION_DATASET_FACTORY_V1",
                "quality": self.quality_report(dataset.id),
            }
            if export_format == "COCO":
                target.writestr(
                    "annotations.json", json.dumps(payload, ensure_ascii=False, indent=2)
                )
            else:
                native = [
                    {
                        "annotation_id": row.id,
                        "project_id": row.project_id,
                        "document_version_id": row.document_version_id,
                        "sheet_id": row.sheet_id,
                        "task_type": row.task_type,
                        "class_name": row.class_name,
                        "geometry_type": row.geometry_type,
                        "geometry_normalized": row.geometry_normalized,
                        "quality_score": row.quality_score,
                        "rights_status": row.rights_status,
                    }
                    for row in valid
                ]
                target.writestr("samples.json", json.dumps(native, ensure_ascii=False, indent=2))
            target.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        content = archive.getvalue()
        checksum = hashlib.sha256(content).hexdigest()
        storage_key = f"dataset-exports/{dataset.id}/{checksum}.zip"
        self._storage.save(storage_key=storage_key, source=io.BytesIO(content))
        export = self._session.scalar(
            select(VisionDatasetExportORM).where(VisionDatasetExportORM.job_id == job.id)
        )
        if export:
            export.storage_key = storage_key
            export.checksum_sha256 = checksum
            export.manifest_json = manifest
            export.status = "COMPLETED"
        self._session.flush()
        return {"export_id": export.id if export else None, "checksum": checksum, **manifest}

    def readiness(self) -> dict[str, Any]:
        eligible_projects = (
            self._session.scalar(
                select(func.count())
                .select_from(TrainingProjectManifestORM)
                .where(
                    TrainingProjectManifestORM.rights_status.in_(self._rules["allowed_rights"]),
                    TrainingProjectManifestORM.training_allowed.is_(True),
                    TrainingProjectManifestORM.anonymization_status == "COMPLETED",
                )
            )
            or 0
        )
        verified_annotations = (
            self._session.scalar(
                select(func.count())
                .select_from(VisionAnnotationORM)
                .where(
                    VisionAnnotationORM.review_status.in_(VERIFIED_STATUSES),
                    VisionAnnotationORM.training_allowed.is_(True),
                )
            )
            or 0
        )
        verified_sheets = (
            self._session.scalar(
                select(func.count())
                .select_from(SheetORM)
                .where(SheetORM.review_status.in_(VERIFIED_STATUSES))
            )
            or 0
        )
        class_distribution: dict[str, int] = {}
        class_rows = self._session.execute(
            select(VisionAnnotationORM.class_name, func.count())
            .where(VisionAnnotationORM.review_status.in_(VERIFIED_STATUSES))
            .group_by(VisionAnnotationORM.class_name)
        ).all()
        for class_name, count in class_rows:
            class_distribution[str(class_name)] = int(count)
        task_counts = {
            "sheet_classifier": {"projects": eligible_projects, "samples": verified_sheets},
            "title_block": {
                "projects": eligible_projects,
                "samples": class_distribution.get("TITLE_BLOCK", 0),
            },
            "architectural_elements": {
                "projects": eligible_projects,
                "sheets": len(
                    {
                        row[0]
                        for row in self._session.execute(
                            select(VisionAnnotationORM.sheet_id).where(
                                VisionAnnotationORM.class_name.in_(VISION_CLASSES),
                                VisionAnnotationORM.review_status.in_(VERIFIED_STATUSES),
                            )
                        )
                    }
                ),
                "annotations": sum(class_distribution.get(name, 0) for name in VISION_CLASSES),
            },
        }
        statuses: dict[str, str] = {}
        for task, counts in task_counts.items():
            thresholds = self._rules["vision_readiness"][task]
            ready = all(counts.get(key, 0) >= int(value) for key, value in thresholds.items())
            statuses[task] = (
                "READY_FOR_TRAINING"
                if ready
                else ("COLLECTING" if any(counts.values()) else "NOT_READY")
            )
        return {
            "training_eligible_projects": eligible_projects,
            "verified_sheets": verified_sheets,
            "verified_annotations": verified_annotations,
            "class_distribution": class_distribution,
            "task_counts": task_counts,
            "task_statuses": statuses,
        }

    def request_training(
        self, *, dataset_id: str, model_name: str, version: str, task: str
    ) -> dict[str, Any]:
        readiness = self.readiness()
        status = readiness["task_statuses"].get(task, "NOT_READY")
        if status != "READY_FOR_TRAINING":
            return {"status": "DATASET_NOT_READY", "model_id": None, "readiness": readiness}
        dataset = self._session.get(DatasetVersionORM, dataset_id)
        if dataset is None or dataset.status != "FROZEN":
            raise VisionRuleError("Frozen dataset required")
        raise VisionRuleError("VISION_PROVIDER_NOT_CONFIGURED")

    def create_archive_preview(
        self, *, project_id: str, manifest: dict[str, Any], document_version_ids: list[str]
    ) -> ProjectArchiveImportORM:
        if self._session.get(ProjectORM, project_id) is None:
            raise VisionNotFoundError(project_id)
        valid_ids: list[str] = []
        errors: list[str] = []
        for document_id in document_version_ids:
            version = self._session.get(DocumentVersionORM, document_id)
            if version is None or version.project_id != project_id:
                errors.append(document_id)
            else:
                valid_ids.append(document_id)
        payload = json.dumps({"manifest": manifest, "documents": sorted(valid_ids)}, sort_keys=True)
        row = ProjectArchiveImportORM(
            project_id=project_id,
            manifest_json=manifest,
            source_document_ids_json=valid_ids,
            package_checksum=hashlib.sha256(payload.encode()).hexdigest(),
            validation_json={"valid": not errors and bool(valid_ids), "invalid_documents": errors},
            status="VALIDATED" if not errors and valid_ids else "PREVIEW",
        )
        self._session.add(row)
        self._session.flush()
        return row

    def approve_archive(
        self, archive_id: str, principal: ReviewPrincipal
    ) -> ProjectArchiveImportORM:
        row = self._session.get(ProjectArchiveImportORM, archive_id)
        if row is None:
            raise VisionNotFoundError(archive_id)
        if not row.validation_json.get("valid"):
            raise VisionRuleError("Archive validation failed")
        row.status = "IMPORTED"
        row.approved_by = principal.reviewer_id
        row.imported_at = utc_now()
        append_audit(
            self._session,
            action="PROJECT_ARCHIVE_IMPORTED",
            aggregate_id=row.id,
            actor_type=principal.role,
            actor_id=principal.reviewer_id,
            detail={"checksum": row.package_checksum, "documents": row.source_document_ids_json},
        )
        self._session.flush()
        return row

    def create_shadow_prediction(
        self,
        *,
        project_id: str,
        document_version_id: str,
        sheet_id: str,
        model_version_id: str,
        class_name: str,
        confidence: float,
        geometry: dict[str, Any],
        structured_class: str | None,
    ) -> VisionPredictionORM:
        model = self._session.get(VisionModelVersionORM, model_version_id)
        if model is None or model.status != "SHADOW":
            raise VisionRuleError("Only SHADOW model can produce shadow predictions")
        self._validate_geometry(geometry)
        row = VisionPredictionORM(
            project_id=project_id,
            document_version_id=document_version_id,
            sheet_id=sheet_id,
            model_version_id=model.id,
            class_name=class_name,
            confidence=confidence,
            raw_model_confidence=confidence,
            calibrated_confidence=None,
            geometry_normalized=geometry,
            review_status="PREDICTED",
        )
        self._session.add(row)
        self._session.flush()
        training = TrainingPipelineService(self._session)
        threshold = float(self._rules["active_learning"]["low_confidence_threshold"])
        if confidence < threshold:
            training.enqueue_active_learning(
                project_id=project_id,
                source_reference=f"VISION_PREDICTION:{row.id}",
                prediction={"class_name": class_name, "geometry": geometry},
                confidence=confidence,
                reason="LOW_CONFIDENCE",
            )
        if structured_class and structured_class != class_name:
            training.enqueue_active_learning(
                project_id=project_id,
                source_reference=f"VISION_PREDICTION:{row.id}",
                prediction={"vision": class_name, "structured_verified": structured_class},
                confidence=confidence,
                reason="MODEL_DISAGREEMENT",
            )
        return row

    @staticmethod
    def _quality_score(
        *,
        review_status: str,
        source_type: str,
        geometry: dict[str, Any],
        rights_status: str,
        anonymized: bool,
    ) -> int:
        score = 0
        if review_status in VERIFIED_STATUSES:
            score += 40
        if source_type in {"EXPERT_MANUAL_ANNOTATION", "EXPERT_CORRECTION"}:
            score += 25
        elif source_type in {"VECTOR_PDF", "DXF", "IFC"}:
            score += 15
        if geometry:
            score += 15
        if rights_status in {"OPEN_LICENSED", "INTERNAL_PROJECT", "LICENSED_BY_GURSAR"}:
            score += 10
        if anonymized:
            score += 10
        return min(score, 100)

    def _items(self, dataset_id: str) -> list[VisionDatasetItemORM]:
        return list(
            self._session.scalars(
                select(VisionDatasetItemORM).where(
                    VisionDatasetItemORM.dataset_version_id == dataset_id
                )
            ).all()
        )

    def _document(self, project_id: str, document_version_id: str) -> DocumentVersionORM:
        row = self._session.get(DocumentVersionORM, document_version_id)
        if row is None or row.project_id != project_id or row.media_type != "application/pdf":
            raise VisionNotFoundError(document_version_id)
        return row

    def _manifest(self, project_id: str) -> TrainingProjectManifestORM:
        row = self._session.scalar(
            select(TrainingProjectManifestORM).where(
                TrainingProjectManifestORM.project_id == project_id
            )
        )
        if row is None:
            raise VisionRuleError("Training manifest required")
        return row

    def _eligible_manifest(self, project_id: str) -> TrainingProjectManifestORM:
        row = self._manifest(project_id)
        if not self._manifest_training_eligible(row):
            raise VisionRuleError("Project is not training eligible")
        return row

    def _manifest_training_eligible(self, row: TrainingProjectManifestORM) -> bool:
        return (
            row.rights_status in self._rules["allowed_rights"]
            and row.training_allowed
            and row.anonymization_status == "COMPLETED"
            and (
                row.data_completeness_score >= int(self._rules["minimum_training_completeness"])
                or row.eligibility_override
            )
        )

    def _anonymized_derivative(self, document_version_id: str) -> AnonymizedDerivativeORM:
        row = self._session.scalar(
            select(AnonymizedDerivativeORM).where(
                AnonymizedDerivativeORM.source_document_version_id == document_version_id,
                AnonymizedDerivativeORM.status == "COMPLETED",
            )
        )
        if row is None:
            raise VisionRuleError("Anonymized document derivative required")
        return row

    def _project_split(self, project_id: str) -> str:
        ratios = self._rules["vision_dataset"]["split_ratios"]
        bucket = int(hashlib.sha256(project_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        if bucket < float(ratios["TRAIN"]):
            return "TRAIN"
        if bucket < float(ratios["TRAIN"]) + float(ratios["VALIDATION"]):
            return "VALIDATION"
        return "TEST"

    @staticmethod
    def _validate_geometry(geometry: dict[str, Any]) -> None:
        values: list[float] = []

        def collect(value: Any) -> None:
            if isinstance(value, (int, float)):
                values.append(float(value))
            elif isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item)

        collect(geometry)
        if not values or any(value < 0 or value > 1 for value in values):
            raise VisionRuleError("Annotation coordinates must be normalized to 0..1")

    @staticmethod
    def _perceptual_hash(image: Image.Image) -> str:
        sample = image.convert("L").resize((8, 8))
        pixels = [int(cast(float, sample.getpixel((x, y)))) for y in range(8) for x in range(8)]
        average = sum(pixels) / len(pixels)
        bits = "".join("1" if value >= average else "0" for value in pixels)
        return f"{int(bits, 2):016x}"

    @staticmethod
    def _coco_annotation(
        row: VisionAnnotationORM,
        annotation_id: int,
        image_id: int,
        categories: dict[str, int],
    ) -> dict[str, Any]:
        geometry = row.geometry_normalized
        if row.geometry_type == "BBOX":
            bbox = geometry.get("bbox", geometry.get("coordinates", []))
            x0, y0, x1, y1 = map(float, bbox)
            pixel_bbox = [
                x0 * row.original_width,
                y0 * row.original_height,
                (x1 - x0) * row.original_width,
                (y1 - y0) * row.original_height,
            ]
            segmentation: list[list[float]] = []
            area = pixel_bbox[2] * pixel_bbox[3]
        else:
            points = geometry.get("points", [])
            flattened = [
                coordinate
                for point in points
                for coordinate in (point[0] * row.original_width, point[1] * row.original_height)
            ]
            xs = flattened[0::2]
            ys = flattened[1::2]
            pixel_bbox = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
            segmentation = [flattened] if row.geometry_type in {"POLYGON", "MASK"} else []
            area = pixel_bbox[2] * pixel_bbox[3]
        return {
            "id": annotation_id,
            "image_id": image_id,
            "category_id": categories[row.class_name],
            "bbox": pixel_bbox,
            "area": area,
            "segmentation": segmentation,
            "iscrowd": 0,
            "source_annotation_id": row.id,
            "review_status": row.review_status,
        }
