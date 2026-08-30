from __future__ import annotations

import math
from pathlib import Path

import pytest
from ezdxf import units
from ezdxf.filemanagement import new
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.project_graph.infrastructure.models import (
    BuildingElementORM,
    MeasurementEvidenceORM,
    MeasurementORM,
)
from app.modules.training.infrastructure.models import ActiveLearningItemORM

HEADERS = {
    "X-Admin-Key": "test-admin-key",
    "X-Reviewer-Id": "dxf.engineer@example.test",
    "X-Reviewer-Role": "ENGINEER",
}


def _project(client: TestClient) -> str:
    return client.post("/api/v1/projects", json={"name": "DXF Intelligence"}).json()["id"]


def _upload(client: TestClient, project_id: str, path: Path) -> str:
    with path.open("rb") as source:
        response = client.post(
            f"/api/v1/projects/{project_id}/documents",
            files={"file": (path.name, source, "image/vnd.dxf")},
        )
    assert response.status_code == 201, response.text
    return response.json()["latest_version"]["id"]


def _mapping(client: TestClient, project_id: str, layer: str, element_type: str) -> None:
    response = client.post(
        f"/api/v1/projects/{project_id}/dxf/layer-mappings",
        headers=HEADERS,
        json={"raw_layer_name": layer, "normalized_element_type": element_type, "scope": "PROJECT"},
    )
    assert response.status_code == 201, response.text


def _analyze(client: TestClient, project_id: str, version_id: str):
    return client.post(f"/api/v1/projects/{project_id}/documents/{version_id}/analyze-dxf")


def test_mm_geometry_mapping_evidence_and_idempotency(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    doc = new("R2010")
    doc.units = units.MM
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "MIM_DUVAR"})
    msp.add_lwpolyline(
        [(0, 0), (10000, 0), (10000, 5000), (0, 5000)],
        close=True,
        dxfattribs={"layer": "MIM_DUVAR"},
    )
    path = tmp_path / "mapped-mm.dxf"
    doc.saveas(path)
    project_id = _project(client)
    version_id = _upload(client, project_id, path)
    _mapping(client, project_id, "MIM_DUVAR", "WALL")

    first = _analyze(client, project_id, version_id)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["unit_status"] == "VERIFIED"
    assert body["raw_unit"] == "millimeter" and body["normalized_unit"] == "meter"
    assert body["recognized_count"] == 2
    with app.state.session_factory() as session:
        elements_before = session.query(BuildingElementORM).filter_by(project_id=project_id).count()
        measurements = session.query(MeasurementORM).filter_by(project_id=project_id).all()
        evidence = session.query(MeasurementEvidenceORM).all()
        assert any(
            row.measurement_type == "LENGTH" and float(row.calculated_value) == 1
            for row in measurements
        )
        assert any(
            row.measurement_type == "AREA" and float(row.calculated_value) == 50
            for row in measurements
        )
        assert len(evidence) == len(measurements)
        assert all(row.source_coordinates["entity_handle"] for row in evidence)
    second = _analyze(client, project_id, version_id)
    assert second.status_code == 201 and second.json()["reused"] is True
    with app.state.session_factory() as session:
        assert (
            session.query(BuildingElementORM).filter_by(project_id=project_id).count()
            == elements_before
        )


def test_unknown_unit_blocks_physical_measurements(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    doc = new("R2010")
    doc.units = 0
    doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "A-WALL"})
    path = tmp_path / "unknown-unit.dxf"
    doc.saveas(path)
    project_id = _project(client)
    version_id = _upload(client, project_id, path)
    response = _analyze(client, project_id, version_id)
    assert response.json()["unit_status"] == "UNKNOWN"
    with app.state.session_factory() as session:
        assert session.query(MeasurementORM).filter_by(project_id=project_id).count() == 0
        element = session.query(BuildingElementORM).filter_by(project_id=project_id).one()
        assert element.element_type == "WALL" and element.review_status == "REVIEW_REQUIRED"


def test_verified_block_mapping_creates_door_count(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    doc = new("R2010")
    doc.units = units.M
    block = doc.blocks.new("KAPI90")
    block.add_line((0, 0), (0.9, 0))
    for x in range(12):
        doc.modelspace().add_blockref("KAPI90", (x, 0))
    path = tmp_path / "doors.dxf"
    doc.saveas(path)
    project_id = _project(client)
    version_id = _upload(client, project_id, path)
    response = client.post(
        f"/api/v1/projects/{project_id}/dxf/block-mappings",
        headers=HEADERS,
        json={"block_name": "KAPI90", "normalized_element_type": "DOOR", "scope": "PROJECT"},
    )
    assert response.status_code == 201
    assert _analyze(client, project_id, version_id).json()["recognized_count"] == 12
    with app.state.session_factory() as session:
        rows = (
            session.query(MeasurementORM)
            .filter_by(project_id=project_id, measurement_type="COUNT")
            .all()
        )
        assert len(rows) == 12 and sum(float(row.calculated_value) for row in rows) == 12


def test_unknown_layer_is_not_forced_to_class(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    doc = new("R2010")
    doc.units = units.M
    doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "XYZ123"})
    path = tmp_path / "unknown-layer.dxf"
    doc.saveas(path)
    project_id = _project(client)
    _analyze(client, project_id, _upload(client, project_id, path))
    with app.state.session_factory() as session:
        element = session.query(BuildingElementORM).filter_by(project_id=project_id).one()
        assert element.element_type == "UNKNOWN"
        assert element.review_status == "REVIEW_REQUIRED"


