from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.modules.estimation.domain.confidence import evaluate_confidence
from app.modules.estimation.domain.geometry import (
    basement_wall_concrete,
    compactness_class,
    compactness_ratio,
    facade_geometry,
    footprint_perimeter,
    gross_areas,
    raft_concrete,
    wall_volume_to_area,
)
from app.modules.estimation.domain.models import (
    BuildingInput,
    BuildingUse,
    EstimateItem,
    EstimationResult,
    FoundationType,
    QuantityBand,
    SeismicDemand,
    SoilCondition,
    SourceClass,
    StructuralSystem,
)
from app.modules.estimation.infrastructure.registry import EstimationRegistry

DECIMAL_THOUSAND = Decimal("1000")


class EstimationEngine:
    def __init__(
        self,
        registry: EstimationRegistry,
        *,
        calibrated_coefficients: dict[str, Any] | None = None,
        model_versions: dict[str, str] | None = None,
    ) -> None:
        self._registry = registry
        self._calibrated = calibrated_coefficients or {}
        versions = model_versions or {}
        self._model_version = versions.get(
            "estimation_model_version",
            str(registry.learning["model_version"]),
        )
        self._coefficient_version = versions.get(
            "coefficient_registry_version",
            str(registry.learning["coefficient_registry_version"]),
        )
        self._dataset_version = versions.get("training_dataset_version", "dataset_untrained")

    def estimate(
        self,
        building: BuildingInput,
        *,
        project_quantities: dict[str, Decimal] | None = None,
        request_id: str | None = None,
    ) -> EstimationResult:
        assumptions: list[str] = []
        normalized = self._with_safe_defaults(building, assumptions)
        total_gross, above_gross, basement_gross = gross_areas(normalized)
        confidence = evaluate_confidence(building, self._registry.confidence)
        profile = self._registry.building_profile(normalized.building_use.value)
        span = self._span_modifier(normalized.average_span_m or Decimal("0"))
        floor_system = self._floor_system_modifier(normalized.structural_system)
        soil = self._registry.modifiers["soil_modifiers"][normalized.soil_condition.value]
        seismic = self._registry.modifiers["seismic_modifiers"][normalized.seismic_demand.value]
        segment_key = calibration_segment(normalized)

        coefficient_bands: dict[str, QuantityBand] = {}
        item_sources: dict[str, SourceClass] = {}
        for code in ("concrete", "reinforcement", "formwork", "masonry"):
            coefficient_bands[code] = self._coefficient_band(
                code=code,
                configured=profile[code],
                building=normalized,
                span=span,
                floor_system=floor_system,
                soil=soil,
                seismic=seismic,
            )
            calibrated = self._calibrated_coefficient(segment_key, code)
            if calibrated is not None:
                coefficient_bands[code] = self._recenter_band(coefficient_bands[code], calibrated)
                item_sources[code] = SourceClass.LOCAL_CALIBRATED_ESTIMATE
            else:
                item_sources[code] = SourceClass.HEURISTIC_ESTIMATE

        structural = {
            code: self._multiply_band(band, above_gross) for code, band in coefficient_bands.items()
        }
        if basement_gross:
            basement_profile = self._registry.modifiers["basement_frame"]
            for code in ("concrete", "reinforcement", "formwork"):
                basement_band = self._configured_band(basement_profile[code], code)
                structural[code] = self._add_bands(
                    structural[code],
                    self._multiply_band(basement_band, basement_gross),
                )

        project_quantities = project_quantities or {}
        items: list[EstimateItem] = []
        labels = {
            "concrete": "Beton",
            "reinforcement": "Donatı",
            "formwork": "Kalıp",
            "masonry": "Duvar",
        }
        output_units = {
            "concrete": "m3",
            "reinforcement": "ton",
            "formwork": "m2",
            "masonry": "m3",
        }
        for code in ("concrete", "reinforcement", "formwork", "masonry"):
            quantity = structural[code]
            if code == "reinforcement":
                quantity = self._divide_band(quantity, DECIMAL_THOUSAND, "ton")
            if code in project_quantities:
                exact = project_quantities[code]
                quantity = QuantityBand(exact, exact, exact, output_units[code])
                source = SourceClass.PROJECT_QUANTITY
                source_name = "Uploaded/approved project takeoff"
            else:
                source = item_sources[code]
                source_name = (
                    "Verified local calibrated segment"
                    if source == SourceClass.LOCAL_CALIBRATED_ESTIMATE
                    else str(self._registry.buildings["source_name"])
                )
            items.append(
                EstimateItem(
                    code=code,
                    label=labels[code],
                    quantity=quantity,
                    source_class=source,
                    source_name=source_name,
                    coefficient=coefficient_bands[code],
                    notes=("Yaklaşık mühendislik tahminidir; kesin proje metrajı değildir.",),
                )
            )

        items.extend(self._foundation_and_basement_items(normalized, assumptions))
        items.extend(self._facade_items(normalized, assumptions))
        items.extend(self._finish_items(total_gross, structural["masonry"], normalized))
        items = self._apply_project_quantity_priority(items, project_quantities)
        return EstimationResult(
            request_id=request_id or str(uuid4()),
            building_use=normalized.building_use,
            structural_system=normalized.structural_system,
            foundation_type=normalized.foundation_type,
            gross_area_m2=total_gross,
            above_ground_gross_area_m2=above_gross,
            basement_gross_area_m2=basement_gross,
            items=tuple(items),
            assumptions=tuple(dict.fromkeys(assumptions)),
            confidence=confidence,
            reference_profile=str(self._registry.empirical["profile"]["id"]),
            estimation_model_version=self._model_version,
            coefficient_registry_version=self._coefficient_version,
            training_dataset_version=self._dataset_version,
            structural_uncertainty=(
                "HIGH"
                if normalized.average_span_m is not None
                and normalized.average_span_m > Decimal("7")
                else "NORMAL"
            ),
            seismic_data_status=(
                "ASSUMED" if building.seismic_demand == SeismicDemand.UNKNOWN else "PROVIDED"
            ),
            generated_at=datetime.now(UTC),
        )

    def _with_safe_defaults(
        self,
        building: BuildingInput,
        assumptions: list[str],
    ) -> BuildingInput:
        defaults = self._registry.modifiers["defaults"]
        normalized = building
        if building.building_use == BuildingUse.UNKNOWN:
            normalized = replace(normalized, building_use=BuildingUse(defaults["building_use"]))
            assumptions.append("building_use=residential")
        if building.floor_height_m is None:
            normalized = replace(
                normalized,
                floor_height_m=Decimal(str(defaults["floor_height_m"])),
            )
            assumptions.append("floor_height=3.00m")
        if building.structural_system == StructuralSystem.UNKNOWN:
            normalized = replace(
                normalized,
                structural_system=StructuralSystem(defaults["structural_system"]),
            )
            assumptions.append("structural_system=frame_plus_shear_wall")
        if building.average_span_m is None:
            normalized = replace(
                normalized,
                average_span_m=Decimal(str(defaults["average_span_m"])),
            )
            assumptions.append("average_span=4.50m")
        if building.soil_condition == SoilCondition.UNKNOWN:
            normalized = replace(
                normalized, soil_condition=SoilCondition(defaults["soil_condition"])
            )
            assumptions.append("soil_condition=normal")
        if building.seismic_demand == SeismicDemand.UNKNOWN:
            normalized = replace(
                normalized,
                seismic_demand=SeismicDemand(defaults["seismic_demand"]),
            )
            assumptions.append("seismic_demand=medium_assumed")
        if building.basement_floors == 0:
            assumptions.append("no_basement")
        if (
            building.foundation_type in {FoundationType.RAFT, FoundationType.PILED_RAFT}
            and building.raft_thickness_m is None
        ):
            assumptions.append("raft_thickness=missing_foundation_quantity_not_added")
        return normalized

    def _coefficient_band(
        self,
        *,
        code: str,
        configured: dict[str, Any],
        building: BuildingInput,
        span: dict[str, Any],
        floor_system: dict[str, Any],
        soil: dict[str, Any],
        seismic: dict[str, Any],
    ) -> QuantityBand:
        band = self._configured_band(configured, code)
        if building.structural_intensity is not None and code != "masonry":
            intensity = self._registry.modifiers["intensity_profiles"][
                building.structural_intensity.value
            ][code]
            low = Decimal(str(intensity["low"]))
            high = Decimal(str(intensity["high"]))
            band = QuantityBand(
                low=low,
                base=(low + high) / Decimal("2"),
                high=high,
                unit=band.unit,
            )
        if code in {"concrete", "reinforcement", "formwork"}:
            floor_mod = floor_system[code]
            band = QuantityBand(
                low=band.low * Decimal(str(span[code])) * Decimal(str(floor_mod["low"])),
                base=band.base * Decimal(str(span[code])) * Decimal(str(floor_mod["base"])),
                high=band.high * Decimal(str(span[code])) * Decimal(str(floor_mod["high"])),
                unit=band.unit,
            )
        if code in {"concrete", "reinforcement"}:
            band = self._multiply_factors(
                band,
                Decimal(str(soil[code])),
                Decimal(str(seismic[code])),
            )
        return QuantityBand(
            low=min(band.low, band.base, band.high),
            base=band.base,
            high=max(band.low, band.base, band.high),
            unit=band.unit,
        )

    def _configured_band(self, configured: dict[str, Any], code: str) -> QuantityBand:
        unit = str(configured.get("unit") or ("kg" if code == "reinforcement" else "m2"))
        return QuantityBand(
            low=Decimal(str(configured["low"])),
            base=Decimal(str(configured.get("base", configured["low"]))),
            high=Decimal(str(configured["high"])),
            unit=f"{unit}_per_gross_m2",
        )

    def _span_modifier(self, span_m: Decimal) -> dict[str, Any]:
        for modifier in self._registry.modifiers["span_modifiers"].values():
            maximum = modifier["max_m"]
            if span_m >= Decimal(str(modifier["min_m"])) and (
                maximum is None or span_m < Decimal(str(maximum))
            ):
                return modifier
        raise ValueError(f"No span modifier for {span_m}")

    def _floor_system_modifier(self, system: StructuralSystem) -> dict[str, Any]:
        key = {
            StructuralSystem.FLAT_SLAB: "flat_slab",
            StructuralSystem.RIBBED_SLAB: "ribbed_slab",
            StructuralSystem.WAFFLE: "waffle",
        }.get(system, "beam_slab")
        return self._registry.modifiers["floor_system_modifiers"][key]

    def _calibrated_coefficient(self, segment_key: str, code: str) -> Decimal | None:
        value = self._calibrated.get(segment_key, {}).get(code, {}).get("blended")
        return Decimal(str(value)) if value is not None else None

    @staticmethod
    def _recenter_band(band: QuantityBand, center: Decimal) -> QuantityBand:
        return QuantityBand(
            low=center * band.low / band.base,
            base=center,
            high=center * band.high / band.base,
            unit=band.unit,
        )

    @staticmethod
    def _multiply_band(band: QuantityBand, factor: Decimal) -> QuantityBand:
        return QuantityBand(
            low=band.low * factor,
            base=band.base * factor,
            high=band.high * factor,
            unit=band.unit.split("_per_gross_m2")[0],
        )

    @staticmethod
    def _multiply_factors(band: QuantityBand, first: Decimal, second: Decimal) -> QuantityBand:
        factor = first * second
        return QuantityBand(
            band.low * factor,
            band.base * factor,
            band.high * factor,
            band.unit,
        )

    @staticmethod
    def _divide_band(band: QuantityBand, divisor: Decimal, unit: str) -> QuantityBand:
        return QuantityBand(band.low / divisor, band.base / divisor, band.high / divisor, unit)

    @staticmethod
    def _add_bands(first: QuantityBand, second: QuantityBand) -> QuantityBand:
        return QuantityBand(
            first.low + second.low,
            first.base + second.base,
            first.high + second.high,
            first.unit,
        )

    def _foundation_and_basement_items(
        self,
        building: BuildingInput,
        assumptions: list[str],
    ) -> list[EstimateItem]:
        items: list[EstimateItem] = []
        footprint = building.footprint_area_m2
        if (
            building.foundation_type in {FoundationType.RAFT, FoundationType.PILED_RAFT}
            and footprint is not None
            and building.raft_thickness_m is not None
        ):
            value = raft_concrete(footprint, building.raft_thickness_m)
            items.append(self._geometric_item("raft_concrete", "Radye temel betonu", value, "m3"))
        if building.basement_floors <= 0 or footprint is None:
            return items
        try:
            perimeter, method = footprint_perimeter(building)
        except ValueError:
            return items
        if method == "assumed_square":
            assumptions.append("footprint_geometry=square")
        height = building.basement_height_m
        thickness = building.basement_wall_thickness_m
        if height is not None and thickness is not None:
            wall_value = basement_wall_concrete(
                perimeter,
                height * building.basement_floors,
                thickness,
            )
            items.append(
                self._geometric_item(
                    "basement_wall_concrete",
                    "Bodrum çevre perdesi betonu",
                    wall_value,
                    "m3",
                )
            )
        return items

    def _facade_items(
        self,
        building: BuildingInput,
        assumptions: list[str],
    ) -> list[EstimateItem]:
        if building.footprint_area_m2 is None or building.above_ground_floors is None:
            return []
        perimeter, perimeter_method = footprint_perimeter(building)
        if perimeter_method == "assumed_square":
            assumptions.append("footprint_geometry=square")
        ratio_config = self._registry.modifiers["facade_opening_ratio_defaults"].get(
            building.building_use.value,
            self._registry.modifiers["facade_opening_ratio_defaults"]["unknown"],
        )
        ratio = building.facade_opening_ratio
        if ratio is None:
            ratio = Decimal(str(ratio_config["base"]))
            assumptions.append(f"facade_opening_ratio={ratio}")
        gross, windows, opaque = facade_geometry(
            perimeter_m=perimeter,
            floor_height_m=building.floor_height_m or Decimal("0"),
            floors=building.above_ground_floors,
            opening_ratio=ratio,
        )
        compactness = compactness_ratio(perimeter, building.footprint_area_m2)
        notes = (
            f"footprint_perimeter={perimeter}",
            f"compactness_ratio={compactness}",
            f"compactness_class={compactness_class(compactness)}",
        )
        items = [
            self._geometric_item("gross_facade", "Brüt cephe", gross, "m2", notes),
            self._geometric_item("window_area", "Pencere/doğrama alanı", windows, "m2"),
            self._geometric_item("opaque_facade", "Net opak cephe", opaque, "m2"),
        ]
        if building.facade_opening_ratio is None:
            items.append(
                EstimateItem(
                    code="facade_opening_ratio_assumption",
                    label="Cephe açıklık oranı varsayımı",
                    quantity=QuantityBand(ratio, ratio, ratio, "ratio"),
                    source_class=SourceClass.DESIGN_ASSUMPTION,
                    source_name="Building-use facade opening default",
                    notes=("Supplied facade geometry should replace this assumption.",),
                )
            )
        return items

    def _finish_items(
        self,
        gross_area: Decimal,
        masonry_volume: QuantityBand,
        building: BuildingInput,
    ) -> list[EstimateItem]:
        coefficients = self._registry.empirical["coefficients"]
        items = [
            self._empirical_item(
                "internal_plaster",
                "İç sıva",
                gross_area,
                coefficients["internal_plaster"],
            )
        ]
        finish_bands = self._registry.modifiers["finish_bands"]
        for code, label in (("ceiling", "Tavan"), ("floor_finish", "Döşeme kaplaması")):
            coefficient = finish_bands[code]
            band = QuantityBand(
                Decimal(str(coefficient["low"])),
                Decimal(str(coefficient["base"])),
                Decimal(str(coefficient["high"])),
                "m2_per_gross_m2",
            )
            items.append(
                EstimateItem(
                    code=code,
                    label=label,
                    quantity=self._multiply_band(band, gross_area),
                    source_class=SourceClass.EMPIRICAL_REFERENCE,
                    source_name=str(self._registry.empirical["profile"]["name"]),
                    coefficient=band,
                    notes=("Proje bitiş planı geldiğinde geometrik metrajla değiştirilmelidir.",),
                )
            )
        thickness = building.wall_system.internal_wall_thickness_m
        if thickness is not None:
            area_band = QuantityBand(
                wall_volume_to_area(masonry_volume.low, thickness),
                wall_volume_to_area(masonry_volume.base, thickness),
                wall_volume_to_area(masonry_volume.high, thickness),
                "m2",
            )
            items.append(
                EstimateItem(
                    code="masonry_wall_area",
                    label="Duvar yüzey alanı",
                    quantity=area_band,
                    source_class=SourceClass.HEURISTIC_ESTIMATE,
                    source_name="Estimated wall volume converted with supplied thickness",
                    notes=(f"wall_thickness={thickness}m",),
                )
            )
        return items

    def _empirical_item(
        self,
        code: str,
        label: str,
        gross_area: Decimal,
        configured: dict[str, Any],
    ) -> EstimateItem:
        value = Decimal(str(configured["value"]))
        coefficient = QuantityBand(value, value, value, str(configured["unit"]))
        quantity = QuantityBand(
            gross_area * value,
            gross_area * value,
            gross_area * value,
            "m2",
        )
        return EstimateItem(
            code=code,
            label=label,
            quantity=quantity,
            source_class=SourceClass.EMPIRICAL_REFERENCE,
            source_name=str(configured["source_name"]),
            coefficient=coefficient,
            notes=(str(configured["notes"]), "NOT_CODE_MINIMUM", "NOT_EXACT_PROJECT_QUANTITY"),
        )

    @staticmethod
    def _geometric_item(
        code: str,
        label: str,
        value: Decimal,
        unit: str,
        notes: tuple[str, ...] = (),
    ) -> EstimateItem:
        return EstimateItem(
            code=code,
            label=label,
            quantity=QuantityBand(value, value, value, unit),
            source_class=SourceClass.GEOMETRIC_CALCULATION,
            source_name="Supplied dimensions and deterministic geometry",
            notes=notes,
        )

    @staticmethod
    def _apply_project_quantity_priority(
        items: list[EstimateItem], project_quantities: dict[str, Decimal]
    ) -> list[EstimateItem]:
        result: list[EstimateItem] = []
        for item in items:
            if item.code not in project_quantities:
                result.append(item)
                continue
            exact = project_quantities[item.code]
            result.append(
                replace(
                    item,
                    quantity=QuantityBand(exact, exact, exact, item.quantity.unit),
                    source_class=SourceClass.PROJECT_QUANTITY,
                    source_name="Uploaded/approved project takeoff",
                    notes=("Direct project quantity; estimation coefficient was not used.",),
                )
            )
        return result


def calibration_segment(building: BuildingInput) -> str:
    floors = building.above_ground_floors or 0
    floor_bucket = "1_3" if floors <= 3 else "4_8" if floors <= 8 else "9_plus"
    basement_bucket = "none" if building.basement_floors == 0 else "with_basement"
    span = building.average_span_m
    span_bucket = (
        "unknown"
        if span is None
        else "small"
        if span < Decimal("4")
        else "normal"
        if span <= Decimal("5.5")
        else "large"
        if span <= Decimal("7")
        else "very_large"
    )
    return "|".join(
        (
            building.building_use.value,
            building.structural_system.value,
            floor_bucket,
            basement_bucket,
            span_bucket,
            building.soil_condition.value,
            building.foundation_type.value,
        )
    )
