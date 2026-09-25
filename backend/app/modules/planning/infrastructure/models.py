from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class ProductivityRecordORM(Base):
    __tablename__ = "productivity_records"
    __table_args__ = (
        UniqueConstraint(
            "service_code", "project_type", "version", name="uq_productivity_record_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    service_code: Mapped[str] = mapped_column(String(120), index=True)
    work_type: Mapped[str | None] = mapped_column(String(160), index=True)
    unit: Mapped[str] = mapped_column(String(30))
    output_per_crew_day: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    crew_size: Mapped[int] = mapped_column(Integer)
    crew_composition_json: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    sample_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    sample_duration_days: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    productive_hours: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    project_type: Mapped[str] = mapped_column(String(60), index=True)
    location: Mapped[str | None] = mapped_column(String(240), index=True)
    floor: Mapped[str | None] = mapped_column(String(100))
    conditions: Mapped[str] = mapped_column(String(500))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.5"))
    source_tier: Mapped[str] = mapped_column(String(40), default="HEURISTIC", index=True)
    source: Mapped[str] = mapped_column(String(300))
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScheduleBaselineORM(Base):
    __tablename__ = "schedule_baselines"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_schedule_baseline_version"),
        UniqueConstraint(
            "project_id",
            "analysis_version",
            "input_checksum",
            name="uq_schedule_baseline_input",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(240))
    version: Mapped[int] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(20))
    duration_days: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    status: Mapped[str] = mapped_column(String(30), default="FROZEN", index=True)
    analysis_version: Mapped[str] = mapped_column(String(80))
    input_checksum: Mapped[str] = mapped_column(String(64), index=True)
    activities_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    capacities_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    overloads_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    assumptions_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScheduleProgressUpdateORM(Base):
    __tablename__ = "schedule_progress_updates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    baseline_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_baselines.id", ondelete="CASCADE"), index=True
    )
    activity_id: Mapped[str] = mapped_column(String(80), index=True)
    observed_day: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    actual_progress: Mapped[Decimal] = mapped_column(Numeric(7, 6))
    planned_progress: Mapped[Decimal] = mapped_column(Numeric(7, 6))
    progress_variance: Mapped[Decimal] = mapped_column(Numeric(7, 6))
    actual_start_day: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    actual_finish_day: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    forecast_finish_day: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    finish_variance_days: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    source: Mapped[str] = mapped_column(String(300))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScheduleDelayEventORM(Base):
    __tablename__ = "schedule_delay_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    baseline_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_baselines.id", ondelete="CASCADE"), index=True
    )
    activity_id: Mapped[str] = mapped_column(String(80), index=True)
    root_cause: Mapped[str] = mapped_column(String(40), index=True)
    impact_days: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    project_impact_days: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    reason: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScheduleActivityElementLinkORM(Base):
    __tablename__ = "schedule_activity_element_links"
    __table_args__ = (
        UniqueConstraint(
            "baseline_id",
            "activity_id",
            "element_id",
            name="uq_schedule_activity_element_link",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    baseline_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_baselines.id", ondelete="CASCADE"), index=True
    )
    activity_id: Mapped[str] = mapped_column(String(80), index=True)
    element_id: Mapped[str] = mapped_column(
        ForeignKey("building_elements.id", ondelete="CASCADE"), index=True
    )
    link_method: Mapped[str] = mapped_column(String(40), default="ELEMENT_TYPE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
