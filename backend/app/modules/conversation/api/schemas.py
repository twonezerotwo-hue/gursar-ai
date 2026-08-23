from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str = "tr"


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    ordinal: int
    created_at: datetime


class ConversationResponse(BaseModel):
    conversation_id: str
    locale: str
    messages: list[MessageResponse]
