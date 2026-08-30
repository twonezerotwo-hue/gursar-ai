from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.security import ConversationTokenSigner, conversation_cookie_name
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
    response: Response,
    session: Session = Depends(get_session),
) -> QRConversationResponse:
    service = QRConversationService(
        session=session,
        qr_sources=QRSourceRepository(session),
        conversations=ConversationRepository(session),
        locale=request.app.state.settings.default_locale,
        token_signer=ConversationTokenSigner(request.app.state.settings.conversation_token_secret),
        session_ttl_seconds=request.app.state.settings.conversation_session_ttl_seconds,
    )
    try:
        result = service.start_or_resume(code)
    except QRSourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QR kaynağı bulunamadı.",
        ) from exc
    response.set_cookie(
        key=conversation_cookie_name(result.conversation_id),
        value=result.conversation_owner_token,
        max_age=request.app.state.settings.conversation_session_ttl_seconds,
        httponly=True,
        secure=request.url.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https",
        samesite="strict",
        path=f"/api/v1/conversations/{result.conversation_id}",
    )
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
        conversation_owner_token=result.conversation_owner_token,
        session_expires_at=result.session_expires_at,
    )
