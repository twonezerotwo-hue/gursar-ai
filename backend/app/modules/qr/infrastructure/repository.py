from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.qr.infrastructure.models import QRSourceORM


@dataclass(frozen=True, slots=True)
class QRSource:
    id: str
    code: str
    source_type: str
    display_name: str
    metadata: dict[str, Any]


class QRSourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active_by_code(self, code: str) -> QRSource | None:
        row = self._session.scalar(
            select(QRSourceORM).where(
                QRSourceORM.code == code.upper(),
                QRSourceORM.is_active.is_(True),
            )
        )
        return self._to_domain(row) if row else None

    def add_if_missing(
        self,
        *,
        code: str,
        source_type: str,
        display_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.get_active_by_code(code):
            return
        self._session.add(
            QRSourceORM(
                code=code.upper(),
                source_type=source_type,
                display_name=display_name,
                metadata_json=metadata or {},
            )
        )

    @staticmethod
    def _to_domain(row: QRSourceORM) -> QRSource:
        return QRSource(
            id=row.id,
            code=row.code,
            source_type=row.source_type,
            display_name=row.display_name,
            metadata=row.metadata_json or {},
        )
