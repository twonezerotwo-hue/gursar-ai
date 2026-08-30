from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolCapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    purpose: str
    github_url: str
    formats: list[str]
    available: bool
    version: str | None


class DocumentToolsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted_extensions: list[str]
    max_upload_bytes: int
    tools: list[ToolCapabilityResponse]


class DocumentAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    format: str
    media_type: str
    tools_used: list[str]
    summary: str
    extracted_text: str
    metrics: dict[str, Any]
    analysis_id: str | None
    conversation_id: str | None
    sha256: str
    size_bytes: int
    cached: bool
