from __future__ import annotations

import math
from io import BytesIO

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.modules.documents.infrastructure.models import SheetORM
from app.modules.project_graph.infrastructure.models import (
    ExtractedPdfTextORM,
    MeasurementEvidenceORM,
    MeasurementORM,
    ScaleCalibrationORM,
    SheetMetadataRevisionORM,
    VectorPdfAnalysisORM,
    ViewRegionORM,
)
from app.modules.training.infrastructure.models import ActiveLearningItemORM, TrainingSampleORM

HEADERS = {
    "X-Admin-Key": "test-admin-key",
    "X-Reviewer-Id": "pdf.engineer@example.test",
    "X-Reviewer-Role": "ENGINEER",
}


def _vector_pdf(
    *,
    title: str = "MIMARI ZEMIN KAT PLANI",
    scale: str | None = "1:50",
    detail_scale: str | None = None,
) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=1000, height=700)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    commands = [
        f"BT /F1 18 Tf 60 620 Td ({title}) Tj ET",
        "BT /F1 12 Tf 180 355 Td (SALON) Tj ET",
        "80 300 m 180 300 l S",
        "100 250 200 100 re S",
        "BT /F1 12 Tf 700 120 Td (A-101) Tj ET",
        "BT /F1 12 Tf 700 90 Td (REV 02) Tj ET",
        "BT /F1 12 Tf 700 60 Td (PROJE GURSAR) Tj ET",
    ]
    if scale:
        label = "PLAN" if detail_scale else "OLCEK"
        commands.append(f"BT /F1 12 Tf 700 30 Td ({label} {scale}) Tj ET")
    if detail_scale:
        commands.append(f"BT /F1 12 Tf 100 500 Td (DETAIL {detail_scale}) Tj ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _upload_analyze(client: TestClient, content: bytes) -> tuple[str, str, dict]:
    project_id = client.post("/api/v1/projects", json={"name": "PDF Intelligence"}).json()["id"]
    upload = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("A-101.pdf", content, "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    version_id = upload.json()["latest_version"]["id"]
    response = client.post(
        f"/api/v1/projects/{project_id}/documents/{version_id}/analyze-vector-pdf"
    )
    assert response.status_code == 201, response.text
    return project_id, version_id, response.json()


def test_native_text_title_metadata_classification_and_regions(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, body = _upload_analyze(client, _vector_pdf())
    assert body["page_count"] == 1 and body["vector_page_count"] == 1
    assert body["summary_json"]["pages"][0]["sheet_number"] == "A-101"
    assert body["summary_json"]["pages"][0]["revision"] == "02"
    with app.state.session_factory() as session:
        sheet = session.query(SheetORM).filter_by(document_version_id=version_id).one()
        assert (sheet.discipline, sheet.drawing_type) == ("ARCHITECTURAL", "PLAN")
        assert sheet.metadata_json["title_block"]["confidence"] >= 0.8
        texts = session.query(ExtractedPdfTextORM).filter_by(sheet_id=sheet.id).all()
        assert texts and {row.source for row in texts} == {"PDF_TEXT"}
        regions = session.query(ViewRegionORM).filter_by(sheet_id=sheet.id).all()
        assert {row.view_type for row in regions} == {"PLAN", "TITLE_BLOCK"}
        plan = next(row for row in regions if row.view_type == "PLAN")
        assert float(plan.scale) == 50 and plan.scale_status == "INFERRED"
    search = client.get(f"/api/v1/projects/{project_id}/sheets?q=A-101")
    assert search.status_code == 200 and len(search.json()) == 1


def test_structural_sheet_classification(app: FastAPI, client: TestClient) -> None:
    _, version_id, _ = _upload_analyze(client, _vector_pdf(title="2. KAT KALIP PLANI"))
    with app.state.session_factory() as session:
        sheet = session.query(SheetORM).filter_by(document_version_id=version_id).one()
        assert sheet.discipline == "STRUCTURAL" and sheet.drawing_type == "PLAN"


def test_multiple_scales_create_independent_view_regions(app: FastAPI, client: TestClient) -> None:
    _, version_id, _ = _upload_analyze(client, _vector_pdf(detail_scale="1:10"))
    with app.state.session_factory() as session:
        sheet = session.query(SheetORM).filter_by(document_version_id=version_id).one()
        regions = session.query(ViewRegionORM).filter_by(sheet_id=sheet.id).all()
        pairs = {(row.view_type, int(row.scale)) for row in regions if row.scale is not None}
        assert ("PLAN", 50) in pairs and ("DETAIL", 10) in pairs
        assert sheet.metadata_json["scale"]["value"] is None


def test_vector_measurements_have_scale_and_coordinate_evidence(
    app: FastAPI, client: TestClient
) -> None:
    _, version_id, body = _upload_analyze(client, _vector_pdf())
    assert body["measurement_count"] >= 3
    ratio = 0.0254 / 72 * 50
    with app.state.session_factory() as session:
        measurements = session.query(MeasurementORM).filter_by(source_document_id=version_id).all()
        assert any(
            row.measurement_type == "LENGTH"
            and math.isclose(float(row.calculated_value), 100 * ratio, rel_tol=1e-5)
            for row in measurements
        )
        assert any(
            row.measurement_type == "AREA"
            and math.isclose(float(row.calculated_value), 20_000 * ratio**2, rel_tol=1e-5)
            for row in measurements
        )
        evidence = session.query(MeasurementEvidenceORM).all()
        assert len(evidence) == len(measurements)
        assert all(row.source_coordinates["document_version_id"] == version_id for row in evidence)
        assert all(row.geometry_json["normalized_geometry"] for row in evidence)
        assert all(row.scale_json["scale_calibration_id"] for row in evidence)


def test_scale_unknown_blocks_physical_measurement_and_enters_learning_queue(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, body = _upload_analyze(client, _vector_pdf(scale=None))
    assert body["measurement_count"] == 0
    with app.state.session_factory() as session:
        assert session.query(MeasurementORM).filter_by(source_document_id=version_id).count() == 0
        items = session.query(ActiveLearningItemORM).filter_by(project_id=project_id).all()
        assert any(item.reason == "SCALE_UNKNOWN" for item in items)


def test_analysis_is_idempotent_and_page_render_and_overlays_work(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, _ = _upload_analyze(client, _vector_pdf())
    with app.state.session_factory() as session:
        before = (
            session.query(ViewRegionORM).count(),
            session.query(MeasurementORM).count(),
            session.query(MeasurementEvidenceORM).count(),
        )
    second = client.post(f"/api/v1/projects/{project_id}/documents/{version_id}/analyze-vector-pdf")
    assert second.status_code == 201 and second.json()["reused"] is True
    with app.state.session_factory() as session:
        after = (
            session.query(ViewRegionORM).count(),
            session.query(MeasurementORM).count(),
            session.query(MeasurementEvidenceORM).count(),
        )
        assert session.query(VectorPdfAnalysisORM).count() == 1
    assert after == before
    render = client.get(f"/api/v1/projects/{project_id}/documents/{version_id}/pages/1/render")
    assert render.status_code == 200 and render.content.startswith(b"\x89PNG")
    overlays = client.get(f"/api/v1/projects/{project_id}/documents/{version_id}/pages/1/overlays")
    assert overlays.status_code == 200 and overlays.json()["view_regions"]


def test_sheet_metadata_correction_is_immutable_and_audited(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, _ = _upload_analyze(client, _vector_pdf(title="GENEL PAFTA"))
    with app.state.session_factory() as session:
        sheet_id = session.query(SheetORM).filter_by(document_version_id=version_id).one().id
    response = client.post(
        f"/api/v1/projects/{project_id}/sheets/{sheet_id}/metadata-corrections",
        headers=HEADERS,
        json={
            "corrections": {"discipline": "STRUCTURAL", "drawing_type": "PLAN"},
            "task_type": "SHEET_CLASSIFICATION",
            "reason": "Uzman pafta kontrolü",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["original_prediction_json"]["discipline"] == "UNKNOWN"
    with app.state.session_factory() as session:
        revisions = session.query(SheetMetadataRevisionORM).filter_by(sheet_id=sheet_id).all()
        assert len(revisions) == 1 and revisions[0].revision == 1
        sheet = session.get(SheetORM, sheet_id)
        assert sheet.discipline == "STRUCTURAL" and sheet.review_status == "CORRECTED"


def test_verified_sheet_correction_can_become_training_sample(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, _ = _upload_analyze(client, _vector_pdf(title="GENEL PAFTA"))
    with app.state.session_factory() as session:
        sheet_id = session.query(SheetORM).filter_by(document_version_id=version_id).one().id
    correction = client.post(
        f"/api/v1/projects/{project_id}/sheets/{sheet_id}/metadata-corrections",
        headers=HEADERS,
        json={
            "corrections": {"discipline": "ARCHITECTURAL", "drawing_type": "PLAN"},
            "task_type": "SHEET_CLASSIFICATION",
            "reason": "Uzman doğrulaması",
        },
    )
    assert correction.status_code == 201
    manifest = client.put(
        f"/api/v1/projects/{project_id}/training-manifest",
        headers=HEADERS,
        json={
            "rights_status": "INTERNAL_PROJECT",
            "training_allowed": True,
            "architectural_documents": True,
            "structural_documents": True,
            "geotechnical_documents": True,
            "boq_available": True,
            "verified_quantity_available": True,
            "as_built_available": True,
        },
    )
    assert manifest.status_code == 200
    anonymize = client.post(
        f"/api/v1/projects/{project_id}/training/anonymize/{version_id}",
        headers=HEADERS,
        json={"metadata": {"customer_name": "Test Müşteri", "sheet": "A-101"}},
    )
    assert anonymize.status_code == 201
    sample = client.post(
        f"/api/v1/projects/{project_id}/training-samples",
        headers=HEADERS,
        json={
            "subject_type": "SHEET_REVISION",
            "subject_id": correction.json()["id"],
            "task_type": "SHEET_CLASSIFICATION",
        },
    )
    assert sample.status_code == 201, sample.text
    with app.state.session_factory() as session:
        assert session.get(TrainingSampleORM, sample.json()["id"]).source_sheet_id == sheet_id


def test_manual_scale_calculation_is_backend_deterministic(
    app: FastAPI, client: TestClient
) -> None:
    project_id, version_id, _ = _upload_analyze(client, _vector_pdf())
    with app.state.session_factory() as session:
        sheet = session.query(SheetORM).filter_by(document_version_id=version_id).one()
        region_id = (
            session.query(ViewRegionORM).filter_by(sheet_id=sheet.id, view_type="PLAN").one().id
        )
    response = client.post(
        f"/api/v1/projects/{project_id}/scale-calibrations",
        json={
            "view_region_id": region_id,
            "method": "MANUAL_TWO_POINT",
            "known_distance": 5,
            "pixel_distance": 250,
            "drawing_units": "PIXEL",
            "confidence": 1,
            "verified": True,
            "reviewer": "pdf.engineer@example.test",
        },
    )
    assert response.status_code == 201, response.text
    assert math.isclose(float(response.json()["scale_ratio"]), 0.02)
    with app.state.session_factory() as session:
        assert session.get(ScaleCalibrationORM, response.json()["id"]).verified is True
