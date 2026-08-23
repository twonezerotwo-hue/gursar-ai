from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    database_url: str
    admin_api_key: str
    default_location_code: str
    default_locale: str
    understanding_provider: str
    openai_api_key: str | None
    openai_model: str
    chat_provider: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"
    ollama_timeout_seconds: float = 120.0
    quote_first: bool = True

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            environment=os.getenv("GURSAR_ENVIRONMENT", "development"),
            database_url=os.getenv("GURSAR_DATABASE_URL", "sqlite:///./gursar.db"),
            admin_api_key=os.getenv("GURSAR_ADMIN_API_KEY", "local-development-key"),
            default_location_code=os.getenv("GURSAR_DEFAULT_LOCATION_CODE", "TR/ISTANBUL"),
            default_locale=os.getenv("GURSAR_DEFAULT_LOCALE", "tr"),
            understanding_provider=os.getenv("GURSAR_UNDERSTANDING_PROVIDER", "rule_based"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            chat_provider=os.getenv("GURSAR_CHAT_PROVIDER", "ollama"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:4b"),
            ollama_timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")),
        )
