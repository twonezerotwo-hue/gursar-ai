from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class OutboxEventORM(Base):
    __tablename__ = "outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEventORM(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    action: Mapped[str] = mapped_column(String(120), index=True)
    actor_type: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str | None] = mapped_column(String(120))
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


def append_outbox(
    session: Session,
    *,
    event_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        OutboxEventORM(
            event_type=event_type,
            aggregate_id=aggregate_id,
            payload=payload,
        )
    )


def append_audit(
    session: Session,
    *,
    action: str,
    aggregate_id: str,
    detail: dict[str, Any],
    actor_type: str = "SYSTEM",
    actor_id: str | None = None,
) -> None:
    session.add(
        AuditEventORM(
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            aggregate_id=aggregate_id,
            detail=detail,
        )
    )
