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

    assert first_quote["service_code"] == "formwork.labor"
    assert Decimal(first_quote["unit_price"]) == Decimal("700")
    assert first_quote["total_price"] is None
    assert first_quote["price_version"] == 1
    assert "700 TL/m² + KDV" in first["assistant_text"]
    assert "Kaç m²" in first["assistant_text"]

    second = send(client, conversation_id, "4 bin m2")
    second_quote = second["quotes"][0]

    assert second_quote["job_id"] == first_quote["job_id"]
    assert Decimal(second_quote["quantity"]) == Decimal("4000")
    assert Decimal(second_quote["total_price"]) == Decimal("2800000")
    assert "4.000 m² × 700 TL/m² = 2.800.000 TL + KDV" in second["assistant_text"]

    restored = client.get(f"/api/v1/conversations/{conversation_id}/quotes")
    assert restored.status_code == 200
    assert Decimal(restored.json()[0]["total_price"]) == Decimal("2800000")


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
    assert Decimal(quote["unit_price"]) == Decimal("700")
    assert Decimal(quote["total_price"]) == Decimal("70000")


def test_user_provided_labor_prices_and_formwork_material_supplement(
    client: TestClient,
    conversation_id: str,
) -> None:
    reinforcement = send(client, conversation_id, "100 m2 demir işçiliği")["quotes"][0]
    formwork = send(client, conversation_id, "100 m2 kalıp işçiliği")["quotes"][0]
    masonry = send(client, conversation_id, "100 m2 duvar işçiliği")["quotes"][0]
    formwork_with_material = send(
        client,
        conversation_id,
        "100 m2 kalıp malzemesi dahil",
    )["quotes"][0]

    assert Decimal(reinforcement["unit_price"]) == Decimal("500")
    assert Decimal(reinforcement["total_price"]) == Decimal("50000")
    assert Decimal(formwork["unit_price"]) == Decimal("700")
    assert Decimal(formwork["total_price"]) == Decimal("70000")
    assert Decimal(masonry["unit_price"]) == Decimal("200")
    assert Decimal(masonry["total_price"]) == Decimal("20000")
    assert Decimal(formwork_with_material["unit_price"]) == Decimal("925")
    assert Decimal(formwork_with_material["total_price"]) == Decimal("92500")


def test_labor_price_question_returns_unit_price_without_assuming_quantity(
    client: TestClient,
    conversation_id: str,
) -> None:
    reinforcement = send(client, conversation_id, "Demir işçiliğinin m² fiyatı nedir?")["quotes"][0]
    formwork = send(client, conversation_id, "Kalıp işçiliğinin m² fiyatı nedir?")["quotes"][0]
    masonry = send(client, conversation_id, "Duvar işçiliğinin m² fiyatı nedir?")["quotes"][0]

    assert Decimal(reinforcement["unit_price"]) == Decimal("500")
    assert reinforcement["quantity"] is None
    assert reinforcement["total_price"] is None
    assert Decimal(formwork["unit_price"]) == Decimal("700")
    assert formwork["quantity"] is None
    assert formwork["total_price"] is None
    assert Decimal(masonry["unit_price"]) == Decimal("200")
    assert masonry["quantity"] is None
    assert masonry["total_price"] is None


def test_quantity_follow_up_stays_on_latest_job_but_named_trade_switches_context(
    client: TestClient,
    conversation_id: str,
) -> None:
    reinforcement = send(client, conversation_id, "demir işçiliği m2 fiyatı")["quotes"][0]
    formwork = send(client, conversation_id, "kalıp işçiliği m2 fiyatı")["quotes"][0]

    formwork_total = send(client, conversation_id, "5000")["quotes"][0]
    reinforcement_total = send(client, conversation_id, "demi 4555 m2")["quotes"][0]

    assert formwork_total["job_id"] == formwork["job_id"]
    assert formwork_total["service_code"] == "formwork.labor"
    assert Decimal(formwork_total["total_price"]) == Decimal("3500000")
    assert reinforcement_total["job_id"] == reinforcement["job_id"]
    assert reinforcement_total["service_code"] == "reinforcement.labor"
    assert Decimal(reinforcement_total["unit_price"]) == Decimal("500")
    assert Decimal(reinforcement_total["total_price"]) == Decimal("2277500")

    restored = client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()
    assert len(restored) == 2
    assert {quote["service_code"] for quote in restored} == {
        "reinforcement.labor",
        "formwork.labor",
    }


