from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from importlib.resources import files
from typing import Any, ClassVar

import yaml
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit, utc_now
from app.core.security import ReviewPrincipal
from app.modules.documents.infrastructure.models import DocumentVersionORM
from app.modules.project_graph.infrastructure.models import (
    BuildingElementORM,
    MeasurementORM,
    SheetMetadataRevisionORM,
)
from app.modules.projects.infrastructure.models import ProjectORM
from app.modules.training.infrastructure.models import (
    ActiveLearningItemORM,
    AnonymizedDerivativeORM,
    DatasetMembershipORM,
    DatasetVersionORM,
    TrainingProjectManifestORM,
    TrainingSampleORM,
)


class TrainingRuleError(ValueError):
    pass


class TrainingNotFoundError(LookupError):
    pass


def training_rules() -> dict[str, Any]:
    path = files("app.modules.training").joinpath("config/training_data_rules.yaml")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise RuntimeError("training_data_rules.yaml geçersiz")
    return loaded


class TrainingPipelineService:
    sensitive_keys: ClassVar[set[str]] = {
        "customer",
        "customer_name",
        "owner",
        "owner_name",
        "phone",
        "email",
        "tax_number",
        "company_identity",
        "full_address",
        "parcel",
        "parcel_id",
        "personal_identifier",
        "tc_no",
        "tckn",
        "vkn",
    }

    def __init__(self, session: Session) -> None:
        self._session = session
        self._rules = training_rules()

    def upsert_manifest(
        self, *, project_id: str, values: dict[str, Any], principal: ReviewPrincipal
    ) -> TrainingProjectManifestORM:
        if self._session.get(ProjectORM, project_id) is None:
            raise TrainingNotFoundError(project_id)
        row = self._session.scalar(
            select(TrainingProjectManifestORM).where(
                TrainingProjectManifestORM.project_id == project_id
            )
        )
        if row is None:
            row = TrainingProjectManifestORM(project_id=project_id, **values)
            self._session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        row.data_completeness_score = self._completeness(row)
        if values.get("eligibility_override"):
            append_audit(
                self._session,
                action="TRAINING_ELIGIBILITY_OVERRIDDEN",
                aggregate_id=project_id,
                actor_type=principal.role,
                actor_id=principal.reviewer_id,
                detail={"score": row.data_completeness_score},
            )
        self._session.flush()
        return row

    def anonymize(
        self,
        *,
        project_id: str,
        document_version_id: str,
        metadata: dict[str, Any],
        principal: ReviewPrincipal,
    ) -> AnonymizedDerivativeORM:
        version = self._session.get(DocumentVersionORM, document_version_id)
        if version is None or version.project_id != project_id:
            raise TrainingNotFoundError(document_version_id)
        existing = self._session.scalar(
            select(AnonymizedDerivativeORM).where(
                AnonymizedDerivativeORM.source_document_version_id == document_version_id,
                AnonymizedDerivativeORM.anonymization_version == "text-metadata-v1",
            )
        )
        if existing:
            return existing
        derivative = AnonymizedDerivativeORM(
            project_id=project_id,
            source_document_version_id=document_version_id,
            anonymization_version="text-metadata-v1",
            anonymized_metadata_json=self._redact(metadata),
            status="COMPLETED",
        )
        self._session.add(derivative)
        manifest = self._manifest(project_id)
        manifest.anonymization_status = "COMPLETED"
        append_audit(
            self._session,
            action="TRAINING_DERIVATIVE_CREATED",
            aggregate_id=project_id,
            actor_type=principal.role,
            actor_id=principal.reviewer_id,
            detail={"document_version_id": document_version_id, "version": "text-metadata-v1"},
        )
        self._session.flush()
        return derivative

    def create_sample(
        self,
        *,
        project_id: str,
        subject_type: str,
        subject_id: str,
        task_type: str,
        annotation: dict[str, Any],
    ) -> TrainingSampleORM:
        manifest = self._manifest(project_id)
        self._require_eligible(manifest)
        subject = self._subject(project_id, subject_type, subject_id)
        if subject.review_status not in {"VERIFIED", "CORRECTED"}:
            raise TrainingRuleError("Yalnız doğrulanmış veya düzeltilmiş kayıt sample olabilir")
        is_sheet_revision = isinstance(subject, SheetMetadataRevisionORM)
        is_vision_annotation = subject.__class__.__name__ == "VisionAnnotationORM"
        if is_vision_annotation and not subject.training_allowed:
            raise TrainingRuleError("Vision annotation training_allowed değil")
        source_document_id = (
            subject.document_version_id
            if is_sheet_revision or is_vision_annotation
            else subject.source_document_id
        )
        if not source_document_id:
            raise TrainingRuleError("Geçerli source document gerekli")
        derivative = self._session.scalar(
            select(AnonymizedDerivativeORM).where(
                AnonymizedDerivativeORM.source_document_version_id == source_document_id,
                AnonymizedDerivativeORM.status == "COMPLETED",
            )
        )
        if derivative is None:
            raise TrainingRuleError("Anonimleştirilmiş derivative gerekli")
        snapshot = jsonable_encoder(
            {column.name: getattr(subject, column.name) for column in subject.__table__.columns}
        )
        sample = TrainingSampleORM(
            project_id=project_id,
            source_document_id=source_document_id,
            source_sheet_id=(
                subject.sheet_id
                if is_sheet_revision or is_vision_annotation
                else subject.source_sheet_id
            ),
            source_view_region_id=(
                None
                if is_sheet_revision
                else (
                    subject.view_region_id
                    if is_vision_annotation
                    else subject.source_view_region_id
                )
            ),
            task_type=task_type,
            input_reference=f"{subject_type}:{subject_id}",
            annotation_json=annotation,
            source_prediction=snapshot,
            verified_output=(subject.corrected_metadata_json if is_sheet_revision else snapshot),
            review_revision=subject.revision,
            reviewer=getattr(subject, "reviewer", None) or "verified-system",
            rights_status=manifest.rights_status,
            anonymization_status=derivative.status,
        )
        self._session.add(sample)
        self._session.flush()
        return sample

    def enqueue_active_learning(
        self,
        *,
        project_id: str,
        source_reference: str,
        prediction: dict[str, Any],
        confidence: float,
        reason: str,
    ) -> ActiveLearningItemORM:
        cfg = self._rules["active_learning"]
        score = int(
            cfg["base_priority"]
            + (1 - max(0.0, min(1.0, confidence))) * cfg["confidence_weight"]
            + cfg["reason_weights"].get(reason, 0)
        )
        row = ActiveLearningItemORM(
            project_id=project_id,
            source_reference=source_reference,
            prediction=prediction,
            confidence=confidence,
            priority=score,
            reason=reason,
            status="OPEN",
        )
        self._session.add(row)
        self._session.flush()
        return row

    def mark_reviewed(self, source_reference: str) -> None:
        rows = self._session.scalars(
            select(ActiveLearningItemORM).where(
                ActiveLearningItemORM.source_reference == source_reference,
                ActiveLearningItemORM.status.in_(("OPEN", "ASSIGNED")),
            )
        ).all()
        for row in rows:
            row.status = "REVIEWED"
            row.reviewed_at = utc_now()

    def create_dataset(
        self, *, dataset_name: str, version: str, parent_version_id: str | None
    ) -> DatasetVersionORM:
        row = DatasetVersionORM(
            dataset_name=dataset_name,
            version=version,
            parent_version_id=parent_version_id,
            status="DRAFT",
        )
        self._session.add(row)
        self._session.flush()
        return row

    def add_sample(self, *, dataset_id: str, sample_id: str) -> DatasetMembershipORM:
        dataset = self._dataset(dataset_id)
        if dataset.status != "DRAFT":
            raise TrainingRuleError("FROZEN dataset membership değiştirilemez")
        sample = self._session.get(TrainingSampleORM, sample_id)
        if sample is None:
            raise TrainingNotFoundError(sample_id)
        membership = DatasetMembershipORM(
            dataset_version_id=dataset.id,
            sample_id=sample.id,
            project_id=sample.project_id,
            dataset_split=self._project_split(sample.project_id),
        )
        self._session.add(membership)
        self._session.flush()
        return membership

    def freeze_dataset(self, dataset_id: str) -> DatasetVersionORM:
        dataset = self._dataset(dataset_id)
        if dataset.status != "DRAFT":
            raise TrainingRuleError("Yalnız DRAFT dataset dondurulabilir")
        memberships = self._session.scalars(
            select(DatasetMembershipORM).where(
                DatasetMembershipORM.dataset_version_id == dataset.id
            )
        ).all()
        samples = [self._session.get(TrainingSampleORM, item.sample_id) for item in memberships]
        valid_samples = [item for item in samples if item is not None]
        dataset.project_count = len({item.project_id for item in valid_samples})
        dataset.sheet_count = len(
            {item.source_sheet_id for item in valid_samples if item.source_sheet_id}
        )
        dataset.sample_count = len(valid_samples)
        dataset.rights_summary_json = dict(Counter(item.rights_status for item in valid_samples))
        dataset.task_types_json = dict(Counter(item.task_type for item in valid_samples))
        payload = sorted(
            (item.sample_id, item.project_id, item.dataset_split) for item in memberships
        )
        dataset.checksum = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
        dataset.status = "FROZEN"
        self._session.flush()
        return dataset

    def _completeness(self, row: TrainingProjectManifestORM) -> int:
        weights = self._rules["completeness_weights"]
        flags = {
            "architecture": row.architectural_documents,
            "structural": row.structural_documents,
            "geotechnical": row.geotechnical_documents,
            "boq": row.boq_available,
            "final_quantities": row.verified_quantity_available or row.final_account_available,
            "as_built": row.as_built_available,
        }
        return sum(int(weights[key]) for key, present in flags.items() if present)

    def _require_eligible(self, row: TrainingProjectManifestORM) -> None:
        if row.rights_status not in set(self._rules["allowed_rights"]):
            raise TrainingRuleError(f"{row.rights_status} training için uygun değil")
        if not row.training_allowed:
            raise TrainingRuleError("Project training_allowed değil")
        minimum = int(self._rules["minimum_training_completeness"])
        if row.data_completeness_score < minimum and not row.eligibility_override:
            raise TrainingRuleError(f"Completeness en az {minimum} olmalı")
        if row.anonymization_status != "COMPLETED":
            raise TrainingRuleError("Anonymization tamamlanmadı")

    def _manifest(self, project_id: str) -> TrainingProjectManifestORM:
        row = self._session.scalar(
            select(TrainingProjectManifestORM).where(
                TrainingProjectManifestORM.project_id == project_id
            )
        )
        if row is None:
            raise TrainingNotFoundError("Training manifest bulunamadı")
        return row

    def _subject(self, project_id: str, subject_type: str, subject_id: str) -> Any:
        from app.modules.vision.infrastructure.models import VisionAnnotationORM

        model = {
            "BUILDING_ELEMENT": BuildingElementORM,
            "MEASUREMENT": MeasurementORM,
            "SHEET_REVISION": SheetMetadataRevisionORM,
            "VISION_ANNOTATION": VisionAnnotationORM,
        }.get(subject_type)
        if model is None:
            raise TrainingRuleError("Geçersiz subject_type")
        row = self._session.get(model, subject_id)
        if row is None or row.project_id != project_id:
            raise TrainingNotFoundError(subject_id)
        return row

    def _dataset(self, dataset_id: str) -> DatasetVersionORM:
        row = self._session.get(DatasetVersionORM, dataset_id)
        if row is None:
            raise TrainingNotFoundError(dataset_id)
        return row

    def _project_split(self, project_id: str) -> str:
        bucket = int(hashlib.sha256(project_id.encode()).hexdigest()[:8], 16) % 100
        percentages = self._rules["dataset_split_percentages"]
        if bucket < int(percentages["TRAIN"]):
            return "TRAIN"
        if bucket < int(percentages["TRAIN"]) + int(percentages["VALIDATION"]):
            return "VALIDATION"
        return "TEST"

    def _redact(self, value: Any, key: str = "") -> Any:
        if key.lower() in self.sensitive_keys:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {k: self._redact(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, str):
            value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", value)
            return re.sub(r"(?:\+90|0)?\s*5\d{2}(?:\s*\d{3}){2}", "[REDACTED_PHONE]", value)
        return value
