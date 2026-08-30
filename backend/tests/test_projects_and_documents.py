from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import create_database_engine
from app.core.persistence import AuditEventORM, OutboxEventORM
from app.main import create_app


def make_pdf(*, pages: list[tuple[float, float]], password: str | None = None) -> bytes:
    writer = PdfWriter()
    for width, height in pages:
        writer.add_blank_page(width=width, height=height)
    if password is not None:
        writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def create_project(client: TestClient, name: str = "Göktürk Konut Projesi") -> str:
    response = client.post("/api/v1/projects", json={"name": name, "locale": "tr"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload_pdf(
    client: TestClient,
    project_id: str,
    *,
    filename: str,
    content: bytes,
    content_type: str = "application/pdf",
):
    return client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": (filename, content, content_type)},
    )


def test_project_create_and_get(client: TestClient) -> None:
    project_id = create_project(client)

    response = client.get(f"/api/v1/projects/{project_id}")

    assert response.status_code == 200
    assert response.json()["name"] == "Göktürk Konut Projesi"
    assert response.json()["status"] == "ACTIVE"


def test_document_capabilities_match_current_project_readers(client: TestClient) -> None:
    response = client.get("/api/v1/documents/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "accepted_extensions": [".pdf", ".ifc", ".dxf"],
        "accepted_media_types": ["application/pdf", "application/x-step", "image/vnd.dxf"],
        "max_upload_bytes": 50 * 1024 * 1024,
        "max_pdf_pages": 500,
        "encrypted_pdf_supported": False,
    }


def test_valid_pdf_upload_is_content_verified_versioned_and_sheet_indexed(
    client: TestClient,
    app: FastAPI,
) -> None:
    project_id = create_project(client)
    content = make_pdf(pages=[(595, 842), (842, 595)])

    response = upload_pdf(
        client,
        project_id,
        filename="../../statik-proje.pdf",
        content=content,
        content_type="text/plain",
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["original_name"] == "statik-proje.pdf"
    assert payload["latest_version"]["version"] == 1
    assert payload["latest_version"]["media_type"] == "application/pdf"
    assert payload["latest_version"]["page_count"] == 2
    assert [sheet["page_number"] for sheet in payload["sheets"]] == [1, 2]
    assert payload["sheets"][0]["width_points"] == "595.000"
    assert payload["sheets"][1]["width_points"] == "842.000"

    document_id = payload["id"]
    fetched = client.get(f"/api/v1/documents/{document_id}")
    sheets = client.get(f"/api/v1/documents/{document_id}/sheets")
    assert fetched.status_code == 200
    assert sheets.status_code == 200
    assert sheets.json() == payload["sheets"]

    storage_root = app.state.settings.document_storage_root
    stored_files = list(storage_root.rglob("*.pdf"))
    assert len(stored_files) == 1
    assert stored_files[0].read_bytes() == content
    assert "statik-proje.pdf" not in str(stored_files[0])

    with app.state.session_factory() as session:
        audit = session.scalar(
            select(AuditEventORM).where(AuditEventORM.action == "DOCUMENT_VERSION_UPLOADED")
        )
        event = session.scalar(
            select(OutboxEventORM).where(
                OutboxEventORM.event_type == "document.version.uploaded.v1"
            )
        )
        assert audit is not None
        assert event is not None


def test_same_name_with_changed_pdf_creates_new_immutable_version(client: TestClient) -> None:
    project_id = create_project(client)
    first = upload_pdf(
        client,
        project_id,
        filename="mimari.pdf",
        content=make_pdf(pages=[(595, 842)]),
    )
    second = upload_pdf(
        client,
        project_id,
        filename="mimari.pdf",
        content=make_pdf(pages=[(600, 900), (600, 900)]),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["latest_version"]["version"] == 2
    assert second.json()["latest_version"]["page_count"] == 2


def test_duplicate_pdf_in_same_project_is_rejected_without_second_file(
    client: TestClient,
    app: FastAPI,
) -> None:
    project_id = create_project(client)
    content = make_pdf(pages=[(595, 842)])
    first = upload_pdf(client, project_id, filename="a.pdf", content=content)
    duplicate = upload_pdf(client, project_id, filename="b.pdf", content=content)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert (
        duplicate.json()["detail"]["existing_document_version_id"]
        == first.json()["latest_version"]["id"]
    )
    assert len(list(app.state.settings.document_storage_root.rglob("*.pdf"))) == 1


def test_pdf_content_can_exist_in_a_different_project(client: TestClient) -> None:
    content = make_pdf(pages=[(595, 842)])
    first_project = create_project(client, "Birinci Proje")
    second_project = create_project(client, "İkinci Proje")

    first = upload_pdf(client, first_project, filename="plan.pdf", content=content)
    second = upload_pdf(client, second_project, filename="plan.pdf", content=content)

    assert first.status_code == 201
    assert second.status_code == 201


def test_fake_corrupt_empty_and_encrypted_pdfs_are_rejected_without_storage(
    client: TestClient,
    app: FastAPI,
) -> None:
    project_id = create_project(client)
    cases = (
        ("empty.pdf", b"", 422),
        ("fake.pdf", b"not-a-pdf", 415),
        ("corrupt.pdf", b"%PDF-1.7\nnot-a-valid-structure", 422),
        ("encrypted.pdf", make_pdf(pages=[(595, 842)], password="secret"), 422),
    )

    for filename, content, expected_status in cases:
        response = upload_pdf(client, project_id, filename=filename, content=content)
        assert response.status_code == expected_status, response.text

    storage_root: Path = app.state.settings.document_storage_root
    assert not storage_root.exists() or list(storage_root.rglob("*.pdf")) == []


def test_document_endpoints_return_not_found(client: TestClient) -> None:
    assert client.get("/api/v1/projects/missing").status_code == 404
    assert client.get("/api/v1/documents/missing").status_code == 404
    assert client.get("/api/v1/documents/missing/sheets").status_code == 404


def test_upload_size_and_page_limits_are_enforced_before_storage(tmp_path: Path) -> None:
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
        document_storage_root=tmp_path / "limited-documents",
        document_max_upload_bytes=256,
        document_max_pdf_pages=1,
    )
    application = create_app(settings=settings, engine=engine)

    with TestClient(application) as limited_client:
        project_id = create_project(limited_client)
        too_large = upload_pdf(
            limited_client,
            project_id,
            filename="large.pdf",
            content=make_pdf(pages=[(595, 842)]),
        )
        assert too_large.status_code == 413

    engine.dispose()

    page_engine = create_database_engine("sqlite+pysqlite:///:memory:")
    page_settings = Settings(
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        admin_api_key="test-admin-key",
        default_location_code="TR/ISTANBUL",
        default_locale="tr",
        understanding_provider="rule_based",
        openai_api_key=None,
        openai_model="gpt-5-mini",
        chat_provider="deterministic",
        document_storage_root=tmp_path / "page-limited-documents",
        document_max_pdf_pages=1,
    )
    page_application = create_app(settings=page_settings, engine=page_engine)

    with TestClient(page_application) as page_client:
        project_id = create_project(page_client)
        too_many_pages = upload_pdf(
            page_client,
            project_id,
            filename="two-pages.pdf",
            content=make_pdf(pages=[(595, 842), (595, 842)]),
        )
        assert too_many_pages.status_code == 422
        assert not page_settings.document_storage_root.exists()

    page_engine.dispose()
