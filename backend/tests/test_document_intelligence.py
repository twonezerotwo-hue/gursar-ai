from __future__ import annotations

from io import BytesIO

from ezdxf.filemanagement import new
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pypdf import PdfWriter
from sqlalchemy import func, select

from app.core.persistence import AuditEventORM, OutboxEventORM


def test_document_tool_registry_exposes_five_ready_integrations(client: TestClient) -> None:
    response = client.get("/api/v1/document-tools")

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted_extensions"] == [
        ".dxf",
        ".ifc",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
    ]
    assert {tool["id"] for tool in payload["tools"]} == {
        "pdfplumber",
        "opencv",
        "rapidocr",
        "ezdxf",
        "ifcopenshell",
    }
    assert all(tool["available"] and tool["version"] for tool in payload["tools"])


def test_opencv_and_rapidocr_analyze_a_real_png(client: TestClient) -> None:
    image = Image.new("RGB", (480, 180), "white")
    ImageDraw.Draw(image).text((30, 60), "GURSAR PROJE 2026", fill="black")
    content = BytesIO()
    image.save(content, format="PNG")

    response = client.post(
        "/api/v1/document-tools/analyze",
        files={"file": ("plan.png", content.getvalue(), "image/png")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["format"] == ".png"
    assert payload["tools_used"] == ["opencv", "rapidocr"]
    assert payload["metrics"]["width_px"] == 480
    assert payload["metrics"]["height_px"] == 180


def test_pdfplumber_analyzes_a_real_pdf(client: TestClient) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    content = BytesIO()
    writer.write(content)

    response = client.post(
        "/api/v1/document-tools/analyze",
        files={"file": ("mimari.pdf", content.getvalue(), "application/pdf")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["format"] == ".pdf"
    assert payload["metrics"]["page_count"] == 1
    assert "pdfplumber" in payload["tools_used"]


def test_ezdxf_analyzes_layers_and_entities(client: TestClient, tmp_path) -> None:
    path = tmp_path / "statik.dxf"
    drawing = new("R2018")
    drawing.layers.add("KOLON")
    drawing.modelspace().add_line((0, 0), (500, 0), dxfattribs={"layer": "KOLON"})
    drawing.modelspace().add_circle((250, 250), radius=50)
    drawing.saveas(path)

    response = client.post(
        "/api/v1/document-tools/analyze",
        files={"file": ("statik.dxf", path.read_bytes(), "image/vnd.dxf")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tools_used"] == ["ezdxf"]
    assert payload["metrics"]["entity_count"] == 2
    assert "KOLON" in payload["metrics"]["layers"]


def test_ifcopenshell_reads_a_minimal_ifc_model(client: TestClient) -> None:
    content = b"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
FILE_NAME('gursar.ifc','2026-08-24T00:00:00',(),(),'GURSAR','GURSAR','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCPERSON($,$,'GURSAR',$,$,$,$,$);
#2=IFCORGANIZATION($,'GURSAR',$,$,$);
#3=IFCPERSONANDORGANIZATION(#1,#2,$);
#4=IFCAPPLICATION(#2,'1.0','GURSAR AI','GURSAR');
#5=IFCOWNERHISTORY(#3,#4,$,.ADDED.,$,$,$,0);
#6=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);
#7=IFCUNITASSIGNMENT((#6));
#8=IFCPROJECT('3t3TDZl_D9NOIWB0BSjzJI',#5,'Test Project',$,$,$,$,$,#7);
ENDSEC;
END-ISO-10303-21;
"""

    response = client.post(
        "/api/v1/document-tools/analyze",
        files={"file": ("model.ifc", content, "application/x-step")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tools_used"] == ["ifcopenshell"]
    assert payload["metrics"]["schema"] == "IFC4"
    assert payload["metrics"]["projects"] == 1


def test_file_signature_must_match_extension(client: TestClient) -> None:
    response = client.post(
        "/api/v1/document-tools/analyze",
        files={"file": ("fake.ifc", b"not an IFC", "application/x-step")},
    )

    assert response.status_code == 415


def test_analysis_is_persisted_once_per_conversation_and_reused(
    client: TestClient,
    app: FastAPI,
) -> None:
    conversation = client.post("/api/v1/conversations", json={"locale": "tr"}).json()
    conversation_id = conversation["conversation_id"]
    image = Image.new("RGB", (64, 64), "white")
    content = BytesIO()
    image.save(content, format="PNG")
    files = {"file": ("plan.png", content.getvalue(), "image/png")}

    first = client.post(
        "/api/v1/document-tools/analyze",
        data={"conversation_id": conversation_id},
        files=files,
    )
    second = client.post(
        "/api/v1/document-tools/analyze",
        data={"conversation_id": conversation_id},
        files=files,
    )
    listed = client.get(f"/api/v1/document-tools/conversations/{conversation_id}/analyses")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert second.json()["analysis_id"] == first.json()["analysis_id"]
    assert len(first.json()["sha256"]) == 64
    assert first.json()["size_bytes"] == len(content.getvalue())
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    with app.state.session_factory() as session:
        audit_count = session.scalar(
            select(func.count())
            .select_from(AuditEventORM)
            .where(AuditEventORM.action == "DOCUMENT_ANALYSIS_CREATED")
        )
        outbox_count = session.scalar(
            select(func.count())
            .select_from(OutboxEventORM)
            .where(OutboxEventORM.event_type == "document.analysis.created.v1")
        )
    assert audit_count == 1
    assert outbox_count == 1


def test_analysis_rejects_unknown_conversation(client: TestClient) -> None:
    image = Image.new("RGB", (16, 16), "white")
    content = BytesIO()
    image.save(content, format="PNG")

    response = client.post(
        "/api/v1/document-tools/analyze",
        data={"conversation_id": "missing"},
        files={"file": ("plan.png", content.getvalue(), "image/png")},
    )

    assert response.status_code == 404
