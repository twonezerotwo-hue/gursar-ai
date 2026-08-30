from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.estimation.domain.models import (
    BuildingUse,
    FoundationType,
    SeismicDemand,
    SoilCondition,
    StructuralIntensity,
    StructuralSystem,
)


class FootprintGeometryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    length_m: Decimal | None = Field(default=None, gt=0)
    width_m: Decimal | None = Field(default=None, gt=0)
    perimeter_m: Decimal | None = Field(default=None, gt=0)


class WallSystemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_wall_thickness_m: Decimal | None = Field(default=None, gt=0)
    internal_wall_thickness_m: Decimal | None = Field(default=None, gt=0)
    material: str | None = None


class ProjectAvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    architectural_project: bool = False
    structural_project: bool = False
    geotechnical_report: bool = False
    facade_project: bool = False


class BuildingEstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    footprint_area_m2: Decimal | None = Field(default=None, gt=0)
    gross_area_m2: Decimal | None = Field(default=None, gt=0)
    above_ground_floors: int | None = Field(default=None, gt=0)
    basement_floors: int = Field(default=0, ge=0)
    basement_area_m2: Decimal | None = Field(default=None, gt=0)
    building_use: BuildingUse = BuildingUse.UNKNOWN
    floor_height_m: Decimal | None = Field(default=None, gt=0)
    footprint_geometry: FootprintGeometryRequest = Field(default_factory=FootprintGeometryRequest)
    structural_system: StructuralSystem = StructuralSystem.UNKNOWN
    structural_intensity: StructuralIntensity | None = None
    average_span_m: Decimal | None = Field(default=None, gt=0)
    foundation_type: FoundationType = FoundationType.UNKNOWN
    raft_thickness_m: Decimal | None = Field(default=None, gt=0)
    soil_condition: SoilCondition = SoilCondition.UNKNOWN
    seismic_demand: SeismicDemand = SeismicDemand.UNKNOWN
    facade_opening_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    wall_system: WallSystemRequest = Field(default_factory=WallSystemRequest)
    project_availability: ProjectAvailabilityRequest = Field(
        default_factory=ProjectAvailabilityRequest
    )
    basement_height_m: Decimal | None = Field(default=None, gt=0)
    basement_wall_thickness_m: Decimal | None = Field(default=None, gt=0)
    project_quantities: dict[str, Decimal] = Field(default_factory=dict)
    conversation_id: str | None = None

    @model_validator(mode="after")
    def area_is_available(self) -> BuildingEstimateRequest:
        if self.gross_area_m2 is None and self.footprint_area_m2 is None:
            raise ValueError("footprint_area_m2 or gross_area_m2 is required")
        return self


class CompletedProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=120)
    project_type: BuildingUse
    location: str | None = Field(default=None, max_length=240)
    footprint_area_m2: Decimal | None = Field(default=None, gt=0)
    gross_area_m2: Decimal = Field(gt=0)
    floor_count: int = Field(gt=0)
    basement_count: int = Field(default=0, ge=0)
    structural_system: StructuralSystem = StructuralSystem.UNKNOWN
    structural_intensity: StructuralIntensity | None = None
    average_span_m: Decimal | None = Field(default=None, gt=0)
    foundation_type: FoundationType = FoundationType.UNKNOWN
    soil_condition: SoilCondition = SoilCondition.UNKNOWN
    seismic_parameters: dict[str, Any] = Field(default_factory=dict)
    actual_quantities: dict[str, Decimal]
    estimated_quantities: dict[str, Decimal] = Field(default_factory=dict)
    source: str
    verified: bool
    verification_level: str
