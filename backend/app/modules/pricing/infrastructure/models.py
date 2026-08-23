from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class PriceBookORM(Base):
    __tablename__ = "price_book"
    __table_args__ = (
        UniqueConstraint(
            "service_code",
            "location_scope",
            "version",
            name="uq_price_service_location_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    service_code: Mapped[str] = mapped_column(String(160), index=True)
    location_scope: Mapped[str] = mapped_column(String(200), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    unit: Mapped[str] = mapped_column(String(24))
    currency: Mapped[str] = mapped_column(String(8), default="TRY")
    vat_policy: Mapped[str] = mapped_column(String(40), default="EXCLUDED")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_by: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), index=True)
