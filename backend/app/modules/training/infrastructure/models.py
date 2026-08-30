from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class TrainingProjectManifestORM(Base):
    __tablename__ = "training_project_manifests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    rights_status: Mapped[str] = mapped_column(String(40), index=True)
    training_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    building_use: Mapped[str | None] = mapped_column(String(120))
    gross_area: Mapped[float | None] = mapped_column(Float)
    footprint_area: Mapped[float | None] = mapped_column(Float)
    floor_count: Mapped[int | None] = mapped_column(Integer)
    basement_count: Mapped[int | None] = mapped_column(Integer)
    location_region: Mapped[str | None] = mapped_column(String(160))
    structural_system: Mapped[str | None] = mapped_column(String(160))
    foundation_type: Mapped[str | None] = mapped_column(String(160))
    soil_info: Mapped[str | None] = mapped_column(Text)
    architectural_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    structural_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    mep_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    geotechnical_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    boq_available: Mapped[bool] = mapped_column(Boolean, default=False)
    final_account_available: Mapped[bool] = mapped_column(Boolean, default=False)
    as_built_available: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_quantity_available: Mapped[bool] = mapped_column(Boolean, default=False)
    data_completeness_score: Mapped[int] = mapped_column(Integer, default=0)
    anonymization_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    eligibility_override: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AnonymizedDerivativeORM(Base):
    __tablename__ = "anonymized_derivatives"
    __table_args__ = (
        UniqueConstraint(
            "source_document_version_id",
            "anonymization_version",
            name="uq_anonymized_source_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    anonymized_document_id: Mapped[str] = mapped_column(String(36), default=new_id, unique=True)
    anonymization_version: Mapped[str] = mapped_column(String(40))
    anonymized_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="COMPLETED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TrainingSampleORM(Base):
    __tablename__ = "training_samples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), index=True
    )
    source_sheet_id: Mapped[str | None] = mapped_column(
        ForeignKey("sheets.id", ondelete="SET NULL")
    )
    source_view_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("view_regions.id", ondelete="SET NULL")
    )
    task_type: Mapped[str] = mapped_column(String(50), index=True)
    input_reference: Mapped[str] = mapped_column(String(500))
    annotation_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_prediction: Mapped[dict[str, Any]] = mapped_column(JSON)
    verified_output: Mapped[dict[str, Any]] = mapped_column(JSON)
    review_revision: Mapped[int] = mapped_column(Integer)
    reviewer: Mapped[str] = mapped_column(String(200))
    rights_status: Mapped[str] = mapped_column(String(40))
    anonymization_status: Mapped[str] = mapped_column(String(30))
    dataset_version_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ActiveLearningItemORM(Base):
    __tablename__ = "active_learning_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_reference: Mapped[str] = mapped_column(String(500), index=True)
    prediction: Mapped[dict[str, Any]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    priority: Mapped[int] = mapped_column(Integer, index=True)
    reason: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="OPEN", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetVersionORM(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_name", "version", name="uq_dataset_name_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_name: Mapped[str] = mapped_column(String(160), index=True)
    version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    project_count: Mapped[int] = mapped_column(Integer, default=0)
    sheet_count: Mapped[int] = mapped_column(Integer, default=0)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    class_distribution_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rights_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    task_types_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL")
    )
    checksum: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DatasetMembershipORM(Base):
    __tablename__ = "dataset_memberships"
    __table_args__ = (
        UniqueConstraint("dataset_version_id", "sample_id", name="uq_dataset_sample"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True
    )
    sample_id: Mapped[str] = mapped_column(
        ForeignKey("training_samples.id", ondelete="RESTRICT"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    dataset_split: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
