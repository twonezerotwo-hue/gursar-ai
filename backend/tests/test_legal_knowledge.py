from __future__ import annotations

import hashlib

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.construction_chat.domain.ports import (
    ConstructionChatClassification,
    ConstructionChatContext,
)
from app.modules.legal_knowledge.domain.models import LegalSourceDefinition
from app.modules.legal_knowledge.infrastructure.ingestion import LegalCorpusIngestor
from app.modules.legal_knowledge.infrastructure.repository import LegalKnowledgeRepository


class LegalAdviceProvider:
    provider_name = "fake.legal-advice"

    def classify(self, context: ConstructionChatContext) -> ConstructionChatClassification:
        del context
        return ConstructionChatClassification(in_scope=True, quote_intent=False)

    def refusal(self, locale: str) -> str:
        del locale
        return "Kapsam dışı."

    def respond(self, context: ConstructionChatContext) -> str:
        assert context.legal_context
        assert context.legal_context[0]["id"] == "K1"
        return "Yapı ruhsatı süreci ilgili imar mevzuatına göre yürütülür. [K1]"


class QuoteWithoutLegalContextProvider:
    provider_name = "fake.quote-without-legal-context"

    def classify(self, context: ConstructionChatContext) -> ConstructionChatClassification:
        del context
        return ConstructionChatClassification(in_scope=True, quote_intent=True)

    def refusal(self, locale: str) -> str:
        del locale
        return "Kapsam dışı."

    def respond(self, context: ConstructionChatContext) -> str:
        assert context.quote_results
        assert context.legal_context == []
        return "Kayıtlı şirket birim fiyatı kullanıldı."


def test_official_html_ingestion_discovers_documents_and_is_idempotent(app: FastAPI) -> None:
    catalog_url = "https://yfk.csb.gov.tr/sartnameler-test"
    regulation_url = "https://www.resmigazete.gov.tr/eskiler/2026/01/test.htm"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == catalog_url:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=(
                    "<html><body><h1>Teknik Şartnameler</h1>"
                    f'<a href="{regulation_url}">Yapı Ruhsatı Yönetmeliği</a>'
                    "</body></html>"
                ),
            )
        if str(request.url) == regulation_url:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=(
                    "<html><body><h1>Yapı Ruhsatı Yönetmeliği</h1>"
                    "<p>MADDE 1 Yapı ruhsatı başvurusu ilgili idareye yapılır.</p>"
                    "</body></html>"
                ),
            )
        return httpx.Response(404)

    source = LegalSourceDefinition(
        key="test-catalog",
        title="Test Resmî Katalog",
        category="technical_specification_catalog",
        authority="Test Bakanlığı",
        source_url=catalog_url,
        discover_official_links=True,
    )
    with app.state.session_factory.begin() as session:
        ingestor = LegalCorpusIngestor(session, transport=httpx.MockTransport(handler))
        first = ingestor.ingest((source,))
        second = ingestor.ingest((source,))
        matches = LegalKnowledgeRepository(session).search("yapı ruhsatı başvurusu")

    assert first.imported == 2
    assert first.discovered == 1
    assert first.failed == []
    assert second.unchanged == 2
    assert matches
    assert regulation_url in {match.source_url for match in matches}


def test_legal_context_and_official_source_are_added_to_chat_answer(
    client: TestClient,
    app: FastAPI,
    conversation_id: str,
) -> None:
    source_url = "https://www.resmigazete.gov.tr/eskiler/2026/01/ruhsat.htm"
    text = "MADDE 20 Yapı ruhsatı başvurusu imar mevzuatına göre ilgili idareye yapılır."
    with app.state.session_factory.begin() as session:
        LegalKnowledgeRepository(session).replace_document(
            source_key="test-building-permit",
            title="Yapı Ruhsatı Test Mevzuatı",
            category="regulation",
            authority="Test Bakanlığı",
            source_url=source_url,
            published_on=None,
            official_gazette_number=None,
            checksum_sha256=hashlib.sha256(text.encode()).hexdigest(),
            content_type="text/html",
            text=text,
            chunks=[("MADDE 20", text)],
            metadata={},
        )
    app.state.construction_chat_provider = LegalAdviceProvider()

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": "Yapı ruhsatı hangi mevzuata göre alınır?"},
    )

    assert response.status_code == 200
    answer = response.json()["assistant_text"]
    assert "[K1]" in answer
    assert "Resmî kaynaklar:" in answer
    assert source_url in answer
    assert response.json()["quotes"] == []


def test_search_does_not_drop_a_new_law_after_many_matching_chunks(app: FastAPI) -> None:
    with app.state.session_factory.begin() as session:
        repository = LegalKnowledgeRepository(session)
        distractor_text = "İşveren şantiyede gerekli güvenlik önlemlerini alır."
        repository.replace_document(
            source_key="older-general-construction-rules",
            title="Genel Yapı Kuralları",
            category="regulation",
            authority="Test Bakanlığı",
            source_url="https://example.gov.tr/general-rules.pdf",
            published_on=None,
            official_gazette_number=None,
            checksum_sha256=hashlib.sha256(distractor_text.encode()).hexdigest(),
            content_type="application/pdf",
            text=distractor_text,
            chunks=[(None, f"{distractor_text} Bölüm {index}") for index in range(350)],
            metadata={},
        )
        law_text = "6331 sayılı İş Sağlığı ve Güvenliği Kanunu işveren yükümlülüklerini düzenler."
        repository.replace_document(
            source_key="law-6331-regression",
            title="6331 Sayılı İş Sağlığı ve Güvenliği Kanunu",
            category="law",
            authority="T.C. Çalışma ve Sosyal Güvenlik Bakanlığı",
            source_url="https://example.gov.tr/6331.pdf",
            published_on=None,
            official_gazette_number="28339",
            checksum_sha256=hashlib.sha256(law_text.encode()).hexdigest(),
            content_type="application/pdf",
            text=law_text,
            chunks=[("MADDE 4", law_text)],
            metadata={"law_number": "6331"},
        )

        matches = repository.search("6331 sayılı Kanuna göre işveren yükümlülükleri", limit=3)

    assert matches
    assert matches[0].document_key == "law-6331-regression"


def test_quote_response_does_not_attach_unrelated_legal_sources(
    client: TestClient,
    app: FastAPI,
    conversation_id: str,
) -> None:
    source_url = "https://example.gov.tr/formwork-rules.pdf"
    text = "Kalıp işçiliği sırasında çalışma iskelesi güvenli kurulmalıdır."
    with app.state.session_factory.begin() as session:
        LegalKnowledgeRepository(session).replace_document(
            source_key="test-formwork-rules",
            title="Kalıp İşleri Test Şartnamesi",
            category="technical_specification",
            authority="Test Bakanlığı",
            source_url=source_url,
            published_on=None,
            official_gazette_number=None,
            checksum_sha256=hashlib.sha256(text.encode()).hexdigest(),
            content_type="application/pdf",
            text=text,
            chunks=[(None, text)],
            metadata={},
        )
    app.state.construction_chat_provider = QuoteWithoutLegalContextProvider()

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": "Kalıp işçiliğinin m² fiyatı nedir?"},
    )

    assert response.status_code == 200
    answer = response.json()["assistant_text"]
    assert "700 TL/m² + KDV" in answer
    assert "Resmî kaynaklar:" not in answer
    assert source_url not in answer
