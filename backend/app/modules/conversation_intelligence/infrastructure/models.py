from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class ConversationProjectContextORM(Base):
    __tablename__ = "conversation_project_contexts"
    __table_args__ = (UniqueConstraint("conversation_id", name="uq_conversation_project_context"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    project_type: Mapped[str | None] = mapped_column(String(60))
    footprint_m2: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    gross_area_m2: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    floor_count: Mapped[int | None] = mapped_column(Integer)
    structural_system: Mapped[str | None] = mapped_column(String(80))
    stage: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(240))
    goal: Mapped[str | None] = mapped_column(String(80))
    available_crew: Mapped[int | None] = mapped_column(Integer)
    facts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ConversationPlanORM(Base):
    __tablename__ = "conversation_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    context_version: Mapped[int] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(20))
    duration_days: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    peak_crew: Mapped[int] = mapped_column(Integer)
    schedule_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    assumptions_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
