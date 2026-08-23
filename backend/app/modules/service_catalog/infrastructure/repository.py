from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.service_catalog.domain.models import ServiceDefinition
from app.modules.service_catalog.infrastructure.models import ServiceCatalogORM


class ServiceCatalogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active(self, code: str) -> ServiceDefinition | None:
        row = self._session.scalar(
            select(ServiceCatalogORM)
            .where(
                ServiceCatalogORM.code == code,
                ServiceCatalogORM.is_active.is_(True),
            )
            .order_by(ServiceCatalogORM.version.desc())
            .limit(1)
        )
        return self._to_domain(row) if row else None

    def add_if_missing(self, definition: ServiceDefinition) -> None:
        if self.get_active(definition.code):
            return
        self._session.add(
            ServiceCatalogORM(
                code=definition.code,
                labels=definition.labels,
                version=definition.version,
                is_active=definition.is_active,
            )
        )

    @staticmethod
    def _to_domain(row: ServiceCatalogORM) -> ServiceDefinition:
        return ServiceDefinition(
            code=row.code,
            labels=row.labels,
            version=row.version,
            is_active=row.is_active,
        )
