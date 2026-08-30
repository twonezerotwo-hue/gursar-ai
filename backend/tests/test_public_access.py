from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import create_database_engine
from app.main import create_app


@contextmanager
def _protected_client() -> Iterator[TestClient]:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    settings = Settings(
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        admin_api_key="test-admin-key",
        default_location_code="TR/ISTANBUL",
        default_locale="tr",
        understanding_provider="rule_based",
        openai_api_key=None,
        openai_model="gpt-5-mini",
        chat_provider="deterministic",
        public_access_username="gursar",
        public_access_password="strong-test-password",
    )
    application = create_app(settings=settings, engine=engine)
    try:
        with TestClient(application) as client:
            yield client
    finally:
        engine.dispose()


def test_public_access_requires_valid_basic_credentials() -> None:
    with _protected_client() as client:
        unauthorized = client.get("/api/health")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"].startswith("Basic")


def test_public_access_accepts_credentials_and_adds_security_headers() -> None:
    token = base64.b64encode(b"gursar:strong-test-password").decode("ascii")
    with _protected_client() as client:
        response = client.get(
            "/api/health",
            headers={
                "Authorization": f"Basic {token}",
                "X-Forwarded-Proto": "https",
            },
        )

        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["strict-transport-security"] == "max-age=31536000"
