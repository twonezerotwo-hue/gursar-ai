from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.shape
import ifcopenshell.util.unit

from app.modules.project_graph.domain.models import Discipline, ElementType

IFC_TYPES: dict[str, ElementType] = {
    "IfcWall": ElementType.WALL,
    "IfcWallStandardCase": ElementType.WALL,
    "IfcColumn": ElementType.COLUMN,
    "IfcBeam": ElementType.BEAM,
    "IfcSlab": ElementType.SLAB,
    "IfcFooting": ElementType.FOOTING,
    "IfcPile": ElementType.PILE,
    "IfcDoor": ElementType.DOOR,
    "IfcWindow": ElementType.WINDOW,
    "IfcStair": ElementType.STAIR,
    "IfcStairFlight": ElementType.STAIR,
    "IfcRamp": ElementType.RAMP,
    "IfcRoof": ElementType.ROOF,
    "IfcSpace": ElementType.ROOM,
}


@dataclass(frozen=True, slots=True)
class IfcBuildingData:
    source_key: str
    name: str


@dataclass(frozen=True, slots=True)
class IfcLevelData:
    source_key: str
    name: str
    elevation: float | None


@dataclass(frozen=True, slots=True)
class IfcElementData:
    source_key: str
    element_type: ElementType
    ifc_class: str
    name: str | None
    object_type: str | None
    predefined_type: str | None
    level_source_key: str | None
    material: str | None
    properties: dict[str, Any]
    stated_quantities: dict[str, float]
    quantities: dict[str, float]
    geometry_quantities: dict[str, float]
    dimensions: dict[str, float]
    extraction_method: str


@dataclass(frozen=True, slots=True)
class IfcExtraction:
    schema: str
    buildings: list[IfcBuildingData]
    levels: list[IfcLevelData]
    elements: list[IfcElementData]


class IfcQuantityExtractor:
    version = "ifc-quantity-v1"

    def extract(self, path: Path) -> IfcExtraction:
        model = ifcopenshell.open(path)
        unit_scale = float(ifcopenshell.util.unit.calculate_unit_scale(model) or 1.0)
        buildings = [
            IfcBuildingData(source_key=_source_key(item), name=item.Name or "Bina")
            for item in model.by_type("IfcBuilding")
        ]
        levels = [
            IfcLevelData(
                source_key=_source_key(item),
                name=item.Name or "Kat",
                elevation=(
                    float(item.Elevation) * unit_scale if item.Elevation is not None else None
                ),
            )
            for item in model.by_type("IfcBuildingStorey")
        ]
        elements: list[IfcElementData] = []
        seen: set[str] = set()
        for ifc_class, element_type in IFC_TYPES.items():
            for element in model.by_type(ifc_class):
                source_key = _source_key(element)
                if source_key in seen:
                    continue
                seen.add(source_key)
                qtos = ifcopenshell.util.element.get_psets(
                    element, psets_only=False, qtos_only=True
                )
                psets = ifcopenshell.util.element.get_psets(
                    element, psets_only=True, qtos_only=False
                )
                quantities = _flatten_quantities(qtos, unit_scale=unit_scale)
                geometry = _geometry_quantities(element)
                merged = dict(quantities)
                for key, value in geometry.items():
                    merged.setdefault(key, value)
                container = ifcopenshell.util.element.get_container(
                    element, should_get_direct=False, ifc_class="IfcBuildingStorey"
                )
                material = ifcopenshell.util.element.get_material(element)
                elements.append(
                    IfcElementData(
                        source_key=source_key,
                        element_type=element_type,
                        ifc_class=element.is_a(),
                        name=getattr(element, "Name", None),
                        object_type=getattr(element, "ObjectType", None),
                        predefined_type=str(getattr(element, "PredefinedType", "") or "") or None,
                        level_source_key=_source_key(container) if container is not None else None,
                        material=_material_name(material),
                        properties=_json_safe(psets),
                        stated_quantities=quantities,
                        quantities=merged,
                        geometry_quantities=geometry,
                        dimensions=_dimensions(merged),
                        extraction_method=(
                            "IFC_QUANTITY_SET" if quantities else "IFC_GEOMETRY_CALCULATION"
                        ),
                    )
                )
        return IfcExtraction(
            schema=str(model.schema),
            buildings=buildings,
            levels=levels,
            elements=elements,
        )


def _source_key(entity: Any) -> str:
    return str(getattr(entity, "GlobalId", None) or f"ifc-id:{entity.id()}")


def _flatten_quantities(qtos: dict[str, Any], *, unit_scale: float) -> dict[str, float]:
    result: dict[str, float] = {}
    for quantity_set in qtos.values():
        if not isinstance(quantity_set, dict):
            continue
        for key, value in quantity_set.items():
            if key == "id" or not isinstance(value, (int, float)):
                continue
            exponent = 3 if "volume" in key.lower() else 2 if "area" in key.lower() else 1
            if any(token in key.lower() for token in ("count", "number", "weight", "mass")):
                exponent = 0
            result.setdefault(key, float(value) * unit_scale**exponent)
    return result


def _geometry_quantities(element: Any) -> dict[str, float]:
    try:
        settings = ifcopenshell.geom.settings()
        shape: Any = ifcopenshell.geom.create_shape(settings, element)
        geometry: Any = shape.geometry
        return {
            "GeometryVolume": float(ifcopenshell.util.shape.get_volume(geometry)),
            "GeometrySurfaceArea": float(ifcopenshell.util.shape.get_area(geometry)),
            "GeometryLength": float(ifcopenshell.util.shape.get_x(geometry)),
            "GeometryWidth": float(ifcopenshell.util.shape.get_y(geometry)),
            "GeometryHeight": float(ifcopenshell.util.shape.get_z(geometry)),
        }
    except (RuntimeError, ValueError, TypeError, AttributeError):
        return {}


def _dimensions(values: dict[str, float]) -> dict[str, float]:
    aliases = {
        "length": ("Length", "OverallLength", "GeometryLength"),
        "width": ("Width", "OverallWidth", "GeometryWidth"),
        "height": ("Height", "OverallHeight", "GeometryHeight"),
        "thickness": ("Thickness",),
        "area": (
            "NetSideArea",
            "GrossSideArea",
            "NetArea",
            "GrossArea",
            "Area",
            "GeometrySurfaceArea",
        ),
        "volume": ("NetVolume", "GrossVolume", "Volume", "GeometryVolume"),
    }
    dimensions: dict[str, float] = {}
    for target, candidates in aliases.items():
        for candidate in candidates:
            if candidate in values:
                dimensions[target] = values[candidate]
                break
    return dimensions


def _material_name(material: Any) -> str | None:
    if material is None:
        return None
    return str(
        getattr(material, "Name", None)
        or getattr(material, "LayerSetName", None)
        or material.is_a()
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items() if key != "id"}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def discipline_for(element_type: ElementType) -> Discipline:
    if element_type in {
        ElementType.DOOR,
        ElementType.WINDOW,
        ElementType.ROOM,
        ElementType.STAIR,
        ElementType.RAMP,
        ElementType.ROOF,
    }:
        return Discipline.ARCHITECTURAL
    return Discipline.STRUCTURAL
