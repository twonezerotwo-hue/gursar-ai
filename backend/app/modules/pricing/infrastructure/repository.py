from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.pricing.domain.models import NewPriceVersion, PriceRecord
from app.modules.pricing.infrastructure.models import PriceBookORM


class PriceBookRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_version(self, draft: NewPriceVersion) -> PriceRecord:
        latest_version = self._session.scalar(
            select(func.coalesce(func.max(PriceBookORM.version), 0)).where(
                PriceBookORM.service_code == draft.service_code,
                PriceBookORM.location_scope == draft.location_scope,
            )
        )
        row = PriceBookORM(
            service_code=draft.service_code,
            location_scope=draft.location_scope,
            price=draft.price,
            unit=draft.unit,
            currency=draft.currency,
            vat_policy=draft.vat_policy,
            valid_from=draft.valid_from,
            valid_to=draft.valid_to,
            version=int(latest_version or 0) + 1,
            is_active=draft.is_active,
            created_by=draft.created_by,
            notes=draft.notes,
            supersedes_id=draft.supersedes_id,
        )
        self._session.add(row)
        self._session.flush()
        return self._to_domain(row)

    def get(self, price_id: str) -> PriceRecord | None:
        row = self._session.get(PriceBookORM, price_id)
        return self._to_domain(row) if row else None

    def latest_version_for_scope(
        self,
        *,
        service_code: str,
        location_scope: str,
        effective_at: datetime,
    ) -> PriceRecord | None:
        row = self._session.scalar(
            select(PriceBookORM)
            .where(
                PriceBookORM.service_code == service_code,
                PriceBookORM.location_scope == location_scope,
                PriceBookORM.valid_from <= effective_at,
                or_(PriceBookORM.valid_to.is_(None), PriceBookORM.valid_to > effective_at),
            )
            .order_by(PriceBookORM.version.desc())
            .limit(1)
        )
        return self._to_domain(row) if row else None

    def list_history(
        self,
        *,
        service_code: str | None = None,
        location_scope: str | None = None,
    ) -> list[PriceRecord]:
        statement = select(PriceBookORM)
        if service_code:
            statement = statement.where(PriceBookORM.service_code == service_code)
        if location_scope:
            statement = statement.where(PriceBookORM.location_scope == location_scope)
        rows = self._session.scalars(
            statement.order_by(
                PriceBookORM.service_code,
                PriceBookORM.location_scope,
                PriceBookORM.version.desc(),
            )
        ).all()
        return [self._to_domain(row) for row in rows]

    @staticmethod
    def _to_domain(row: PriceBookORM) -> PriceRecord:
        return PriceRecord(
            id=row.id,
            service_code=row.service_code,
            location_scope=row.location_scope,
            price=row.price,
            unit=row.unit,
            currency=row.currency,
            vat_policy=row.vat_policy,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            version=row.version,
            is_active=row.is_active,
            created_at=row.created_at,
            created_by=row.created_by,
            notes=row.notes,
            supersedes_id=row.supersedes_id,
        )
