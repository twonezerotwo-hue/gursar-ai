from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.database import Base, create_database_engine, create_session_factory
from app.modules.estimation.application.calibration import CalibrationEngine
from app.modules.estimation.application.engine import EstimationEngine
from app.modules.estimation.domain.confidence import evaluate_confidence
from app.modules.estimation.domain.geometry import (
    basement_wall_concrete,
    facade_geometry,
    footprint_perimeter,
    gross_areas,
    raft_concrete,
    wall_volume_to_area,
)
from app.modules.estimation.domain.models import (
    BuildingInput,
    BuildingUse,
    CompletedProjectRecord,
    FootprintGeometry,
    FoundationType,
    ProjectAvailability,
    SeismicDemand,
    SoilCondition,
    SourceClass,
    StructuralIntensity,
    StructuralSystem,
)
from app.modules.estimation.domain.statistics import error_dashboard, error_percent
from app.modules.estimation.infrastructure.registry import EstimationRegistry
from app.modules.estimation.infrastructure.repository import EstimationRepository

D = Decimal


def canonical_building(**changes: object) -> BuildingInput:
    values = {
        "footprint_area_m2": D("500"),
        "above_ground_floors": 6,
        "basement_floors": 0,
        "building_use": BuildingUse.RESIDENTIAL,
        "floor_height_m": D("3.0"),
        "structural_system": StructuralSystem.FRAME_PLUS_SHEAR_WALL,
        "average_span_m": D("4.5"),
        "soil_condition": SoilCondition.NORMAL,
        "seismic_demand": SeismicDemand.MEDIUM,
        "facade_opening_ratio": D("0.25"),
    }
    values.update(changes)
    return BuildingInput(**values)  # type: ignore[arg-type]


def item(result, code: str):
    return next(entry for entry in result.items if entry.code == code)


def test_canonical_500_m2_six_floor_output() -> None:
    result = EstimationEngine(EstimationRegistry()).estimate(canonical_building())

    assert result.gross_area_m2 == D("3000")
    assert item(result, "concrete").quantity.low == D("1080")
    assert item(result, "concrete").quantity.base == D("1200")
    assert item(result, "concrete").quantity.high == D("1380")
    assert item(result, "reinforcement").quantity.low == D("114")
    assert item(result, "reinforcement").quantity.base == D("135")
    assert item(result, "reinforcement").quantity.high == D("165")
    assert item(result, "formwork").quantity.low == D("7500")
    assert item(result, "formwork").quantity.base == D("8400")
    assert item(result, "formwork").quantity.high == D("9600")
    assert item(result, "masonry").quantity.low == D("360")
    assert item(result, "masonry").quantity.base == D("450")
    assert item(result, "masonry").quantity.high == D("540")
    assert item(result, "internal_plaster").quantity.base == D("7200")
    assert item(result, "ceiling").quantity.base == D("2700")
    assert item(result, "floor_finish").quantity.base == D("2700")
    assert float(item(result, "gross_facade").quantity.base) == pytest.approx(1609.9689, rel=1e-4)
    assert float(item(result, "window_area").quantity.base) == pytest.approx(402.4922, rel=1e-4)
    assert float(item(result, "opaque_facade").quantity.base) == pytest.approx(1207.4767, rel=1e-4)


def test_gross_area_keeps_basement_separate() -> None:
    assert gross_areas(canonical_building(basement_floors=2, basement_area_m2=D("400"))) == (
        D("3800"),
        D("3000"),
        D("800"),
    )


def test_square_and_rectangular_perimeters() -> None:
    square, method = footprint_perimeter(canonical_building())
    rectangle, rectangle_method = footprint_perimeter(
        canonical_building(footprint_geometry=FootprintGeometry(length_m=D("10"), width_m=D("50")))
    )
    assert float(square) == pytest.approx(89.4427, rel=1e-4)
    assert method == "assumed_square"
    assert rectangle == D("120")
    assert rectangle_method == "length_width"


def test_facade_geometry_and_opening_deduction() -> None:
    gross, windows, opaque = facade_geometry(
        perimeter_m=D("120"), floor_height_m=D("3"), floors=6, opening_ratio=D("0.25")
    )
    assert (gross, windows, opaque) == (D("2160"), D("540"), D("1620"))


def test_wall_conversion_raft_and_basement_wall_geometry() -> None:
    assert wall_volume_to_area(D("450"), D("0.15")) == D("3000")
    assert raft_concrete(D("500"), D("0.60")) == D("300")
    perimeter = D("4") * D("500").sqrt()
    assert float(basement_wall_concrete(perimeter, D("3"), D("0.30"))) == pytest.approx(
        80.4984, rel=1e-4
    )


