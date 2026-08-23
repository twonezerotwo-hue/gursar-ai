from __future__ import annotations

from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.persistence import AuditEventORM, OutboxEventORM
from app.modules.conversation.infrastructure.models import ConversationORM, CustomerJobORM
from app.modules.customer_understanding.infrastructure.models import AnalysisSnapshotORM
from app.modules.qr.infrastructure.models import QRSourceORM


def send(client: TestClient, conversation_id: str, text: str) -> dict:
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": text},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_formwork_quote_first_then_deterministic_total(
    client: TestClient,
    conversation_id: str,
) -> None:
    first = send(client, conversation_id, "kalıbı kaça yapıyorsun")
    first_quote = first["quotes"][0]

    assert first_quote["service_code"] == "formwork.standard.turnkey"
    assert Decimal(first_quote["unit_price"]) == Decimal("1350")
    assert first_quote["total_price"] is None
    assert first_quote["price_version"] == 1
    assert "1.350 TL/m² + KDV" in first["assistant_text"]
    assert "Kaç m²" in first["assistant_text"]

    second = send(client, conversation_id, "4 bin m2")
    second_quote = second["quotes"][0]

    assert second_quote["job_id"] == first_quote["job_id"]
    assert Decimal(second_quote["quantity"]) == Decimal("4000")
    assert Decimal(second_quote["total_price"]) == Decimal("5400000")
    assert "4.000 m² × 1.350 TL/m² = 5.400.000 TL + KDV" in second["assistant_text"]

    restored = client.get(f"/api/v1/conversations/{conversation_id}/quotes")
    assert restored.status_code == 200
    assert Decimal(restored.json()[0]["total_price"]) == Decimal("5400000")


def test_two_jobs_are_kept_separate(client: TestClient, conversation_id: str) -> None:
    result = send(
        client,
        conversation_id,
        "Göktürk 2000 m2 kompozit, Avcılar 2000 m2 mantolama boya",
    )

    assert len(result["quotes"]) == 2
    composite, insulation = result["quotes"]
    assert composite["job_id"] != insulation["job_id"]
    assert composite["service_code"] == "facade.composite.turnkey"
    assert composite["location_code"] == "TR/ISTANBUL/EYUPSULTAN/GOKTURK"
    assert Decimal(composite["total_price"]) == Decimal("5900000")
    assert insulation["service_code"] == "facade.insulation_paint.turnkey"
    assert insulation["location_code"] == "TR/ISTANBUL/AVCILAR"
    assert Decimal(insulation["total_price"]) == Decimal("3700000")


def test_generic_facade_request_is_clarified_then_completed_in_same_job(
    client: TestClient,
    conversation_id: str,
) -> None:
    first = send(client, conversation_id, "Avcılar'da 1500 m2 dış cephe")
    first_quote = first["quotes"][0]

    assert first_quote["status"] == "SERVICE_CLARIFICATION_REQUIRED"
    assert first_quote["service_label"] == "Dış Cephe"
    assert first_quote["location_code"] == "TR/ISTANBUL/AVCILAR"
    assert Decimal(first_quote["quantity"]) == Decimal("1500")
    assert "Kompozit" in first["assistant_text"]
    assert "mantolama" in first["assistant_text"]

    second = send(client, conversation_id, "mantolama boya")
    second_quote = second["quotes"][0]

    assert second_quote["job_id"] == first_quote["job_id"]
    assert second_quote["service_code"] == "facade.insulation_paint.turnkey"
    assert second_quote["location_code"] == "TR/ISTANBUL/AVCILAR"
    assert Decimal(second_quote["quantity"]) == Decimal("1500")
    assert Decimal(second_quote["unit_price"]) == Decimal("1850")
    assert Decimal(second_quote["total_price"]) == Decimal("2775000")
    assert "1.500 m² × 1.850 TL/m² = 2.775.000 TL + KDV" in second["assistant_text"]


