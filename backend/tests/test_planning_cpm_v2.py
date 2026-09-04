from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.conversation_intelligence.infrastructure.models import ConversationPlanORM
from app.modules.planning.application.service import CpmEngine, InvalidScheduleError
from app.modules.planning.domain.models import (
    Activity,
    ActivityDependency,
    DependencyType,
    WbsNode,
    WorkBreakdownStructure,
)

ADMIN = {"X-Admin-Key": "test-admin-key"}


def _scheduled(result, activity_id: str):
    return next(item for item in result.activities if item.activity.id == activity_id)


def test_wbs_hierarchy_validates_activity_membership() -> None:
    wbs = WorkBreakdownStructure(
        (
            WbsNode("1", "Project"),
            WbsNode("1.1", "Structure", "1"),
            WbsNode("1.1.1", "Columns", "1.1"),
        )
    )
    result = CpmEngine().calculate(
        (Activity("A", "Columns", "1.1.1", Decimal("2"), "structure", 6),),
        wbs,
    )
    assert result.duration_days == Decimal("2")

    with pytest.raises(InvalidScheduleError, match="unknown WBS"):
        CpmEngine().calculate(
            (Activity("B", "Beam", "9.9", Decimal("1"), "structure", 4),),
            wbs,
        )


def test_wbs_rejects_missing_parent_and_cycle() -> None:
    activity = (Activity("A", "A", "1", Decimal("1"), "crew", 1),)
    with pytest.raises(InvalidScheduleError, match="parent does not exist"):
        CpmEngine().calculate(
            activity,
            WorkBreakdownStructure((WbsNode("1", "Orphan", "0"),)),
        )
    with pytest.raises(InvalidScheduleError, match="contains a cycle"):
        CpmEngine().calculate(
            activity,
            WorkBreakdownStructure((WbsNode("1", "One", "2"), WbsNode("2", "Two", "1"))),
        )


@pytest.mark.parametrize(
    ("relationship", "lag", "expected_start"),
    (
        (DependencyType.FINISH_START, "2", "12"),
        (DependencyType.START_START, "2", "2"),
        (DependencyType.FINISH_FINISH, "2", "8"),
        (DependencyType.START_FINISH, "6", "2"),
    ),
)
def test_cpm_supports_all_dependency_types_and_lag(
    relationship: DependencyType,
    lag: str,
    expected_start: str,
) -> None:
    result = CpmEngine().calculate(
        (
            Activity("A", "A", "1", Decimal("10"), "crew-a", 2),
            Activity(
                "B",
                "B",
                "2",
                Decimal("4"),
                "crew-b",
                3,
                (ActivityDependency("A", relationship, Decimal(lag)),),
            ),
        )
    )
    assert _scheduled(result, "B").early_start == Decimal(expected_start)
    assert all(item.total_float >= 0 for item in result.activities)


def test_backward_pass_respects_project_finish_for_start_relationships() -> None:
    result = CpmEngine().calculate(
        (
            Activity("A", "Long", "1", Decimal("10"), "crew-a", 2),
            Activity(
                "B",
                "Short",
                "2",
                Decimal("4"),
                "crew-b",
                2,
                (ActivityDependency("A", DependencyType.START_START, Decimal("2")),),
            ),
        )
    )
    assert _scheduled(result, "A").late_start == Decimal("0")
    assert _scheduled(result, "A").late_finish == result.duration_days


def test_total_and_free_float_are_reported_separately() -> None:
    result = CpmEngine().calculate(
        (
            Activity("A", "A", "1", Decimal("2"), "crew", 1),
            Activity(
                "B",
                "B",
                "2",
                Decimal("2"),
                "crew",
                1,
                (ActivityDependency("A"),),
            ),
            Activity("C", "Long independent", "3", Decimal("10"), "crew", 1),
        )
    )
    first = _scheduled(result, "A")
    second = _scheduled(result, "B")
    assert first.total_float == Decimal("6")
    assert first.free_float == Decimal("0")
    assert second.total_float == Decimal("6")
    assert second.free_float == Decimal("6")


def test_multiple_critical_paths_are_explicit() -> None:
    result = CpmEngine().calculate(
        (
            Activity("A", "A", "1", Decimal("2"), "crew-a", 2),
            Activity("B", "B", "2", Decimal("2"), "crew-b", 3),
            Activity(
                "C",
                "C",
                "3",
                Decimal("1"),
                "crew-c",
                1,
                (ActivityDependency("A"), ActivityDependency("B")),
            ),
        )
    )
    assert set(result.critical_paths) == {("A", "C"), ("B", "C")}
    assert result.critical_path in result.critical_paths


def test_peak_crew_uses_exact_decimal_time_boundaries() -> None:
    result = CpmEngine().calculate(
        (
            Activity("A", "A", "1", Decimal("0.5"), "crew-a", 5),
            Activity(
                "B",
                "B",
                "2",
                Decimal("0.5"),
                "crew-b",
                7,
                (ActivityDependency("A"),),
            ),
        )
    )
    assert result.peak_crew == 7


def test_invalid_activity_and_duplicate_dependency_are_rejected() -> None:
    with pytest.raises(InvalidScheduleError, match="duration"):
        CpmEngine().calculate((Activity("A", "A", "1", Decimal("-1"), "crew", 1),))
    with pytest.raises(InvalidScheduleError, match="Duplicate"):
        CpmEngine().calculate(
            (
                Activity("A", "A", "1", Decimal("1"), "crew", 1),
                Activity(
                    "B",
                    "B",
                    "2",
                    Decimal("1"),
                    "crew",
                    1,
                    (ActivityDependency("A"), ActivityDependency("A")),
                ),
            )
        )


def test_schedule_api_exposes_cpm_v2_fields(client: TestClient) -> None:
    response = client.post(
        "/api/v1/intelligence/schedule",
        headers=ADMIN,
        json={"gross_area_m2": 6_000, "floor_count": 6, "priority": "BALANCED"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["critical_paths"]
    assert all("free_float" in item for item in payload["activities"])
    assert all("total_float" in item for item in payload["activities"])


def test_conversation_schedule_reuses_context_and_persists_cpm_v2(
    app: FastAPI,
    client: TestClient,
    conversation_id: str,
) -> None:
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={
            "text": (
                "1000 m² oturumlu 6 kat betonarme konut, temel tamamlandı. "
                "İş programını ve personeli siz planlayın."
            )
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "SITE_SCHEDULE"
    assert payload["planning"]["critical_paths"]
    assert payload["quotes"] == []

    with app.state.session_factory() as session:
        plan = session.query(ConversationPlanORM).filter_by(conversation_id=conversation_id).one()
        assert plan.context_version >= 1
        assert all("free_float" in item for item in plan.schedule_json)
        assert all("late_finish" in item for item in plan.schedule_json)