@pytest.mark.parametrize(
    ("building_use", "expected"),
    [
        (BuildingUse.RESIDENTIAL, "0.40"),
        (BuildingUse.OFFICE, "0.43"),
        (BuildingUse.HOSPITAL, "0.52"),
    ],
)
def test_building_profile_coefficient_selection(building_use: BuildingUse, expected: str) -> None:
    result = EstimationEngine(EstimationRegistry()).estimate(
        canonical_building(building_use=building_use)
    )
    coefficient = item(result, "concrete").coefficient
    assert coefficient is not None
    assert coefficient.base == D(expected)


def test_span_modifier_and_large_span_uncertainty() -> None:
    normal = EstimationEngine(EstimationRegistry()).estimate(canonical_building())
    large = EstimationEngine(EstimationRegistry()).estimate(
        canonical_building(average_span_m=D("6"))
    )
    very_large = EstimationEngine(EstimationRegistry()).estimate(
        canonical_building(average_span_m=D("7.5"))
    )
    assert item(large, "concrete").quantity.base == item(normal, "concrete").quantity.base * D(
        "1.10"
    )
    assert item(large, "reinforcement").quantity.base == item(
        normal, "reinforcement"
    ).quantity.base * D("1.15")
    assert very_large.structural_uncertainty == "HIGH"
    assert "very_large_span" in very_large.confidence.penalties


@pytest.mark.parametrize(
    ("intensity", "concrete_range", "steel_range", "formwork_range"),
    [
        (StructuralIntensity.LIGHT, (960, 1110), (90, 120), (6900, 8100)),
        (StructuralIntensity.NORMAL, (1080, 1320), (114, 156), (7500, 9300)),
        (StructuralIntensity.SHEAR_WALL_HEAVY, (1200, 1440), (135, 180), (8100, 9900)),
        (StructuralIntensity.HEAVY, (1320, 1650), (156, 210), (8700, 10500)),
    ],
)
def test_structural_intensity_regressions(
    intensity: StructuralIntensity,
    concrete_range: tuple[int, int],
    steel_range: tuple[int, int],
    formwork_range: tuple[int, int],
) -> None:
    result = EstimationEngine(EstimationRegistry()).estimate(
        canonical_building(structural_intensity=intensity)
    )
    assert item(result, "concrete").quantity.low == D(concrete_range[0])
    assert item(result, "concrete").quantity.high == D(concrete_range[1])
    assert item(result, "reinforcement").quantity.low == D(steel_range[0])
    assert item(result, "reinforcement").quantity.high == D(steel_range[1])
    assert item(result, "formwork").quantity.low == D(formwork_range[0])
    assert item(result, "formwork").quantity.high == D(formwork_range[1])


def test_unknown_inputs_reduce_confidence_and_surface_assumptions() -> None:
    registry = EstimationRegistry()
    complete = evaluate_confidence(
        canonical_building(
            foundation_type=FoundationType.RAFT,
            project_availability=ProjectAvailability(
                architectural_project=True, structural_project=True
            ),
        ),
        registry.confidence,
    )
    unknown = EstimationEngine(registry).estimate(BuildingInput(gross_area_m2=D("3000")))
    assert unknown.confidence.estimation_confidence < complete.estimation_confidence
    assert unknown.confidence.label.value in {"LOW", "VERY_LOW"}
    assert "building_use=residential" in unknown.assumptions


def test_geometric_and_project_quantity_priority() -> None:
    result = EstimationEngine(EstimationRegistry()).estimate(
        canonical_building(
            foundation_type=FoundationType.RAFT,
            raft_thickness_m=D("0.6"),
            basement_floors=1,
            basement_area_m2=D("500"),
            basement_height_m=D("3"),
            basement_wall_thickness_m=D("0.3"),
        ),
        project_quantities={"concrete": D("999")},
    )
    assert item(result, "concrete").source_class == SourceClass.PROJECT_QUANTITY
    assert item(result, "concrete").quantity.base == D("999")
    assert item(result, "raft_concrete").source_class == SourceClass.GEOMETRIC_CALCULATION
    assert item(result, "raft_concrete").quantity.base == D("300")
    assert item(result, "basement_wall_concrete").source_class == SourceClass.GEOMETRIC_CALCULATION


def completed_record(
    index: int, steel_ratio: str, *, verified: bool = True
) -> CompletedProjectRecord:
    gross = D("3000")
    return CompletedProjectRecord(
        id=f"record-{index}",
        project_id=f"P-{index}",
        project_type=BuildingUse.RESIDENTIAL,
        location="İstanbul",
        footprint_area_m2=D("500"),
        gross_area_m2=gross,
        floor_count=6,
        basement_count=0,
        structural_system=StructuralSystem.FRAME_PLUS_SHEAR_WALL,
        structural_intensity=None,
        average_span_m=D("4.5"),
        foundation_type=FoundationType.RAFT,
        soil_condition=SoilCondition.NORMAL,
        seismic_parameters={},
        actual_quantities={"reinforcement": D(steel_ratio) * gross},
        estimated_quantities={"reinforcement": D("45") * gross},
        source="approved_project",
        verified=verified,
        verification_level="ENGINEER_APPROVED" if verified else "UNVERIFIED",
    )


