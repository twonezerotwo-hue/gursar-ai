from __future__ import annotations

from datetime import UTC, datetime

from app.modules.pricing.domain.location import location_fallback_chain
from app.modules.pricing.domain.models import PriceRecord
from app.modules.pricing.infrastructure.repository import PriceBookRepository


class PriceResolver:
    def __init__(self, repository: PriceBookRepository) -> None:
        self._repository = repository

    def resolve(
        self,
        *,
        service_code: str,
        location_code: str,
        effective_at: datetime | None = None,
    ) -> PriceRecord | None:
        at = effective_at or datetime.now(UTC)
        for scope in location_fallback_chain(location_code):
            version = self._repository.latest_version_for_scope(
                service_code=service_code,
                location_scope=scope,
                effective_at=at,
            )
            if version and version.is_active:
                return version
        return None
