from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import create_database_engine
from app.main import create_app


@pytest.fixture
def app() -> Generator[FastAPI]:
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
    )
    application = create_app(settings=settings, engine=engine)
    yield application
    engine.dispose()


@pytest.fixture
def client(app: FastAPI) -> Generator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def conversation_id(client: TestClient) -> str:
    response = client.post("/api/v1/conversations", json={"locale": "tr"})
    assert response.status_code == 201
    return response.json()["conversation_id"]
