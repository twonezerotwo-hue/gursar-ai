from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.modules.estimation.domain.models import (
    BuildingInput,
    ConfidenceLabel,
    ConfidenceResult,
    FoundationType,
    SeismicDemand,
    SoilCondition,
    StructuralSystem,
)


def evaluate_confidence(
    building: BuildingInput,
    rules: dict[str, Any],
) -> ConfidenceResult:
    availability = building.project_availability
    if availability.architectural_project and availability.structural_project:
        grade = "A"
    elif (
        availability.architectural_project
        and building.structural_system != StructuralSystem.UNKNOWN
    ):
        grade = "B"
    elif availability.architectural_project:
        grade = "C"
    elif (
        building.footprint_area_m2 is not None
        and building.above_ground_floors is not None
        and building.building_use.value != "unknown"
    ):
        grade = "D"
    else:
        grade = "E"

    configured = rules["classes"][grade]
    score = int(configured["score"])
    penalties: list[str] = []
    penalty_config = rules["penalties"]

    def penalize(condition: bool, key: str) -> None:
        nonlocal score
        if condition:
            score -= int(penalty_config[key])
            penalties.append(key)

    penalize(
        building.structural_system == StructuralSystem.UNKNOWN,
        "unknown_structural_system",
    )
    penalize(building.soil_condition == SoilCondition.UNKNOWN, "unknown_soil")
    penalize(building.foundation_type == FoundationType.UNKNOWN, "unknown_foundation")
    penalize(building.floor_height_m is None, "unknown_floor_height")
    penalize(building.facade_opening_ratio is None, "unknown_facade_ratio")
    penalize(
        building.average_span_m is not None and building.average_span_m > Decimal("7"),
        "very_large_span",
    )
    penalize(
        building.basement_floors > 0
        and (
            building.basement_area_m2 is None
            or building.basement_height_m is None
            or building.basement_wall_thickness_m is None
        ),
        "basement_geometry_missing",
    )
    penalize(building.seismic_demand == SeismicDemand.UNKNOWN, "seismic_assumed")
    score = max(0, min(99, score))
    label = next(entry["label"] for entry in rules["labels"] if score >= int(entry["min_score"]))
    return ConfidenceResult(
        grade=grade,
        label=ConfidenceLabel(label),
        estimation_confidence=score,
        mode=str(configured["mode"]),
        penalties=tuple(penalties),
    )
