from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import ifcopenshell
import ifcopenshell.guid
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import create_database_engine
from app.main import create_app
from app.modules.conversation.infrastructure.models import VisitorSessionORM


def _project(client: TestClient) -> str:
    response = client.post("/api/v1/projects", json={"name": "Graph Test", "locale": "tr"})
    assert response.status_code == 201
    return response.json()["id"]


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _ifc() -> bytes:
    model = ifcopenshell.file(schema="IFC4")
    project = model.create_entity("IfcProject", GlobalId=ifcopenshell.guid.new(), Name="Test")
    site = model.create_entity("IfcSite", GlobalId=ifcopenshell.guid.new(), Name="Site")
    building = model.create_entity("IfcBuilding", GlobalId=ifcopenshell.guid.new(), Name="A Blok")
    storey = model.create_entity(
        "IfcBuildingStorey",
        GlobalId=ifcopenshell.guid.new(),
        Name="Zemin Kat",
        Elevation=0.0,
    )
    wall = model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.new(), Name="W-01")
    for parent, children in (
        (project, [site]),
        (site, [building]),
        (building, [storey]),
    ):
        model.create_entity(
            "IfcRelAggregates",
            GlobalId=ifcopenshell.guid.new(),
            RelatingObject=parent,
            RelatedObjects=children,
        )
    model.create_entity(
        "IfcRelContainedInSpatialStructure",
        GlobalId=ifcopenshell.guid.new(),
        RelatedElements=[wall],
        RelatingStructure=storey,
    )
    gross_area = model.create_entity("IfcQuantityArea", Name="GrossSideArea", AreaValue=12.5)
    gross_volume = model.create_entity("IfcQuantityVolume", Name="GrossVolume", VolumeValue=3.25)
    quantities = model.create_entity(
        "IfcElementQuantity",
        GlobalId=ifcopenshell.guid.new(),
        Name="Qto_WallBaseQuantities",
        Quantities=[gross_area, gross_volume],
    )
    model.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifcopenshell.guid.new(),
        RelatedObjects=[wall],
        RelatingPropertyDefinition=quantities,
    )
    return model.to_string().encode("utf-8")


def test_conversation_id_alone_does_not_grant_access(
    client: TestClient,
    app: FastAPI,
) -> None:
    created = client.post("/api/v1/conversations", json={"locale": "tr"})
    conversation_id = created.json()["conversation_id"]
    with TestClient(app) as visitor_b:
        denied = visitor_b.get(f"/api/v1/conversations/{conversation_id}")
    assert denied.status_code == 403


def test_invalid_and_expired_owner_tokens_are_rejected(
    client: TestClient,
    app: FastAPI,
) -> None:
    created = client.post("/api/v1/conversations", json={"locale": "tr"})
    payload = created.json()
    conversation_id = payload["conversation_id"]
    token = payload["conversation_owner_token"]
    invalid = client.get(
        f"/api/v1/conversations/{conversation_id}",
        headers={"X-Conversation-Owner-Token": "invalid-token"},
    )
    assert invalid.status_code == 403

    with app.state.session_factory.begin() as session:
        owner = session.scalar(
            select(VisitorSessionORM).where(VisitorSessionORM.conversation_id == conversation_id)
        )
        assert owner is not None
        owner.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with TestClient(app) as fresh_client:
        expired = fresh_client.get(
            f"/api/v1/conversations/{conversation_id}",
            headers={"X-Conversation-Owner-Token": token},
        )
    assert expired.status_code == 403


def test_same_qr_always_creates_isolated_owner_sessions(client: TestClient) -> None:
    first = client.post("/api/v1/qr/CARD001/conversation").json()
    second = client.post("/api/v1/qr/CARD001/conversation").json()
    assert first["conversation_id"] != second["conversation_id"]
    assert first["conversation_owner_token"] != second["conversation_owner_token"]
    assert first["resumed"] is False
    assert second["resumed"] is False


