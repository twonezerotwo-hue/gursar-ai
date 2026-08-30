from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_type: str
    project_id: str
    document_version_id: str | None = None
    operation_version: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    job_type: str
    project_id: str
    document_version_id: str | None
    operation_version: str
    status: str
    progress: int
    current_stage: str
    result_json: dict[str, Any]
    error_code: str | None
    error_message: str | None
    worker_version: str | None
    result_reference: str | None
    retry_count: int
    max_retries: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    reused: bool = False
