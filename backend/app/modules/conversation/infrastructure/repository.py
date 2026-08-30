from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import ConversationTokenSigner, SignedConversationToken
from app.modules.conversation.domain.models import (
    Conversation,
    ConversationTurnReceipt,
    CustomerJob,
    CustomerJobStatus,
    Message,
    MessageRole,
)
from app.modules.conversation.infrastructure.models import (
    ConversationORM,
    ConversationTurnReceiptORM,
    CustomerJobORM,
    MessageORM,
    VisitorSessionORM,
)


class ConversationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, locale: str, qr_source_id: str | None = None) -> Conversation:
        row = ConversationORM(locale=locale, qr_source_id=qr_source_id)
        self._session.add(row)
        self._session.flush()
        return self._to_conversation(row)

    def get(self, conversation_id: str) -> Conversation | None:
        row = self._session.get(ConversationORM, conversation_id)
        return self._to_conversation(row) if row else None

    def create_owner_session(
        self,
        *,
        conversation_id: str,
        signer: ConversationTokenSigner,
        ttl_seconds: int,
    ) -> SignedConversationToken:
        session_row = VisitorSessionORM(
            conversation_id=conversation_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            owner_token_sha256="pending",
        )
        self._session.add(session_row)
        self._session.flush()
        token = signer.issue(session_id=session_row.id, expires_at=session_row.expires_at)
        session_row.owner_token_sha256 = token.token_sha256
        self._session.flush()
        return token

    def owns_conversation(
        self,
        *,
        conversation_id: str,
        raw_token: str,
        signer: ConversationTokenSigner,
        now: datetime | None = None,
    ) -> bool:
        verified = signer.verify(raw_token, now=now)
        if verified is None:
            return False
        session_id, _ = verified
        row = self._session.get(VisitorSessionORM, session_id)
        if row is None or row.conversation_id != conversation_id:
            return False
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        current = now or datetime.now(UTC)
        return expires_at > current and hmac.compare_digest(
            row.owner_token_sha256,
            hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        )

    def find_latest_by_qr_source(self, qr_source_id: str) -> Conversation | None:
        row = self._session.scalar(
            select(ConversationORM)
            .where(ConversationORM.qr_source_id == qr_source_id)
            .order_by(ConversationORM.created_at.desc())
            .limit(1)
        )
        return self._to_conversation(row) if row else None

    def add_message(
        self,
        *,
        conversation_id: str,
        role: MessageRole,
        content: str,
    ) -> Message:
        next_ordinal = self._session.scalar(
            select(func.coalesce(func.max(MessageORM.ordinal), 0) + 1).where(
                MessageORM.conversation_id == conversation_id
            )
        )
        row = MessageORM(
            conversation_id=conversation_id,
            role=role.value,
            content=content,
            ordinal=int(next_ordinal or 1),
        )
        self._session.add(row)
        self._session.flush()
        return self._to_message(row)

    def list_messages(self, conversation_id: str) -> list[Message]:
        rows = self._session.scalars(
            select(MessageORM)
            .where(MessageORM.conversation_id == conversation_id)
            .order_by(MessageORM.ordinal)
        ).all()
        return [self._to_message(row) for row in rows]

    def get_turn_receipt(
        self,
        *,
        conversation_id: str,
        idempotency_key: str,
    ) -> ConversationTurnReceipt | None:
        row = self._session.scalar(
            select(ConversationTurnReceiptORM).where(
                ConversationTurnReceiptORM.conversation_id == conversation_id,
                ConversationTurnReceiptORM.idempotency_key == idempotency_key,
            )
        )
        if row is None:
            return None
        return ConversationTurnReceipt(
            conversation_id=row.conversation_id,
            idempotency_key=row.idempotency_key,
            request_sha256=row.request_sha256,
            response_payload=row.response_payload,
            created_at=row.created_at,
        )

    def save_turn_receipt(
        self,
        *,
        conversation_id: str,
        idempotency_key: str,
        request_sha256: str,
        response_payload: dict[str, Any],
    ) -> None:
        self._session.add(
            ConversationTurnReceiptORM(
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                response_payload=response_payload,
            )
        )
        self._session.flush()

    def list_jobs(self, conversation_id: str) -> list[CustomerJob]:
        rows = self._session.scalars(
            select(CustomerJobORM)
            .where(
                CustomerJobORM.conversation_id == conversation_id,
                CustomerJobORM.status != CustomerJobStatus.REMOVED.value,
            )
            .order_by(CustomerJobORM.created_at)
        ).all()
        return [self._to_job(row) for row in rows]

    def save_job(
        self,
        *,
        conversation_id: str,
        job_id: str | None,
        service_code: str | None,
        quantity: Decimal | None,
        unit: str | None,
        location_code: str | None,
        location_label: str | None,
        scope_state: list[dict[str, Any]],
        provenance: list[dict[str, Any]],
        status: CustomerJobStatus | None = None,
    ) -> CustomerJob:
        row = self._session.get(CustomerJobORM, job_id) if job_id else None
        if row is None:
            row = CustomerJobORM(conversation_id=conversation_id)
            self._session.add(row)
        else:
            row.version += 1

        row.service_code = service_code
        row.quantity = quantity
        row.unit = unit
        row.location_code = location_code
        row.location_label = location_label
        row.scope_state = scope_state
        row.provenance = provenance
        row.status = (
            status.value
            if status is not None
            else CustomerJobStatus.ACTIVE.value
            if quantity is not None
            else CustomerJobStatus.PENDING.value
        )
        self._session.flush()
        return self._to_job(row)

    def remove_job(self, *, conversation_id: str, job_id: str) -> CustomerJob | None:
        row = self._session.get(CustomerJobORM, job_id)
        if row is None or row.conversation_id != conversation_id:
            return None
        row.status = CustomerJobStatus.REMOVED.value
        row.version += 1
        self._session.flush()
        return self._to_job(row)

    @staticmethod
    def _to_conversation(row: ConversationORM) -> Conversation:
        return Conversation(
            id=row.id,
            locale=row.locale,
            qr_source_id=row.qr_source_id,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_message(row: MessageORM) -> Message:
        return Message(
            id=row.id,
            conversation_id=row.conversation_id,
            role=MessageRole(row.role),
            content=row.content,
            ordinal=row.ordinal,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_job(row: CustomerJobORM) -> CustomerJob:
        return CustomerJob(
            id=row.id,
            conversation_id=row.conversation_id,
            service_code=row.service_code,
            quantity=row.quantity,
            unit=row.unit,
            location_code=row.location_code,
            location_label=row.location_label,
            scope_state=row.scope_state or [],
            provenance=row.provenance or [],
            status=CustomerJobStatus(row.status),
            version=row.version,
        )
