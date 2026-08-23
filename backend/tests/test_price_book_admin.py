from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}


def test_admin_price_versions_preserve_history(client: TestClient) -> None:
    unauthorized = client.get("/api/v1/admin/prices")
    assert unauthorized.status_code == 401

    created = client.post(
        "/api/v1/admin/prices",
        headers=ADMIN_HEADERS,
        json={
            "service_code": "construction.general",
            "location_scope": "TR",
            "price": "1000",
            "unit": "m2",
            "currency": "TRY",
            "vat_policy": "EXCLUDED",
            "valid_from": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    original = created.json()

    updated = client.post(
        f"/api/v1/admin/prices/{original['id']}/versions",
        headers=ADMIN_HEADERS,
        json={
            "price": "1200",
            "valid_from": datetime(2026, 2, 1, tzinfo=UTC).isoformat(),
            "notes": "Approved V2",
        },
    )
    assert updated.status_code == 200, updated.text
    version_two = updated.json()
    assert version_two["id"] != original["id"]
    assert version_two["version"] == 2
    assert version_two["supersedes_id"] == original["id"]

    history = client.get(
        "/api/v1/admin/prices",
        headers=ADMIN_HEADERS,
        params={"service_code": "construction.general", "location_scope": "TR"},
    )
    assert history.status_code == 200
    assert len(history.json()) == 2
    assert Decimal(history.json()[0]["price"]) == Decimal("1200")
    assert Decimal(history.json()[1]["price"]) == Decimal("1000")
