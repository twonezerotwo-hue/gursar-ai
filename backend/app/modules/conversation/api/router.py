from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.composition import build_quick_quote_service, build_quote_history_service
from app.contracts.v1.quick_quote import ChatTurnResponseV1, QuickQuoteV1
from app.core.database import get_session
from app.core.persistence import append_outbox
from app.modules.conversation.api.schemas import (
    ConversationResponse,
    CreateConversationRequest,
    MessageRequest,
    MessageResponse,
)
from app.modules.conversation.domain.models import MessageRole
from app.modules.conversation.infrastructure.repository import ConversationRepository
from app.modules.quick_quote.application.service import ConversationNotFoundError

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: CreateConversationRequest,
    session: Session = Depends(get_session),
) -> ConversationResponse:
    repository = ConversationRepository(session)
    conversation = repository.create(locale=payload.locale)
    repository.add_message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="Merhaba, nasıl bir iş planlıyorsunuz?",
    )
    append_outbox(
        session,
        event_type="conversation.started.v1",
        aggregate_id=conversation.id,
        payload={"source": "DIRECT"},
    )
    return _conversation_response(repository, conversation.id, conversation.locale)


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    session: Session = Depends(get_session),
) -> ConversationResponse:
    repository = ConversationRepository(session)
    conversation = repository.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Görüşme bulunamadı.")
    return _conversation_response(repository, conversation.id, conversation.locale)


@router.get("/{conversation_id}/quotes", response_model=list[QuickQuoteV1])
def get_latest_quotes(
    conversation_id: str,
    session: Session = Depends(get_session),
) -> list[QuickQuoteV1]:
    repository = ConversationRepository(session)
    conversation = repository.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Görüşme bulunamadı.")
    return build_quote_history_service(session).latest_for_conversation(
        conversation_id,
        conversation.locale,
    )


@router.post("/{conversation_id}/messages", response_model=ChatTurnResponseV1)
def add_message(
    conversation_id: str,
    payload: MessageRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> ChatTurnResponseV1:
    try:
        return build_quick_quote_service(session, request.app).handle_customer_message(
            conversation_id=conversation_id,
            text=payload.text.strip(),
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Görüşme bulunamadı.",
        ) from exc


def _conversation_response(
    repository: ConversationRepository,
    conversation_id: str,
    locale: str,
) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=conversation_id,
        locale=locale,
        messages=[
            MessageResponse(
                id=message.id,
                role=message.role.value,
                content=message.content,
                ordinal=message.ordinal,
                created_at=message.created_at,
            )
            for message in repository.list_messages(conversation_id)
        ],
    )