def test_manual_measurement_is_server_calculated_and_has_evidence(client: TestClient) -> None:
    project_id = _project(client)
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("plan.pdf", _pdf(), "application/pdf")},
    ).json()
    sheet_id = uploaded["sheets"][0]["id"]
    region = client.post(
        f"/api/v1/projects/{project_id}/view-regions",
        json={
            "sheet_id": sheet_id,
            "bbox_normalized": [0.0, 0.0, 1.0, 1.0],
            "view_type": "PLAN",
        },
    )
    assert region.status_code == 201, region.text
    calibration = client.post(
        f"/api/v1/projects/{project_id}/scale-calibrations",
        json={
            "view_region_id": region.json()["id"],
            "method": "MANUAL_TWO_POINT",
            "known_distance": 10,
            "pixel_distance": 100,
            "drawing_units": "m",
            "confidence": 1,
            "verified": True,
            "reviewer": "test-engineer",
        },
    )
    assert calibration.status_code == 201, calibration.text
    measurement = client.post(
        f"/api/v1/projects/{project_id}/measurements",
        json={
            "measurement_type": "AREA",
            "geometry": {"points": [[0, 0], [100, 0], [100, 100], [0, 100]]},
            "unit": "m2",
            "service_code": "formwork.manual",
            "description": "Döşeme kalıbı",
            "view_region_id": region.json()["id"],
            "scale_calibration_id": calibration.json()["id"],
        },
    )
    assert measurement.status_code == 201, measurement.text
    assert Decimal(measurement.json()["calculated_value"]) == Decimal("100")
    item_id = measurement.json()["takeoff_item"]["id"]
    evidence = client.get(f"/api/v1/takeoff/{item_id}/evidence")
    assert evidence.status_code == 200
    assert len(evidence.json()["evidence"]) == 1
    assert evidence.json()["item"]["quantity_source"] == "VERIFIED_PROJECT_MEASUREMENT"


def test_ifc_upload_creates_elements_quantities_takeoff_and_evidence(
    client: TestClient,
) -> None:
    project_id = _project(client)
    upload = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("model.ifc", _ifc(), "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    version = upload.json()["latest_version"]
    assert version["media_type"] == "application/x-step"
    assert version["page_count"] == 0
    analyzed = client.post(
        f"/api/v1/projects/{project_id}/analysis",
        json={"document_version_id": version["id"]},
    )
    assert analyzed.status_code == 201, analyzed.text
    summary = analyzed.json()
    assert summary["element_count"] == 1
    assert summary["elements"]["WALL"]["count"] == 1
    assert summary["elements"]["WALL"]["area"] == 12.5
    assert summary["elements"]["WALL"]["volume"] == 3.25
    assert summary["elements"]["WALL"]["storeys"] == {"Zemin Kat": 1}

    elements = client.get(f"/api/v1/projects/{project_id}/elements").json()
    assert elements[0]["source_external_id"]
    assert elements[0]["extraction_method"] == "IFC_QUANTITY_SET"
    takeoff = client.get(f"/api/v1/projects/{project_id}/takeoff").json()
    assert {item["unit"] for item in takeoff} == {"adet", "m2", "m3"}
    for item in takeoff:
        evidence = client.get(f"/api/v1/takeoff/{item['id']}/evidence").json()
        assert len(evidence["evidence"]) == len(item["measurement_ids"])
        assert evidence["evidence"][0]["source_coordinates"]["ifc_global_id"]


def test_ifc_analysis_is_idempotent_for_same_document_version(client: TestClient) -> None:
    project_id = _project(client)
    upload = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("model.ifc", _ifc(), "application/x-step")},
    ).json()
    payload = {"document_version_id": upload["latest_version"]["id"]}
    first = client.post(f"/api/v1/projects/{project_id}/analysis", json=payload)
    second = client.post(f"/api/v1/projects/{project_id}/analysis", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["reused"] is True
    assert second.json()["element_count"] == first.json()["element_count"]


def test_sqlite_backup_is_dry_run_capable_checksummed_and_restore_validated(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "backup-source.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_database_engine(database_url)
    settings = Settings(
        environment="test",
        database_url=database_url,
        admin_api_key="backup-test-key",
        default_location_code="TR/ISTANBUL",
        default_locale="tr",
        understanding_provider="rule_based",
        openai_api_key=None,
        openai_model="test",
        chat_provider="deterministic",
        database_backup_root=tmp_path / "backups",
    )
    application = create_app(settings=settings, engine=engine)
    with TestClient(application) as backup_client:
        headers = {"X-Admin-Key": "backup-test-key"}
        dry_run = backup_client.post("/api/v1/admin/backups?dry_run=true", headers=headers)
        created = backup_client.post("/api/v1/admin/backups", headers=headers)
    engine.dispose()

    assert dry_run.status_code == 200
    assert dry_run.json()["validation_status"] == "DRY_RUN_OK"
    assert created.status_code == 200, created.text
    assert created.json()["validation_status"] == "RESTORE_VALIDATED"
    assert len(created.json()["checksum_sha256"]) == 64
    assert Path(created.json()["target"]).is_file()
