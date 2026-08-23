from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.persistence import new_id, utc_now


class WorkPackageORM(Base):
    __tablename__ = "work_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    service_code: Mapped[str] = mapped_column(String(160), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    customer_scope_summaries: Mapped[dict[str, str]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkPackageItemORM(Base):
    __tablename__ = "work_package_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    work_package_id: Mapped[str] = mapped_column(
        ForeignKey("work_packages.id", ondelete="CASCADE"), index=True
    )
    item_code: Mapped[str] = mapped_column(String(160))
    labels: Mapped[dict[str, str]] = mapped_column(JSON)
    default_included: Mapped[bool] = mapped_column(Boolean, default=True)
    supply_by: Mapped[str] = mapped_column(String(32))
    pricing_role: Mapped[str] = mapped_column(String(32))
    display_order: Mapped[int] = mapped_column(Integer, default=0)
