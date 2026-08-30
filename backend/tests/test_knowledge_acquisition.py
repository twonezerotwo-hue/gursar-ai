from __future__ import annotations

import io
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select

from app.modules.estimation.infrastructure.models import CompletedProjectRecordORM
from app.modules.knowledge_acquisition.application.pipeline import (
    KnowledgeAcquisitionPipeline,
    SourcePolicy,
)
from app.modules.knowledge_acquisition.application.processing import (
    DocumentParser,
    section_diff,
)
from app.modules.knowledge_acquisition.application.retrieval import (
    HybridKnowledgeSearch,
    route_knowledge_query,
)
from app.modules.knowledge_acquisition.domain.models import (
    KnowledgeSourceClass,
    KnowledgeSourceDefinition,
    RedistributionPolicy,
    Taxonomy,
)
from app.modules.knowledge_acquisition.infrastructure.config import KnowledgeConfig
from app.modules.knowledge_acquisition.infrastructure.models import (
    KnowledgeChangeORM,
    KnowledgeConflictORM,
    KnowledgeDocumentVersionORM,
    KnowledgeRuleORM,
)
from app.modules.knowledge_acquisition.infrastructure.repository import KnowledgeRepository


def source_definition(
    *,
    key: str = "test-fire-regulation",
    url: str = "https://www.afad.gov.tr/test-rule.html",
    quality: int = 100,
    redistribution: RedistributionPolicy = RedistributionPolicy.ALLOWED,
) -> KnowledgeSourceDefinition:
    return KnowledgeSourceDefinition(
        key=key,
        name="Test Yangın Yönetmeliği",
        authority="Test Resmî Kurum",
        source_url=url,
        taxonomy=Taxonomy(
            discipline="fire",
            subdiscipline="fire_safety",
            topic="escape_distance",
            document_type="regulation",
            source_class=KnowledgeSourceClass.REGULATION,
            jurisdiction="TR",
        ),
        quality_score=quality,
        license="official-publication",
        copyright_status="public-official-document",
        redistribution_policy=redistribution,
        published_date=date(2026, 1, 1),
        effective_from=date(2026, 2, 1),
        version_label="2026.1",
    )


def html_rule(distance: int = 30) -> bytes:
    return (
        "<html><body><h1>Yangın Güvenliği</h1>"
        f"<p>MADDE 10 Kaçış mesafesi en fazla {distance} m olmak zorundadır.</p>"
        "</body></html>"
    ).encode()


def test_source_policy_rejects_untrusted_insecure_and_invalid_quality() -> None:
    policy = SourcePolicy(KnowledgeConfig())
    policy.validate(source_definition())
    with pytest.raises(ValueError, match="HTTPS"):
        policy.validate(source_definition(url="http://www.afad.gov.tr/test.pdf"))
    with pytest.raises(ValueError, match="not trusted"):
        policy.validate(source_definition(url="https://seo-spam.example/test.pdf"))
    with pytest.raises(ValueError, match="SourceQualityScore"):
        policy.validate(source_definition(quality=101))


def test_pipeline_ingests_approves_deduplicates_extracts_rules_and_isolates_training(
    app: FastAPI, tmp_path: Path
) -> None:
    url = "https://www.afad.gov.tr/test-rule.html"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8", "etag": '"v1"'},
            content=html_rule(),
            request=request,
        )

    with app.state.session_factory.begin() as session:
        repository = KnowledgeRepository(session)
        pipeline = KnowledgeAcquisitionPipeline(
            repository,
            KnowledgeConfig(),
            storage_root=tmp_path / "knowledge",
            transport=httpx.MockTransport(handler),
        )
        first = pipeline.run((source_definition(),), approve_official=True)
        second = pipeline.run((source_definition(),), approve_official=True)
        dashboard = repository.dashboard()
        learned_projects = session.scalar(
            select(func.count()).select_from(CompletedProjectRecordORM)
        )

    assert first.downloaded == 1
    assert first.ingested == 1
    assert first.active == 1
    assert first.chunks_created >= 1
    assert first.rules_created >= 1
    assert second.unchanged == 1
    assert dashboard["active_documents"] == 1
    assert dashboard["structured_rules"] >= 1
    assert dashboard["formulas"] == 4
    assert learned_projects == 0
    assert list((tmp_path / "knowledge" / "documents").iterdir())


def test_licensed_standard_is_metadata_only_and_never_downloaded(
    app: FastAPI, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Metadata-only source must not be downloaded: {request.url}")

    definition = replace(
        source_definition(key="tse-test", url="https://www.tse.org.tr/"),
        redistribution_policy=RedistributionPolicy.METADATA_ONLY,
        license="licensed-standard",
        copyright_status="copyrighted",
    )
    with app.state.session_factory.begin() as session:
        report = KnowledgeAcquisitionPipeline(
            KnowledgeRepository(session),
            KnowledgeConfig(),
            storage_root=tmp_path / "knowledge",
            transport=httpx.MockTransport(handler),
        ).run((definition,), approve_official=True)
        version = session.scalar(
            select(KnowledgeDocumentVersionORM).where(
                KnowledgeDocumentVersionORM.status == "METADATA_ONLY"
            )
        )
    assert report.metadata_only == 1
    assert report.downloaded == 0
    assert version is not None
    assert version.parsed_text == ""
    assert version.metadata_json["full_text_ingestion"] == "REJECTED_BY_LICENSE_POLICY"


def test_new_content_creates_version_and_semantic_change_records(
    app: FastAPI, tmp_path: Path
) -> None:
    current = {"content": html_rule(30)}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=current["content"],
            request=request,
        )

    with app.state.session_factory.begin() as session:
        repository = KnowledgeRepository(session)
        pipeline = KnowledgeAcquisitionPipeline(
            repository,
            KnowledgeConfig(),
            storage_root=tmp_path / "knowledge",
            transport=httpx.MockTransport(handler),
        )
        pipeline.run((source_definition(),), approve_official=True)
        current["content"] = html_rule(25)
        pipeline.run((replace(source_definition(), version_label="2026.2"),), approve_official=True)
        versions = session.scalars(
            select(KnowledgeDocumentVersionORM).order_by(KnowledgeDocumentVersionORM.version_number)
        ).all()
        changes = session.scalars(select(KnowledgeChangeORM)).all()

    assert [version.status for version in versions] == ["SUPERSEDED", "ACTIVE"]
    assert [version.version_label for version in versions] == ["2026.1", "2026.2"]
    assert {change.change_type for change in changes} == {"ADDED", "REMOVED"}


