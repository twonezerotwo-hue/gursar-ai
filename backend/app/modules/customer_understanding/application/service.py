from __future__ import annotations

from pydantic import ValidationError

from app.contracts.v1.customer_understanding import (
    CustomerUnderstandingContextV1,
    CustomerUnderstandingResultV1,
)
from app.modules.customer_understanding.domain.ports import CustomerUnderstandingProvider
from app.modules.customer_understanding.infrastructure.repository import SnapshotRepository


class InvalidUnderstandingResultError(ValueError):
    pass


class CustomerUnderstandingService:
    def __init__(
        self,
        provider: CustomerUnderstandingProvider,
        snapshots: SnapshotRepository,
    ) -> None:
        self._provider = provider
        self._snapshots = snapshots

    def analyze(
        self,
        *,
        message: str,
        context: CustomerUnderstandingContextV1,
    ) -> CustomerUnderstandingResultV1:
        try:
            raw_result = self._provider.understand(message, context)
            result = CustomerUnderstandingResultV1.model_validate(raw_result.model_dump())
        except (ValidationError, TypeError, ValueError) as exc:
            raise InvalidUnderstandingResultError(
                "Provider returned an invalid customer-understanding contract"
            ) from exc

        self._snapshots.append(
            conversation_id=context.conversation_id,
            source_message_id=context.message_id,
            provider=self._provider.provider_name,
            result=result,
        )
        return result
