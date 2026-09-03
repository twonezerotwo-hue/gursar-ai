from __future__ import annotations

from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.persistence import AuditEventORM
from app.modules.project_graph.infrastructure.models import (
    BuildingElementORM,
    ElementRelationshipORM,
    ProjectLevelORM,
)
from app.modules.structural_intelligence.infrastructure.models import (
    StructuralAnalysisRunORM,
    StructuralLoadORM,
)

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _project(client: TestClient, name: str = "Structural QA") -> str:
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def _seed_structure(app: FastAPI, project_id: str) -> dict[str, str]:
    with app.state.session_factory.begin() as session:
        foundation_level = ProjectLevelORM(
            project_id=project_id,
            name="Foundation",
            elevation=Decimal("-1"),
            source_key="level-foundation",
        )
        ground_level = ProjectLevelORM(
            project_id=project_id,
            name="Ground",
            elevation=Decimal("0"),
            source_key="level-ground",
        )
        upper_level = ProjectLevelORM(
            project_id=project_id,
            name="Upper",
            elevation=Decimal("3"),
            source_key="level-upper",
        )
        session.add_all([foundation_level, ground_level, upper_level])
        session.flush()
        definitions = {
            "footing": ("FOOTING", foundation_level.id, {}),
            "column": (
                "COLUMN",
                ground_level.id,
                {"material": "C30", "width": Decimal("0.4"), "height": Decimal("3")},
            ),
            "beam": (
                "BEAM",
                ground_level.id,
                {"material": "C30", "width": Decimal("0.3"), "height": Decimal("0.6")},
            ),
            "slab": (
                "SLAB",
                upper_level.id,
                {"material": "C30", "thickness": Decimal("0.15")},
            ),
        }
        result: dict[str, str] = {}
        for key, (element_type, level_id, values) in definitions.items():
            element = BuildingElementORM(
                project_id=project_id,
                level_id=level_id,
                discipline="STRUCTURAL",
                element_type=element_type,
                name=key,
                geometry_json={},
                properties_json={},
                source_type="MANUAL",
                source_external_id=f"structural-{key}-{project_id}",
                source_coordinates={},
                extraction_method="TEST",
                extraction_version="test-v1",
                confidence=Decimal("1"),
                review_status="VERIFIED",
                revision=1,
                **values,
            )
            session.add(element)
            session.flush()
            result[key] = element.id
        result["foundation_level"] = foundation_level.id
        result["upper_level"] = upper_level.id
        return result


def _payload(ids: dict[str, str]) -> dict:
    return {
        "element_ids": [ids["footing"], ids["column"], ids["beam"], ids["slab"]],
        "supports": [
            {
                "supported_element_id": ids["slab"],
                "supporting_element_id": ids["beam"],
                "connection_type": "MONOLITHIC",
            },
            {
                "supported_element_id": ids["beam"],
                "supporting_element_id": ids["column"],
                "connection_type": "MONOLITHIC",
            },
            {
                "supported_element_id": ids["column"],
                "supporting_element_id": ids["footing"],
                "connection_type": "BEARS_ON",
            },
        ],
        "loads": [
            {
                "key": "slab-dead-01",
                "element_id": ids["slab"],
                "load_type": "DEAD",
                "magnitude_kn": 100,
                "factor": 1.5,
                "source_reference": "Engineer input LC-01",
            }
        ],
    }


def _post(client: TestClient, project_id: str, payload: dict):
    return client.post(
        f"/api/v1/intelligence/projects/{project_id}/structural/load-path",
        headers=ADMIN,
        json=payload,
    )


def test_structural_load_path_requires_admin(app: FastAPI, client: TestClient) -> None:
    project_id = _project(client)
    ids = _seed_structure(app, project_id)
    response = client.post(
        f"/api/v1/intelligence/projects/{project_id}/structural/load-path",
        json=_payload(ids),
    )
    assert response.status_code == 401


def test_complete_load_path_is_evidence_linked_and_idempotent(
    app: FastAPI, client: TestClient
) -> None:
    project_id = _project(client)
    ids = _seed_structure(app, project_id)
    first = _post(client, project_id, _payload(ids))
    second = _post(client, project_id, _payload(ids))

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["reused"] is False
    assert second.json()["reused"] is True
    assert first.json()["issues"] == []
    metrics = {item["code"]: item["value"] for item in first.json()["metrics"]}
    assert float(metrics["DECLARED_FACTORED_LOAD"]) == 150.0
    assert metrics["VERIFIED_ELEMENT_RATIO"] == "1.0000"
    assert len(first.json()["relationship_ids"]) == 3
    assert len(first.json()["load_ids"]) == 1

    with app.state.session_factory() as session:
        assert session.query(StructuralAnalysisRunORM).filter_by(project_id=project_id).count() == 1
        assert session.query(StructuralLoadORM).filter_by(project_id=project_id).count() == 1
        assert (
            session.query(ElementRelationshipORM)
            .filter_by(project_id=project_id, relationship_type="STRUCTURAL_SUPPORTS")
            .count()
            == 3
        )
        assert (
            session.query(AuditEventORM).filter_by(action="STRUCTURAL_LOAD_PATH_ANALYZED").count()
            == 1
        )


