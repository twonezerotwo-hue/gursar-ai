from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.modules.conversation.infrastructure.repository import ConversationRepository
from app.modules.customer_understanding.application.service import CustomerUnderstandingService
from app.modules.customer_understanding.infrastructure.location import TurkishLocationNormalizer
from app.modules.customer_understanding.infrastructure.repository import SnapshotRepository
from app.modules.legal_knowledge.infrastructure.repository import LegalKnowledgeRepository
from app.modules.pricing.application.service import PriceResolver
from app.modules.pricing.infrastructure.repository import PriceBookRepository
from app.modules.quick_quote.application.history import QuoteHistoryService
from app.modules.quick_quote.application.service import QuickQuoteService
from app.modules.quick_quote.infrastructure.repository import QuickQuoteRepository
from app.modules.service_catalog.infrastructure.repository import ServiceCatalogRepository
from app.modules.work_package.infrastructure.repository import WorkPackageRepository


def build_quick_quote_service(session: Session, app: FastAPI) -> QuickQuoteService:
    conversations = ConversationRepository(session)
    price_book = PriceBookRepository(session)
    return QuickQuoteService(
        session=session,
        conversations=conversations,
        understanding=CustomerUnderstandingService(
            provider=app.state.understanding_provider,
            snapshots=SnapshotRepository(session),
        ),
        services=ServiceCatalogRepository(session),
        work_packages=WorkPackageRepository(session),
        price_resolver=PriceResolver(price_book),
        quotes=QuickQuoteRepository(session),
        location_normalizer=TurkishLocationNormalizer(),
        default_location_code=app.state.settings.default_location_code,
        construction_chat_provider=app.state.construction_chat_provider,
        legal_knowledge=LegalKnowledgeRepository(session),
    )


def build_quote_history_service(session: Session) -> QuoteHistoryService:
    return QuoteHistoryService(
        conversations=ConversationRepository(session),
        quotes=QuickQuoteRepository(session),
        services=ServiceCatalogRepository(session),
        work_packages=WorkPackageRepository(session),
    )
