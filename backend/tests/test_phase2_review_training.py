from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.persistence import AuditEventORM, new_id
from app.modules.documents.infrastructure.models import DocumentVersionORM, ProjectDocumentORM
from app.modules.project_graph.infrastructure.models import BuildingElementORM, ReviewDecisionORM
from app.modules.training.infrastructure.models import ActiveLearningItemORM, DatasetMembershipORM

REVIEW_HEADERS = {
    "X-Admin-Key": "test-admin-key",
    "X-Reviewer-Id": "engineer@example.test",
    "X-Reviewer-Role": "ENGINEER",
}


def _seed_element(
    app: FastAPI, client: TestClient, *, confidence: str = "0.42"
) -> tuple[str, str, str]:
    project_id = client.post("/api/v1/projects", json={"name": "Phase 2"}).json()["id"]
    document_id = new_id()
    version_id = new_id()
    element_id = new_id()
    with app.state.session_factory.begin() as session:
        session.add(
            ProjectDocumentORM(id=document_id, project_id=project_id, original_name="plan.ifc")
        )
        session.add(
            DocumentVersionORM(
                id=version_id,
                document_id=document_id,
                project_id=project_id,
                version=1,
                sha256="a" * 64,
                size_bytes=100,
                media_type="application/x-step",
                storage_key=f"tests/{version_id}.ifc",
                page_count=0,
                status="READY",
            )
        )
        session.add(
            BuildingElementORM(
                id=element_id,
                project_id=project_id,
                discipline="ARCHITECTURAL",
                element_type="WALL",
                geometry_type="POLYLINE",
                geometry_json={"points": [[0, 0], [5, 0]]},
                length=Decimal("5"),
                properties_json={},
                source_type="IFC",
                source_document_id=version_id,
                source_external_id=f"wall-{element_id}",
                source_coordinates={"ifc_global_id": element_id},
                extraction_method="TEST_PREDICTION",
                extraction_version="test-v1",
                confidence=Decimal(confidence),
                review_status="PREDICTED",
                revision=1,
            )
        )
    return project_id, version_id, element_id


def _review(client: TestClient, element_id: str, payload: dict[str, Any]):
    return client.post(
        f"/api/v1/review/elements/{element_id}",
        headers=REVIEW_HEADERS,
        json=payload,
    )


def test_review_requires_authorized_role(app: FastAPI, client: TestClient) -> None:
    _, _, element_id = _seed_element(app, client)
    assert (
        client.post(f"/api/v1/review/elements/{element_id}", json={"action": "ACCEPT"}).status_code
        == 401
    )
    forbidden = _review(client, element_id, {"action": "ACCEPT"})
    assert forbidden.status_code == 201


def test_review_accept_and_reject_are_audited(app: FastAPI, client: TestClient) -> None:
    project_id, _, accepted_id = _seed_element(app, client)
    _, _, rejected_id = _seed_element(app, client)
    assert _review(client, accepted_id, {"action": "ACCEPT"}).json()["action"] == "ACCEPT"
    assert (
        _review(client, rejected_id, {"action": "REJECT", "reason": "not a wall"}).json()["action"]
        == "REJECT"
    )
    with app.state.session_factory() as session:
        assert session.get(BuildingElementORM, accepted_id).review_status == "VERIFIED"
        assert session.get(BuildingElementORM, rejected_id).review_status == "REJECTED"
        assert (
            session.query(AuditEventORM).filter(AuditEventORM.aggregate_id == project_id).count()
            >= 1
        )


