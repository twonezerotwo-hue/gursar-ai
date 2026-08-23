from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SupplyBy(StrEnum):
    COMPANY = "COMPANY"
    CUSTOMER = "CUSTOMER"
    EITHER = "EITHER"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PricingRole(StrEnum):
    BASE_PACKAGE = "BASE_PACKAGE"
    ADD_ON = "ADD_ON"
    EXCLUSION = "EXCLUSION"
    CREDIT = "CREDIT"
    CUSTOM_PRICE = "CUSTOM_PRICE"


@dataclass(frozen=True, slots=True)
class WorkPackageItem:
    item_code: str
    labels: dict[str, str]
    default_included: bool
    supply_by: SupplyBy
    pricing_role: PricingRole
    display_order: int

    def label(self, locale: str) -> str:
        return self.labels.get(locale) or self.labels.get("en") or self.item_code


@dataclass(frozen=True, slots=True)
class WorkPackage:
    id: str
    service_code: str
    version: int
    items: list[WorkPackageItem]
    customer_scope_summaries: dict[str, str]

    def scope_summary(self, locale: str) -> str:
        return (
            self.customer_scope_summaries.get(locale)
            or self.customer_scope_summaries.get("en")
            or ""
        )
