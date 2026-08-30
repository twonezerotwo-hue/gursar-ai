from __future__ import annotations

import hashlib
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.composition import build_quick_quote_service, build_quote_history_service
from app.contracts.v1.quick_quote import ChatTurnResponseV1, EstimateLedgerV1, QuickQuoteV1
from app.core.database import get_session
from app.core.persistence import append_outbox
from app.core.security import ConversationTokenSigner, conversation_cookie_name
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
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: CreateConversationRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> ConversationResponse:
    repository = ConversationRepository(session)
    conversation = repository.create(locale=payload.locale)
    owner = repository.create_owner_session(
        conversation_id=conversation.id,
        signer=_signer(request),
        ttl_seconds=request.app.state.settings.conversation_session_ttl_seconds,
    )
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
    _set_owner_cookie(response, request, conversation.id, owner.raw_token)
    return _conversation_response(
        repository,
        conversation.id,
        conversation.locale,
        owner_token=owner.raw_token,
        session_expires_at=owner.expires_at,
    )


def require_conversation_owner(
    conversation_id: str,
    request: Request,
    owner_header: str | None = Header(default=None, alias="X-Conversation-Owner-Token"),
    session: Session = Depends(get_session),
) -> None:
    raw_token = owner_header or request.cookies.get(conversation_cookie_name(conversation_id))
    if not raw_token or not ConversationRepository(session).owns_conversation(
        conversation_id=conversation_id,
        raw_token=raw_token,
        signer=_signer(request),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu görüşmeye erişim yetkiniz yok veya oturumun süresi dolmuş.",
        )


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    _owner: None = Depends(require_conversation_owner),
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
    _owner: None = Depends(require_conversation_owner),
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


@router.get("/{conversation_id}/ledger", response_model=EstimateLedgerV1)
def get_estimate_ledger(
    conversation_id: str,
    _owner: None = Depends(require_conversation_owner),
    session: Session = Depends(get_session),
) -> EstimateLedgerV1:
    repository = ConversationRepository(session)
    conversation = repository.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Görüşme bulunamadı.")
    return build_quote_history_service(session).ledger_for_conversation(
        conversation_id,
        conversation.locale,
    )


@router.post("/{conversation_id}/messages", response_model=ChatTurnResponseV1)
def add_message(
    conversation_id: str,
    payload: MessageRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _owner: None = Depends(require_conversation_owner),
    session: Session = Depends(get_session),
) -> ChatTurnResponseV1:
    normalized_text = payload.text.strip()
    request_sha256 = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    repository = ConversationRepository(session)
    if idempotency_key is not None:
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Idempotency-Key 8-128 karakter olmalı ve yalnızca güvenli "
                    "karakterler içermelidir."
                ),
            )
        receipt = repository.get_turn_receipt(
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        if receipt is not None:
            if receipt.request_sha256 != request_sha256:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Bu Idempotency-Key daha önce farklı bir mesaj için kullanıldı.",
                )
            return ChatTurnResponseV1.model_validate(receipt.response_payload)
    try:
        response = build_quick_quote_service(session, request.app).handle_customer_message(
            conversation_id=conversation_id,
            text=normalized_text,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Görüşme bulunamadı.",
        ) from exc
    if idempotency_key is not None:
        repository.save_turn_receipt(
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            response_payload=response.model_dump(mode="json"),
        )
    return response


def _conversation_response(
    repository: ConversationRepository,
    conversation_id: str,
    locale: str,
    owner_token: str | None = None,
    session_expires_at=None,
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
        conversation_owner_token=owner_token,
        session_expires_at=session_expires_at,
    )


def _signer(request: Request) -> ConversationTokenSigner:
    return ConversationTokenSigner(request.app.state.settings.conversation_token_secret)


def _set_owner_cookie(
    response: Response,
    request: Request,
    conversation_id: str,
    raw_token: str,
) -> None:
    response.set_cookie(
        key=conversation_cookie_name(conversation_id),
        value=raw_token,
        max_age=request.app.state.settings.conversation_session_ttl_seconds,
        httponly=True,
        secure=request.url.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https",
        samesite="strict",
        path=f"/api/v1/conversations/{conversation_id}",
    )
