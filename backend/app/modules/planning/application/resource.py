from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from itertools import pairwise

from app.modules.planning.application.service import CpmEngine, InvalidScheduleError
from app.modules.planning.domain.models import (
    Activity,
    DependencyType,
    WorkBreakdownStructure,
)
from app.modules.planning.domain.resource import (
    LeveledActivity,
    ResourceCapacity,
    ResourceKind,
    ResourceLevelingResult,
    ResourceOverload,
)


class ResourcePlanningError(ValueError):
    pass


class ResourceLevelingEngine:
    """Deterministic serial scheduling with explicit workforce/equipment capacities."""

    def __init__(self, *, search_step_days: Decimal = Decimal("1"), max_shifts: int = 10_000):
        if search_step_days <= 0 or max_shifts < 1:
            raise ValueError("Resource leveling bounds must be positive")
        self._step = search_step_days
        self._max_shifts = max_shifts
        self._cpm = CpmEngine()

    def level(
        self,
        activities: tuple[Activity, ...],
        capacities: tuple[ResourceCapacity, ...],
        wbs: WorkBreakdownStructure | None = None,
    ) -> ResourceLevelingResult:
        capacity_map = self._capacity_map(capacities)
        calculated = self._cpm.calculate(activities, wbs)
        initial = self.detect_overloads(
            tuple(
                LeveledActivity(item.activity, item.early_start, item.early_finish)
                for item in calculated.activities
            ),
            capacities,
        )
        self._validate_individual_demands(activities, capacity_map)

        placed: dict[str, LeveledActivity] = {}
        shifted: list[str] = []
        for scheduled in calculated.activities:
            activity = scheduled.activity
            earliest = self._dependency_start(activity, placed)
            candidate = max(Decimal("0"), earliest)
            attempts = 0
            while not self._fits(activity, candidate, placed.values(), capacity_map):
                candidate += self._step
                attempts += 1
                if attempts > self._max_shifts:
                    raise ResourcePlanningError("RESOURCE_LEVELING_SEARCH_LIMIT")
            finish = candidate + activity.duration_days
            placed[activity.id] = LeveledActivity(activity, candidate, finish)
            if candidate != scheduled.early_start:
                shifted.append(activity.id)

        result_activities = tuple(placed[item.activity.id] for item in calculated.activities)
        remaining = self.detect_overloads(result_activities, capacities)
        return ResourceLevelingResult(
            activities=result_activities,
            duration_days=max(
                (item.planned_finish for item in result_activities), default=Decimal("0")
            ),
            initial_overloads=initial,
            remaining_overloads=remaining,
            shifted_activity_ids=tuple(shifted),
        )

    def detect_overloads(
        self,
        activities: tuple[LeveledActivity, ...],
        capacities: tuple[ResourceCapacity, ...],
    ) -> tuple[ResourceOverload, ...]:
        capacity_map = self._capacity_map(capacities)
        moments = sorted(
            {moment for item in activities for moment in (item.planned_start, item.planned_finish)}
        )
        overloads: list[ResourceOverload] = []
        for start, finish in pairwise(moments):
            if finish <= start:
                continue
            active = tuple(
                item
                for item in activities
                if item.planned_start < finish and item.planned_finish > start
            )
            demand: dict[tuple[ResourceKind, str], list[tuple[str, int]]] = defaultdict(list)
            for item in active:
                demand[(ResourceKind.WORKFORCE, item.activity.crew_code)].append(
                    (item.activity.id, item.activity.crew_size)
                )
                for code, quantity in item.activity.equipment_requirements:
                    demand[(ResourceKind.EQUIPMENT, code)].append((item.activity.id, quantity))
            for key, values in demand.items():
                capacity = capacity_map.get(key)
                requested = sum(value for _, value in values)
                if capacity is not None and requested > capacity:
                    overloads.append(
                        ResourceOverload(
                            code=key[1],
                            kind=key[0],
                            start_day=start,
                            finish_day=finish,
                            demand=requested,
                            capacity=capacity,
                            activity_ids=tuple(sorted(activity_id for activity_id, _ in values)),
                        )
                    )
        return tuple(overloads)

    @staticmethod
    def _capacity_map(
        capacities: tuple[ResourceCapacity, ...],
    ) -> dict[tuple[ResourceKind, str], int]:
        result: dict[tuple[ResourceKind, str], int] = {}
        for item in capacities:
            if not item.code.strip() or item.capacity <= 0:
                raise ResourcePlanningError("INVALID_RESOURCE_CAPACITY")
            key = (item.kind, item.code)
            if key in result:
                raise ResourcePlanningError("DUPLICATE_RESOURCE_CAPACITY")
            result[key] = item.capacity
        return result

    @staticmethod
    def _validate_individual_demands(
        activities: tuple[Activity, ...],
        capacities: dict[tuple[ResourceKind, str], int],
    ) -> None:
        for activity in activities:
            workforce = capacities.get((ResourceKind.WORKFORCE, activity.crew_code))
            if workforce is not None and activity.crew_size > workforce:
                raise ResourcePlanningError(
                    f"RESOURCE_CAPACITY_INFEASIBLE:{activity.id}:{activity.crew_code}"
                )
            for code, quantity in activity.equipment_requirements:
                equipment = capacities.get((ResourceKind.EQUIPMENT, code))
                if quantity <= 0:
                    raise ResourcePlanningError("INVALID_EQUIPMENT_REQUIREMENT")
                if equipment is not None and quantity > equipment:
                    raise ResourcePlanningError(
                        f"RESOURCE_CAPACITY_INFEASIBLE:{activity.id}:{code}"
                    )

    @staticmethod
    def _dependency_start(
        activity: Activity,
        placed: dict[str, LeveledActivity],
    ) -> Decimal:
        starts = [Decimal("0")]
        for dependency in activity.dependencies:
            predecessor = placed.get(dependency.predecessor_id)
            if predecessor is None:
                raise InvalidScheduleError("Dependency order is not topological")
            lag = dependency.lag_days
            if dependency.relationship == DependencyType.START_START:
                starts.append(predecessor.planned_start + lag)
            elif dependency.relationship == DependencyType.FINISH_FINISH:
                starts.append(predecessor.planned_finish + lag - activity.duration_days)
            elif dependency.relationship == DependencyType.START_FINISH:
                starts.append(predecessor.planned_start + lag - activity.duration_days)
            else:
                starts.append(predecessor.planned_finish + lag)
        return max(starts)

    @staticmethod
    def _fits(
        activity: Activity,
        start: Decimal,
        placed: Iterable[LeveledActivity],
        capacities: dict[tuple[ResourceKind, str], int],
    ) -> bool:
        finish = start + activity.duration_days
        workforce_demand = activity.crew_size
        equipment_demand = dict(activity.equipment_requirements)
        for existing in placed:
            if existing.planned_start >= finish or existing.planned_finish <= start:
                continue
            if existing.activity.crew_code == activity.crew_code:
                workforce_demand += existing.activity.crew_size
            for code, quantity in existing.activity.equipment_requirements:
                if code in equipment_demand:
                    equipment_demand[code] += quantity
        workforce_capacity = capacities.get((ResourceKind.WORKFORCE, activity.crew_code))
        if workforce_capacity is not None and workforce_demand > workforce_capacity:
            return False
        return all(
            capacities.get((ResourceKind.EQUIPMENT, code), quantity) >= quantity
            for code, quantity in equipment_demand.items()
        )