def test_unknown_named_quantity_does_not_overwrite_previous_trade(
    client: TestClient,
    conversation_id: str,
) -> None:
    formwork = send(client, conversation_id, "5000 m2 kalıp işçiliği")["quotes"][0]
    result = send(client, conversation_id, "bilinmeyen iş 4555 m2")

    assert result["quotes"][0]["status"] == "SERVICE_CLARIFICATION_REQUIRED"
    restored = client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()
    assert len(restored) == 1
    assert restored[0]["job_id"] == formwork["job_id"]
    assert Decimal(restored[0]["total_price"]) == Decimal("3500000")


def test_repeated_trade_revises_existing_item_and_total_question_summarizes(
    client: TestClient,
    conversation_id: str,
) -> None:
    reinforcement = send(client, conversation_id, "demir işçiliğinin m2 fiyatı")["quotes"][0]
    send(client, conversation_id, "2000")
    send(client, conversation_id, "kalıp işçiliğinin m2 fiyatı")
    send(client, conversation_id, "2000")
    send(client, conversation_id, "duvar")
    send(client, conversation_id, "4000")

    revision = send(client, conversation_id, "demir kaçtı")
    assert revision["quotes"][0]["job_id"] == reinforcement["job_id"]
    assert Decimal(revision["quotes"][0]["total_price"]) == Decimal("1000000")

    restored = client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()
    assert len(restored) == 3
    assert {quote["service_code"] for quote in restored} == {
        "reinforcement.labor",
        "formwork.labor",
        "masonry.labor",
    }

    summary = send(client, conversation_id, "toplam ne etti maliyet")
    assert summary["quotes"] == []
    assert "Demir İşçiliği" in summary["assistant_text"]
    assert "Kalıp İşçiliği" in summary["assistant_text"]
    assert "Duvar İşçiliği" in summary["assistant_text"]
    assert "Genel toplam: 3.200.000 TL + KDV" in summary["assistant_text"]
    assert len(client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()) == 3


def test_formwork_material_can_be_added_in_a_follow_up(
    client: TestClient,
    conversation_id: str,
) -> None:
    first = send(client, conversation_id, "100 m2 kalıp işçiliği")["quotes"][0]
    second = send(client, conversation_id, "malzeme dahil")["quotes"][0]

    assert second["job_id"] == first["job_id"]
    assert second["service_code"] == "formwork.labor_material"
    assert Decimal(second["quantity"]) == Decimal("100")
    assert Decimal(second["unit_price"]) == Decimal("925")
    assert Decimal(second["total_price"]) == Decimal("92500")


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


def test_rough_construction_requires_material_scope_before_pricing(
    client: TestClient,
    conversation_id: str,
) -> None:
    result = send(client, conversation_id, "kaba inşaat")

    assert result["quotes"][0]["status"] == "SERVICE_CLARIFICATION_REQUIRED"
    assert result["quotes"][0]["service_code"] == "rough_construction.turnkey"
    assert "malzemeli mi, malzemesiz mi" in result["assistant_text"]
    assert "Toplam metraj kaç m²" in result["assistant_text"]


