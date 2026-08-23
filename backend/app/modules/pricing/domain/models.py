from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PriceRecord:
    id: str
    service_code: str
    location_scope: str
    price: Decimal
    unit: str
    currency: str
    vat_policy: str
    valid_from: datetime
    valid_to: datetime | None
    version: int
    is_active: bool
    created_at: datetime
    created_by: str
    notes: str | None
    supersedes_id: str | None


@dataclass(frozen=True, slots=True)
class NewPriceVersion:
    service_code: str
    location_scope: str
    price: Decimal
    unit: str
    currency: str
    vat_policy: str
    valid_from: datetime
    valid_to: datetime | None
    is_active: bool
    created_by: str
    notes: str | None = None
    supersedes_id: str | None = None
