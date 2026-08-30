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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class TrainingImageDerivativeORM(Base):
    __tablename__ = "training_image_derivatives"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id", "page_number", "render_profile", name="uq_training_render"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    sheet_id: Mapped[str] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    render_profile: Mapped[str] = mapped_column(String(80))
    render_version: Mapped[str] = mapped_column(String(80))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    dpi: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), index=True)
    perceptual_hash: Mapped[str] = mapped_column(String(64), index=True)
    rights_status: Mapped[str] = mapped_column(String(40))
    anonymization_status: Mapped[str] = mapped_column(String(30))
    redaction_version: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class VisionAnnotationORM(Base):
    __tablename__ = "vision_annotations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    sheet_id: Mapped[str] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    view_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("view_regions.id", ondelete="SET NULL"), index=True
    )
    image_derivative_id: Mapped[str | None] = mapped_column(
        ForeignKey("training_image_derivatives.id", ondelete="SET NULL"), index=True
    )
    task_type: Mapped[str] = mapped_column(String(50), index=True)
    class_name: Mapped[str] = mapped_column(String(50), index=True)
    geometry_type: Mapped[str] = mapped_column(String(20))
    geometry_normalized: Mapped[dict[str, Any]] = mapped_column(JSON)
    original_width: Mapped[int] = mapped_column(Integer)
    original_height: Mapped[int] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    annotation_source: Mapped[str] = mapped_column(String(50), default="MANUAL", index=True)
    source_element_id: Mapped[str | None] = mapped_column(String(36), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(30), index=True)
    reviewer: Mapped[str | None] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    previous_annotation_id: Mapped[str | None] = mapped_column(
        ForeignKey("vision_annotations.id", ondelete="SET NULL")
    )
    training_allowed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    anonymized: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    rights_status: Mapped[str] = mapped_column(String(40), default="UNKNOWN", index=True)
    quality_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class VisionDatasetItemORM(Base):
    __tablename__ = "vision_dataset_items"
    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id", "annotation_id", name="uq_vision_dataset_annotation"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True
    )
    annotation_id: Mapped[str] = mapped_column(
        ForeignKey("vision_annotations.id", ondelete="RESTRICT"), index=True
    )
    image_derivative_id: Mapped[str] = mapped_column(
        ForeignKey("training_image_derivatives.id", ondelete="RESTRICT"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    dataset_split: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class VisionDatasetExportORM(Base):
    __tablename__ = "vision_dataset_exports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_jobs.id", ondelete="SET NULL"), index=True
    )
    export_format: Mapped[str] = mapped_column(String(30))
    storage_key: Mapped[str | None] = mapped_column(String(500))
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class VisionModelVersionORM(Base):
    __tablename__ = "vision_model_versions"
    __table_args__ = (UniqueConstraint("model_name", "version", name="uq_vision_model_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_name: Mapped[str] = mapped_column(String(160), index=True)
    model_family: Mapped[str] = mapped_column(String(120))
    task: Mapped[str] = mapped_column(String(80), default="ELEMENT_DETECTION", index=True)
    version: Mapped[str] = mapped_column(String(40))
    dataset_version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"), index=True
    )
    classes_json: Mapped[list[str]] = mapped_column(JSON)
    training_config_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), index=True)
    artifact_storage_key: Mapped[str | None] = mapped_column(String(500))
    framework: Mapped[str] = mapped_column(String(80), default="provider-abstraction")
    device: Mapped[str] = mapped_column(String(40), default="cpu")
    artifact_checksum: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class VisionPredictionORM(Base):
    __tablename__ = "vision_predictions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    sheet_id: Mapped[str] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    view_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("view_regions.id", ondelete="SET NULL")
    )
    model_version_id: Mapped[str] = mapped_column(
        ForeignKey("vision_model_versions.id", ondelete="RESTRICT"), index=True
    )
    class_name: Mapped[str] = mapped_column(String(50), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    raw_model_confidence: Mapped[float] = mapped_column(Float)
    calibrated_confidence: Mapped[float | None] = mapped_column(Float)
    geometry_normalized: Mapped[dict[str, Any]] = mapped_column(JSON)
    review_status: Mapped[str] = mapped_column(String(30), default="PREDICTED", index=True)
    annotation_id: Mapped[str | None] = mapped_column(
        ForeignKey("vision_annotations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DetectionConflictORM(Base):
    __tablename__ = "detection_conflicts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    sheet_id: Mapped[str] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    source_a_type: Mapped[str] = mapped_column(String(40))
    source_a_id: Mapped[str] = mapped_column(String(36))
    source_b_type: Mapped[str] = mapped_column(String(40))
    source_b_id: Mapped[str] = mapped_column(String(36))
    class_a: Mapped[str] = mapped_column(String(50))
    class_b: Mapped[str] = mapped_column(String(50))
    geometry_a_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    geometry_b_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    resolution: Mapped[str | None] = mapped_column(String(50))
    reviewer: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="REVIEW_REQUIRED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class VisionBenchmarkRunORM(Base):
    __tablename__ = "vision_benchmark_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("vision_model_versions.id", ondelete="SET NULL"), index=True
    )
    dataset_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL"), index=True
    )
    golden_set_version: Mapped[str] = mapped_column(String(80), index=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    comparison_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectArchiveImportORM(Base):
    __tablename__ = "project_archive_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_document_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    package_checksum: Mapped[str] = mapped_column(String(64), index=True)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="PREVIEW", index=True)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
