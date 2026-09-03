from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, UniqueConstraint
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
    unit: Mapped[str] = mapped_column(String(30))
    output_per_crew_day: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    crew_size: Mapped[int] = mapped_column(Integer)
    project_type: Mapped[str] = mapped_column(String(60), index=True)
    location: Mapped[str | None] = mapped_column(String(240), index=True)
    conditions: Mapped[str] = mapped_column(String(500))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source: Mapped[str] = mapped_column(String(300))
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
