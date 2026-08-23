from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.persistence import append_audit
from app.core.security import require_admin_key
from app.modules.pricing.api.schemas import (
    PriceResponse,
    PriceUpdateRequest,
    PriceVersionRequest,
)
from app.modules.pricing.application.service import PriceResolver
from app.modules.pricing.domain.models import NewPriceVersion, PriceRecord
from app.modules.pricing.infrastructure.repository import PriceBookRepository

router = APIRouter(
    prefix="/api/v1/admin/prices",
    tags=["admin-price-book"],
    dependencies=[Depends(require_admin_key)],
)


@router.post("", response_model=PriceResponse, status_code=status.HTTP_201_CREATED)
def create_price(
    payload: PriceVersionRequest,
    session: Session = Depends(get_session),
) -> PriceResponse:
    record = PriceBookRepository(session).add_version(
        NewPriceVersion(
            **payload.model_dump(),
            is_active=True,
            created_by="ADMIN_API",
        )
    )
    append_audit(
        session,
        action="PRICE_VERSION_CREATED",
        aggregate_id=record.id,
        detail={"service_code": record.service_code, "version": record.version},
        actor_type="ADMIN",
        actor_id="ADMIN_API",
    )
    return _response(record)


@router.post("/{price_id}/versions", response_model=PriceResponse)
def create_new_version(
    price_id: str,
    payload: PriceUpdateRequest,
    session: Session = Depends(get_session),
) -> PriceResponse:
    repository = PriceBookRepository(session)
    current = repository.get(price_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fiyat bulunamadı.")
    record = repository.add_version(
        NewPriceVersion(
            service_code=current.service_code,
            location_scope=current.location_scope,
            price=payload.price,
            unit=current.unit,
            currency=current.currency,
            vat_policy=current.vat_policy,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            is_active=True,
            created_by="ADMIN_API",
            notes=payload.notes,
            supersedes_id=current.id,
        )
    )
    append_audit(
        session,
        action="PRICE_VERSION_CREATED",
        aggregate_id=record.id,
        detail={"supersedes_id": current.id, "version": record.version},
        actor_type="ADMIN",
        actor_id="ADMIN_API",
    )
    return _response(record)


@router.post("/{price_id}/deactivate", response_model=PriceResponse)
def deactivate_price(
    price_id: str,
    session: Session = Depends(get_session),
) -> PriceResponse:
    repository = PriceBookRepository(session)
    current = repository.get(price_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fiyat bulunamadı.")
    record = repository.add_version(
        NewPriceVersion(
            service_code=current.service_code,
            location_scope=current.location_scope,
            price=current.price,
            unit=current.unit,
            currency=current.currency,
            vat_policy=current.vat_policy,
            valid_from=datetime.now(UTC),
            valid_to=None,
            is_active=False,
            created_by="ADMIN_API",
            notes="Deactivated through V1 admin API.",
            supersedes_id=current.id,
        )
    )
    append_audit(
        session,
        action="PRICE_DEACTIVATED",
        aggregate_id=record.id,
        detail={"supersedes_id": current.id},
        actor_type="ADMIN",
        actor_id="ADMIN_API",
    )
    return _response(record)


@router.get("", response_model=list[PriceResponse])
def list_price_history(
    service_code: str | None = Query(default=None),
    location_scope: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[PriceResponse]:
    records = PriceBookRepository(session).list_history(
        service_code=service_code,
        location_scope=location_scope,
    )
    return [_response(record) for record in records]


@router.get("/effective", response_model=PriceResponse)
def get_effective_price(
    service_code: str,
    location_code: str,
    session: Session = Depends(get_session),
) -> PriceResponse:
    record = PriceResolver(PriceBookRepository(session)).resolve(
        service_code=service_code,
        location_code=location_code,
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etkin fiyat bulunamadı.")
    return _response(record)


def _response(record: PriceRecord) -> PriceResponse:
    return PriceResponse(**asdict(record))
