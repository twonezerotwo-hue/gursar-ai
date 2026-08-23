from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.work_package.domain.models import (
    PricingRole,
    SupplyBy,
    WorkPackage,
    WorkPackageItem,
)
from app.modules.work_package.infrastructure.models import (
    WorkPackageItemORM,
    WorkPackageORM,
)


class WorkPackageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active(self, service_code: str) -> WorkPackage | None:
        package = self._session.scalar(
            select(WorkPackageORM)
            .where(
                WorkPackageORM.service_code == service_code,
                WorkPackageORM.is_active.is_(True),
            )
            .order_by(WorkPackageORM.version.desc())
            .limit(1)
        )
        if package is None:
            return None
        items = self._session.scalars(
            select(WorkPackageItemORM)
            .where(WorkPackageItemORM.work_package_id == package.id)
            .order_by(WorkPackageItemORM.display_order)
        ).all()
        return WorkPackage(
            id=package.id,
            service_code=package.service_code,
            version=package.version,
            customer_scope_summaries=package.customer_scope_summaries,
            items=[self._item_to_domain(item) for item in items],
        )

    def add_if_missing(self, package: WorkPackage) -> None:
        if self.get_active(package.service_code):
            return
        row = WorkPackageORM(
            id=package.id,
            service_code=package.service_code,
            version=package.version,
            customer_scope_summaries=package.customer_scope_summaries,
        )
        self._session.add(row)
        self._session.flush()
        for item in package.items:
            self._session.add(
                WorkPackageItemORM(
                    work_package_id=row.id,
                    item_code=item.item_code,
                    labels=item.labels,
                    default_included=item.default_included,
                    supply_by=item.supply_by.value,
                    pricing_role=item.pricing_role.value,
                    display_order=item.display_order,
                )
            )

    @staticmethod
    def _item_to_domain(row: WorkPackageItemORM) -> WorkPackageItem:
        return WorkPackageItem(
            item_code=row.item_code,
            labels=row.labels,
            default_included=row.default_included,
            supply_by=SupplyBy(row.supply_by),
            pricing_role=PricingRole(row.pricing_role),
            display_order=row.display_order,
        )
