from __future__ import annotations

import math
from enum import StrEnum
from itertools import pairwise
from typing import Any


class Discipline(StrEnum):
    ARCHITECTURAL = "ARCHITECTURAL"
    STRUCTURAL = "STRUCTURAL"
    MECHANICAL = "MECHANICAL"
    ELECTRICAL = "ELECTRICAL"
    LANDSCAPE = "LANDSCAPE"
    FIRE = "FIRE"
    UNKNOWN = "UNKNOWN"


class ElementType(StrEnum):
    WALL = "WALL"
    COLUMN = "COLUMN"
    SHEAR_WALL = "SHEAR_WALL"
    BEAM = "BEAM"
    SLAB = "SLAB"
    FOUNDATION = "FOUNDATION"
    FOOTING = "FOOTING"
    PILE = "PILE"
    DOOR = "DOOR"
    WINDOW = "WINDOW"
    ROOM = "ROOM"
    STAIR = "STAIR"
    RAMP = "RAMP"
    ROOF = "ROOF"
    CEILING = "CEILING"
    FACADE = "FACADE"
    PIPE = "PIPE"
    DUCT = "DUCT"
    CABLE_TRAY = "CABLE_TRAY"
    EQUIPMENT = "EQUIPMENT"
    UNKNOWN = "UNKNOWN"


class SourceType(StrEnum):
    IFC = "IFC"
    DXF = "DXF"
    VECTOR_PDF = "VECTOR_PDF"
    RASTER_PDF = "RASTER_PDF"
    IMAGE = "IMAGE"
    VISION = "VISION"
    MANUAL = "MANUAL"


class ReviewStatus(StrEnum):
    AUTO = "AUTO"
    PREDICTED = "PREDICTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    VERIFIED = "VERIFIED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ReviewAction(StrEnum):
    ACCEPT = "ACCEPT"
    CORRECT = "CORRECT"
    REJECT = "REJECT"


class MeasurementType(StrEnum):
    LENGTH = "LENGTH"
    AREA = "AREA"
    VOLUME = "VOLUME"
    COUNT = "COUNT"


class ScaleMethod(StrEnum):
    TITLEBLOCK = "TITLEBLOCK"
    DIMENSION_TEXT = "DIMENSION_TEXT"
    MANUAL_TWO_POINT = "MANUAL_TWO_POINT"
    VECTOR_UNIT = "VECTOR_UNIT"
    IFC_NATIVE = "IFC_NATIVE"
    DXF_NATIVE = "DXF_NATIVE"


def calculate_measurement(
    measurement_type: MeasurementType,
    geometry: dict[str, Any],
    *,
    coordinate_scale: float,
) -> float:
    points = geometry.get("points")
    if measurement_type is MeasurementType.COUNT:
        if not isinstance(points, list):
            raise ValueError("COUNT geometry.points must be a list")
        if not points:
            raise ValueError("COUNT requires at least one point")
        return float(len(points))
    if measurement_type is MeasurementType.VOLUME:
        base = geometry.get("base_area")
        height = geometry.get("height")
        if not isinstance(base, (int, float)) or not isinstance(height, (int, float)):
            raise ValueError("VOLUME requires numeric base_area and height")
        return float(base) * float(height) * coordinate_scale**3
    if not isinstance(points, list):
        raise ValueError("geometry.points must be a list")
    parsed = [_point(item) for item in points]
    if measurement_type is MeasurementType.LENGTH:
        if len(parsed) < 2:
            raise ValueError("LENGTH requires at least two points")
        raw = sum(math.dist(left, right) for left, right in pairwise(parsed))
        return raw * coordinate_scale
    if measurement_type is MeasurementType.AREA:
        if len(parsed) < 3:
            raise ValueError("AREA requires at least three points")
        raw = (
            abs(
                sum(
                    x1 * y2 - x2 * y1
                    for (x1, y1), (x2, y2) in zip(parsed, parsed[1:] + parsed[:1], strict=True)
                )
            )
            / 2
        )
        return raw * coordinate_scale**2
    raise ValueError(f"Unsupported measurement type: {measurement_type}")


def _point(value: Any) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("Each point must be [x, y]")
    x, y = value
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        raise ValueError("Point coordinates must be numeric")
    return float(x), float(y)
