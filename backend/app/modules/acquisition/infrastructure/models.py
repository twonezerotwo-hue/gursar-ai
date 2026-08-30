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


class DiscoveredDatasetSourceORM(Base):
    __tablename__ = "discovered_dataset_sources"
    __table_args__ = (UniqueConstraint("source_url", name="uq_discovered_source_url"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(60), index=True)
    source_url: Mapped[str] = mapped_column(String(1000))
    repository_url: Mapped[str | None] = mapped_column(String(1000))
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str | None] = mapped_column(String(300))
    detected_formats_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    estimated_project_count: Mapped[int | None] = mapped_column(Integer)
    estimated_file_count: Mapped[int | None] = mapped_column(Integer)
    annotation_available: Mapped[bool] = mapped_column(Boolean, default=False)
    ground_truth_available: Mapped[bool] = mapped_column(Boolean, default=False)
    license_status: Mapped[str] = mapped_column(String(40), default="LICENSE_UNKNOWN", index=True)
    quality_score: Mapped[int] = mapped_column(Integer, default=0)
    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    reputation_score: Mapped[int] = mapped_column(Integer, default=0)
    discovery_query: Mapped[str] = mapped_column(String(500), default="seed")
    access_status: Mapped[str] = mapped_column(String(40), default="PUBLIC")
    terms_restriction: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="DISCOVERED", index=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    source_family_id: Mapped[str | None] = mapped_column(String(120), index=True)
    acquisition_priority_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LicenseAssessmentORM(Base):
    __tablename__ = "license_assessments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="CASCADE"), index=True
    )
    source_url: Mapped[str] = mapped_column(String(1000))
    source_name: Mapped[str] = mapped_column(String(300))
    publisher: Mapped[str | None] = mapped_column(String(300))
    repository_url: Mapped[str | None] = mapped_column(String(1000))
    license_name: Mapped[str | None] = mapped_column(String(200))
    license_url: Mapped[str | None] = mapped_column(String(1000))
    license_text_hash: Mapped[str | None] = mapped_column(String(64))
    commercial_use_allowed: Mapped[bool | None] = mapped_column(Boolean)
    derivative_use_allowed: Mapped[bool | None] = mapped_column(Boolean)
    training_use_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    redistribution_allowed: Mapped[bool | None] = mapped_column(Boolean)
    attribution_required: Mapped[bool] = mapped_column(Boolean, default=False)
    research_only: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_review_required: Mapped[bool] = mapped_column(Boolean, default=True)
    rights_status: Mapped[str] = mapped_column(String(40), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    checker_version: Mapped[str] = mapped_column(String(80))


class UpstreamLicenseLineageORM(Base):
    """Append-only effective-license decision for a source and its upstream chain."""

    __tablename__ = "upstream_license_lineage"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "parent_source_id", "lineage_version", name="uq_source_license_lineage"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="CASCADE"), index=True
    )
    parent_source_id: Mapped[str | None] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="SET NULL"), index=True
    )
    root_source_id: Mapped[str] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="CASCADE"), index=True
    )
    declared_license: Mapped[str | None] = mapped_column(String(200))
    upstream_license: Mapped[str | None] = mapped_column(String(200))
    effective_license: Mapped[str] = mapped_column(String(200))
    derived_from: Mapped[str | None] = mapped_column(Text)
    upstream_url: Mapped[str | None] = mapped_column(String(1000))
    commercial_use_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    training_allowed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    redistribution_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    lineage_verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    manual_review_required: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    conflict_code: Mapped[str | None] = mapped_column(String(80), index=True)
    decision_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    lineage_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DownloadManifestORM(Base):
    __tablename__ = "download_manifests"
    __table_args__ = (UniqueConstraint("archive_hash", name="uq_download_archive_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="CASCADE"), index=True
    )
    license_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    archive_hash: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(1000))
    files_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    total_size: Mapped[int] = mapped_column(Integer)
    formats_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    extraction_status: Mapped[str] = mapped_column(String(40), default="QUARANTINED")
    errors_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExternalProjectPackageORM(Base):
    __tablename__ = "external_project_packages"
    __table_args__ = (
        UniqueConstraint("source_id", "external_project_id", name="uq_external_project_source"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="CASCADE"), index=True
    )
    external_project_id: Mapped[str] = mapped_column(String(300))
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    building_type: Mapped[str | None] = mapped_column(String(100))
    floors: Mapped[int | None] = mapped_column(Integer)
    discipline_availability_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    files_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    annotation_files_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    project_quality_score: Mapped[int] = mapped_column(Integer, default=0)
    training_eligibility: Mapped[str] = mapped_column(String(40), default="BLOCKED")
    rights_status: Mapped[str] = mapped_column(String(40), index=True)
    ingestion_status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    completeness_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ExternalClassMappingORM(Base):
    __tablename__ = "external_class_mappings"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "external_class", "mapping_version", name="uq_external_class_mapping"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="CASCADE"), index=True
    )
    external_class: Mapped[str] = mapped_column(String(200))
    gursar_class: Mapped[str] = mapped_column(String(50), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    mapping_confidence: Mapped[float] = mapped_column(Float)
    mapping_method: Mapped[str] = mapped_column(String(80))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    mapping_version: Mapped[str] = mapped_column(String(80))


class ExternalAnnotationLineageORM(Base):
    __tablename__ = "external_annotation_lineage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    annotation_id: Mapped[str] = mapped_column(
        ForeignKey("vision_annotations.id", ondelete="CASCADE"), unique=True, index=True
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="CASCADE"), index=True
    )
    project_package_id: Mapped[str] = mapped_column(
        ForeignKey("external_project_packages.id", ondelete="CASCADE"), index=True
    )
    original_file: Mapped[str] = mapped_column(String(1000))
    annotation_file: Mapped[str] = mapped_column(String(1000))
    external_class: Mapped[str] = mapped_column(String(200))
    importer_version: Mapped[str] = mapped_column(String(80))
    mapping_version: Mapped[str] = mapped_column(String(80))
    normalization_version: Mapped[str] = mapped_column(String(80))
    quality_tier: Mapped[str] = mapped_column(String(20))


