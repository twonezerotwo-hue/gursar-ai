from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine

from app.bootstrap import initialize_database
from app.core.config import Settings
from app.core.database import create_database_engine, create_session_factory
from app.core.logging import configure_logging
from app.core.security import valid_public_access_credentials
from app.modules.acquisition.api.router import router as acquisition_router
from app.modules.construction_chat.infrastructure.fallback import (
    FallbackConstructionChatProvider,
)
from app.modules.construction_chat.infrastructure.ollama import (
    OllamaConstructionChatProvider,
)
from app.modules.construction_chat.infrastructure.openai_compatible import (
    OpenAICompatibleConstructionChatProvider,
)
from app.modules.conversation.api.router import router as conversation_router
from app.modules.customer_understanding.infrastructure.openai import (
    OpenAICustomerUnderstandingProvider,
)
from app.modules.customer_understanding.infrastructure.rule_based import (
    RuleBasedCustomerUnderstandingProvider,
)
from app.modules.database_backup.api.router import router as database_backup_router
from app.modules.document_intelligence.api.router import router as document_tools_router
from app.modules.document_intelligence.application.service import DocumentIntelligenceService
from app.modules.document_intelligence.infrastructure.analyzers import build_default_analyzers
from app.modules.documents.api.router import router as document_router
from app.modules.documents.infrastructure.storage import LocalDocumentStorage
from app.modules.estimation.api.router import router as estimation_router
from app.modules.intelligence.api.router import router as intelligence_router
from app.modules.jobs.api.router import project_analysis_router
from app.modules.jobs.api.router import router as jobs_router
from app.modules.knowledge_acquisition.api.router import router as knowledge_router
from app.modules.legal_knowledge.infrastructure.repository import LegalKnowledgeRepository
from app.modules.pricing.api.router import router as price_router
from app.modules.project_graph.api.router import router as project_graph_router
from app.modules.projects.api.router import router as project_router
from app.modules.qr.api.router import router as qr_router
from app.modules.training.api.router import router as training_router
from app.modules.vision.api.router import router as vision_router

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPOSITORY_ROOT / "frontend"


def create_app(*, settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    configure_logging()
    actual_settings = settings or Settings.from_environment()
    if bool(actual_settings.public_access_username) != bool(actual_settings.public_access_password):
        raise RuntimeError(
            "GURSAR_PUBLIC_ACCESS_USERNAME and GURSAR_PUBLIC_ACCESS_PASSWORD must be set together"
        )
    if (
        actual_settings.environment == "production"
        and actual_settings.admin_api_key == "local-development-key"
    ):
        raise RuntimeError("A strong GURSAR_ADMIN_API_KEY is required in production")
    if (
        actual_settings.environment == "production"
        and actual_settings.conversation_token_secret == "development-conversation-token-secret"
    ):
        raise RuntimeError("A strong GURSAR_CONVERSATION_TOKEN_SECRET is required in production")
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
    application.state.document_storage = LocalDocumentStorage(actual_settings.document_storage_root)
    application.state.document_intelligence = DocumentIntelligenceService(
        analyzers=build_default_analyzers(
            max_pdf_pages=actual_settings.document_analysis_max_pages
        ),
        max_upload_bytes=actual_settings.document_max_upload_bytes,
    )

    @application.middleware("http")
    async def protect_public_access(request: Request, call_next):
        username = actual_settings.public_access_username
        password = actual_settings.public_access_password
        if (
            username
            and password
            and not valid_public_access_credentials(
                request.headers.get("Authorization"),
                expected_username=username,
                expected_password=password,
            )
        ):
            return Response(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": 'Basic realm="GURSAR AI", charset="UTF-8"'},
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        if request.headers.get("X-Forwarded-Proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    application.include_router(qr_router)
    application.include_router(conversation_router)
    application.include_router(database_backup_router)
    application.include_router(price_router)
    application.include_router(project_router)
    application.include_router(project_graph_router)
    application.include_router(document_router)
    application.include_router(document_tools_router)
    application.include_router(estimation_router)
    application.include_router(knowledge_router)
    application.include_router(training_router)
    application.include_router(jobs_router)
    application.include_router(project_analysis_router)
    application.include_router(vision_router)
    application.include_router(acquisition_router)
    application.include_router(intelligence_router)

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
    local_provider = None
    if settings.chat_provider == "ollama":
        local_provider = OllamaConstructionChatProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            fallback_model=settings.ollama_fallback_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            context_window=settings.ollama_context_window,
        )

    remote_providers = []
    if settings.remote_chat_provider == "groq":
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is required when Groq fallback is enabled")
        remote_providers.append(
            OpenAICompatibleConstructionChatProvider(
                provider_label="groq",
                base_url=settings.groq_base_url,
                api_key=settings.groq_api_key,
                model=settings.groq_model,
                timeout_seconds=settings.groq_timeout_seconds,
                context_window=settings.groq_context_window,
            )
        )
    elif settings.remote_chat_provider is not None:
        raise RuntimeError(f"Unsupported remote chat provider: {settings.remote_chat_provider}")

    if local_provider is not None and remote_providers:
        return FallbackConstructionChatProvider(
            local=local_provider,
            remotes=remote_providers,
        )
    if local_provider is not None:
        return local_provider
    return remote_providers[0] if remote_providers else None


app = create_app()
