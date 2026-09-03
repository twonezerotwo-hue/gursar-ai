from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class StructuralAnalysisRunORM(Base):
    """Immutable deterministic load-path assessment."""

    __tablename__ = "structural_analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "analysis_version",
            "input_checksum",
            name="uq_structural_analysis_input",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    analysis_version: Mapped[str] = mapped_column(String(80), index=True)
    input_checksum: Mapped[str] = mapped_column(String(64), index=True)
    metrics_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    issues_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    assumptions_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    graph_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reviewer: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StructuralLoadORM(Base):
    """Source-linked load input belonging to one immutable analysis run."""

    __tablename__ = "structural_loads"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "load_key", name="uq_structural_load_run_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("structural_analysis_runs.id", ondelete="CASCADE"), index=True
    )
    element_id: Mapped[str] = mapped_column(
        ForeignKey("building_elements.id", ondelete="CASCADE"), index=True
    )
    load_key: Mapped[str] = mapped_column(String(160))
    load_type: Mapped[str] = mapped_column(String(30), index=True)
    magnitude_kn: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    factor: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    direction: Mapped[str] = mapped_column(String(30))
    source_reference: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
