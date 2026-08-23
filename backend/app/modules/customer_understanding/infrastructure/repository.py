from __future__ import annotations

from sqlalchemy.orm import Session

from app.contracts.v1.customer_understanding import CustomerUnderstandingResultV1
from app.modules.customer_understanding.infrastructure.models import AnalysisSnapshotORM


class SnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        *,
        conversation_id: str,
        source_message_id: str,
        provider: str,
        result: CustomerUnderstandingResultV1,
    ) -> None:
        self._session.add(
            AnalysisSnapshotORM(
                conversation_id=conversation_id,
                source_message_id=source_message_id,
                schema_version=result.schema_version,
                provider=provider,
                result=result.model_dump(mode="json"),
            )
        )