def test_labor_only_rough_construction_is_split_and_totalled(
    client: TestClient,
    conversation_id: str,
) -> None:
    first = send(client, conversation_id, "100 m2 kaba inşaat")
    result = send(client, conversation_id, "malzemesiz")

    assert first["quotes"][0]["status"] == "SERVICE_CLARIFICATION_REQUIRED"
    assert {quote["service_code"] for quote in result["quotes"]} == {
        "reinforcement.labor",
        "formwork.labor",
        "masonry.labor",
    }
    assert sum(Decimal(quote["total_price"]) for quote in result["quotes"]) == Decimal("140000")
    assert "140.000 TL + KDV" in result["assistant_text"]
    assert "yalnız işçilik" in result["assistant_text"]


def test_rough_construction_keeps_quantity_from_a_comma_separated_first_message(
    client: TestClient,
    conversation_id: str,
) -> None:
    first = send(
        client,
        conversation_id,
        "Kaba inşaat yaptırmak istiyorum, toplam 1.200 m².",
    )
    result = send(client, conversation_id, "Malzemesiz olacak.")

    assert "Toplam metraj kaç m²" not in first["assistant_text"]
    assert first["quotes"][0]["quantity"] == "1200"
    assert sum(Decimal(quote["total_price"]) for quote in result["quotes"]) == Decimal("1680000")
    assert "1.680.000 TL + KDV" in result["assistant_text"]


def test_material_rough_construction_uses_only_configured_material_price(
    client: TestClient,
    conversation_id: str,
) -> None:
    send(client, conversation_id, "kaba inşaat")
    result = send(client, conversation_id, "100 m2 malzemeli")

    assert {quote["service_code"] for quote in result["quotes"]} == {
        "reinforcement.labor",
        "formwork.labor_material",
        "masonry.labor",
    }
    assert sum(Decimal(quote["total_price"]) for quote in result["quotes"]) == Decimal("162500")
    assert "162.500 TL + KDV" in result["assistant_text"]
    assert "Kalıp malzemesi dahildir" in result["assistant_text"]
    assert "Demir ve duvar malzemeleri" in result["assistant_text"]


def test_rough_template_keeps_scope_until_later_quantity_and_updates_same_items(
    client: TestClient,
    conversation_id: str,
) -> None:
    first = send(client, conversation_id, "Kaba İnşaat")
    scope = send(client, conversation_id, "malzemeli")
    result = send(client, conversation_id, "100")

    assert "malzemeli mi, malzemesiz mi" in first["assistant_text"]
    assert "Toplam metraj kaç m²" in scope["assistant_text"]
    assert "162.500 TL + KDV" in result["assistant_text"]
    assert {quote["service_code"] for quote in result["quotes"]} == {
        "reinforcement.labor",
        "formwork.labor_material",
        "masonry.labor",
    }
    restored = client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()
    assert len(restored) == 3
    assert sum(Decimal(quote["total_price"]) for quote in restored) == Decimal("162500")


def test_fine_construction_template_waits_for_prices_without_creating_quote(
    client: TestClient,
    conversation_id: str,
) -> None:
    result = send(client, conversation_id, "İnce İnşaat")

    assert result["quotes"] == []
    assert "fiyatları henüz tanımlanmadığı" in result["assistant_text"]
    assert client.get(f"/api/v1/conversations/{conversation_id}/quotes").json() == []


def test_completed_rough_construction_reporting_request_is_not_repriced(
    client: TestClient,
    conversation_id: str,
) -> None:
    result = send(
        client,
        conversation_id,
        (
            "Kaba inşaat bitti. İmalatta eksikler var; ince yapının raporlanması "
            "ve programlanması gerekiyor."
        ),
    )

    assert result["quotes"] == []
    assert "malzemeli mi, malzemesiz mi" not in result["assistant_text"]
    assert "Kat ve mahal bazlı eksik/kusur listesi" in result["assistant_text"]
    assert "hedef teslim tarihini" in result["assistant_text"]
    assert client.get(f"/api/v1/conversations/{conversation_id}/quotes").json() == []


