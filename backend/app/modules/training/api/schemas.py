from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rights_status: str
    training_allowed: bool = False
    building_use: str | None = None
    gross_area: float | None = None
    footprint_area: float | None = None
    floor_count: int | None = None
    basement_count: int | None = None
    location_region: str | None = None
    structural_system: str | None = None
    foundation_type: str | None = None
    soil_info: str | None = None
    architectural_documents: bool = False
    structural_documents: bool = False
    mep_documents: bool = False
    geotechnical_documents: bool = False
    boq_available: bool = False
    final_account_available: bool = False
    as_built_available: bool = False
    verified_quantity_available: bool = False
    anonymization_status: str = "PENDING"
    eligibility_override: bool = False


class ManifestResponse(ManifestRequest):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    data_completeness_score: int


class AnonymizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata: dict[str, Any]


class AnonymizedDerivativeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source_document_version_id: str
    anonymized_document_id: str
    anonymization_version: str
    anonymized_metadata_json: dict[str, Any]
    status: str


class TrainingSampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_type: str
    subject_id: str
    task_type: str
    annotation_json: dict[str, Any] = Field(default_factory=dict)


class TrainingSampleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    source_document_id: str | None
    task_type: str
    input_reference: str
    review_revision: int
    rights_status: str
    anonymization_status: str
    created_at: datetime


class ActiveLearningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    source_reference: str
    prediction: dict[str, Any]
    confidence: float = Field(ge=0, le=1)
    reason: str


class ActiveLearningResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    source_reference: str
    confidence: float
    priority: int
    reason: str
    status: str


class DatasetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_name: str
    version: str
    parent_version_id: str | None = None


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    dataset_name: str
    version: str
    status: str
    project_count: int
    sheet_count: int
    sample_count: int
    checksum: str | None


class DatasetMembershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_id: str


class DatasetMembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    dataset_version_id: str
    sample_id: str
    project_id: str
    dataset_split: str
