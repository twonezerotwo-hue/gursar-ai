from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.persistence import AuditEventORM
from app.modules.planning.application.resource import (
    ResourceLevelingEngine,
    ResourcePlanningError,
)
from app.modules.planning.domain.models import Activity, ActivityDependency
from app.modules.planning.domain.resource import ResourceCapacity, ResourceKind
from app.modules.planning.infrastructure.models import (
    ScheduleActivityElementLinkORM,
    ScheduleDelayEventORM,
    ScheduleProgressUpdateORM,
)
from app.modules.project_graph.infrastructure.models import BuildingElementORM

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _project(client: TestClient) -> str:
    response = client.post("/api/v1/projects", json={"name": "Schedule Control Test"})
    assert response.status_code == 201
    return response.json()["id"]


def _capacity(code: str, amount: int, kind: ResourceKind = ResourceKind.WORKFORCE):
    return ResourceCapacity(code=code, kind=kind, capacity=amount)


def test_resource_loading_detects_and_levels_workforce_overload() -> None:
    activities = (
        Activity("A", "A", "1", Decimal("2"), "shared", 4),
        Activity("B", "B", "2", Decimal("2"), "shared", 4),
    )
    result = ResourceLevelingEngine().level(activities, (_capacity("shared", 4),))
    assert len(result.initial_overloads) == 1
    assert result.initial_overloads[0].demand == 8
    assert result.remaining_overloads == ()
    assert result.shifted_activity_ids == ("B",)
    assert result.activities[1].planned_start == Decimal("2")
    assert result.duration_days == Decimal("4")


def test_equipment_conflict_and_dependencies_survive_leveling() -> None:
    activities = (
        Activity(
            "A",
            "A",
            "1",
            Decimal("2"),
            "crew-a",
            2,
            equipment_requirements=(("tower-crane", 1),),
        ),
        Activity(
            "B",
            "B",
            "2",
            Decimal("2"),
            "crew-b",
            2,
            equipment_requirements=(("tower-crane", 1),),
        ),
        Activity(
            "C",
            "C",
            "3",
            Decimal("1"),
            "crew-c",
            1,
            dependencies=(ActivityDependency("B"),),
        ),
    )
    result = ResourceLevelingEngine().level(
        activities,
        (_capacity("tower-crane", 1, ResourceKind.EQUIPMENT),),
    )
    by_id = {item.activity.id: item for item in result.activities}
    assert result.initial_overloads[0].kind == ResourceKind.EQUIPMENT
    assert by_id["B"].planned_start == Decimal("2")
    assert by_id["C"].planned_start >= by_id["B"].planned_finish
    assert result.remaining_overloads == ()


def test_resource_leveling_rejects_an_individually_impossible_activity() -> None:
    with pytest.raises(ResourcePlanningError, match="RESOURCE_CAPACITY_INFEASIBLE"):
        ResourceLevelingEngine().level(
            (Activity("A", "A", "1", Decimal("2"), "formwork", 8),),
            (_capacity("formwork", 6),),
        )


def _seed_elements(app: FastAPI, project_id: str) -> None:
    with app.state.session_factory.begin() as session:
        for index, status in enumerate(("VERIFIED", "REVIEW_REQUIRED"), start=1):
            session.add(
                BuildingElementORM(
                    project_id=project_id,
                    discipline="ARCHITECTURAL",
                    element_type="WALL",
                    name=f"Wall {index}",
                    geometry_json={},
                    properties_json={},
                    source_type="MANUAL",
                    source_external_id=f"schedule-wall-{project_id}-{index}",
                    source_coordinates={},
                    extraction_method="TEST",
                    extraction_version="test-v1",
                    confidence=Decimal("1"),
                    review_status=status,
                    revision=1,
                )
            )


def _baseline(client: TestClient, project_id: str) -> dict:
    response = client.post(
        f"/api/v1/intelligence/projects/{project_id}/schedule/baselines",
        headers=ADMIN,
        json={
            "name": "Contract Baseline",
            "gross_area_m2": 6000,
            "floor_count": 6,
            "priority": "BALANCED",
            "resource_capacities": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_baseline_is_idempotent_and_links_only_verified_elements(
    app: FastAPI,
    client: TestClient,
) -> None:
    project_id = _project(client)
    _seed_elements(app, project_id)
    first = _baseline(client, project_id)
    second = _baseline(client, project_id)
    assert first["reused"] is False
    assert second["reused"] is True
    assert second["id"] == first["id"]
    assert first["building_element_link_count"] == 1
    assert first["status"] == "FROZEN"

    with app.state.session_factory() as session:
        links = session.query(ScheduleActivityElementLinkORM).all()
        assert len(links) == 1
        assert links[0].activity_id == "A200"
        audits = session.query(AuditEventORM).filter_by(action="SCHEDULE_BASELINE_CREATED").all()
        assert len(audits) == 1


def test_progress_is_append_only_and_reports_baseline_variance(
    app: FastAPI,
    client: TestClient,
) -> None:
    project_id = _project(client)
    baseline = _baseline(client, project_id)
    endpoint = (
        f"/api/v1/intelligence/projects/{project_id}/schedule/baselines/"
        f"{baseline['id']}/progress"
    )
    body = {
        "activity_id": "A100",
        "observed_day": 21,
        "actual_progress": 0.25,
        "actual_start_day": 0,
        "forecast_finish_day": 50,
        "source": "Weekly site report 01",
        "verified": True,
    }
    first = client.post(endpoint, headers=ADMIN, json=body)
    second = client.post(endpoint, headers=ADMIN, json={**body, "actual_progress": 0.3})
    assert first.status_code == 201
    assert first.json()["planned_progress"] == "0.500000"
    assert first.json()["progress_variance"] == "-0.250000"
    assert Decimal(first.json()["finish_variance_days"]) == Decimal("8")
    assert second.status_code == 201

    with app.state.session_factory() as session:
        rows = session.query(ScheduleProgressUpdateORM).order_by(
            ScheduleProgressUpdateORM.created_at
        ).all()
        assert len(rows) == 2
        assert rows[0].actual_progress == Decimal("0.250000")
        assert rows[1].actual_progress == Decimal("0.300000")


def test_verified_delay_has_deterministic_critical_path_impact(
    app: FastAPI,
    client: TestClient,
) -> None:
    project_id = _project(client)
    baseline = _baseline(client, project_id)
    response = client.post(
        (
            f"/api/v1/intelligence/projects/{project_id}/schedule/baselines/"
            f"{baseline['id']}/delays"
        ),
        headers=ADMIN,
        json={
            "activity_id": "A100",
            "root_cause": "MATERIAL",
            "impact_days": 3,
            "reason": "Approved material arrived late",
            "verified": True,
            "created_by": "site-engineer",
        },
    )
    assert response.status_code == 201
    assert Decimal(response.json()["project_impact_days"]) == Decimal("3")
    with app.state.session_factory() as session:
        row = session.query(ScheduleDelayEventORM).one()
        assert row.root_cause == "MATERIAL"
        assert row.project_impact_days == Decimal("3.000000")


def test_schedule_control_endpoints_require_admin(client: TestClient) -> None:
    project_id = _project(client)
    response = client.post(
        f"/api/v1/intelligence/projects/{project_id}/schedule/baselines",
        json={"gross_area_m2": 1000, "floor_count": 2},
    )
    assert response.status_code == 401
