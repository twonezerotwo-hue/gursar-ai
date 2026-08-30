from __future__ import annotations

from decimal import Decimal

from app.modules.estimation.domain.models import BuildingInput


def gross_areas(building: BuildingInput) -> tuple[Decimal, Decimal, Decimal]:
    if building.gross_area_m2 is not None:
        if building.gross_area_m2 <= 0:
            raise ValueError("gross_area_m2 must be positive")
        return building.gross_area_m2, building.gross_area_m2, Decimal("0")
    if building.footprint_area_m2 is None or building.footprint_area_m2 <= 0:
        raise ValueError("footprint_area_m2 or gross_area_m2 is required")
    if building.above_ground_floors is None or building.above_ground_floors <= 0:
        raise ValueError("above_ground_floors must be positive")
    above = building.footprint_area_m2 * building.above_ground_floors
    basement_area = building.basement_area_m2 or building.footprint_area_m2
    basement = basement_area * building.basement_floors
    return above + basement, above, basement


def footprint_perimeter(building: BuildingInput) -> tuple[Decimal, str]:
    geometry = building.footprint_geometry
    if geometry.perimeter_m is not None:
        if geometry.perimeter_m <= 0:
            raise ValueError("perimeter_m must be positive")
        return geometry.perimeter_m, "provided_perimeter"
    if geometry.length_m is not None and geometry.width_m is not None:
        if geometry.length_m <= 0 or geometry.width_m <= 0:
            raise ValueError("footprint dimensions must be positive")
        return Decimal("2") * (geometry.length_m + geometry.width_m), "length_width"
    if building.footprint_area_m2 is None or building.footprint_area_m2 <= 0:
        raise ValueError("footprint geometry is unavailable")
    side = building.footprint_area_m2.sqrt()
    return Decimal("4") * side, "assumed_square"


def compactness_ratio(perimeter_m: Decimal, footprint_area_m2: Decimal) -> Decimal:
    return perimeter_m / (Decimal("4") * footprint_area_m2.sqrt())


def compactness_class(ratio: Decimal) -> str:
    if ratio <= Decimal("1.05"):
        return "very_compact"
    if ratio <= Decimal("1.15"):
        return "compact"
    if ratio <= Decimal("1.30"):
        return "elongated"
    return "irregular"


def facade_geometry(
    *,
    perimeter_m: Decimal,
    floor_height_m: Decimal,
    floors: int,
    opening_ratio: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    if not Decimal("0") <= opening_ratio <= Decimal("1"):
        raise ValueError("facade opening ratio must be between 0 and 1")
    gross = perimeter_m * floor_height_m * floors
    windows = gross * opening_ratio
    return gross, windows, gross - windows


def raft_concrete(foundation_area_m2: Decimal, thickness_m: Decimal) -> Decimal:
    if foundation_area_m2 <= 0 or thickness_m <= 0:
        raise ValueError("raft area and thickness must be positive")
    return foundation_area_m2 * thickness_m


def basement_wall_concrete(
    perimeter_m: Decimal,
    basement_height_m: Decimal,
    wall_thickness_m: Decimal,
) -> Decimal:
    if perimeter_m <= 0 or basement_height_m <= 0 or wall_thickness_m <= 0:
        raise ValueError("basement wall geometry must be positive")
    return perimeter_m * basement_height_m * wall_thickness_m


def wall_volume_to_area(volume_m3: Decimal, thickness_m: Decimal) -> Decimal:
    if volume_m3 < 0 or thickness_m <= 0:
        raise ValueError("wall volume must be non-negative and thickness must be positive")
    return volume_m3 / thickness_m