def test_hybrid_retrieval_binds_version_article_date_and_prefers_authority(
    app: FastAPI, tmp_path: Path
) -> None:
    urls = {
        "https://www.afad.gov.tr/high.html": html_rule(30),
        "https://example.edu.tr/low.html": html_rule(45),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=urls[str(request.url)],
            request=request,
        )

    high = source_definition(key="high", url="https://www.afad.gov.tr/high.html", quality=100)
    low = source_definition(key="low", url="https://example.edu.tr/low.html", quality=90)
    with app.state.session_factory.begin() as session:
        repository = KnowledgeRepository(session)
        pipeline = KnowledgeAcquisitionPipeline(
            repository,
            KnowledgeConfig(),
            storage_root=tmp_path / "knowledge",
            transport=httpx.MockTransport(handler),
        )
        pipeline.run((low, high), approve_official=True)
        hits = HybridKnowledgeSearch(repository, KnowledgeConfig()).search(
            "yangın kaçış mesafesi en fazla kaç metre",
            limit=2,
            filters={"discipline": "fire"},
        )

    assert len(hits) == 2
    assert hits[0].source_quality_score == 100
    assert hits[0].article is not None and "MADDE 10" in hits[0].article
    assert hits[0].version == "2026.1"
    assert hits[0].effective_from == date(2026, 2, 1)
    assert hits[0].source_confidence == 100
    assert hits[0].data_confidence == 100


def test_conflicting_active_rules_are_recorded_not_silently_overwritten(
    app: FastAPI, tmp_path: Path
) -> None:
    urls = {
        "https://www.afad.gov.tr/a.html": html_rule(30),
        "https://www.afad.gov.tr/b.html": html_rule(45),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=urls[str(request.url)],
            request=request,
        )

    sources = (
        source_definition(key="rule-a", url="https://www.afad.gov.tr/a.html"),
        source_definition(key="rule-b", url="https://www.afad.gov.tr/b.html"),
    )
    with app.state.session_factory.begin() as session:
        repository = KnowledgeRepository(session)
        report = KnowledgeAcquisitionPipeline(
            repository,
            KnowledgeConfig(),
            storage_root=tmp_path / "knowledge",
            transport=httpx.MockTransport(handler),
        ).run(sources, approve_official=True)
        conflicts = session.scalars(select(KnowledgeConflictORM)).all()
        all_rules = session.scalars(select(KnowledgeRuleORM)).all()
        active_rules = [rule for rule in all_rules if rule.status == "ACTIVE"]

    assert report.conflicts_created >= 1, [
        (rule.topic, rule.building_type, rule.requirement, rule.status) for rule in all_rules
    ]
    assert len(conflicts) >= 1
    assert conflicts[0].detail_json["type"] == "SOURCE_CONFLICT"
    assert len(active_rules) >= 2


def test_pdf_text_layer_parser_extracts_article() -> None:
    buffer = io.BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (MADDE 1 Betonarme tasarim.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(buffer)

    parsed = DocumentParser().parse(
        buffer.getvalue(), content_type="application/pdf", filename="test.pdf"
    )
    assert parsed.parser_name == "pypdf-text-layer"
    assert "MADDE 1" in parsed.text


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Yangın merdiveni kaçış mesafesi", "FIRE"),
        ("Radye temel ve zemin", "GEOTECHNICAL"),
        ("2026 YFK mantolama pozu", "YFK_COST"),
        ("Emsal hesabı", "ZONING"),
        ("IFC dosyasındaki IfcWall", "BIM"),
        ("Bu projede kaç ton demir?", "ESTIMATION"),
    ],
)
def test_acceptance_query_routing(query: str, expected: str) -> None:
    assert expected in route_knowledge_query(query)["routes"]


def test_section_diff_marks_added_and_removed_without_overwriting() -> None:
    changes = section_diff("MADDE 1\nEski kural", "MADDE 1\nYeni kural")
    assert {change["change_type"] for change in changes} == {"ADDED", "REMOVED"}


def test_knowledge_admin_dashboard_requires_admin_key(client) -> None:
    unauthorized = client.get("/api/v1/knowledge/admin/dashboard")
    authorized = client.get(
        "/api/v1/knowledge/admin/dashboard",
        headers={"X-Admin-Key": "test-admin-key"},
    )
    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert "total_documents" in authorized.json()