def test_completed_rough_scope_does_not_capture_next_labor_item(
    client: TestClient,
    conversation_id: str,
) -> None:
    send(client, conversation_id, "100 m2 kaba inşaat")
    rough_quotes = send(client, conversation_id, "malzemesiz")["quotes"]
    rough_formwork = next(
        quote for quote in rough_quotes if quote["service_code"] == "formwork.labor"
    )
    next_item = send(client, conversation_id, "50 m2 kalıp işçiliği")

    assert len(next_item["quotes"]) == 1
    assert next_item["quotes"][0]["service_code"] == "formwork.labor"
    assert next_item["quotes"][0]["job_id"] == rough_formwork["job_id"]

    restored = client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()
    assert len(restored) == 3
    assert sum(Decimal(quote["total_price"]) for quote in restored) == Decimal("105000")


def test_quote_history_accumulates_separate_labor_items(
    client: TestClient,
    conversation_id: str,
) -> None:
    send(client, conversation_id, "100 m2 demir işçiliği")
    send(client, conversation_id, "200 m2 kalıp işçiliği")
    send(client, conversation_id, "300 m2 duvar işçiliği")

    restored = client.get(f"/api/v1/conversations/{conversation_id}/quotes")
    assert restored.status_code == 200
    quotes = restored.json()
    assert {quote["service_code"] for quote in quotes} == {
        "reinforcement.labor",
        "formwork.labor",
        "masonry.labor",
    }
    assert sum(Decimal(quote["total_price"]) for quote in quotes) == Decimal("250000")


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


def test_unit_price_question_stays_out_of_ledger_until_quantity_is_given(
    client: TestClient,
    conversation_id: str,
) -> None:
    first = send(client, conversation_id, "Demir işçiliğinin m² fiyatı nedir?")

    assert first["quotes"][0]["unit_price"] == "500.0000"
    assert first["quotes"][0]["total_price"] is None
    assert client.get(f"/api/v1/conversations/{conversation_id}/quotes").json() == []

    send(client, conversation_id, "2000")
    ledger = client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()

    assert len(ledger) == 1
    assert ledger[0]["service_code"] == "reinforcement.labor"
    assert Decimal(ledger[0]["total_price"]) == Decimal("1000000")


def test_querying_existing_item_does_not_duplicate_ledger(
    client: TestClient,
    conversation_id: str,
) -> None:
    send(client, conversation_id, "2000 m2 demir işçiliği")
    answer = send(client, conversation_id, "demir kaçtı?")
    ledger = client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()

    assert len(ledger) == 1
    assert Decimal(ledger[0]["quantity"]) == Decimal("2000")
    assert Decimal(ledger[0]["total_price"]) == Decimal("1000000")
    assert "1.000.000 TL" in answer["assistant_text"]


def test_querying_an_old_item_keeps_original_ledger_order(
    client: TestClient,
    conversation_id: str,
) -> None:
    send(client, conversation_id, "2000 m2 demir işçiliği")
    send(client, conversation_id, "1000 m2 kalıp işçiliği")
    send(client, conversation_id, "demir kaçtı?")
    ledger = client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()

    assert [quote["service_code"] for quote in ledger] == [
        "reinforcement.labor",
        "formwork.labor",
    ]


def test_explicit_revision_updates_existing_item_instead_of_adding(
    client: TestClient,
    conversation_id: str,
) -> None:
    send(client, conversation_id, "2000 m2 demir işçiliği")
    result = send(client, conversation_id, "demiri 2500 m2 olarak güncelle")
    ledger = client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()

    assert len(ledger) == 1
    assert result["quotes"][0]["service_code"] == "reinforcement.labor"
    assert Decimal(ledger[0]["quantity"]) == Decimal("2500")
    assert Decimal(ledger[0]["total_price"]) == Decimal("1250000")


