from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

FACADE_SERVICE_CONSTRAINT = "SERVICE_CATEGORY:FACADE"


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceType(StrEnum):
    USER_EXPLICIT = "USER_EXPLICIT"
    USER_CONFIRMED = "USER_CONFIRMED"
    AI_INFERRED = "AI_INFERRED"
    DOCUMENT_EXTRACTED = "DOCUMENT_EXTRACTED"
    SYSTEM_CALCULATED = "SYSTEM_CALCULATED"
    HUMAN_VERIFIED = "HUMAN_VERIFIED"
    EXTERNAL_DATA = "EXTERNAL_DATA"


class EvidenceV1(StrictContract):
    field_path: str
    value: Any
    source_message_id: str
    source_type: SourceType
    confidence: float = Field(ge=0, le=1)


class QuantityV1(StrictContract):
    value: Decimal = Field(gt=0)
    unit: str = "m2"


class LocationV1(StrictContract):
    country: str = "TR"
    city: str | None = None
    district: str | None = None
    locality: str | None = None
    code: str | None = None
    display_name: str | None = None


class JobUnderstandingV1(StrictContract):
    job_reference: str | None = None
    service_candidate: str | None = None
    service_confidence: float = Field(default=0, ge=0, le=1)
    quantity: QuantityV1 | None = None
    location: LocationV1 | None = None
    customer_additions: list[str] = Field(default_factory=list)
    customer_exclusions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    evidence: list[EvidenceV1] = Field(default_factory=list)


class CustomerUnderstandingResultV1(StrictContract):
    schema_version: str = "customer-understanding.v1"
    detected_language: str = "tr"
    jobs: list[JobUnderstandingV1]


class ExistingJobContextV1(StrictContract):
    job_id: str
    service_code: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    location_code: str | None = None
    location_label: str | None = None


class CustomerUnderstandingContextV1(StrictContract):
    conversation_id: str
    message_id: str
    locale: str = "tr"
    jobs: list[ExistingJobContextV1] = Field(default_factory=list)
