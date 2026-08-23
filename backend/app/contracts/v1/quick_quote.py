from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QuickQuoteV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "quick-quote.v1"
    quote_id: str | None = None
    job_id: str
    service_code: str
    service_label: str
    location_code: str
    location_label: str
    quantity: Decimal | None = None
    unit: str
    unit_price: Decimal | None = None
    total_price: Decimal | None = None
    currency: str = "TRY"
    vat_policy: str = "EXCLUDED"
    price_version: int | None = None
    status: str
    scope_summary: str
    scope_items: list[dict[str, Any]] = Field(default_factory=list)
    clarification_question: str | None = None
    customer_text: str


class ChatTurnResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    assistant_message_id: str
    assistant_text: str
    quotes: list[QuickQuoteV1] = Field(default_factory=list)
