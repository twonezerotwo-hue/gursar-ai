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
        )
