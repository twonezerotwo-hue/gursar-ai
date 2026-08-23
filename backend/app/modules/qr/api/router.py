from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.modules.conversation.api.schemas import MessageResponse
from app.modules.conversation.infrastructure.repository import ConversationRepository
from app.modules.qr.api.schemas import QRConversationResponse
from app.modules.qr.application.service import (
    QRConversationService,
    QRSourceNotFoundError,
)
from app.modules.qr.infrastructure.repository import QRSourceRepository

router = APIRouter(prefix="/api/v1/qr", tags=["qr"])


@router.post("/{code}/conversation", response_model=QRConversationResponse)
def start_qr_conversation(
    code: str,
    request: Request,
    session: Session = Depends(get_session),
) -> QRConversationResponse:
    service = QRConversationService(
        session=session,
        qr_sources=QRSourceRepository(session),
        conversations=ConversationRepository(session),
        locale=request.app.state.settings.default_locale,
    )
    try:
        result = service.start_or_resume(code)
    except QRSourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QR kaynağı bulunamadı.",
        ) from exc
    return QRConversationResponse(
        conversation_id=result.conversation_id,
        source_code=result.source_code,
        source_name=result.source_name,
        resumed=result.resumed,
        messages=[
            MessageResponse(
                id=message.id,
                role=message.role.value,
                content=message.content,
                ordinal=message.ordinal,
                created_at=message.created_at,
            )
            for message in result.messages
        ],
    )
