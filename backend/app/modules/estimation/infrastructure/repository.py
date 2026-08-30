from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.persistence import new_id
from app.modules.estimation.domain.models import (
    BuildingUse,
    CalibrationVersion,
    CompletedProjectRecord,
    FoundationType,
    SoilCondition,
    StructuralIntensity,
    StructuralSystem,
)
from app.modules.estimation.infrastructure.models import (
    CompletedProjectRecordORM,
    EstimationModelVersionORM,
    EstimationRunORM,
)


class EstimationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        return self._session

    def add_completed_project(self, record: CompletedProjectRecord) -> CompletedProjectRecord:
        row = CompletedProjectRecordORM(
            id=record.id or new_id(),
            project_id=record.project_id,
            project_type=record.project_type.value,
            location=record.location,
            footprint_area_m2=record.footprint_area_m2,
            gross_area_m2=record.gross_area_m2,
            floor_count=record.floor_count,
            basement_count=record.basement_count,
            structural_system=record.structural_system.value,
            structural_intensity=(
                record.structural_intensity.value if record.structural_intensity else None
            ),
            average_span_m=record.average_span_m,
            foundation_type=record.foundation_type.value,
            soil_condition=record.soil_condition.value,
            seismic_parameters=record.seismic_parameters,
            actual_quantities={key: str(value) for key, value in record.actual_quantities.items()},
            estimated_quantities={
                key: str(value) for key, value in record.estimated_quantities.items()
            },
            source=record.source,
            verified=record.verified,
            verification_level=record.verification_level,
            outlier_flags=record.outlier_flags,
        )
        self._session.add(row)
        self._session.flush()
        return self._project(row)

    def list_completed_projects(
        self, *, verified_only: bool = False
    ) -> list[CompletedProjectRecord]:
        statement = select(CompletedProjectRecordORM).order_by(
            CompletedProjectRecordORM.created_at,
            CompletedProjectRecordORM.id,
        )
        if verified_only:
            statement = statement.where(CompletedProjectRecordORM.verified.is_(True))
        return [self._project(row) for row in self._session.scalars(statement)]

    def update_outlier_flags(self, record_id: str, flags: dict[str, bool]) -> None:
        self._session.execute(
            update(CompletedProjectRecordORM)
            .where(CompletedProjectRecordORM.id == record_id)
            .values(outlier_flags=flags)
        )

    def create_calibration(self, calibration: CalibrationVersion) -> CalibrationVersion:
        row = EstimationModelVersionORM(
            id=calibration.id,
            estimation_model_version=calibration.estimation_model_version,
            coefficient_registry_version=calibration.coefficient_registry_version,
            training_dataset_version=calibration.training_dataset_version,
            status=calibration.status,
            coefficients=calibration.coefficients,
            statistics=calibration.statistics,
            calibration_timestamp=calibration.calibration_timestamp,
            activated_at=calibration.activated_at,
        )
        self._session.add(row)
        self._session.flush()
        return self._calibration(row)

    def list_calibrations(self) -> list[CalibrationVersion]:
        rows = self._session.scalars(
            select(EstimationModelVersionORM).order_by(
                EstimationModelVersionORM.calibration_timestamp,
                EstimationModelVersionORM.id,
            )
        )
        return [self._calibration(row) for row in rows]

    def get_calibration(self, calibration_id: str) -> CalibrationVersion | None:
        row = self._session.get(EstimationModelVersionORM, calibration_id)
        return self._calibration(row) if row is not None else None

    def activate_calibration(self, calibration_id: str, activated_at: datetime) -> None:
        self._session.execute(
            update(EstimationModelVersionORM)
            .where(EstimationModelVersionORM.status == "ACTIVE")
            .values(status="SUPERSEDED")
        )
        self._session.execute(
            update(EstimationModelVersionORM)
            .where(EstimationModelVersionORM.id == calibration_id)
            .values(status="ACTIVE", activated_at=activated_at)
        )

    def active_calibration(self) -> CalibrationVersion | None:
        row = self._session.scalar(
            select(EstimationModelVersionORM)
            .where(EstimationModelVersionORM.status == "ACTIVE")
            .order_by(EstimationModelVersionORM.activated_at.desc())
            .limit(1)
        )
        return self._calibration(row) if row is not None else None

    def save_run(
        self,
        *,
        request_id: str,
        conversation_id: str | None,
        input_payload: dict[str, Any],
        result_payload: dict[str, Any],
        assumptions: list[str],
        estimation_model_version: str,
        coefficient_registry_version: str,
        training_dataset_version: str,
    ) -> None:
        self._session.add(
            EstimationRunORM(
                request_id=request_id,
                conversation_id=conversation_id,
                input_payload=input_payload,
                result_payload=result_payload,
                assumptions=assumptions,
                estimation_model_version=estimation_model_version,
                coefficient_registry_version=coefficient_registry_version,
                training_dataset_version=training_dataset_version,
            )
        )

    @staticmethod
    def _project(row: CompletedProjectRecordORM) -> CompletedProjectRecord:
        return CompletedProjectRecord(
            id=row.id,
            project_id=row.project_id,
            project_type=BuildingUse(row.project_type),
            location=row.location,
            footprint_area_m2=(
                Decimal(str(row.footprint_area_m2)) if row.footprint_area_m2 is not None else None
            ),
            gross_area_m2=Decimal(str(row.gross_area_m2)),
            floor_count=row.floor_count,
            basement_count=row.basement_count,
            structural_system=StructuralSystem(row.structural_system),
            structural_intensity=(
                StructuralIntensity(row.structural_intensity)
                if row.structural_intensity is not None
                else None
            ),
            average_span_m=(
                Decimal(str(row.average_span_m)) if row.average_span_m is not None else None
            ),
            foundation_type=FoundationType(row.foundation_type),
            soil_condition=SoilCondition(row.soil_condition),
            seismic_parameters=dict(row.seismic_parameters),
            actual_quantities={key: Decimal(value) for key, value in row.actual_quantities.items()},
            estimated_quantities={
                key: Decimal(value) for key, value in row.estimated_quantities.items()
            },
            source=row.source,
            verified=row.verified,
            verification_level=row.verification_level,
            outlier_flags=dict(row.outlier_flags),
            created_at=row.created_at,
        )

    @staticmethod
    def _calibration(row: EstimationModelVersionORM) -> CalibrationVersion:
        return CalibrationVersion(
            id=row.id,
            estimation_model_version=row.estimation_model_version,
            coefficient_registry_version=row.coefficient_registry_version,
            training_dataset_version=row.training_dataset_version,
            status=row.status,
            coefficients=dict(row.coefficients),
            statistics=dict(row.statistics),
            calibration_timestamp=row.calibration_timestamp,
            activated_at=row.activated_at,
        )