def test_remove_action_hides_only_target_item_from_ledger(
    client: TestClient,
    conversation_id: str,
) -> None:
    send(client, conversation_id, "2000 m2 demir işçiliği")
    send(client, conversation_id, "1000 m2 kalıp işçiliği")
    removed = send(client, conversation_id, "demiri sil")
    ledger = client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()

    assert "hesap defterinden çıkarıldı" in removed["assistant_text"]
    assert len(ledger) == 1
    assert ledger[0]["service_code"] == "formwork.labor"


def test_message_idempotency_replays_response_without_duplicate_state(
    client: TestClient,
    conversation_id: str,
    app: FastAPI,
) -> None:
    headers = {"Idempotency-Key": "turn-reinforcement-0001"}
    first = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"text": "2000 m2 demir işçiliği"},
    )
    replay = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"text": "2000 m2 demir işçiliği"},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert len(client.get(f"/api/v1/conversations/{conversation_id}/quotes").json()) == 1
    with app.state.session_factory() as session:
        snapshots = session.scalars(
            select(AnalysisSnapshotORM).where(
                AnalysisSnapshotORM.conversation_id == conversation_id
            )
        ).all()
        assert len(snapshots) == 1


def test_idempotency_key_reuse_with_different_message_returns_conflict(
    client: TestClient,
    conversation_id: str,
) -> None:
    headers = {"Idempotency-Key": "turn-conflict-0001"}
    first = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"text": "100 m2 kalıp işçiliği"},
    )
    conflict = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"text": "100 m2 demir işçiliği"},
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert "farklı bir mesaj" in conflict.json()["detail"]
    ledger = client.get(f"/api/v1/conversations/{conversation_id}/ledger").json()
    assert [item["service_code"] for item in ledger["items"]] == ["formwork.labor"]


def test_server_owned_ledger_returns_canonical_line_items_and_totals(
    client: TestClient,
    conversation_id: str,
) -> None:
    send(client, conversation_id, "2000 m2 demir işçiliği")
    send(client, conversation_id, "2000 m2 kalıp işçiliği")
    send(client, conversation_id, "4000 m2 duvar işçiliği")

    response = client.get(f"/api/v1/conversations/{conversation_id}/ledger")

    assert response.status_code == 200
    ledger = response.json()
    assert ledger["schema_version"] == "estimate-ledger.v1"
    assert [item["service_code"] for item in ledger["items"]] == [
        "reinforcement.labor",
        "formwork.labor",
        "masonry.labor",
    ]
    assert ledger["quantity_totals"][0]["unit"] == "m2"
    assert Decimal(ledger["quantity_totals"][0]["quantity"]) == Decimal("6000")
    assert Decimal(ledger["grand_total"]) == Decimal("3200000")


def test_turkish_decimal_quantity_is_calculated_deterministically(
    client: TestClient,
    conversation_id: str,
) -> None:
    quote = send(client, conversation_id, "12,5 m2 demir işçiliği")["quotes"][0]

    assert Decimal(quote["quantity"]) == Decimal("12.5")
    assert Decimal(quote["total_price"]) == Decimal("6250")


def test_invalid_or_unsupported_quantities_never_enter_the_ledger(
    client: TestClient,
) -> None:
    invalid_messages = (
        "-5 m2 demir işçiliği",
        "0 m2 demir işçiliği",
        "10000001 m2 demir işçiliği",
        "100 m3 demir işçiliği",
    )

    for message in invalid_messages:
        created = client.post("/api/v1/conversations", json={"locale": "tr"})
        conversation_id = created.json()["conversation_id"]
        result = send(client, conversation_id, message)

        assert result["quotes"][0]["quantity"] is None
        assert result["quotes"][0]["total_price"] is None
        assert client.get(f"/api/v1/conversations/{conversation_id}/ledger").json()["items"] == []


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

    isolated = client.post("/api/v1/qr/CARD001/conversation")
    assert isolated.status_code == 200
    assert isolated.json()["conversation_id"] != payload["conversation_id"]
    assert isolated.json()["resumed"] is False
