from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine

from app.bootstrap import initialize_database
from app.core.config import Settings
from app.core.database import create_database_engine, create_session_factory
from app.core.logging import configure_logging
from app.modules.construction_chat.infrastructure.ollama import (
    OllamaConstructionChatProvider,
)
from app.modules.conversation.api.router import router as conversation_router
from app.modules.customer_understanding.infrastructure.openai import (
    OpenAICustomerUnderstandingProvider,
)
from app.modules.customer_understanding.infrastructure.rule_based import (
    RuleBasedCustomerUnderstandingProvider,
)
from app.modules.legal_knowledge.infrastructure.repository import LegalKnowledgeRepository
from app.modules.pricing.api.router import router as price_router
from app.modules.qr.api.router import router as qr_router

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPOSITORY_ROOT / "frontend"


def create_app(*, settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    configure_logging()
    actual_settings = settings or Settings.from_environment()
    database_engine = engine or create_database_engine(actual_settings.database_url)
    session_factory = create_session_factory(database_engine)
    initialize_database(database_engine, session_factory)

    application = FastAPI(
        title="GÜRSAR AI",
        version="0.1.0",
        description="Quote-first construction intake and internal pricing API",
        debug=False,
    )
    application.state.settings = actual_settings
    application.state.engine = database_engine
    application.state.session_factory = session_factory
    application.state.understanding_provider = _build_provider(actual_settings)
    application.state.construction_chat_provider = _build_chat_provider(actual_settings)

    application.include_router(qr_router)
    application.include_router(conversation_router)
    application.include_router(price_router)

    @application.get("/api/health", tags=["system"])
    def health() -> dict[str, str]:
        chat_provider = application.state.construction_chat_provider
        with session_factory() as session:
            legal_documents, legal_chunks = LegalKnowledgeRepository(session).counts()
        return {
            "status": "ok",
            "service": "gursar-ai",
            "chat_provider": (
                chat_provider.provider_name if chat_provider is not None else "deterministic"
            ),
            "legal_documents": str(legal_documents),
            "legal_chunks": str(legal_chunks),
        }

    application.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @application.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    @application.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @application.get("/q/{code}", include_in_schema=False)
    def qr_entry(code: str) -> FileResponse:
        del code
        return FileResponse(FRONTEND_DIR / "index.html")

    return application


def _build_provider(settings: Settings):
    if settings.understanding_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for the OpenAI provider")
        return OpenAICustomerUnderstandingProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
    return RuleBasedCustomerUnderstandingProvider()


def _build_chat_provider(settings: Settings):
    if settings.chat_provider == "ollama":
        return OllamaConstructionChatProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
    return None


app = create_app()
