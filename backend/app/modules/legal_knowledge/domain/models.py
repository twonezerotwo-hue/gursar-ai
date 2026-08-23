from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class LegalSourceDefinition:
    key: str
    title: str
    category: str
    authority: str
    source_url: str
    published_on: date | None = None
    official_gazette_number: str | None = None
    discover_official_links: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LegalContextChunk:
    document_key: str
    title: str
    category: str
    authority: str
    source_url: str
    fetched_at: str
    chunk_index: int
    content: str
    score: float
