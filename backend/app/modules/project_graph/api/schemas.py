from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnalyzeProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_version_id: str


class ProjectGraphResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    project_id: str
    building_count: int
    level_count: int
    element_count: int
    elements: dict[str, dict[str, Any]]
    reused: bool | None = None


class BuildingElementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    building_id: str | None
    level_id: str | None
    discipline: str
    element_type: str
    subtype: str | None
    name: str | None
    length: Decimal | None
    width: Decimal | None
    height: Decimal | None
    thickness: Decimal | None
    area: Decimal | None
    volume: Decimal | None
    material: str | None
    source_type: str
    source_document_id: str | None
    source_external_id: str | None
    extraction_method: str
    extraction_version: str
    confidence: Decimal
    review_status: str
    revision: int
    supersedes_element_id: str | None


class TakeoffItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    service_code: str
    description: str
    quantity: Decimal
    unit: str
    measurement_ids: list[str]
    calculation_rule: str
    calculation_rule_version: str
    quantity_source: str
    review_status: str
    revision: int


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    measurement_id: str
    source_coordinates: dict[str, Any]
    geometry_json: dict[str, Any]
    page_number: int | None
    view_region_id: str | None
    scale_json: dict[str, Any]
    thumbnail_reference: str | None
    algorithm: str
    model_version: str
    review_status: str
    created_at: datetime


class TakeoffEvidenceResponse(BaseModel):
    item: TakeoffItemResponse
    evidence: list[EvidenceResponse]


class ViewRegionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheet_id: str
    bbox_normalized: list[float] = Field(min_length=4, max_length=4)
    view_type: str = "UNKNOWN"
    level_name: str | None = None


class ViewRegionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    sheet_id: str
    bbox_normalized: list[float]
    view_type: str
    level_name: str | None


class ScaleCalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view_region_id: str
    method: str
    known_distance: float = Field(gt=0)
    pixel_distance: float = Field(gt=0)
    drawing_units: str = Field(min_length=1, max_length=24)
    confidence: float = Field(ge=0, le=1)
    verified: bool = False
    reviewer: str | None = None


class ScaleCalibrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    view_region_id: str
    method: str
    known_distance: Decimal
    pixel_distance: Decimal
    drawing_units: str
    scale_ratio: Decimal
    confidence: Decimal
    verified: bool
    reviewer: str | None


class ManualMeasurementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measurement_type: str
    geometry: dict[str, Any]
    unit: str = Field(min_length=1, max_length=24)
    service_code: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    view_region_id: str | None = None
    scale_calibration_id: str | None = None

    @model_validator(mode="after")
    def require_region_with_calibration(self) -> ManualMeasurementRequest:
        if self.scale_calibration_id and not self.view_region_id:
            raise ValueError("view_region_id is required with scale_calibration_id")
        return self


class ManualMeasurementResponse(BaseModel):
    measurement_id: str
    evidence_id: str
    takeoff_item: TakeoffItemResponse
    calculated_value: Decimal
    unit: str
    quantity_source: str


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    reason: str | None = Field(default=None, max_length=2000)
    correction: dict[str, Any] | None = None


class ReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    subject_type: str
    subject_id: str
    action: str
    from_revision: int
    to_revision: int
    resulting_subject_id: str | None
    reviewer: str
    reviewer_role: str
    reason: str | None
    original_snapshot_json: dict[str, Any]
    corrected_snapshot_json: dict[str, Any] | None
    created_at: datetime


class DxfMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalized_element_type: str
    scope: str = "PROJECT"
    office_profile: str | None = None


class LayerMappingRequest(DxfMappingRequest):
    raw_layer_name: str = Field(min_length=1, max_length=300)


class BlockMappingRequest(DxfMappingRequest):
    block_name: str = Field(min_length=1, max_length=300)


class SemanticMappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    normalized_element_type: str
    scope: str
    project_id: str | None
    office_profile: str | None
    confidence: Decimal
    verified: bool
    created_by: str
    reviewed_by: str | None
    raw_layer_name: str | None = None
    block_name: str | None = None


class DxfUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: str
    reason: str = Field(min_length=1, max_length=2000)


class DxfUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    document_version_id: str
    unit: str
    reason: str
    reviewer: str
    revision: int


class DxfAnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    document_version_id: str
    analysis_version: str
    raw_unit: str
    normalized_unit: str | None
    unit_status: str
    unit_confidence: Decimal
    entity_count: int
    recognized_count: int
    unknown_count: int
    measurement_count: int
    summary_json: dict[str, Any]
    reused: bool = False


class VectorPdfAnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    document_version_id: str
    analysis_version: str
    page_count: int
    vector_page_count: int
    raster_page_count: int
    hybrid_page_count: int
    text_object_count: int
    vector_object_count: int
    view_region_count: int
    measurement_count: int
    review_required_count: int
    summary_json: dict[str, Any]
    reused: bool = False


class ExtractedPdfTextResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    sheet_id: str
    page_number: int
    text: str
    bbox_points: list[float]
    bbox_normalized: list[float]
    font_size: Decimal | None
    font_name: str | None
    rotation: int
    confidence: Decimal
    source: str


class SheetMetadataCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corrections: dict[str, Any]
    task_type: str
    reason: str = Field(min_length=1, max_length=2000)


class SheetMetadataRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    document_version_id: str
    sheet_id: str
    revision: int
    previous_revision_id: str | None
    original_prediction_json: dict[str, Any]
    corrected_metadata_json: dict[str, Any]
    task_type: str
    reviewer: str
    review_status: str
    created_at: datetime


class CrossModalAlignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link_id: str
    storey_external_id: str = Field(min_length=1, max_length=160)
    view_region_id: str | None = None
    source_points: list[list[float]] = Field(min_length=3)
    target_points: list[list[float]] = Field(min_length=3)
    manual_verified: bool = False


class CrossModalReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    reason: str | None = Field(default=None, max_length=2000)
