from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from typing import ClassVar

from app.modules.planning.domain.models import (
    CrewPlan,
    PlanningPriority,
    ProductivityRecord,
    ProductivitySourceTier,
)


class ProductivityPlanningError(ValueError):
    """Raised when an auditable crew plan cannot be calculated."""


class CrewPlanningService:
    """Deterministic quantity-to-crew calculator with explicit data provenance."""

    _SCENARIO_FACTORS: ClassVar[dict[PlanningPriority, Decimal]] = {
        PlanningPriority.FAST: Decimal("0.75"),
        PlanningPriority.BALANCED: Decimal("1"),
        PlanningPriority.ECONOMIC: Decimal("1.25"),
    }

    def plan(
        self,
        record: ProductivityRecord,
        *,
        quantity: Decimal,
        unit: str,
        target_duration_days: Decimal,
        priority: PlanningPriority = PlanningPriority.BALANCED,
        max_workers: int | None = None,
    ) -> CrewPlan:
        if quantity <= 0 or target_duration_days <= 0:
            raise ProductivityPlanningError("QUANTITY_AND_DURATION_MUST_BE_POSITIVE")
        if record.output_per_crew_day <= 0 or record.crew_size <= 0:
            raise ProductivityPlanningError("INVALID_PRODUCTIVITY_RECORD")
        if unit.strip().casefold() != record.unit.strip().casefold():
            raise ProductivityPlanningError("PRODUCTIVITY_UNIT_MISMATCH")

        crew_days = quantity / record.output_per_crew_day
        desired_crews = self._ceil_int(crew_days / target_duration_days)
        crews = max(1, desired_crews)
        risks: list[str] = []

        if max_workers is not None:
            if max_workers <= 0:
                raise ProductivityPlanningError("MAX_WORKERS_MUST_BE_POSITIVE")
            available_crews = max_workers // record.crew_size
            if available_crews < 1:
                raise ProductivityPlanningError("INSUFFICIENT_WORKERS_FOR_ONE_CREW")
            if available_crews < crews:
                crews = available_crews
                risks.append(
                    "Mevcut personel sınırı hedef süre için gereken ekip sayısını karşılamıyor."
                )

        planned_duration = (crew_days / Decimal(crews)).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
        if planned_duration > target_duration_days:
            risks.append("Hedef sürenin aşılması bekleniyor.")

        assumptions = [
            "Üretkenlik, vardiya boyunca ekip başına sabit kabul edildi.",
            "Hava, lojistik ve iş duruşları ayrıca modellenmedi.",
        ]
        if record.source_tier == ProductivitySourceTier.HEURISTIC:
            assumptions.append(
                "Üretkenlik HEURISTIC kaynaktır; doğrulanmış saha gerçekleşmesi değildir."
            )

        composition = record.crew_composition or (("worker", record.crew_size),)
        return CrewPlan(
            priority=priority,
            service_code=record.service_code,
            quantity=quantity,
            unit=record.unit,
            target_duration_days=target_duration_days,
            crew_days=crew_days.quantize(Decimal("0.01"), rounding=ROUND_CEILING),
            required_crews=crews,
            required_workers=crews * record.crew_size,
            planned_duration_days=planned_duration,
            daily_output=record.output_per_crew_day * Decimal(crews),
            crew_composition=composition,
            total_composition=tuple((role, count * crews) for role, count in composition),
            source_tier=record.source_tier,
            source=record.source,
            confidence=record.confidence,
            verified=record.verified,
            risks=tuple(dict.fromkeys(risks)),
            assumptions=tuple(assumptions),
        )

    def scenarios(
        self,
        record: ProductivityRecord,
        *,
        quantity: Decimal,
        unit: str,
        balanced_duration_days: Decimal,
        max_workers: int | None = None,
    ) -> tuple[CrewPlan, ...]:
        return tuple(
            self.plan(
                record,
                quantity=quantity,
                unit=unit,
                target_duration_days=max(
                    Decimal("1"),
                    (balanced_duration_days * self._SCENARIO_FACTORS[priority]).quantize(
                        Decimal("1"), rounding=ROUND_CEILING
                    ),
                ),
                priority=priority,
                max_workers=max_workers,
            )
            for priority in (
                PlanningPriority.FAST,
                PlanningPriority.BALANCED,
                PlanningPriority.ECONOMIC,
            )
        )

    @staticmethod
    def _ceil_int(value: Decimal) -> int:
        return int(value.quantize(Decimal("1"), rounding=ROUND_CEILING))
