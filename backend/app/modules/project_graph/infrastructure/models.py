from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class ProjectBuildingORM(Base):
    __tablename__ = "project_buildings"
    __table_args__ = (
        UniqueConstraint("project_id", "source_key", name="uq_project_building_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    source_key: Mapped[str] = mapped_column(String(240))
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProjectLevelORM(Base):
    __tablename__ = "project_levels"
    __table_args__ = (UniqueConstraint("project_id", "source_key", name="uq_project_level_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    building_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_buildings.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    elevation: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    source_key: Mapped[str] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ViewRegionORM(Base):
    __tablename__ = "view_regions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sheet_id: Mapped[str] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    bbox_normalized: Mapped[list[float]] = mapped_column(JSON)
    view_type: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    level_name: Mapped[str | None] = mapped_column(String(300))
    scale: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    scale_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0"))
    review_status: Mapped[str] = mapped_column(String(30), default="REVIEW_REQUIRED", index=True)
    scale_status: Mapped[str] = mapped_column(String(30), default="UNKNOWN", index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="MANUAL")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScaleCalibrationORM(Base):
    __tablename__ = "scale_calibrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    view_region_id: Mapped[str] = mapped_column(
        ForeignKey("view_regions.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column(String(40))
    known_distance: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    pixel_distance: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    drawing_units: Mapped[str] = mapped_column(String(24))
    scale_ratio: Mapped[Decimal] = mapped_column(Numeric(18, 9))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewer: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BuildingElementORM(Base):
    __tablename__ = "building_elements"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id", "source_external_id", name="uq_element_source_external"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    building_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_buildings.id", ondelete="SET NULL"), index=True
    )
    level_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_levels.id", ondelete="SET NULL"), index=True
    )
    discipline: Mapped[str] = mapped_column(String(40), index=True)
    element_type: Mapped[str] = mapped_column(String(40), index=True)
    subtype: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str | None] = mapped_column(String(500))
    geometry_type: Mapped[str | None] = mapped_column(String(80))
    geometry_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    length: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    width: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    height: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    thickness: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    area: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    material: Mapped[str | None] = mapped_column(String(500))
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), index=True
    )
    source_sheet_id: Mapped[str | None] = mapped_column(
        ForeignKey("sheets.id", ondelete="SET NULL"), index=True
    )
    source_view_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("view_regions.id", ondelete="SET NULL"), index=True
    )
    source_external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    source_coordinates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extraction_method: Mapped[str] = mapped_column(String(80))
    extraction_model: Mapped[str | None] = mapped_column(String(160))
    extraction_version: Mapped[str] = mapped_column(String(80))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(String(30), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_element_id: Mapped[str | None] = mapped_column(
        ForeignKey("building_elements.id", ondelete="SET NULL"), index=True
    )
    reviewer: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReviewDecisionORM(Base):
    """Append-only expert decision; prediction payloads are never overwritten."""

    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    subject_type: Mapped[str] = mapped_column(String(30), index=True)
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(20), index=True)
    from_revision: Mapped[int] = mapped_column(Integer)
    to_revision: Mapped[int] = mapped_column(Integer)
    resulting_subject_id: Mapped[str | None] = mapped_column(String(36), index=True)
    reviewer: Mapped[str] = mapped_column(String(200))
    reviewer_role: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text)
    original_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    corrected_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ElementRelationshipORM(Base):
    __tablename__ = "element_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_element_id",
            "relationship_type",
            "target_element_id",
            name="uq_element_relationship",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_element_id: Mapped[str] = mapped_column(
        ForeignKey("building_elements.id", ondelete="CASCADE"), index=True
    )
    target_element_id: Mapped[str] = mapped_column(
        ForeignKey("building_elements.id", ondelete="CASCADE"), index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(40), index=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    source_type: Mapped[str] = mapped_column(String(40))
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MeasurementORM(Base):
    __tablename__ = "measurements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    element_id: Mapped[str | None] = mapped_column(
        ForeignKey("building_elements.id", ondelete="SET NULL"), index=True
    )
    measurement_type: Mapped[str] = mapped_column(String(30), index=True)
    measurement_purpose: Mapped[str] = mapped_column(
        String(40), default="ENGINEERING_QUANTITY", index=True
    )
    geometry_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    unit: Mapped[str] = mapped_column(String(24))
    scale_calibration_id: Mapped[str | None] = mapped_column(
        ForeignKey("scale_calibrations.id", ondelete="SET NULL"), index=True
    )
    calculated_value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), index=True
    )
    source_sheet_id: Mapped[str | None] = mapped_column(
        ForeignKey("sheets.id", ondelete="SET NULL"), index=True
    )
    source_view_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("view_regions.id", ondelete="SET NULL"), index=True
    )
    method: Mapped[str] = mapped_column(String(80))
    algorithm_version: Mapped[str] = mapped_column(String(80))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(String(30), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_measurement_id: Mapped[str | None] = mapped_column(
        ForeignKey("measurements.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GeometryArtifactORM(Base):
    __tablename__ = "geometry_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "source_external_id",
            "analysis_version",
            name="uq_geometry_artifact_source_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    sheet_id: Mapped[str | None] = mapped_column(
        ForeignKey("sheets.id", ondelete="CASCADE"), index=True
    )
    view_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("view_regions.id", ondelete="SET NULL"), index=True
    )
    source_external_id: Mapped[str] = mapped_column(String(240))
    geometry_type: Mapped[str] = mapped_column(String(40), index=True)
    raw_geometry_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    normalized_geometry_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    artifact_stage: Mapped[str] = mapped_column(String(40), default="RAW_GEOMETRY", index=True)
    semantic_class: Mapped[str | None] = mapped_column(String(40), index=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1"))
    review_status: Mapped[str] = mapped_column(String(30), default="PREDICTED", index=True)
    analysis_version: Mapped[str] = mapped_column(String(80))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MeasurementEvidenceORM(Base):
    __tablename__ = "measurement_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    measurement_id: Mapped[str] = mapped_column(
        ForeignKey("measurements.id", ondelete="CASCADE"), unique=True, index=True
    )
    source_coordinates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    geometry_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    page_number: Mapped[int | None] = mapped_column(Integer)
    view_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("view_regions.id", ondelete="SET NULL"), index=True
    )
    scale_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    thumbnail_reference: Mapped[str | None] = mapped_column(String(500))
    algorithm: Mapped[str] = mapped_column(String(160))
    model_version: Mapped[str] = mapped_column(String(100))
    review_status: Mapped[str] = mapped_column(String(30), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TakeoffItemORM(Base):
    __tablename__ = "takeoff_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    service_code: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    unit: Mapped[str] = mapped_column(String(24))
    measurement_ids: Mapped[list[str]] = mapped_column(JSON)
    calculation_rule: Mapped[str] = mapped_column(Text)
    calculation_rule_version: Mapped[str] = mapped_column(String(80))
    price_reference: Mapped[str | None] = mapped_column(String(240))
    quantity_source: Mapped[str] = mapped_column(String(50), index=True)
    review_status: Mapped[str] = mapped_column(String(30), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class QuantityConflictORM(Base):
    __tablename__ = "quantity_conflicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    element_id: Mapped[str] = mapped_column(
        ForeignKey("building_elements.id", ondelete="CASCADE"), index=True
    )
    quantity_name: Mapped[str] = mapped_column(String(80))
    stated_value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    calculated_value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    difference_ratio: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    status: Mapped[str] = mapped_column(String(30), default="OPEN", index=True)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DxfAnalysisORM(Base):
    __tablename__ = "dxf_analyses"
    __table_args__ = (
        UniqueConstraint("document_version_id", "analysis_version", name="uq_dxf_analysis_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    analysis_version: Mapped[str] = mapped_column(String(80))
    raw_unit: Mapped[str] = mapped_column(String(30))
    normalized_unit: Mapped[str | None] = mapped_column(String(30))
    unit_status: Mapped[str] = mapped_column(String(30), index=True)
    unit_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    entity_count: Mapped[int] = mapped_column(Integer)
    recognized_count: Mapped[int] = mapped_column(Integer)
    unknown_count: Mapped[int] = mapped_column(Integer)
    measurement_count: Mapped[int] = mapped_column(Integer)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DxfUnitResolutionORM(Base):
    __tablename__ = "dxf_unit_resolutions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    unit: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(200))
    reviewer_role: Mapped[str] = mapped_column(String(30))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LayerSemanticMappingORM(Base):
    __tablename__ = "layer_semantic_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    raw_layer_name: Mapped[str] = mapped_column(String(300), index=True)
    normalized_element_type: Mapped[str] = mapped_column(String(40), index=True)
    scope: Mapped[str] = mapped_column(String(30), index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    office_profile: Mapped[str | None] = mapped_column(String(160), index=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by: Mapped[str] = mapped_column(String(200))
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class BlockSemanticMappingORM(Base):
    __tablename__ = "block_semantic_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    block_name: Mapped[str] = mapped_column(String(300), index=True)
    normalized_element_type: Mapped[str] = mapped_column(String(40), index=True)
    scope: Mapped[str] = mapped_column(String(30), index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    office_profile: Mapped[str | None] = mapped_column(String(160), index=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by: Mapped[str] = mapped_column(String(200))
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class VectorPdfAnalysisORM(Base):
    __tablename__ = "vector_pdf_analyses"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id", "analysis_version", name="uq_vector_pdf_analysis_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    analysis_version: Mapped[str] = mapped_column(String(80))
    page_count: Mapped[int] = mapped_column(Integer)
    vector_page_count: Mapped[int] = mapped_column(Integer)
    raster_page_count: Mapped[int] = mapped_column(Integer)
    hybrid_page_count: Mapped[int] = mapped_column(Integer)
    text_object_count: Mapped[int] = mapped_column(Integer)
    vector_object_count: Mapped[int] = mapped_column(Integer)
    view_region_count: Mapped[int] = mapped_column(Integer)
    measurement_count: Mapped[int] = mapped_column(Integer)
    review_required_count: Mapped[int] = mapped_column(Integer)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExtractedPdfTextORM(Base):
    __tablename__ = "extracted_pdf_texts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("vector_pdf_analyses.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    sheet_id: Mapped[str] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer, index=True)
    text: Mapped[str] = mapped_column(Text)
    bbox_points: Mapped[list[float]] = mapped_column(JSON)
    bbox_normalized: Mapped[list[float]] = mapped_column(JSON)
    font_size: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    font_name: Mapped[str | None] = mapped_column(String(200))
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    source: Mapped[str] = mapped_column(String(30), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SheetMetadataRevisionORM(Base):
    __tablename__ = "sheet_metadata_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    sheet_id: Mapped[str] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    previous_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("sheet_metadata_revisions.id", ondelete="SET NULL")
    )
    original_prediction_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    corrected_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    task_type: Mapped[str] = mapped_column(String(50))
    reviewer: Mapped[str] = mapped_column(String(200))
    reviewer_role: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(30), default="VERIFIED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