def test_verified_only_calibration_outlier_blending_version_and_metrics() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    ratios = ["44", "46", "47", "45", "49", "48", "46", "47", "45", "200"]
    with factory.begin() as session:
        repository = EstimationRepository(session)
        for index, ratio in enumerate(ratios):
            repository.add_completed_project(completed_record(index, ratio))
        repository.add_completed_project(completed_record(99, "500", verified=False))
        calibration = CalibrationEngine(repository, EstimationRegistry()).propose()
        records = repository.list_completed_projects()
        metrics = CalibrationEngine(repository, EstimationRegistry()).metrics()

    segment_data = next(iter(calibration.coefficients.values()))["reinforcement"]
    segment_stats = next(iter(calibration.statistics.values()))["reinforcement"]
    assert segment_stats["raw_count"] == 10
    assert segment_stats["count"] == "9"
    assert "record-9" in segment_stats["outlier_record_ids"]
    assert next(record for record in records if record.id == "record-9").outlier_flags[
        "reinforcement"
    ]
    assert "record-99" not in segment_stats["outlier_record_ids"]
    assert D(segment_data["blended"]) == D("45") * D("0.8") + D("46") * D("0.2")
    assert calibration.status == "PROPOSED"
    assert calibration.estimation_model_version == "construction_estimator_v1.0.1"
    assert D(metrics["reinforcement"]["mean_absolute_percentage_error"]) > 0
    engine.dispose()


def test_actual_vs_estimate_error_and_dashboard() -> None:
    assert error_percent(D("100"), D("90")) == D("10")
    dashboard = error_dashboard([D("10"), D("-20")])
    assert dashboard["mean_absolute_percentage_error"] == D("15.0")
    assert dashboard["bias"] == D("-5.0")


def test_estimation_api_persists_an_auditable_result(client) -> None:
    response = client.post(
        "/api/v1/estimation/estimate",
        json={
            "footprint_area_m2": 500,
            "above_ground_floors": 6,
            "building_use": "residential",
            "floor_height_m": 3,
            "structural_system": "frame_plus_shear_wall",
            "average_span_m": 4.5,
            "soil_condition": "normal",
            "seismic_demand": "medium",
            "facade_opening_ratio": 0.25,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["gross_area_m2"] == "3000"
    assert payload["reference_profile"] == "TR_RC_BASELINE_v1"
    assert {entry["source_class"] for entry in payload["items"]} >= {
        "HEURISTIC_ESTIMATE",
        "GEOMETRIC_CALCULATION",
        "EMPIRICAL_REFERENCE",
    }


def test_chat_routes_building_takeoff_to_estimation_engine(client, conversation_id: str) -> None:
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={
            "text": (
                "500 m² oturum, 6 kat konut. Ne kadar demir, beton, kalıp, duvar ve cephe çıkar?"
            )
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert "3.000 m² brüt alan" in payload["assistant_text"]
    assert "Beton: ≈ 1.080–1.380 m3" in payload["assistant_text"]
    assert "kesin proje metrajı değildir" in payload["assistant_text"]
    assert payload["quotes"] == []


def test_chat_keeps_multi_turn_estimation_in_deterministic_engine(
    client, conversation_id: str
) -> None:
    intake = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": "Projem için metraj çıkarmak istiyorum."},
    )
    assert intake.status_code == 200
    assert "oturum alanını (m²) ve kat sayısını" in intake.json()["assistant_text"]
    assert "klasik betonarme konut" in intake.json()["assistant_text"]

    estimate = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": "500 m2 oturum 6 kat normal döşeme radye temel"},
    )
    assert estimate.status_code == 200
    answer = estimate.json()["assistant_text"]
    assert "3.000 m² brüt alan" in answer
    assert "Yapı kabulü: Klasik konut, betonarme çerçeve + perde" in answer
    assert "Beton: ≈ 1.080–1.380 m3" in answer
    assert "Radye kalınlığı verilmediği için" in answer
    assert "Döşeme kalınlığı" not in answer


def test_chat_collects_area_and_floor_across_short_followups(client, conversation_id: str) -> None:
    client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": "Kabataslak metraj hesapla"},
    )
    area = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": "500 m2 oturum"},
    )
    assert "kat sayısını" in area.json()["assistant_text"]

    floors = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": "6 kat"},
    )
    assert "3.000 m² brüt alan" in floors.json()["assistant_text"]
