from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.persistence import AuditEventORM
from app.modules.planning.application.productivity import (
    CrewPlanningService,
    ProductivityPlanningError,
)
from app.modules.planning.domain.models import (
    PlanningPriority,
    ProductivityRecord,
    ProductivitySourceTier,
)
from app.modules.planning.infrastructure.models import ProductivityRecordORM
from app.modules.planning.infrastructure.repository import ProductivityRepository

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _record(**overrides) -> ProductivityRecord:
    values = {
        "service_code": "formwork",
        "unit": "m2",
        "output_per_crew_day": Decimal("50"),
        "crew_size": 5,
        "project_type": "RESIDENTIAL",
        "conditions": "normal residential site",
        "verified": True,
        "source": "GURSAR verified field log",
        "source_tier": ProductivitySourceTier.VERIFIED_PROJECT_ACTUAL,
        "work_type": "Formwork installation",
        "crew_composition": (("carpenter", 4), ("foreman", 1)),
        "sample_quantity": Decimal("500"),
        "sample_duration_days": Decimal("10"),
        "productive_hours": Decimal("8"),
        "location": "TR/ISTANBUL",
        "floor": "TYPICAL",
        "confidence": Decimal("0.95"),
    }
    values.update(overrides)
    return ProductivityRecord(**values)


def test_crew_planner_calculates_crew_days_workers_and_composition() -> None:
    plan = CrewPlanningService().plan(
        _record(),
        quantity=Decimal("500"),
        unit="m2",
        target_duration_days=Decimal("5"),
    )
    assert plan.crew_days == Decimal("10.00")
    assert plan.required_crews == 2
    assert plan.required_workers == 10
    assert plan.planned_duration_days == Decimal("5.00")
    assert plan.daily_output == Decimal("100")
    assert dict(plan.total_composition) == {"carpenter": 8, "foreman": 2}


def test_crew_planner_reports_worker_cap_and_unit_errors() -> None:
    planner = CrewPlanningService()
    capped = planner.plan(
        _record(),
        quantity=Decimal("1000"),
        unit="m2",
        target_duration_days=Decimal("5"),
        max_workers=10,
    )
    assert capped.required_crews == 2
    assert capped.planned_duration_days == Decimal("10.00")
    assert len(capped.risks) == 2

    with pytest.raises(ProductivityPlanningError, match="PRODUCTIVITY_UNIT_MISMATCH"):
        planner.plan(
            _record(),
            quantity=Decimal("10"),
            unit="m3",
            target_duration_days=Decimal("1"),
        )


def test_fast_balanced_economy_scenarios_are_deterministic() -> None:
    scenarios = CrewPlanningService().scenarios(
        _record(source_tier=ProductivitySourceTier.HEURISTIC, verified=False),
        quantity=Decimal("500"),
        unit="m2",
        balanced_duration_days=Decimal("10"),
    )
    assert [item.priority for item in scenarios] == [
        PlanningPriority.FAST,
        PlanningPriority.BALANCED,
        PlanningPriority.ECONOMIC,
    ]
    assert [item.target_duration_days for item in scenarios] == [
        Decimal("8"),
        Decimal("10"),
        Decimal("13"),
    ]
    assert scenarios[0].required_workers >= scenarios[1].required_workers
    assert "HEURISTIC" in scenarios[0].assumptions[-1]


def test_productivity_versions_are_immutable_and_source_ranked(
    app: FastAPI,
    client: TestClient,
) -> None:
    with app.state.session_factory() as session:
        repository = ProductivityRepository(session)
        first = repository.add_version(
            _record(
                source_tier=ProductivitySourceTier.COMPANY_STANDARD,
                source="Company standard V1",
                output_per_crew_day=Decimal("40"),
            )
        )
        first_id = first.id
        second = repository.add_version(_record(source="Verified project actual V2"))
        session.commit()

        historical = session.get(ProductivityRecordORM, first_id)
        assert historical is not None
        assert historical.output_per_crew_day == Decimal("40")
        assert historical.active is False
        assert second.version > first.version
        selected = repository.active_catalog("RESIDENTIAL", "TR/ISTANBUL")["formwork"]
        assert selected.source_tier == ProductivitySourceTier.VERIFIED_PROJECT_ACTUAL
        assert selected.output_per_crew_day == Decimal("50")


def test_repository_rejects_false_provenance_and_bad_composition(
    app: FastAPI,
    client: TestClient,
) -> None:
    with app.state.session_factory() as session:
        repository = ProductivityRepository(session)
        with pytest.raises(ValueError, match="cannot use HEURISTIC"):
            repository.add_version(
                _record(source_tier=ProductivitySourceTier.HEURISTIC, verified=True)
            )
        with pytest.raises(ValueError, match="composition"):
            repository.add_version(_record(crew_composition=(("worker", 2),)))


def test_productivity_admin_api_versions_audits_and_lists_provenance(
    app: FastAPI,
    client: TestClient,
) -> None:
    body = {
        "service_code": "paint.internal",
        "work_type": "Interior paint",
        "unit": "m2",
        "output_per_crew_day": 180,
        "crew_size": 3,
        "crew_composition": {"painter": 3},
        "sample_quantity": 1800,
        "sample_duration_days": 10,
        "productive_hours": 8,
        "project_type": "RESIDENTIAL",
        "location": "TR/ISTANBUL",
        "conditions": "occupied floors excluded",
        "verified": True,
        "confidence": 0.9,
        "source_tier": "COMPANY_STANDARD",
        "source": "GURSAR 2026 company standard",
    }
    unauthorized = client.post("/api/v1/intelligence/productivity-records", json=body)
    assert unauthorized.status_code == 401
    created = client.post(
        "/api/v1/intelligence/productivity-records", headers=ADMIN, json=body
    )
    assert created.status_code == 201
    assert created.json()["source_tier"] == "COMPANY_STANDARD"
    listed = client.get(
        "/api/v1/intelligence/productivity-records",
        headers=ADMIN,
        params={"location": "TR/ISTANBUL"},
    )
    item = next(
        row for row in listed.json()["records"] if row["service_code"] == "paint.internal"
    )
    assert item["crew_composition"] == {"painter": 3}
    assert item["verified"] is True

    with app.state.session_factory() as session:
        audit = (
            session.query(AuditEventORM)
            .filter_by(action="PRODUCTIVITY_RECORD_VERSION_CREATED")
            .one()
        )
        assert audit.detail["source_tier"] == "COMPANY_STANDARD"


def test_crew_plan_api_returns_provenance_and_three_scenarios(client: TestClient) -> None:
    response = client.post(
        "/api/v1/intelligence/productivity/crew-plan",
        headers=ADMIN,
        json={
            "service_code": "masonry",
            "quantity": 1100,
            "unit": "m2",
            "balanced_duration_days": 10,
            "max_workers": 16,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["productivity"]["source_tier"] == "HEURISTIC"
    assert [item["priority"] for item in payload["scenarios"]] == [
        "FAST",
        "BALANCED",
        "ECONOMIC",
    ]
    assert all(item["required_workers"] <= 16 for item in payload["scenarios"])
    assert all(item["source_tier"] == "HEURISTIC" for item in payload["scenarios"])


def test_schedule_discloses_heuristic_productivity(client: TestClient) -> None:
    response = client.post(
        "/api/v1/intelligence/schedule",
        headers=ADMIN,
        json={"gross_area_m2": 6_000, "floor_count": 6, "priority": "BALANCED"},
    )
    assert response.status_code == 200
    assert any("HEURISTIC" in item for item in response.json()["assumptions"])
