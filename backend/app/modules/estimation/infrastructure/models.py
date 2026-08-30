from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class CompletedProjectRecordORM(Base):
    __tablename__ = "completed_project_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    project_type: Mapped[str] = mapped_column(String(40), index=True)
    location: Mapped[str | None] = mapped_column(String(240))
    footprint_area_m2: Mapped[float | None] = mapped_column(Numeric(18, 4))
    gross_area_m2: Mapped[float] = mapped_column(Numeric(18, 4))
    floor_count: Mapped[int] = mapped_column(Integer)
    basement_count: Mapped[int] = mapped_column(Integer)
    structural_system: Mapped[str] = mapped_column(String(60), index=True)
    structural_intensity: Mapped[str | None] = mapped_column(String(40))
    average_span_m: Mapped[float | None] = mapped_column(Numeric(10, 4))
    foundation_type: Mapped[str] = mapped_column(String(40), index=True)
    soil_condition: Mapped[str] = mapped_column(String(40), index=True)
    seismic_parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    actual_quantities: Mapped[dict[str, str]] = mapped_column(JSON)
    estimated_quantities: Mapped[dict[str, str]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(40))
    verified: Mapped[bool] = mapped_column(Boolean, index=True)
    verification_level: Mapped[str] = mapped_column(String(40))
    outlier_flags: Mapped[dict[str, bool]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EstimationModelVersionORM(Base):
    __tablename__ = "estimation_model_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    estimation_model_version: Mapped[str] = mapped_column(String(80), unique=True)
    coefficient_registry_version: Mapped[str] = mapped_column(String(80))
    training_dataset_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), index=True)
    coefficients: Mapped[dict[str, Any]] = mapped_column(JSON)
    statistics: Mapped[dict[str, Any]] = mapped_column(JSON)
    calibration_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EstimationRunORM(Base):
    __tablename__ = "estimation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    assumptions: Mapped[list[str]] = mapped_column(JSON)
    estimation_model_version: Mapped[str] = mapped_column(String(80))
    coefficient_registry_version: Mapped[str] = mapped_column(String(80))
    training_dataset_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
