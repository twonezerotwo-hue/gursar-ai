from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolCapability:
    id: str
    name: str
    purpose: str
    github_url: str
    formats: tuple[str, ...]
    available: bool
    version: str | None


@dataclass(frozen=True, slots=True)
class DocumentAnalysis:
    filename: str
    format: str
    media_type: str
    tools_used: tuple[str, ...]
    summary: str
    extracted_text: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    analysis_id: str | None = None
    conversation_id: str | None = None
    sha256: str = ""
    size_bytes: int = 0
    cached: bool = False