def test_location_falls_back_from_gokturk_to_istanbul_price(
    client: TestClient,
    conversation_id: str,
) -> None:
    result = send(client, conversation_id, "Göktürk 100 m2 kalıp")
    quote = result["quotes"][0]

    assert quote["location_code"] == "TR/ISTANBUL/EYUPSULTAN/GOKTURK"
    assert Decimal(quote["unit_price"]) == Decimal("1350")
    assert Decimal(quote["total_price"]) == Decimal("135000")


def test_missing_price_never_invents_number(
    client: TestClient,
    conversation_id: str,
    app: FastAPI,
) -> None:
    result = send(client, conversation_id, "500 m2 mimari tasarım")
    quote = result["quotes"][0]

    assert quote["status"] == "PRICE_NOT_CONFIGURED"
    assert quote["unit_price"] is None
    assert "güncel fiyatı netleştirip" in result["assistant_text"]
    assert "veritaban" not in result["assistant_text"].lower()

    with app.state.session_factory() as session:
        audit = session.scalar(
            select(AuditEventORM).where(AuditEventORM.action == "PRICE_NOT_CONFIGURED")
        )
        event = session.scalar(
            select(OutboxEventORM).where(OutboxEventORM.event_type == "price.missing.v1")
        )
        assert audit is not None
        assert event is not None


def test_standard_rough_construction_package_applies_defaults(
    client: TestClient,
    conversation_id: str,
) -> None:
    result = send(client, conversation_id, "kaba inşaat")
    scope = {item["item_code"]: item["state"] for item in result["quotes"][0]["scope_items"]}

    assert scope["formwork"] == "INCLUDED_BY_DEFAULT"
    assert scope["reinforcement"] == "INCLUDED_BY_DEFAULT"
    assert scope["masonry"] == "INCLUDED_BY_DEFAULT"
    assert "Duvar dahil mi" not in result["assistant_text"]


def test_customer_exclusion_overrides_standard_package(
    client: TestClient,
    conversation_id: str,
) -> None:
    result = send(client, conversation_id, "kaba inşaat ama duvar hariç")
    scope = {item["item_code"]: item["state"] for item in result["quotes"][0]["scope_items"]}

    assert scope["masonry"] == "EXCLUDED_BY_CUSTOMER"
    assert scope["formwork"] == "INCLUDED_BY_DEFAULT"


def test_analysis_snapshots_are_appended_not_overwritten(
    client: TestClient,
    conversation_id: str,
    app: FastAPI,
) -> None:
    send(client, conversation_id, "kalıp fiyatı")
    send(client, conversation_id, "100 m2")

    with app.state.session_factory() as session:
        snapshots = session.scalars(
            select(AnalysisSnapshotORM).where(
                AnalysisSnapshotORM.conversation_id == conversation_id
            )
        ).all()
        jobs = session.scalars(
            select(CustomerJobORM).where(CustomerJobORM.conversation_id == conversation_id)
        ).all()
        assert len(snapshots) == 2
        assert len(jobs) == 1
        assert jobs[0].version == 2


def test_qr_entry_creates_conversation_with_source_attribution(
    client: TestClient,
    app: FastAPI,
) -> None:
    response = client.post("/api/v1/qr/CARD001/conversation")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_code"] == "CARD001"
    assert payload["resumed"] is False
    assert payload["messages"][0]["role"] == "ASSISTANT"

    with app.state.session_factory() as session:
        source = session.scalar(select(QRSourceORM).where(QRSourceORM.code == "CARD001"))
        conversation = session.get(ConversationORM, payload["conversation_id"])
        assert source is not None
        assert conversation is not None
        assert conversation.qr_source_id == source.id

    resumed = client.post("/api/v1/qr/CARD001/conversation")
    assert resumed.status_code == 200
    assert resumed.json()["conversation_id"] == payload["conversation_id"]
    assert resumed.json()["resumed"] is True