class IfcHarvestSummaryORM(Base):
    __tablename__ = "ifc_harvest_summaries"
    __table_args__ = (UniqueConstraint("document_version_id", name="uq_ifc_harvest_document"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="CASCADE"), index=True
    )
    project_package_id: Mapped[str] = mapped_column(
        ForeignKey("external_project_packages.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    quality_score: Mapped[int] = mapped_column(Integer)
    corpus_role: Mapped[str] = mapped_column(String(40), default="PROJECT_INTELLIGENCE")
    estimator_calibration_allowed: Mapped[bool] = mapped_column(Boolean, default=False)


class Ifc2DRenderORM(Base):
    __tablename__ = "ifc_2d_renders"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "level_external_id",
            "renderer_version",
            "render_style",
            name="uq_ifc_2d_render",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="CASCADE"), index=True
    )
    project_package_id: Mapped[str] = mapped_column(
        ForeignKey("external_project_packages.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    level_external_id: Mapped[str] = mapped_column(String(160), index=True)
    level_name: Mapped[str] = mapped_column(String(300))
    level_elevation: Mapped[float | None] = mapped_column(Float)
    project_family_id: Mapped[str] = mapped_column(String(160), index=True)
    render_style: Mapped[str] = mapped_column(String(50))
    renderer_version: Mapped[str] = mapped_column(String(80))
    projection_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    image_storage_key: Mapped[str] = mapped_column(String(1000), unique=True)
    annotation_storage_key: Mapped[str] = mapped_column(String(1000), unique=True)
    image_checksum: Mapped[str] = mapped_column(String(64), index=True)
    annotation_checksum: Mapped[str] = mapped_column(String(64), index=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    element_count: Mapped[int] = mapped_column(Integer, default=0)
    valid_geometry_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_score: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="COMPLETED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Ifc2DAnnotationLineageORM(Base):
    __tablename__ = "ifc_2d_annotation_lineage"
    __table_args__ = (UniqueConstraint("annotation_id", name="uq_ifc_2d_annotation_lineage"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    annotation_id: Mapped[str] = mapped_column(
        ForeignKey("vision_annotations.id", ondelete="CASCADE"), index=True
    )
    render_id: Mapped[str] = mapped_column(
        ForeignKey("ifc_2d_renders.id", ondelete="CASCADE"), index=True
    )
    source_document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    ifc_global_id: Mapped[str] = mapped_column(String(160), index=True)
    ifc_class: Mapped[str] = mapped_column(String(100), index=True)
    storey_external_id: Mapped[str] = mapped_column(String(160), index=True)
    source_geometry_hash: Mapped[str] = mapped_column(String(64))
    projection_parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    renderer_version: Mapped[str] = mapped_column(String(80))
    quality_tier: Mapped[str] = mapped_column(String(40), default="BIM_DERIVED_SILVER")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CrossModalProjectLinkORM(Base):
    __tablename__ = "cross_modal_project_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_package_id: Mapped[str] = mapped_column(
        ForeignKey("external_project_packages.id", ondelete="CASCADE"), index=True
    )
    ifc_document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    drawing_document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    level_mapping_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    alignment_status: Mapped[str] = mapped_column(String(40), default="NOT_ALIGNED")
    confidence: Mapped[float] = mapped_column(Float, default=0)


class RealDrawingImportORM(Base):
    __tablename__ = "real_drawing_imports"
    __table_args__ = (
        UniqueConstraint("source_id", "document_version_id", name="uq_real_drawing_import"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("discovered_dataset_sources.id", ondelete="CASCADE"), index=True
    )
    project_package_id: Mapped[str] = mapped_column(
        ForeignKey("external_project_packages.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    source_family_id: Mapped[str] = mapped_column(String(120), index=True)
    drawing_format: Mapped[str] = mapped_column(String(30), index=True)
    discipline: Mapped[str] = mapped_column(String(50), default="ARCHITECTURAL")
    revision_family: Mapped[str | None] = mapped_column(String(160), index=True)
    license_lineage_id: Mapped[str] = mapped_column(
        ForeignKey("upstream_license_lineage.id", ondelete="RESTRICT"), index=True
    )
    vector_available: Mapped[bool] = mapped_column(Boolean, default=False)
    annotation_available: Mapped[bool] = mapped_column(Boolean, default=False)
    paired_ifc_available: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_score: Mapped[int] = mapped_column(Integer, default=0)
    quality_factors_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    training_eligibility: Mapped[str] = mapped_column(String(40), default="REVIEW_REQUIRED")
    status: Mapped[str] = mapped_column(String(40), default="IMPORTED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IfcGeometryCacheORM(Base):
    __tablename__ = "ifc_geometry_cache"
    __table_args__ = (
        UniqueConstraint(
            "document_hash",
            "ifc_global_id",
            "storey_external_id",
            "geometry_settings_version",
            "slice_height",
            name="uq_ifc_geometry_cache",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_hash: Mapped[str] = mapped_column(String(64), index=True)
    ifc_global_id: Mapped[str] = mapped_column(String(160), index=True)
    storey_external_id: Mapped[str] = mapped_column(String(160), index=True)
    geometry_settings_version: Mapped[str] = mapped_column(String(80))
    slice_height: Mapped[float] = mapped_column(Float)
    extraction_mode: Mapped[str] = mapped_column(String(40), index=True)
    polygons_json: Mapped[list[list[list[float]]]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_geometry_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CrossModalAlignmentORM(Base):
    __tablename__ = "cross_modal_alignments"
    __table_args__ = (
        UniqueConstraint(
            "link_id",
            "ifc_storey_external_id",
            "drawing_view_region_id",
            "alignment_version",
            name="uq_cross_modal_alignment",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    link_id: Mapped[str] = mapped_column(
        ForeignKey("cross_modal_project_links.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    ifc_storey_external_id: Mapped[str] = mapped_column(String(160), index=True)
    drawing_view_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("view_regions.id", ondelete="SET NULL"), index=True
    )
    method: Mapped[str] = mapped_column(String(50))
    transform_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="REVIEW_REQUIRED", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(Text)
    alignment_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CrossModalAnnotationLineageORM(Base):
    __tablename__ = "cross_modal_annotation_lineage"
    __table_args__ = (UniqueConstraint("annotation_id", name="uq_cross_modal_annotation"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    annotation_id: Mapped[str] = mapped_column(
        ForeignKey("vision_annotations.id", ondelete="CASCADE"), index=True
    )
    alignment_id: Mapped[str] = mapped_column(
        ForeignKey("cross_modal_alignments.id", ondelete="CASCADE"), index=True
    )
    ifc_annotation_id: Mapped[str] = mapped_column(
        ForeignKey("vision_annotations.id", ondelete="CASCADE"), index=True
    )
    ifc_global_id: Mapped[str] = mapped_column(String(160), index=True)
    vector_candidate_id: Mapped[str | None] = mapped_column(String(36), index=True)
    vector_agreement: Mapped[float | None] = mapped_column(Float)
    projection_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    clipped: Mapped[bool] = mapped_column(Boolean, default=False)
    source_type: Mapped[str] = mapped_column(String(50), default="CROSS_MODAL_AUTO_ANNOTATION")
    auto_silver_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SyntheticProjectORM(Base):
    __tablename__ = "synthetic_projects"
    __table_args__ = (UniqueConstraint("generator_version", "spec_hash", name="uq_synthetic_spec"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    generator_version: Mapped[str] = mapped_column(String(80))
    seed: Mapped[int] = mapped_column(Integer)
    spec_hash: Mapped[str] = mapped_column(String(64), index=True)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_checksum: Mapped[str] = mapped_column(String(64), index=True)
    image_storage_key: Mapped[str] = mapped_column(String(1000))
    annotation_storage_key: Mapped[str] = mapped_column(String(1000))
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="GENERATED", index=True)


class AcquisitionRunORM(Base):
    __tablename__ = "acquisition_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_jobs.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(80))
    query_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    bytes_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    failed_sources: Mapped[int] = mapped_column(Integer, default=0)
    license_unknown: Mapped[int] = mapped_column(Integer, default=0)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(30), default="DISCOVERY_ONLY", index=True)
    status: Mapped[str] = mapped_column(String(50), default="RUNNING", index=True)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
