from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.persistence import AuditEventORM
from app.modules.architecture_intelligence.infrastructure.models import (
    ArchitecturalZoneMembershipORM,
    ArchitecturalZoneORM,
    ArchitectureAnalysisRunORM,
)
from app.modules.project_graph.infrastructure.models import (
    BuildingElementORM,
    ElementRelationshipORM,
)

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _project(client: TestClient) -> str:
    response = client.post("/api/v1/projects", json={"name": "Spatial QA"})
    assert response.status_code == 201
    return response.json()["id"]


def _space(
    key: str,
    boundary: list[list[float]],
    *,
    kind: str = "ROOM",
    privacy: str = "SEMI_PRIVATE",
    **flags: bool,
) -> dict:
    return {
        "key": key,
        "name": key.title(),
        "function": key,
        "kind": kind,
        "privacy": privacy,
        "boundary_m": boundary,
        **flags,
    }


def _payload() -> dict:
    return {
        "gross_area_m2": 100,
        "spaces": [
            _space("entrance", [[0, 0], [2, 0], [2, 2], [0, 2]], kind="ENTRANCE"),
            _space(
                "corridor",
                [[2, 0], [4, 0], [4, 2], [2, 2]],
                kind="CIRCULATION",
            ),
            _space(
                "bedroom",
                [[4, 0], [9, 0], [9, 4], [4, 4]],
                privacy="PRIVATE",
                daylight_required=True,
                has_exterior_opening=True,
            ),
        ],
        "connections": [
            {
                "source_key": "entrance",
                "target_key": "corridor",
                "connection_type": "OPENING",
            },
            {
                "source_key": "corridor",
                "target_key": "bedroom",
                "connection_type": "DOOR",
            },
        ],
        "zones": [
            {
                "key": "private-zone",
                "name": "Private Zone",
                "zone_type": "PRIVATE",
                "member_space_keys": ["bedroom"],
            }
        ],
    }


def _post(client: TestClient, project_id: str, payload: dict):
    return client.post(
        f"/api/v1/intelligence/projects/{project_id}/architecture/spatial-graph",
        headers=ADMIN,
        json=payload,
    )


def test_spatial_graph_requires_admin_permission(client: TestClient) -> None:
    project_id = _project(client)
    response = client.post(
        f"/api/v1/intelligence/projects/{project_id}/architecture/spatial-graph",
        json=_payload(),
    )
    assert response.status_code == 401


def test_spatial_graph_calculates_area_and_is_idempotent(app: FastAPI, client: TestClient) -> None:
    project_id = _project(client)
    first = _post(client, project_id, _payload())
    second = _post(client, project_id, _payload())

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["reused"] is False
    assert second.json()["reused"] is True
    metrics = {item["code"]: item["value"] for item in first.json()["metrics"]}
    assert float(metrics["NET_PROGRAM_AREA"]) == 28.0
    assert {item["key"] for item in first.json()["spaces"]} == {
        "entrance",
        "corridor",
        "bedroom",
    }
    assert not any(item["code"] == "UNREACHABLE_SPACE" for item in first.json()["issues"])

    with app.state.session_factory() as session:
        assert session.query(BuildingElementORM).filter_by(project_id=project_id).count() == 3
        assert session.query(ElementRelationshipORM).filter_by(project_id=project_id).count() == 2
        assert (
            session.query(ArchitectureAnalysisRunORM).filter_by(project_id=project_id).count() == 1
        )
        assert (
            session.query(AuditEventORM)
            .filter_by(action="ARCHITECTURE_SPATIAL_GRAPH_ANALYZED")
            .count()
            == 1
        )


def test_spatial_graph_persists_zone_membership(app: FastAPI, client: TestClient) -> None:
    project_id = _project(client)
    response = _post(client, project_id, _payload())
    assert response.status_code == 201

    with app.state.session_factory() as session:
        zone = session.query(ArchitecturalZoneORM).filter_by(project_id=project_id).one()
        membership = session.query(ArchitecturalZoneMembershipORM).filter_by(zone_id=zone.id).one()
        bedroom = session.get(BuildingElementORM, membership.element_id)
        assert bedroom is not None
        assert bedroom.properties_json["space_key"] == "bedroom"


def test_spatial_qa_reports_evidence_linked_issues(client: TestClient) -> None:
    project_id = _project(client)
    payload = {
        "gross_area_m2": 80,
        "spaces": [
            _space("entry", [[0, 0], [2, 0], [2, 2], [0, 2]], kind="ENTRANCE"),
            _space(
                "salon",
                [[2, 0], [8, 0], [8, 5], [2, 5]],
                privacy="PUBLIC",
            ),
            _space(
                "bedroom",
                [[8, 0], [12, 0], [12, 4], [8, 4]],
                privacy="PRIVATE",
                daylight_required=True,
            ),
            _space(
                "bathroom",
                [[0, 6], [2, 6], [2, 8], [0, 8]],
                wet_area=True,
                accessible_required=True,
                accessible=False,
            ),
        ],
        "connections": [
            {
                "source_key": "entry",
                "target_key": "salon",
                "connection_type": "OPENING",
            },
            {
                "source_key": "salon",
                "target_key": "bedroom",
                "connection_type": "DOOR",
            },
        ],
    }
    response = _post(client, project_id, payload)
    assert response.status_code == 201
    issues = {item["code"]: item for item in response.json()["issues"]}
    assert {
        "UNREACHABLE_SPACE",
        "PRIVACY_CONFLICT",
        "DAYLIGHT_NOT_DEMONSTRATED",
        "ACCESSIBILITY_CONFLICT",
        "WET_SPACE_CORE_DISCONNECTED",
    } <= issues.keys()
    assert all(item["evidence_element_ids"] for item in issues.values())
    assert issues["PRIVACY_CONFLICT"]["evidence_relationship_ids"]


def test_invalid_space_geometry_does_not_persist_partial_graph(
    app: FastAPI, client: TestClient
) -> None:
    project_id = _project(client)
    payload = {
        "gross_area_m2": 20,
        "spaces": [_space("bowtie", [[0, 0], [2, 2], [0, 2], [2, 0]])],
    }
    response = _post(client, project_id, payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "INVALID_SPACE_BOUNDARY"
    with app.state.session_factory() as session:
        assert session.query(BuildingElementORM).filter_by(project_id=project_id).count() == 0


def test_changed_existing_graph_requires_immutable_revision(client: TestClient) -> None:
    project_id = _project(client)
    assert _post(client, project_id, _payload()).status_code == 201
    changed = _payload()
    changed["spaces"][2]["boundary_m"] = [[4, 0], [10, 0], [10, 4], [4, 4]]
    response = _post(client, project_id, changed)
    assert response.status_code == 409
    assert response.json()["detail"] == "SPATIAL_GRAPH_REVISION_REQUIRED"


def test_latest_spatial_graph_can_be_retrieved(client: TestClient) -> None:
    project_id = _project(client)
    created = _post(client, project_id, _payload())
    response = client.get(
        f"/api/v1/intelligence/projects/{project_id}/architecture/spatial-graph",
        headers=ADMIN,
    )
    assert response.status_code == 200
    assert response.json()["analysis_id"] == created.json()["analysis_id"]
    assert response.json()["reused"] is True
