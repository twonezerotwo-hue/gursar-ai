from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    locale: str
    qr_source_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    conversation_id: str
    role: MessageRole
    content: str
    ordinal: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CustomerJob:
    id: str
    conversation_id: str
    service_code: str | None
    quantity: Decimal | None
    unit: str | None
    location_code: str | None
    location_label: str | None
    scope_state: list[dict[str, Any]] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    version: int = 1
