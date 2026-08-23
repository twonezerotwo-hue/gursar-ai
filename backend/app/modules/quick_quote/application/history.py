from __future__ import annotations

from app.contracts.v1.quick_quote import QuickQuoteV1
from app.modules.conversation.infrastructure.repository import ConversationRepository
from app.modules.quick_quote.infrastructure.repository import QuickQuoteRepository
from app.modules.service_catalog.infrastructure.repository import ServiceCatalogRepository
from app.modules.work_package.infrastructure.repository import WorkPackageRepository


class QuoteHistoryService:
    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        quotes: QuickQuoteRepository,
        services: ServiceCatalogRepository,
        work_packages: WorkPackageRepository,
    ) -> None:
        self._conversations = conversations
        self._quotes = quotes
        self._services = services
        self._work_packages = work_packages

    def latest_for_conversation(self, conversation_id: str, locale: str) -> list[QuickQuoteV1]:
        jobs = {job.id: job for job in self._conversations.list_jobs(conversation_id)}
        results: list[QuickQuoteV1] = []
        for stored in self._quotes.list_latest_by_conversation(conversation_id):
            job = jobs.get(stored.job_id)
            service = self._services.get_active(stored.service_code)
            if job is None or service is None:
                continue
            package = self._work_packages.get_active(stored.service_code)
            results.append(
                QuickQuoteV1(
                    quote_id=stored.id,
                    job_id=stored.job_id,
                    service_code=stored.service_code,
                    service_label=service.label(locale),
                    location_code=job.location_code or "GLOBAL",
                    location_label=job.location_label or "Genel",
                    quantity=stored.quantity,
                    unit=job.unit or "m2",
                    unit_price=stored.unit_price,
                    total_price=stored.total_price,
                    currency=stored.currency,
                    vat_policy=stored.vat_policy,
                    price_version=stored.price_version,
                    status="QUOTED",
                    scope_summary=package.scope_summary(locale) if package else "",
                    scope_items=job.scope_state,
                    customer_text="",
                )
            )
        return results
