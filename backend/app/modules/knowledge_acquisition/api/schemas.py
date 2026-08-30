from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class AcquisitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_keys: list[str] = Field(default_factory=list, max_length=100)
    approve_official: bool = False


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=1000)
    limit: int = Field(default=5, ge=1, le=20)
    as_of: date | None = None
    filters: dict[str, str] = Field(default_factory=dict)
