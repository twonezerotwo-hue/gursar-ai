from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.persistence import append_audit, append_outbox
from app.modules.conversation.domain.models import Message, MessageRole
from app.modules.conversation.infrastructure.repository import ConversationRepository
from app.modules.qr.infrastructure.repository import QRSourceRepository


class QRSourceNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class QRConversationStart:
    conversation_id: str
    source_code: str
    source_name: str
    resumed: bool
    messages: list[Message]


class QRConversationService:
    def __init__(
        self,
        *,
        session: Session,
        qr_sources: QRSourceRepository,
        conversations: ConversationRepository,
        locale: str,
    ) -> None:
        self._session = session
        self._qr_sources = qr_sources
        self._conversations = conversations
        self._locale = locale

    def start_or_resume(self, code: str) -> QRConversationStart:
        source = self._qr_sources.get_active_by_code(code)
        if source is None:
            raise QRSourceNotFoundError(code)

        conversation = self._conversations.find_latest_by_qr_source(source.id)
        resumed = conversation is not None
        if conversation is None:
            conversation = self._conversations.create(
                locale=self._locale,
                qr_source_id=source.id,
            )
            self._conversations.add_message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content="Merhaba, nasıl bir iş planlıyorsunuz?",
            )
            append_outbox(
                self._session,
                event_type="conversation.started.v1",
                aggregate_id=conversation.id,
                payload={"qr_source_code": source.code, "qr_source_id": source.id},
            )
            append_audit(
                self._session,
                action="QR_CONVERSATION_STARTED",
                aggregate_id=conversation.id,
                detail={"qr_source_code": source.code},
                actor_type="ANONYMOUS_CUSTOMER",
            )

        return QRConversationStart(
            conversation_id=conversation.id,
            source_code=source.code,
            source_name=source.display_name,
            resumed=resumed,
            messages=self._conversations.list_messages(conversation.id),
        )