def test_unsupported_and_incomplete_elements_are_reported(app: FastAPI, client: TestClient) -> None:
    project_id = _project(client)
    ids = _seed_structure(app, project_id)
    payload = _payload(ids)
    payload["supports"] = payload["supports"][1:]
    with app.state.session_factory.begin() as session:
        slab = session.get(BuildingElementORM, ids["slab"])
        assert slab is not None
        slab.thickness = None
        slab.review_status = "PREDICTED"
    response = _post(client, project_id, payload)
    assert response.status_code == 201
    issues = {item["code"]: item for item in response.json()["issues"]}
    assert {
        "UNSUPPORTED_STRUCTURAL_ELEMENT",
        "STRUCTURAL_PROPERTIES_INCOMPLETE",
        "STRUCTURAL_ELEMENT_REVIEW_REQUIRED",
    } <= issues.keys()
    assert all(item["evidence_element_ids"] for item in issues.values())


def test_cycle_and_incompatible_support_are_not_silently_accepted(
    app: FastAPI, client: TestClient
) -> None:
    project_id = _project(client)
    ids = _seed_structure(app, project_id)
    payload = _payload(ids)
    payload["supports"] = [
        {
            "supported_element_id": ids["beam"],
            "supporting_element_id": ids["column"],
            "connection_type": "MONOLITHIC",
        },
        {
            "supported_element_id": ids["column"],
            "supporting_element_id": ids["beam"],
            "connection_type": "TRANSFER",
        },
    ]
    response = _post(client, project_id, payload)
    assert response.status_code == 201
    issue_codes = {item["code"] for item in response.json()["issues"]}
    assert "LOAD_PATH_CYCLE" in issue_codes
    assert "INCOMPATIBLE_SUPPORT_TYPE" in issue_codes
    assert "LOAD_PATH_NOT_TO_FOUNDATION" in issue_codes


def test_support_on_higher_level_is_reported(app: FastAPI, client: TestClient) -> None:
    project_id = _project(client)
    ids = _seed_structure(app, project_id)
    payload = _payload(ids)
    payload["supports"] = [
        {
            "supported_element_id": ids["column"],
            "supporting_element_id": ids["slab"],
            "connection_type": "TRANSFER",
        }
    ]
    response = _post(client, project_id, payload)
    issues = {item["code"]: item for item in response.json()["issues"]}
    assert "SUPPORT_ABOVE_SUPPORTED_ELEMENT" in issues
    assert issues["SUPPORT_ABOVE_SUPPORTED_ELEMENT"]["evidence_relationship_ids"]


def test_changed_support_requires_revision_instead_of_overwrite(
    app: FastAPI, client: TestClient
) -> None:
    project_id = _project(client)
    ids = _seed_structure(app, project_id)
    assert _post(client, project_id, _payload(ids)).status_code == 201
    changed = _payload(ids)
    changed["supports"][0]["connection_type"] = "ANCHORED"
    response = _post(client, project_id, changed)
    assert response.status_code == 409
    assert response.json()["detail"] == "STRUCTURAL_GRAPH_REVISION_REQUIRED"


def test_cross_project_elements_are_rejected(app: FastAPI, client: TestClient) -> None:
    first_project = _project(client, "First")
    second_project = _project(client, "Second")
    ids = _seed_structure(app, first_project)
    response = _post(client, second_project, _payload(ids))
    assert response.status_code == 422
    assert response.json()["detail"] == "ELEMENT_NOT_IN_PROJECT"


def test_latest_load_path_is_available(app: FastAPI, client: TestClient) -> None:
    project_id = _project(client)
    ids = _seed_structure(app, project_id)
    created = _post(client, project_id, _payload(ids))
    response = client.get(
        f"/api/v1/intelligence/projects/{project_id}/structural/load-path",
        headers=ADMIN,
    )
    assert response.status_code == 200
    assert response.json()["analysis_id"] == created.json()["analysis_id"]
    assert response.json()["reused"] is True
