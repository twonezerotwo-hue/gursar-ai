from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.planning.domain.models import ProductivityRecord, ProductivitySourceTier
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
                crew_composition_json=dict(record.crew_composition),
                sample_quantity=record.sample_quantity,
                sample_duration_days=record.sample_duration_days,
                productive_hours=record.productive_hours,
                project_type=record.project_type,
                work_type=record.work_type,
                location=record.location,
                floor=record.floor,
                conditions=record.conditions,
                verified=record.verified,
                confidence=record.confidence,
                source_tier=record.source_tier.value,
                source=record.source,
                version=version,
            )
        )
        self._session.flush()

    def add_version(self, record: ProductivityRecord) -> ProductivityRecordORM:
        if record.output_per_crew_day <= 0 or record.crew_size <= 0:
            raise ValueError("Productivity output and crew size must be positive")
        composition_size = sum(count for _, count in record.crew_composition)
        if record.crew_composition and composition_size != record.crew_size:
            raise ValueError("Crew composition must equal crew size")
        if not Decimal("0") <= record.confidence <= Decimal("1"):
            raise ValueError("Productivity confidence must be between zero and one")
        if record.verified and record.source_tier == ProductivitySourceTier.HEURISTIC:
            raise ValueError("A verified record cannot use HEURISTIC source tier")
        current_version = self._session.scalar(
            select(func.max(ProductivityRecordORM.version)).where(
                ProductivityRecordORM.service_code == record.service_code,
                ProductivityRecordORM.project_type == record.project_type,
            )
        )
        current_rows = self._session.scalars(
            select(ProductivityRecordORM).where(
                ProductivityRecordORM.service_code == record.service_code,
                ProductivityRecordORM.project_type == record.project_type,
                ProductivityRecordORM.location == record.location,
                ProductivityRecordORM.active.is_(True),
            )
        ).all()
        for row in current_rows:
            row.active = False
        row = ProductivityRecordORM(
            service_code=record.service_code,
            work_type=record.work_type,
            unit=record.unit,
            output_per_crew_day=record.output_per_crew_day,
            crew_size=record.crew_size,
            crew_composition_json=dict(record.crew_composition),
            sample_quantity=record.sample_quantity,
            sample_duration_days=record.sample_duration_days,
            productive_hours=record.productive_hours,
            project_type=record.project_type,
            location=record.location,
            floor=record.floor,
            conditions=record.conditions,
            verified=record.verified,
            confidence=record.confidence,
            source_tier=record.source_tier.value,
            source=record.source,
            version=int(current_version or 0) + 1,
            active=True,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def active_catalog(
        self,
        project_type: str = "RESIDENTIAL",
        location: str | None = None,
    ) -> dict[str, ProductivityRecord]:
        filters = [
            ProductivityRecordORM.project_type == project_type,
            ProductivityRecordORM.active.is_(True),
        ]
        if location is None:
            filters.append(ProductivityRecordORM.location.is_(None))
        else:
            filters.append(ProductivityRecordORM.location.in_((None, location)))
        rows = self._session.scalars(select(ProductivityRecordORM).where(*filters)).all()
        priority = {
            ProductivitySourceTier.VERIFIED_PROJECT_ACTUAL.value: 4,
            ProductivitySourceTier.COMPANY_STANDARD.value: 3,
            ProductivitySourceTier.VERIFIED_REFERENCE.value: 2,
            ProductivitySourceTier.HEURISTIC.value: 1,
        }
        selected: dict[str, ProductivityRecordORM] = {}
        for row in rows:
            current = selected.get(row.service_code)
            row_rank = (
                priority.get(row.source_tier, 0),
                int(location is not None and row.location == location),
                int(row.verified),
                row.version,
            )
            current_rank = (
                (
                    priority.get(current.source_tier, 0),
                    int(location is not None and current.location == location),
                    int(current.verified),
                    current.version,
                )
                if current is not None
                else (-1, -1, -1, -1)
            )
            if row_rank > current_rank:
                selected[row.service_code] = row
        return {code: self._to_domain(row) for code, row in selected.items()}

    @staticmethod
    def _to_domain(row: ProductivityRecordORM) -> ProductivityRecord:
        return ProductivityRecord(
            service_code=row.service_code,
            unit=row.unit,
            output_per_crew_day=row.output_per_crew_day,
            crew_size=row.crew_size,
            project_type=row.project_type,
            conditions=row.conditions,
            verified=row.verified,
            source=row.source,
            source_tier=ProductivitySourceTier(row.source_tier),
            work_type=row.work_type,
            crew_composition=tuple(sorted((row.crew_composition_json or {}).items())),
            sample_quantity=row.sample_quantity,
            sample_duration_days=row.sample_duration_days,
            productive_hours=row.productive_hours,
            location=row.location,
            floor=row.floor,
            confidence=row.confidence,
        )