def test_dxf_correction_closes_unknown_and_can_become_training_sample(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    doc = new("R2010")
    doc.units = units.M
    doc.modelspace().add_line((0, 0), (2, 0), dxfattribs={"layer": "XYZ123"})
    path = tmp_path / "review-training.dxf"
    doc.saveas(path)
    project_id = _project(client)
    version_id = _upload(client, project_id, path)
    _analyze(client, project_id, version_id)
    with app.state.session_factory() as session:
        original = session.query(BuildingElementORM).filter_by(project_id=project_id).one()
        original_id = original.id
    correction = client.post(
        f"/api/v1/review/elements/{original_id}",
        headers=HEADERS,
        json={
            "action": "CORRECT",
            "reason": "Verified as structural column",
            "correction": {"element_type": "COLUMN"},
        },
    )
    assert correction.status_code == 201, correction.text
    corrected_id = correction.json()["resulting_subject_id"]
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
        },
    )
    assert manifest.status_code == 200
    assert (
        client.post(
            f"/api/v1/projects/{project_id}/training/anonymize/{version_id}",
            headers=HEADERS,
            json={"metadata": {"owner_name": "Private Owner", "drawing": "S-101"}},
        ).status_code
        == 201
    )
    sample = client.post(
        f"/api/v1/projects/{project_id}/training-samples",
        headers=HEADERS,
        json={
            "subject_type": "BUILDING_ELEMENT",
            "subject_id": corrected_id,
            "task_type": "DXF_ELEMENT_CLASSIFICATION",
        },
    )
    assert sample.status_code == 201, sample.text
    with app.state.session_factory() as session:
        original = session.get(BuildingElementORM, original_id)
        corrected = session.get(BuildingElementORM, corrected_id)
        assert original.element_type == "UNKNOWN" and original.revision == 1
        assert corrected.element_type == "COLUMN" and corrected.revision == 2
        unknown_item = (
            session.query(ActiveLearningItemORM)
            .filter_by(source_reference=f"DXF:{version_id}:{original.source_external_id}")
            .one()
        )
        assert unknown_item.status == "REVIEWED"


def test_self_intersecting_polygon_does_not_emit_area(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    doc = new("R2010")
    doc.units = units.M
    doc.modelspace().add_lwpolyline(
        [(0, 0), (2, 2), (0, 2), (2, 0)], close=True, dxfattribs={"layer": "A-WALL"}
    )
    path = tmp_path / "invalid.dxf"
    doc.saveas(path)
    project_id = _project(client)
    _analyze(client, project_id, _upload(client, project_id, path))
    with app.state.session_factory() as session:
        element = session.query(BuildingElementORM).filter_by(project_id=project_id).one()
        assert element.properties_json["geometry_error"] == "GEOMETRY_INVALID"
        assert session.query(MeasurementORM).filter_by(project_id=project_id).count() == 0


def test_circle_area_and_circumference(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    doc = new("R2010")
    doc.units = units.M
    doc.modelspace().add_circle((0, 0), radius=1, dxfattribs={"layer": "A-WALL"})
    path = tmp_path / "circle.dxf"
    doc.saveas(path)
    project_id = _project(client)
    _analyze(client, project_id, _upload(client, project_id, path))
    with app.state.session_factory() as session:
        values = {
            row.measurement_type: float(row.calculated_value)
            for row in session.query(MeasurementORM).filter_by(project_id=project_id)
        }
        assert math.isclose(values["AREA"], math.pi, rel_tol=1e-6)
        assert math.isclose(values["LENGTH"], 2 * math.pi, rel_tol=1e-6)


def test_expert_unit_override_enables_measurement(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    doc = new("R2010")
    doc.units = 0
    doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "A-WALL"})
    path = tmp_path / "override.dxf"
    doc.saveas(path)
    project_id = _project(client)
    version_id = _upload(client, project_id, path)
    override = client.post(
        f"/api/v1/projects/{project_id}/documents/{version_id}/dxf-unit",
        headers=HEADERS,
        json={"unit": "millimeter", "reason": "Office drawing standard"},
    )
    assert override.status_code == 201
    assert _analyze(client, project_id, version_id).json()["unit_status"] == "VERIFIED"
    with app.state.session_factory() as session:
        value = (
            session.query(MeasurementORM).filter_by(project_id=project_id).one().calculated_value
        )
        assert float(value) == 1.0


def test_invalid_dxf_is_rejected(client: TestClient) -> None:
    project_id = _project(client)
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("broken.dxf", b"0\nSECTION\ninvalid", "image/vnd.dxf")},
    )
    assert response.status_code == 415


@pytest.mark.parametrize(
    ("unit", "raw_length"),
    [(units.MM, 1000), (units.CM, 100), (units.M, 1)],
)
def test_unit_conversion_produces_same_meter_length(
    app: FastAPI,
    client: TestClient,
    tmp_path: Path,
    unit: int,
    raw_length: float,
) -> None:
    doc = new("R2010")
    doc.units = unit
    doc.modelspace().add_line((0, 0), (raw_length, 0), dxfattribs={"layer": "A-WALL"})
    path = tmp_path / f"unit-{unit}.dxf"
    doc.saveas(path)
    project_id = _project(client)
    _analyze(client, project_id, _upload(client, project_id, path))
    with app.state.session_factory() as session:
        value = (
            session.query(MeasurementORM).filter_by(project_id=project_id).one().calculated_value
        )
        assert math.isclose(float(value), 1.0)
