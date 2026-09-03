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


class KnowledgeRuleEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=1000)
    jurisdiction: str = Field(default="TR", min_length=2, max_length=40)
    building_type: str = Field(default="all", min_length=1, max_length=80)
    topic: str | None = Field(default=None, min_length=1, max_length=120)
    as_of: date | None = None
