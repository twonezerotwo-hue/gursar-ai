from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=240)
    locale: str = Field(default="tr", min_length=2, max_length=12)


class ProjectResponse(BaseModel):
    id: str
    name: str
    locale: str
    status: str
    created_at: datetime
    updated_at: datetime
