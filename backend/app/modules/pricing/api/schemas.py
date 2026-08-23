from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PriceVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_code: str = Field(min_length=3, max_length=160)
    location_scope: str = Field(min_length=2, max_length=200)
    price: Decimal = Field(ge=0)
    unit: str = Field(min_length=1, max_length=24)
    currency: str = "TRY"
    vat_policy: str = "EXCLUDED"
    valid_from: datetime
    valid_to: datetime | None = None
    notes: str | None = None


class PriceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: Decimal = Field(ge=0)
    valid_from: datetime
    valid_to: datetime | None = None
    notes: str | None = None


class PriceResponse(BaseModel):
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
