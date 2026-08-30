from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.persistence import new_id
from app.modules.estimation.application.engine import calibration_segment
from app.modules.estimation.domain.models import (
    BuildingInput,
    CalibrationVersion,
    CompletedProjectRecord,
    SeismicDemand,
)
from app.modules.estimation.domain.statistics import (
    descriptive_statistics,
    error_dashboard,
    error_percent,
    outlier_indices,
)
from app.modules.estimation.infrastructure.registry import EstimationRegistry
from app.modules.estimation.infrastructure.repository import EstimationRepository

CALIBRATABLE_QUANTITIES = (
    "concrete",
    "reinforcement",
    "formwork",
    "masonry",
    "facade",
    "plaster",
    "paint",
)


class CalibrationEngine:
    def __init__(self, repository: EstimationRepository, registry: EstimationRegistry) -> None:
        self._repository = repository
        self._registry = registry

    def propose(self) -> CalibrationVersion:
        records = self._repository.list_completed_projects(verified_only=True)
        grouped: dict[tuple[str, str], list[tuple[CompletedProjectRecord, Decimal]]] = defaultdict(
            list
        )
        record_flags: dict[str, dict[str, bool]] = defaultdict(dict)
        for record in records:
            segment = self._segment(record)
            for code in CALIBRATABLE_QUANTITIES:
                actual = record.actual_quantities.get(code)
                if actual is not None and record.gross_area_m2 > 0:
                    grouped[(segment, code)].append((record, actual / record.gross_area_m2))

        coefficients: dict[str, dict[str, Any]] = defaultdict(dict)
        statistics: dict[str, dict[str, Any]] = defaultdict(dict)
        outlier_config = self._registry.learning["outlier"]
        for (segment, code), samples in grouped.items():
            values = [value for _, value in samples]
            rejected = outlier_indices(
                values,
                mad_z_threshold=Decimal(str(outlier_config["mad_z_threshold"])),
                iqr_multiplier=Decimal(str(outlier_config["iqr_multiplier"])),
                minimum_sample=int(outlier_config["minimum_sample"]),
            )
            clean = [value for index, value in enumerate(values) if index not in rejected]
            for index, (record, _) in enumerate(samples):
                record_flags[record.id][code] = index in rejected
            if not clean:
                continue
            summary = descriptive_statistics(clean)
            statistics[segment][code] = {
                **{key: str(value) for key, value in summary.items()},
                "outlier_record_ids": [samples[index][0].id for index in sorted(rejected)],
                "raw_count": len(samples),
            }
            threshold = self._registry.learning_threshold(len(clean))
            if Decimal(str(threshold["learned_weight"])) == 0:
                continue
            reference = self._reference_coefficient(samples[0][0], code)
            if reference is None:
                continue
            learned = Decimal(str(summary["median"]))
            reference_weight = Decimal(str(threshold["reference_weight"]))
            learned_weight = Decimal(str(threshold["learned_weight"]))
            coefficients[segment][code] = {
                "reference": str(reference),
                "learned_median": str(learned),
                "reference_weight": str(reference_weight),
                "learned_weight": str(learned_weight),
                "blended": str(reference * reference_weight + learned * learned_weight),
                "sample_count": len(clean),
                "action": str(threshold["action"]),
            }

        for record_id, flags in record_flags.items():
            self._repository.update_outlier_flags(record_id, flags)

        version_number = self._next_patch_version(
            [item.estimation_model_version for item in self._repository.list_calibrations()],
            str(self._registry.learning["model_version"]),
        )
        coefficient_version = self._next_patch_version(
            [item.coefficient_registry_version for item in self._repository.list_calibrations()],
            str(self._registry.learning["coefficient_registry_version"]),
        )
        calibration = CalibrationVersion(
            id=new_id(),
            estimation_model_version=version_number,
            coefficient_registry_version=coefficient_version,
            training_dataset_version=f"dataset_{datetime.now(UTC):%Y_%m_%d}_{len(records)}",
            status="PROPOSED",
            coefficients=dict(coefficients),
            statistics=dict(statistics),
            calibration_timestamp=datetime.now(UTC),
        )
        return self._repository.create_calibration(calibration)

    def metrics(self) -> dict[str, Any]:
        grouped: dict[str, list[Decimal]] = defaultdict(list)
        for record in self._repository.list_completed_projects(verified_only=True):
            for code, actual in record.actual_quantities.items():
                estimated = record.estimated_quantities.get(code)
                if estimated is not None and actual != 0:
                    grouped[code].append(error_percent(actual, estimated))
        return {
            code: {key: str(value) for key, value in error_dashboard(errors).items()}
            for code, errors in grouped.items()
        }

    def _segment(self, record: CompletedProjectRecord) -> str:
        return calibration_segment(
            BuildingInput(
                footprint_area_m2=record.footprint_area_m2,
                gross_area_m2=record.gross_area_m2,
                above_ground_floors=record.floor_count,
                basement_floors=record.basement_count,
                building_use=record.project_type,
                structural_system=record.structural_system,
                structural_intensity=record.structural_intensity,
                average_span_m=record.average_span_m,
                foundation_type=record.foundation_type,
                soil_condition=record.soil_condition,
                seismic_demand=SeismicDemand.UNKNOWN,
            )
        )

    def _reference_coefficient(self, record: CompletedProjectRecord, code: str) -> Decimal | None:
        profile = self._registry.building_profile(record.project_type.value)
        configured = profile.get(code)
        return Decimal(str(configured["base"])) if configured is not None else None

    @staticmethod
    def _next_patch_version(existing: list[str], base: str) -> str:
        versions = [base, *existing]
        latest = versions[-1]
        prefix, separator, patch = latest.rpartition(".")
        if not separator or not patch.isdigit():
            return f"{base}.1"
        return f"{prefix}.{int(patch) + 1}"
