from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.quick_quote.infrastructure.models import QuickQuoteORM


@dataclass(frozen=True, slots=True)
class StoredQuickQuote:
    id: str
    job_id: str
    service_code: str
    price_version: int
    quantity: Decimal | None
    unit_price: Decimal
    total_price: Decimal | None
    currency: str
    vat_policy: str


class QuickQuoteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        *,
        conversation_id: str,
        job_id: str,
        service_code: str,
        price_book_id: str,
        price_version: int,
        quantity: Decimal | None,
        unit_price: Decimal,
        total_price: Decimal | None,
        currency: str,
        vat_policy: str,
    ) -> str:
        expression = (
            f"{quantity} * {unit_price} = {total_price}"
            if quantity is not None and total_price is not None
            else f"unit_price = {unit_price}"
        )
        row = QuickQuoteORM(
            conversation_id=conversation_id,
            job_id=job_id,
            service_code=service_code,
            price_book_id=price_book_id,
            price_version=price_version,
            quantity=quantity,
            unit_price=unit_price,
            total_price=total_price,
            currency=currency,
            vat_policy=vat_policy,
            calculation_expression=expression,
        )
        self._session.add(row)
        self._session.flush()
        return row.id

    def list_latest_by_conversation(self, conversation_id: str) -> list[StoredQuickQuote]:
        rows = self._session.scalars(
            select(QuickQuoteORM)
            .where(QuickQuoteORM.conversation_id == conversation_id)
            .order_by(QuickQuoteORM.created_at.desc())
        ).all()
        latest_by_job: dict[str, QuickQuoteORM] = {}
        for row in rows:
            latest_by_job.setdefault(row.job_id, row)
        return [
            StoredQuickQuote(
                id=row.id,
                job_id=row.job_id,
                service_code=row.service_code,
                price_version=row.price_version,
                quantity=row.quantity,
                unit_price=row.unit_price,
                total_price=row.total_price,
                currency=row.currency,
                vat_policy=row.vat_policy,
            )
            for row in reversed(list(latest_by_job.values()))
        ]