def test_review_correction_creates_immutable_revision_and_active_learning(
    app: FastAPI, client: TestClient
) -> None:
    _, _, element_id = _seed_element(app, client)
    response = _review(
        client,
        element_id,
        {
            "action": "CORRECT",
            "reason": "wall boundary corrected",
            "correction": {
                "geometry_json": {"points": [[0, 0], [7, 0]]},
                "length": 7,
                "element_type": "WALL",
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["from_revision"] == 1 and body["to_revision"] == 2
    assert body["resulting_subject_id"] != element_id
    with app.state.session_factory() as session:
        original = session.get(BuildingElementORM, element_id)
        corrected = session.get(BuildingElementORM, body["resulting_subject_id"])
        assert original.revision == 1 and original.length == Decimal("5")
        assert corrected.revision == 2 and corrected.length == Decimal("7")
        assert corrected.supersedes_element_id == original.id
        assert session.query(ReviewDecisionORM).filter_by(subject_id=element_id).count() == 1
        item = (
            session.query(ActiveLearningItemORM)
            .filter_by(source_reference=f"BUILDING_ELEMENT:{element_id}")
            .one()
        )
        assert item.reason == "HUMAN_CORRECTION" and item.priority >= 80


def _manifest(client: TestClient, project_id: str, rights_status: str) -> None:
    response = client.put(
        f"/api/v1/projects/{project_id}/training-manifest",
        headers=REVIEW_HEADERS,
        json={
            "rights_status": rights_status,
            "training_allowed": True,
            "architectural_documents": True,
            "structural_documents": True,
            "geotechnical_documents": True,
            "boq_available": True,
            "verified_quantity_available": True,
            "as_built_available": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data_completeness_score"] == 100


def _anonymize(client: TestClient, project_id: str, version_id: str) -> None:
    response = client.post(
        f"/api/v1/projects/{project_id}/training/anonymize/{version_id}",
        headers=REVIEW_HEADERS,
        json={
            "metadata": {
                "customer_name": "Ada Lovelace",
                "email": "ada@example.com",
                "title": "A-101",
            }
        },
    )
    assert response.status_code == 201, response.text
    metadata = response.json()["anonymized_metadata_json"]
    assert metadata["customer_name"] == "[REDACTED]"
    assert metadata["email"] == "[REDACTED]"
    assert metadata["title"] == "A-101"


def test_training_rights_block_reference_only(app: FastAPI, client: TestClient) -> None:
    project_id, version_id, element_id = _seed_element(app, client)
    assert _review(client, element_id, {"action": "ACCEPT"}).status_code == 201
    _manifest(client, project_id, "REFERENCE_ONLY")
    _anonymize(client, project_id, version_id)
    response = client.post(
        f"/api/v1/projects/{project_id}/training-samples",
        headers=REVIEW_HEADERS,
        json={
            "subject_type": "BUILDING_ELEMENT",
            "subject_id": element_id,
            "task_type": "QUANTITY",
        },
    )
    assert response.status_code == 422
    assert "REFERENCE_ONLY" in response.text


def test_verified_sample_project_split_and_dataset_freeze(app: FastAPI, client: TestClient) -> None:
    project_id, version_id, element_id = _seed_element(app, client)
    assert _review(client, element_id, {"action": "ACCEPT"}).status_code == 201
    _manifest(client, project_id, "INTERNAL_PROJECT")
    _anonymize(client, project_id, version_id)
    sample_ids = []
    for task_type in ("ELEMENT_CLASSIFICATION", "QUANTITY"):
        response = client.post(
            f"/api/v1/projects/{project_id}/training-samples",
            headers=REVIEW_HEADERS,
            json={
                "subject_type": "BUILDING_ELEMENT",
                "subject_id": element_id,
                "task_type": task_type,
            },
        )
        assert response.status_code == 201, response.text
        sample_ids.append(response.json()["id"])
    dataset = client.post(
        "/api/v1/datasets",
        headers=REVIEW_HEADERS,
        json={"dataset_name": "project-reader", "version": "2.0.0"},
    ).json()
    for sample_id in sample_ids:
        assert (
            client.post(
                f"/api/v1/datasets/{dataset['id']}/samples",
                headers=REVIEW_HEADERS,
                json={"sample_id": sample_id},
            ).status_code
            == 201
        )
    frozen = client.post(f"/api/v1/datasets/{dataset['id']}/freeze", headers=REVIEW_HEADERS)
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "FROZEN"
    assert frozen.json()["sample_count"] == 2 and frozen.json()["checksum"]
    blocked = client.post(
        f"/api/v1/datasets/{dataset['id']}/samples",
        headers=REVIEW_HEADERS,
        json={"sample_id": sample_ids[0]},
    )
    assert blocked.status_code == 409
    with app.state.session_factory() as session:
        splits = {
            row.dataset_split
            for row in session.query(DatasetMembershipORM).filter_by(
                dataset_version_id=dataset["id"]
            )
        }
        assert len(splits) == 1


def test_active_learning_priority_is_deterministic(client: TestClient) -> None:
    project_id = client.post("/api/v1/projects", json={"name": "AL"}).json()["id"]
    low = client.post(
        "/api/v1/active-learning",
        headers=REVIEW_HEADERS,
        json={
            "project_id": project_id,
            "source_reference": "COLUMN:42",
            "prediction": {"class": "COLUMN"},
            "confidence": 0.42,
            "reason": "LOW_CONFIDENCE",
        },
    )
    assert low.status_code == 201
    correction = client.post(
        "/api/v1/active-learning",
        headers=REVIEW_HEADERS,
        json={
            "project_id": project_id,
            "source_reference": "COLUMN:43",
            "prediction": {"class": "UNKNOWN"},
            "confidence": 0.42,
            "reason": "HUMAN_CORRECTION",
        },
    )
    assert correction.json()["priority"] > low.json()["priority"]
