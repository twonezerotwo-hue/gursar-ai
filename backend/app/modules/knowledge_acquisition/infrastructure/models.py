from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class KnowledgeSourceORM(Base):
    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_key: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(500), index=True)
    authority: Mapped[str] = mapped_column(String(300), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_class: Mapped[str] = mapped_column(String(50), index=True)
    collection: Mapped[str] = mapped_column(
        String(50), default="TECHNICAL_SPECIFICATIONS", index=True
    )
    quality_score: Mapped[int] = mapped_column(Integer)
    license: Mapped[str] = mapped_column(String(160))
    copyright_status: Mapped[str] = mapped_column(String(160))
    redistribution_policy: Mapped[str] = mapped_column(String(30), index=True)
    taxonomy_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeDocumentVersionORM(Base):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "version_number", name="uq_knowledge_source_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    version_label: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), index=True)
    published_date: Mapped[date | None] = mapped_column(Date)
    effective_from: Mapped[date | None] = mapped_column(Date, index=True)
    effective_until: Mapped[date | None] = mapped_column(Date)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(Text)
    checksum_sha256: Mapped[str] = mapped_column(String(64), index=True)
    content_type: Mapped[str] = mapped_column(String(120))
    parser_name: Mapped[str] = mapped_column(String(100))
    parsed_text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    ocr_derived: Mapped[bool] = mapped_column(Boolean, default=False)
    etag: Mapped[str | None] = mapped_column(String(240))
    last_modified: Mapped[str | None] = mapped_column(String(240))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class KnowledgeChunkORM(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "chunk_index", name="uq_knowledge_chunk_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"),
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    hierarchy_type: Mapped[str] = mapped_column(String(40), index=True)
    heading: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    normalized_content: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[list[float]] = mapped_column(JSON)
    taxonomy_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    checksum_sha256: Mapped[str] = mapped_column(String(64), index=True)


class KnowledgeRuleORM(Base):
    __tablename__ = "knowledge_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    document_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"),
        index=True,
    )
    topic: Mapped[str] = mapped_column(String(120), index=True)
    building_type: Mapped[str] = mapped_column(String(80), default="all")
    condition: Mapped[str] = mapped_column(Text)
    requirement: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(40))
    article: Mapped[str | None] = mapped_column(String(240))
    source_class: Mapped[str] = mapped_column(String(50))
    effective_from: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), index=True)
    extraction_method: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeFormulaORM(Base):
    __tablename__ = "knowledge_formulas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    formula_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300))
    discipline: Mapped[str] = mapped_column(String(80), index=True)
    formula: Mapped[str] = mapped_column(Text)
    variables_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    units_json: Mapped[dict[str, str]] = mapped_column(JSON)
    conditions_json: Mapped[list[str]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(Text)
    source_class: Mapped[str] = mapped_column(String(50))
    version: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class KnowledgeConflictORM(Base):
    __tablename__ = "knowledge_conflicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conflict_key: Mapped[str] = mapped_column(String(160), index=True)
    left_rule_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_rules.id"))
    right_rule_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_rules.id"))
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="OPEN", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeChangeORM(Base):
    __tablename__ = "knowledge_changes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_sources.id"))
    old_version_id: Mapped[str] = mapped_column(String(36))
    new_version_id: Mapped[str] = mapped_column(String(36))
    change_type: Mapped[str] = mapped_column(String(30), index=True)
    section_key: Mapped[str] = mapped_column(String(500))
    old_text: Mapped[str] = mapped_column(Text)
    new_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeAcquisitionRunORM(Base):
    __tablename__ = "knowledge_acquisition_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(30), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failures_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
