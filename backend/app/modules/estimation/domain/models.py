from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class SourceClass(StrEnum):
    PROJECT_QUANTITY = "PROJECT_QUANTITY"
    GEOMETRIC_CALCULATION = "GEOMETRIC_CALCULATION"
    LOCAL_CALIBRATED_ESTIMATE = "LOCAL_CALIBRATED_ESTIMATE"
    EMPIRICAL_REFERENCE = "EMPIRICAL_REFERENCE"
    HEURISTIC_ESTIMATE = "HEURISTIC_ESTIMATE"
    DESIGN_ASSUMPTION = "DESIGN_ASSUMPTION"


class BuildingUse(StrEnum):
    RESIDENTIAL = "residential"
    OFFICE = "office"
    HOTEL = "hotel"
    HOSPITAL = "hospital"
    MALL = "mall"
    PARKING = "parking"
    DORMITORY = "dormitory"
    MIXED_USE = "mixed_use"
    INDUSTRIAL = "industrial"
    UNKNOWN = "unknown"


class StructuralSystem(StrEnum):
    MOMENT_FRAME = "moment_frame"
    FRAME_PLUS_SHEAR_WALL = "frame_plus_shear_wall"
    SHEAR_WALL_DOMINANT = "shear_wall_dominant"
    FLAT_SLAB = "flat_slab"
    RIBBED_SLAB = "ribbed_slab"
    WAFFLE = "waffle"
    PRECAST = "precast"
    UNKNOWN = "unknown"


class StructuralIntensity(StrEnum):
    LIGHT = "LIGHT"
    NORMAL = "NORMAL"
    SHEAR_WALL_HEAVY = "SHEAR_WALL_HEAVY"
    HEAVY = "HEAVY"


class FoundationType(StrEnum):
    ISOLATED = "isolated"
    STRIP = "strip"
    RAFT = "raft"
    PILED_RAFT = "piled_raft"
    PILE = "pile"
    UNKNOWN = "unknown"


class SoilCondition(StrEnum):
    GOOD = "good"
    NORMAL = "normal"
    POOR = "poor"
    VERY_POOR = "very_poor"
    UNKNOWN = "unknown"


class SeismicDemand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"
    UNKNOWN = "unknown"


class ConfidenceLabel(StrEnum):
    VERY_HIGH = "VERY_HIGH"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    VERY_LOW = "VERY_LOW"


@dataclass(frozen=True, slots=True)
class ProjectAvailability:
    architectural_project: bool = False
    structural_project: bool = False
    geotechnical_report: bool = False
    facade_project: bool = False


@dataclass(frozen=True, slots=True)
class FootprintGeometry:
    length_m: Decimal | None = None
    width_m: Decimal | None = None
    perimeter_m: Decimal | None = None


@dataclass(frozen=True, slots=True)
class WallSystem:
    external_wall_thickness_m: Decimal | None = None
    internal_wall_thickness_m: Decimal | None = None
    material: str | None = None


@dataclass(frozen=True, slots=True)
class BuildingInput:
    footprint_area_m2: Decimal | None = None
    gross_area_m2: Decimal | None = None
    above_ground_floors: int | None = None
    basement_floors: int = 0
    basement_area_m2: Decimal | None = None
    building_use: BuildingUse = BuildingUse.UNKNOWN
    floor_height_m: Decimal | None = None
    footprint_geometry: FootprintGeometry = field(default_factory=FootprintGeometry)
    structural_system: StructuralSystem = StructuralSystem.UNKNOWN
    structural_intensity: StructuralIntensity | None = None
    average_span_m: Decimal | None = None
    foundation_type: FoundationType = FoundationType.UNKNOWN
    raft_thickness_m: Decimal | None = None
    soil_condition: SoilCondition = SoilCondition.UNKNOWN
    seismic_demand: SeismicDemand = SeismicDemand.UNKNOWN
    facade_opening_ratio: Decimal | None = None
    wall_system: WallSystem = field(default_factory=WallSystem)
    project_availability: ProjectAvailability = field(default_factory=ProjectAvailability)
    basement_height_m: Decimal | None = None
    basement_wall_thickness_m: Decimal | None = None


@dataclass(frozen=True, slots=True)
class QuantityBand:
    low: Decimal
    base: Decimal
    high: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class EstimateItem:
    code: str
    label: str
    quantity: QuantityBand
    source_class: SourceClass
    source_name: str
    coefficient: QuantityBand | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfidenceResult:
    grade: str
    label: ConfidenceLabel
    estimation_confidence: int
    mode: str
    penalties: tuple[str, ...]
    disclaimer: str = (
        "ESTIMATION_CONFIDENCE is an input-completeness classification, not a guarantee."
    )


@dataclass(frozen=True, slots=True)
class EstimationResult:
    request_id: str
    building_use: BuildingUse
    structural_system: StructuralSystem
    foundation_type: FoundationType
    gross_area_m2: Decimal
    above_ground_gross_area_m2: Decimal
    basement_gross_area_m2: Decimal
    items: tuple[EstimateItem, ...]
    assumptions: tuple[str, ...]
    confidence: ConfidenceResult
    reference_profile: str
    estimation_model_version: str
    coefficient_registry_version: str
    training_dataset_version: str
    structural_uncertainty: str
    seismic_data_status: str
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class CompletedProjectRecord:
    id: str
    project_id: str
    project_type: BuildingUse
    location: str | None
    footprint_area_m2: Decimal | None
    gross_area_m2: Decimal
    floor_count: int
    basement_count: int
    structural_system: StructuralSystem
    structural_intensity: StructuralIntensity | None
    average_span_m: Decimal | None
    foundation_type: FoundationType
    soil_condition: SoilCondition
    seismic_parameters: dict[str, Any]
    actual_quantities: dict[str, Decimal]
    estimated_quantities: dict[str, Decimal]
    source: str
    verified: bool
    verification_level: str
    outlier_flags: dict[str, bool] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SegmentStatistics:
    segment_key: str
    quantity_code: str
    count: int
    mean: Decimal
    median: Decimal
    p10: Decimal
    p25: Decimal
    p50: Decimal
    p75: Decimal
    p90: Decimal
    standard_deviation: Decimal
    mad: Decimal
    outlier_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CalibrationVersion:
    id: str
    estimation_model_version: str
    coefficient_registry_version: str
    training_dataset_version: str
    status: str
    coefficients: dict[str, Any]
    statistics: dict[str, Any]
    calibration_timestamp: datetime
    activated_at: datetime | None = None
