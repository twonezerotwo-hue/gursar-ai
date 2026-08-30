from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class ProjectDocumentORM(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("project_id", "original_name", name="uq_document_project_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DocumentVersionORM(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_version_number"),
        UniqueConstraint("project_id", "sha256", name="uq_document_project_sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    page_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SheetORM(Base):
    __tablename__ = "sheets"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "page_number",
            name="uq_sheet_document_version_page",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(500))
    discipline: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    sheet_number: Mapped[str | None] = mapped_column(String(120), index=True)
    sheet_name: Mapped[str | None] = mapped_column(String(500))
    revision: Mapped[str | None] = mapped_column(String(80))
    drawing_type: Mapped[str] = mapped_column(String(40), default="UNKNOWN", index=True)
    level_name: Mapped[str | None] = mapped_column(String(300))
    classification_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    page_type: Mapped[str] = mapped_column(String(20), default="UNKNOWN", index=True)
    page_type_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    orientation: Mapped[str] = mapped_column(String(20), default="PORTRAIT")
    standard_size: Mapped[str] = mapped_column(String(20), default="CUSTOM")
    discipline_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    discipline_method: Mapped[str | None] = mapped_column(String(80))
    drawing_type_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    drawing_type_method: Mapped[str | None] = mapped_column(String(80))
    review_status: Mapped[str] = mapped_column(String(30), default="REVIEW_REQUIRED", index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    width_points: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    height_points: Mapped[Decimal] = mapped_column(Numeric(12, 3))
