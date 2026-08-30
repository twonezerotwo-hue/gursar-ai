from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SheetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document_version_id: str
    page_number: int
    title: str | None
    discipline: str
    width_points: Decimal
    height_points: Decimal
    sheet_number: str | None = None
    sheet_name: str | None = None
    revision: str | None = None
    drawing_type: str = "UNKNOWN"
    level_name: str | None = None
    classification_confidence: Decimal | None = None


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: int
    sha256: str
    size_bytes: int
    media_type: str
    page_count: int
    status: str
    created_at: datetime


class DocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    original_name: str
    created_at: datetime
    latest_version: DocumentVersionResponse
    sheets: list[SheetResponse]


class DocumentCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted_extensions: list[str]
    accepted_media_types: list[str]
    max_upload_bytes: int
    max_pdf_pages: int
    encrypted_pdf_supported: bool
