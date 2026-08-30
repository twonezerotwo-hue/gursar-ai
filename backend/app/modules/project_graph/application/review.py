from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.persistence import append_audit, append_outbox, utc_now
from app.core.security import ReviewPrincipal
from app.modules.documents.infrastructure.models import DocumentVersionORM
from app.modules.project_graph.domain.models import (
    MeasurementType,
    ReviewAction,
    ReviewStatus,
    calculate_measurement,
)
from app.modules.project_graph.infrastructure.models import (
    BuildingElementORM,
    MeasurementEvidenceORM,
    MeasurementORM,
    ReviewDecisionORM,
    TakeoffItemORM,
)


class ReviewNotFoundError(LookupError):
    pass


class InvalidReviewError(ValueError):
    pass


class ExpertReviewService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def review_element(
        self,
        *,
        element_id: str,
        action: ReviewAction,
        principal: ReviewPrincipal,
        reason: str | None,
        correction: dict[str, Any] | None,
    ) -> ReviewDecisionORM:
        source = self._session.get(BuildingElementORM, element_id)
        if source is None:
            raise ReviewNotFoundError(element_id)
        self._ensure_reviewable("BUILDING_ELEMENT", source.id)
        original = self._snapshot(source)
        resulting_id = source.id
        target_revision = source.revision
        corrected_snapshot = None
        if action is ReviewAction.CORRECT:
            if not reason or not correction:
                raise InvalidReviewError("CORRECT için reason ve correction gerekli")
            target_revision = source.revision + 1
            values = self._element_values(source)
            allowed = {
                "element_type",
                "subtype",
                "name",
                "geometry_type",
                "geometry_json",
                "length",
                "width",
                "height",
                "thickness",
                "area",
                "volume",
                "material",
                "properties_json",
                "source_coordinates",
            }
            unknown = set(correction) - allowed
            if unknown:
                raise InvalidReviewError(f"Desteklenmeyen düzeltme alanları: {sorted(unknown)}")
            values.update(correction)
            values.update(
                revision=target_revision,
                supersedes_element_id=source.id,
                review_status=ReviewStatus.CORRECTED.value,
                reviewer=principal.reviewer_id,
                reviewed_at=utc_now(),
                source_external_id=self._revision_external_id(source, target_revision),
            )
            result = BuildingElementORM(**values)
            self._session.add(result)
            self._session.flush()
            resulting_id = result.id
            corrected_snapshot = self._snapshot(result)
            if source.source_type == "DXF":
                from app.modules.training.application.service import TrainingPipelineService

                TrainingPipelineService(self._session).mark_reviewed(
                    f"DXF:{source.source_document_id}:{source.source_external_id}"
                )
            self._record_human_correction(
                project_id=source.project_id,
                source_reference=f"BUILDING_ELEMENT:{source.id}",
                prediction=original,
                confidence=float(source.confidence),
            )
        else:
            source.review_status = (
                ReviewStatus.VERIFIED.value
                if action is ReviewAction.ACCEPT
                else ReviewStatus.REJECTED.value
            )
            source.reviewer = principal.reviewer_id
            source.reviewed_at = utc_now()
        decision = self._decision(
            project_id=source.project_id,
            subject_type="BUILDING_ELEMENT",
            subject_id=source.id,
            action=action,
            from_revision=source.revision,
            to_revision=target_revision,
            resulting_subject_id=resulting_id,
            principal=principal,
            reason=reason,
            original=original,
            corrected=corrected_snapshot,
        )
        return decision

    def review_measurement(
        self,
        *,
        measurement_id: str,
        action: ReviewAction,
        principal: ReviewPrincipal,
        reason: str | None,
        correction: dict[str, Any] | None,
    ) -> ReviewDecisionORM:
        source = self._session.get(MeasurementORM, measurement_id)
        if source is None:
            raise ReviewNotFoundError(measurement_id)
        self._ensure_reviewable("MEASUREMENT", source.id)
        original = self._snapshot(source)
        resulting_id = source.id
        target_revision = source.revision
        corrected_snapshot = None
        reviewed_measurement = source
        if action is ReviewAction.CORRECT:
            if not reason or not correction:
                raise InvalidReviewError("CORRECT için reason ve correction gerekli")
            target_revision = source.revision + 1
            geometry = correction.get("geometry_json", source.geometry_json)
            measurement_type = MeasurementType(
                correction.get("measurement_type", source.measurement_type)
            )
            calculated = correction.get("calculated_value")
            raw = correction.get("raw_value")
            if calculated is None:
                scale = self._coordinate_scale(source)
                calculated = calculate_measurement(
                    measurement_type, geometry, coordinate_scale=scale
                )
            if raw is None:
                raw = calculate_measurement(measurement_type, geometry, coordinate_scale=1.0)
            result = MeasurementORM(
                project_id=source.project_id,
                element_id=source.element_id,
                measurement_type=measurement_type.value,
                measurement_purpose=source.measurement_purpose,
                geometry_json=geometry,
                raw_value=Decimal(str(raw)),
                unit=correction.get("unit", source.unit),
                scale_calibration_id=source.scale_calibration_id,
                calculated_value=Decimal(str(calculated)),
                source_document_id=source.source_document_id,
                source_sheet_id=source.source_sheet_id,
                source_view_region_id=source.source_view_region_id,
                method="EXPERT_CORRECTION",
                algorithm_version=source.algorithm_version,
                confidence=Decimal("1"),
                review_status=ReviewStatus.CORRECTED.value,
                revision=target_revision,
                supersedes_measurement_id=source.id,
            )
            self._session.add(result)
            self._session.flush()
            reviewed_measurement = result
            evidence = self._session.scalar(
                select(MeasurementEvidenceORM).where(
                    MeasurementEvidenceORM.measurement_id == source.id
                )
            )
            self._session.add(
                MeasurementEvidenceORM(
                    measurement_id=result.id,
                    source_coordinates=evidence.source_coordinates if evidence else {},
                    geometry_json=geometry,
                    page_number=evidence.page_number if evidence else None,
                    view_region_id=evidence.view_region_id if evidence else None,
                    scale_json=evidence.scale_json if evidence else {},
                    thumbnail_reference=evidence.thumbnail_reference if evidence else None,
                    algorithm="EXPERT_CORRECTION",
                    model_version=source.algorithm_version,
                    review_status=ReviewStatus.CORRECTED.value,
                )
            )
            resulting_id = result.id
            corrected_snapshot = self._snapshot(result)
            self._record_human_correction(
                project_id=source.project_id,
                source_reference=f"MEASUREMENT:{source.id}",
                prediction=original,
                confidence=float(source.confidence),
            )
        else:
            source.review_status = (
                ReviewStatus.VERIFIED.value
                if action is ReviewAction.ACCEPT
                else ReviewStatus.REJECTED.value
            )
        if action in {ReviewAction.ACCEPT, ReviewAction.CORRECT}:
            self._create_vector_pdf_takeoff(reviewed_measurement)
        return self._decision(
            project_id=source.project_id,
            subject_type="MEASUREMENT",
            subject_id=source.id,
            action=action,
            from_revision=source.revision,
            to_revision=target_revision,
            resulting_subject_id=resulting_id,
            principal=principal,
            reason=reason,
            original=original,
            corrected=corrected_snapshot,
        )

    def history(self, *, subject_type: str, subject_id: str) -> list[ReviewDecisionORM]:
        return list(
            self._session.scalars(
                select(ReviewDecisionORM)
                .where(
                    ReviewDecisionORM.subject_type == subject_type,
                    ReviewDecisionORM.subject_id == subject_id,
                )
                .order_by(ReviewDecisionORM.created_at, ReviewDecisionORM.id)
            ).all()
        )

    def _ensure_reviewable(self, subject_type: str, subject_id: str) -> None:
        corrected = self._session.scalar(
            select(ReviewDecisionORM.id).where(
                ReviewDecisionORM.subject_type == subject_type,
                ReviewDecisionORM.subject_id == subject_id,
                ReviewDecisionORM.action == ReviewAction.CORRECT.value,
            )
        )
        if corrected:
            raise InvalidReviewError("Superseded revision yeniden incelenemez")

    def _decision(self, **values: Any) -> ReviewDecisionORM:
        action: ReviewAction = values["action"]
        principal: ReviewPrincipal = values.pop("principal")
        values["original_snapshot_json"] = values.pop("original")
        values["corrected_snapshot_json"] = values.pop("corrected")
        values["action"] = action.value
        values["reviewer"] = principal.reviewer_id
        values["reviewer_role"] = principal.role
        row = ReviewDecisionORM(**values)
        self._session.add(row)
        self._session.flush()
        event = f"{values['subject_type']}_{action.value}ED"
        append_audit(
            self._session,
            action=event,
            aggregate_id=values["project_id"],
            actor_type=principal.role,
            actor_id=principal.reviewer_id,
            detail={
                "event": event,
                "subject_id": values["subject_id"],
                "resulting_subject_id": values["resulting_subject_id"],
                "from_revision": values["from_revision"],
                "to_revision": values["to_revision"],
                "reviewer": principal.reviewer_id,
                "reason": values["reason"],
            },
        )
        append_outbox(
            self._session,
            event_type="project.review.completed.v1",
            aggregate_id=values["project_id"],
            payload={"decision_id": row.id, "action": action.value},
        )
        return row

    @staticmethod
    def _snapshot(row: Any) -> dict[str, Any]:
        return jsonable_encoder(
            {column.name: getattr(row, column.name) for column in row.__table__.columns}
        )

    @staticmethod
    def _element_values(source: BuildingElementORM) -> dict[str, Any]:
        excluded = {"id", "created_at", "revision", "supersedes_element_id"}
        return {
            column.name: getattr(source, column.name)
            for column in source.__table__.columns
            if column.name not in excluded
        }

    @staticmethod
    def _revision_external_id(source: BuildingElementORM, revision: int) -> str | None:
        if not source.source_external_id:
            return None
        root = source.source_external_id.split("#revision-")[0]
        return f"{root}#revision-{revision}"

    @staticmethod
    def _coordinate_scale(source: MeasurementORM) -> float:
        if source.raw_value == 0:
            return 1.0
        ratio = float(source.calculated_value / source.raw_value)
        dimension = {"LENGTH": 1, "AREA": 2, "VOLUME": 3, "COUNT": 1}[source.measurement_type]
        return ratio ** (1 / dimension)

    def _record_human_correction(
        self,
        *,
        project_id: str,
        source_reference: str,
        prediction: dict[str, Any],
        confidence: float,
    ) -> None:
        from app.modules.training.application.service import TrainingPipelineService

        training = TrainingPipelineService(self._session)
        training.mark_reviewed(source_reference)
        training.enqueue_active_learning(
            project_id=project_id,
            source_reference=source_reference,
            prediction=prediction,
            confidence=confidence,
            reason="HUMAN_CORRECTION",
        )

    def _create_vector_pdf_takeoff(self, measurement: MeasurementORM) -> None:
        if measurement.measurement_purpose != "ENGINEERING_QUANTITY":
            return
        if not measurement.source_document_id:
            return
        version = self._session.get(DocumentVersionORM, measurement.source_document_id)
        if version is None or version.media_type != "application/pdf":
            return
        existing = self._session.scalars(
            select(TakeoffItemORM).where(TakeoffItemORM.project_id == measurement.project_id)
        ).all()
        if any(measurement.id in row.measurement_ids for row in existing):
            return
        self._session.add(
            TakeoffItemORM(
                project_id=measurement.project_id,
                service_code=f"vector_pdf.verified.{measurement.measurement_type.lower()}",
                description=f"Verified vector PDF {measurement.measurement_type.lower()}",
                quantity=measurement.calculated_value,
                unit=measurement.unit,
                measurement_ids=[measurement.id],
                calculation_rule="EXPERT_VERIFIED_VECTOR_PDF_MEASUREMENT",
                calculation_rule_version=measurement.algorithm_version,
                quantity_source="VERIFIED_VECTOR_PDF",
                review_status=ReviewStatus.VERIFIED.value,
                revision=1,
            )
        )
