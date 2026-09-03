from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class ArchitecturalZoneORM(Base):
    __tablename__ = "architectural_zones"
    __table_args__ = (
        UniqueConstraint("project_id", "zone_key", name="uq_architectural_zone_project_key"),
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
    zone_key: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(300))
    zone_type: Mapped[str] = mapped_column(String(50), index=True)
    review_status: Mapped[str] = mapped_column(String(30), default="VERIFIED", index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="MANUAL")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ArchitecturalZoneMembershipORM(Base):
    __tablename__ = "architectural_zone_memberships"
    __table_args__ = (
        UniqueConstraint("zone_id", "element_id", name="uq_architectural_zone_element"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    zone_id: Mapped[str] = mapped_column(
        ForeignKey("architectural_zones.id", ondelete="CASCADE"), index=True
    )
    element_id: Mapped[str] = mapped_column(
        ForeignKey("building_elements.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ArchitectureAnalysisRunORM(Base):
    """Immutable deterministic architecture QA result."""

    __tablename__ = "architecture_analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "analysis_version",
            "input_checksum",
            name="uq_architecture_analysis_input",
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
    reviewer: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
