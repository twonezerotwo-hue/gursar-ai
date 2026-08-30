from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.modules.conversation.api.schemas import MessageResponse


class QRConversationResponse(BaseModel):
    conversation_id: str
    source_code: str
    source_name: str
    resumed: bool
    messages: list[MessageResponse]
    conversation_owner_token: str
    session_expires_at: datetime
