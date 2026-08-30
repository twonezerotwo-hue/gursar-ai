from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from enum import Enum
from typing import Any

from app.core.persistence import append_audit, append_outbox
from app.modules.estimation.application.engine import EstimationEngine
from app.modules.estimation.domain.models import BuildingInput, EstimationResult
from app.modules.estimation.infrastructure.registry import EstimationRegistry
from app.modules.estimation.infrastructure.repository import EstimationRepository


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def serialize_result(result: EstimationResult) -> dict[str, Any]:
    return json_safe(asdict(result))


class EstimationService:
    def __init__(self, repository: EstimationRepository, registry: EstimationRegistry) -> None:
        self._repository = repository
        self._registry = registry

    def estimate(
        self,
        building: BuildingInput,
        *,
        project_quantities: dict[str, Decimal] | None = None,
        conversation_id: str | None = None,
    ) -> EstimationResult:
        active = self._repository.active_calibration()
        versions = None
        coefficients = None
        if active is not None:
            coefficients = active.coefficients
            versions = {
                "estimation_model_version": active.estimation_model_version,
                "coefficient_registry_version": active.coefficient_registry_version,
                "training_dataset_version": active.training_dataset_version,
            }
        result = EstimationEngine(
            self._registry,
            calibrated_coefficients=coefficients,
            model_versions=versions,
        ).estimate(building, project_quantities=project_quantities)
        input_payload = json_safe(asdict(building))
        result_payload = serialize_result(result)
        self._repository.save_run(
            request_id=result.request_id,
            conversation_id=conversation_id,
            input_payload=input_payload,
            result_payload=result_payload,
            assumptions=list(result.assumptions),
            estimation_model_version=result.estimation_model_version,
            coefficient_registry_version=result.coefficient_registry_version,
            training_dataset_version=result.training_dataset_version,
        )
        append_audit(
            self._repository.session,
            action="ESTIMATION_GENERATED",
            aggregate_id=result.request_id,
            detail={
                "gross_area_m2": str(result.gross_area_m2),
                "building_use": building.building_use.value,
                "confidence": result.confidence.label.value,
                "versions": versions
                or {
                    "estimation_model_version": result.estimation_model_version,
                    "coefficient_registry_version": result.coefficient_registry_version,
                    "training_dataset_version": result.training_dataset_version,
                },
            },
        )
        append_outbox(
            self._repository.session,
            event_type="estimation.generated.v1",
            aggregate_id=result.request_id,
            payload={"request_id": result.request_id, "conversation_id": conversation_id},
        )
        return result
