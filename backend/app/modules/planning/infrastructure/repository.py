from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.planning.domain.models import ProductivityRecord
from app.modules.planning.infrastructure.models import ProductivityRecordORM


class ProductivityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_if_missing(self, record: ProductivityRecord, *, version: int = 1) -> None:
        exists = self._session.scalar(
            select(ProductivityRecordORM.id).where(
                ProductivityRecordORM.service_code == record.service_code,
                ProductivityRecordORM.project_type == record.project_type,
                ProductivityRecordORM.version == version,
            )
        )
        if exists:
            return
        self._session.add(
            ProductivityRecordORM(
                service_code=record.service_code,
                unit=record.unit,
                output_per_crew_day=record.output_per_crew_day,
                crew_size=record.crew_size,
                project_type=record.project_type,
                conditions=record.conditions,
                verified=record.verified,
                source=record.source,
                version=version,
            )
        )
        self._session.flush()

    def active_catalog(self, project_type: str = "RESIDENTIAL") -> dict[str, ProductivityRecord]:
        rows = self._session.scalars(
            select(ProductivityRecordORM).where(
                ProductivityRecordORM.project_type == project_type,
                ProductivityRecordORM.active.is_(True),
            )
        ).all()
        return {
            row.service_code: ProductivityRecord(
                service_code=row.service_code,
                unit=row.unit,
                output_per_crew_day=row.output_per_crew_day,
                crew_size=row.crew_size,
                project_type=row.project_type,
                conditions=row.conditions,
                verified=row.verified,
                source=row.source,
            )
            for row in rows
        }
