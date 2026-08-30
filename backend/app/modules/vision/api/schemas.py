from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RenderImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_version_id: str
    page_number: int = Field(ge=1)
    dpi: int = Field(default=144, ge=72, le=300)
    redact_title_block: bool = False


class TrainingImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    document_version_id: str
    sheet_id: str
    page_number: int
    width: int
    height: int
    dpi: int
    checksum_sha256: str
    perceptual_hash: str
    rights_status: str
    anonymization_status: str
    redaction_version: str | None
    reused: bool = False


class AnnotationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_version_id: str
    sheet_id: str
    view_region_id: str | None = None
    image_derivative_id: str | None = None
    task_type: str
    class_name: str
    geometry_type: str
    geometry_normalized: dict[str, Any]
    source_type: str = "EXPERT_MANUAL_ANNOTATION"
    source_element_id: str | None = None
    confidence: float = Field(default=1, ge=0, le=1)
    review_status: str = "VERIFIED"


class AnnotationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    document_version_id: str
    sheet_id: str
    image_derivative_id: str | None
    task_type: str
    class_name: str
    geometry_type: str
    geometry_normalized: dict[str, Any]
    source_type: str
    confidence: float
    review_status: str
    reviewer: str | None
    revision: int
    previous_annotation_id: str | None
    training_allowed: bool


class AnnotationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    correction: dict[str, Any] | None = None


class ArchivePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, Any]
    document_version_ids: list[str]


class ArchiveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    manifest_json: dict[str, Any]
    source_document_ids_json: list[str]
    package_checksum: str
    validation_json: dict[str, Any]
    status: str
    approved_by: str | None
    created_at: datetime


class TrainingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    model_name: str
    version: str
    task: str


class ShadowPredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_version_id: str
    sheet_id: str
    model_version_id: str
    class_name: str
    confidence: float = Field(ge=0, le=1)
    geometry_normalized: dict[str, Any]
    structured_verified_class: str | None = None


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    export_format: str = "COCO"


class BenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version_id: str | None = None
    golden_version: str = "v1"
    predictions: list[dict[str, Any]]


class ModelRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_name: str
    model_family: str
    version: str
    task: str
    dataset_version_id: str
    classes: list[str]
    framework: str = "provider-abstraction"
    device: str = "cpu"
    artifact_storage_key: str | None = None
    artifact_checksum: str | None = None
