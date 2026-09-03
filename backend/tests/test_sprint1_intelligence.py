from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.modules.architecture_intelligence.application.service import ArchitectureAnalyzer
from app.modules.architecture_intelligence.domain.models import ArchitectureInput
from app.modules.conversation_intelligence.application.service import StructuredIntentRouter
from app.modules.conversation_intelligence.domain.models import ConstructionIntent, ProjectContext
from app.modules.planning.application.service import CpmEngine, InvalidScheduleError
from app.modules.planning.domain.models import (
    Activity,
    ActivityDependency,
    DependencyType,
)


def _send(client: TestClient, conversation_id: str, text: str) -> dict:
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": text},
    )
    assert response.status_code == 200
    return response.json()


def test_intent_router_prioritizes_schedule_over_structural_terms() -> None:
    result = StructuredIntentRouter().route(
        "1000 m², 6 kat betonarme; temel tamamlandı, iş programı istiyorum",
        ProjectContext(conversation_id="C1"),
    )

    assert result.intent == ConstructionIntent.SITE_SCHEDULE
    assert result.updates["footprint_m2"] == Decimal("1000")
    assert result.updates["gross_area_m2"] == Decimal("6000")
    assert result.updates["floor_count"] == 6
    assert result.updates["structural_system"] == "REINFORCED_CONCRETE"
    assert result.updates["stage"] == "FOUNDATION_COMPLETED"


def test_project_context_persists_across_turns_and_drives_schedule(
    client: TestClient,
    conversation_id: str,
) -> None:
    first = _send(
        client,
        conversation_id,
        "1000 m² oturumlu 6 kat betonarme konut, temel tamamlandı. İş programı istiyorum.",
    )
    assert first["intent"] == "SITE_SCHEDULE"
    assert first["planning"] is None
    assert [item["value"] for item in first["quick_replies"]] == [
        "fast",
        "economic",
        "balanced",
    ]

    planned = _send(client, conversation_id, "dengeli")
    assert planned["intent"] == "SITE_SCHEDULE"
    assert planned["quotes"] == []
    assert planned["planning"]["duration_days"]
    assert planned["planning"]["peak_crew"] > 0
    assert planned["planning"]["critical_path"]
    assert "CPM_ENGINE" in planned["tools_used"]
    assert "beton/demir/kalıp" not in planned["assistant_text"]

    context = client.get(f"/api/v1/conversations/{conversation_id}/project-context")
    assert context.status_code == 200
    payload = context.json()
    assert payload["footprint_m2"] == "1000.0000"
    assert payload["gross_area_m2"] == "6000.0000"
    assert payload["floor_count"] == 6
    assert payload["goal"] == "site_schedule"


def test_planning_response_asks_only_one_question(
    client: TestClient,
    conversation_id: str,
) -> None:
    response = _send(client, conversation_id, "İş programı yap")

    assert response["intent"] == "SITE_SCHEDULE"
    assert response["assistant_text"].count("?") == 1
    assert response["quotes"] == []


def test_delegated_crew_request_returns_balanced_plan_without_extra_question(
    client: TestClient,
    conversation_id: str,
) -> None:
    response = _send(
        client,
        conversation_id,
        (
            "1000 m² oturumlu 6 kat betonarme konut. Temel tamamlandı, iş programı "
            "istiyorum; personeli siz planlayın."
        ),
    )

    assert response["intent"] == "SITE_SCHEDULE"
    assert response["planning"]["priority"] == "BALANCED"
    assert response["planning"]["activities"]
    assert response["assistant_text"].count("?") == 0
    assert response["quotes"] == []


def test_cpm_computes_critical_path_and_rejects_cycles() -> None:
    engine = CpmEngine()
    result = engine.calculate(
        (
            Activity("A", "A", "1", Decimal("3"), "crew", 2),
            Activity(
                "B",
                "B",
                "2",
                Decimal("2"),
                "crew",
                3,
                (ActivityDependency("A", DependencyType.FINISH_START),),
            ),
        )
    )
    assert result.duration_days == Decimal("5")
    assert result.critical_path == ("A", "B")

    with pytest.raises(InvalidScheduleError):
        engine.calculate(
            (
                Activity("A", "A", "1", Decimal("1"), "crew", 1, (ActivityDependency("B"),)),
                Activity("B", "B", "2", Decimal("1"), "crew", 1, (ActivityDependency("A"),)),
            )
        )


def test_architecture_analyzer_reports_metrics_and_quality_issues() -> None:
    result = ArchitectureAnalyzer().analyze(
        ArchitectureInput(
            gross_area_m2=Decimal("1000"),
            net_area_m2=Decimal("760"),
            circulation_area_m2=Decimal("270"),
            core_area_m2=Decimal("100"),
            facade_area_m2=Decimal("500"),
            opening_area_m2=Decimal("150"),
            room_count=20,
            door_collision_count=2,
        )
    )
    metrics = {metric.code: metric.value for metric in result.metrics}
    issues = {issue.code: issue for issue in result.issues}
    assert metrics["NET_GROSS_RATIO"] == Decimal("0.7600")
    assert metrics["FACADE_OPENING_RATIO"] == Decimal("0.3000")
    assert issues["HIGH_CIRCULATION_RATIO"].severity == "MEDIUM"
    assert issues["DOOR_COLLISION"].evidence_count == 2


def test_engineering_intelligence_endpoints_require_admin_and_return_results(
    client: TestClient,
) -> None:
    denied = client.post(
        "/api/v1/intelligence/schedule",
        json={"gross_area_m2": 6000, "floor_count": 6},
    )
    assert denied.status_code == 401

    headers = {"X-Admin-Key": "test-admin-key"}
    schedule = client.post(
        "/api/v1/intelligence/schedule",
        headers=headers,
        json={"gross_area_m2": 6000, "floor_count": 6, "priority": "BALANCED"},
    )
    assert schedule.status_code == 200
    assert len(schedule.json()["activities"]) == 6

    analysis = client.post(
        "/api/v1/intelligence/architecture/analyze",
        headers=headers,
        json={"gross_area_m2": 1000, "net_area_m2": 800},
    )
    assert analysis.status_code == 200
    assert any(item["code"] == "NET_GROSS_RATIO" for item in analysis.json()["metrics"])
