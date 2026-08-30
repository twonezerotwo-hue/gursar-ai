from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class DocumentVersionStatus(StrEnum):
    READY = "READY"


class SheetDiscipline(StrEnum):
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProjectDocument:
    id: str
    project_id: str
    original_name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    id: str
    document_id: str
    project_id: str
    version: int
    sha256: str
    size_bytes: int
    media_type: str
    storage_key: str
    page_count: int
    status: DocumentVersionStatus
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Sheet:
    id: str
    document_version_id: str
    page_number: int
    title: str | None
    discipline: SheetDiscipline
    width_points: Decimal
    height_points: Decimal
    sheet_number: str | None = None
    sheet_name: str | None = None
    revision: str | None = None
    drawing_type: str = "UNKNOWN"
    level_name: str | None = None
    classification_confidence: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PDFPageMetadata:
    page_number: int
    width_points: Decimal
    height_points: Decimal


@dataclass(frozen=True, slots=True)
class PDFMetadata:
    pages: list[PDFPageMetadata]


@dataclass(frozen=True, slots=True)
class DocumentUploadResult:
    document: ProjectDocument
    version: DocumentVersion
    sheets: list[Sheet]
