from __future__ import annotations

from collections import defaultdict, deque
from decimal import ROUND_CEILING, Decimal
from typing import ClassVar

from app.modules.planning.domain.models import (
    Activity,
    ActivityDependency,
    DependencyType,
    PlanningPriority,
    PlanningRequest,
    ProductivityRecord,
    ScheduledActivity,
    ScheduleResult,
)


class InvalidScheduleError(ValueError):
    pass


class CpmEngine:
    """Deterministic CPM supporting FS, SS, FF and SF relationships."""

    def calculate(self, activities: tuple[Activity, ...]) -> ScheduleResult:
        if not activities:
            return ScheduleResult((), Decimal("0"), 0, ())
        by_id = {activity.id: activity for activity in activities}
        if len(by_id) != len(activities):
            raise InvalidScheduleError("Activity ids must be unique")
        successors: dict[str, list[tuple[str, ActivityDependency]]] = defaultdict(list)
        indegree = {activity.id: 0 for activity in activities}
        for activity in activities:
            for dependency in activity.dependencies:
                if dependency.predecessor_id not in by_id:
                    raise InvalidScheduleError("Dependency references an unknown activity")
                successors[dependency.predecessor_id].append((activity.id, dependency))
                indegree[activity.id] += 1
        queue = deque(activity_id for activity_id, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while queue:
            activity_id = queue.popleft()
            order.append(activity_id)
            for successor_id, _ in successors[activity_id]:
                indegree[successor_id] -= 1
                if indegree[successor_id] == 0:
                    queue.append(successor_id)
        if len(order) != len(activities):
            raise InvalidScheduleError("Activity dependencies contain a cycle")

        early_start: dict[str, Decimal] = {}
        early_finish: dict[str, Decimal] = {}
        for activity_id in order:
            activity = by_id[activity_id]
            starts = [Decimal("0")]
            for dependency in activity.dependencies:
                predecessor = dependency.predecessor_id
                lag = dependency.lag_days
                starts.append(
                    self._forward_constraint(
                        dependency.relationship,
                        early_start[predecessor],
                        early_finish[predecessor],
                        activity.duration_days,
                        lag,
                    )
                )
            early_start[activity_id] = max(starts)
            early_finish[activity_id] = early_start[activity_id] + activity.duration_days
        project_finish = max(early_finish.values())

        late_start = {
            activity_id: project_finish - by_id[activity_id].duration_days for activity_id in order
        }
        late_finish = {activity_id: project_finish for activity_id in order}
        for activity_id in reversed(order):
            activity = by_id[activity_id]
            constraints = []
            for successor_id, dependency in successors[activity_id]:
                constraints.append(
                    self._backward_constraint(
                        dependency.relationship,
                        late_start[successor_id],
                        late_finish[successor_id],
                        activity.duration_days,
                        dependency.lag_days,
                    )
                )
            if constraints:
                late_start[activity_id] = min(constraints)
                late_finish[activity_id] = late_start[activity_id] + activity.duration_days

        scheduled = tuple(
            ScheduledActivity(
                activity=by_id[activity_id],
                early_start=early_start[activity_id],
                early_finish=early_finish[activity_id],
                late_start=late_start[activity_id],
                late_finish=late_finish[activity_id],
                total_float=late_start[activity_id] - early_start[activity_id],
                critical=abs(late_start[activity_id] - early_start[activity_id]) < Decimal("0.001"),
            )
            for activity_id in order
        )
        return ScheduleResult(
            activities=scheduled,
            duration_days=project_finish,
            peak_crew=self._peak_crew(scheduled),
            critical_path=tuple(item.activity.id for item in scheduled if item.critical),
        )

    @staticmethod
    def _forward_constraint(
        relationship: DependencyType,
        predecessor_start: Decimal,
        predecessor_finish: Decimal,
        duration: Decimal,
        lag: Decimal,
    ) -> Decimal:
        if relationship == DependencyType.START_START:
            return predecessor_start + lag
        if relationship == DependencyType.FINISH_FINISH:
            return predecessor_finish + lag - duration
        if relationship == DependencyType.START_FINISH:
            return predecessor_start + lag - duration
        return predecessor_finish + lag

    @staticmethod
    def _backward_constraint(
        relationship: DependencyType,
        successor_start: Decimal,
        successor_finish: Decimal,
        duration: Decimal,
        lag: Decimal,
    ) -> Decimal:
        if relationship == DependencyType.START_START:
            return successor_start - lag
        if relationship == DependencyType.FINISH_FINISH:
            return successor_finish - lag - duration
        if relationship == DependencyType.START_FINISH:
            return successor_finish - lag
        return successor_start - lag - duration

    @staticmethod
    def _peak_crew(activities: tuple[ScheduledActivity, ...]) -> int:
        finish = int(max((item.early_finish for item in activities), default=Decimal("0"))) + 1
        return max(
            (
                sum(
                    item.activity.crew_size
                    for item in activities
                    if item.early_start <= day < item.early_finish
                )
                for day in range(finish)
            ),
            default=0,
        )


class ConstructionSchedulingService:
    """Residential V1 plan; quantities are planning drivers, never final takeoff."""

    PRODUCTIVITY: ClassVar[dict[str, ProductivityRecord]] = {
        "structure.floor": ProductivityRecord(
            "structure.floor",
            "floor",
            Decimal("0.142857"),
            18,
            "RESIDENTIAL",
            "normal reinforced-concrete floor cycle",
            False,
            "GURSAR planning baseline V1",
        ),
        "masonry": ProductivityRecord(
            "masonry",
            "m2",
            Decimal("55"),
            8,
            "RESIDENTIAL",
            "standard block wall",
            False,
            "GURSAR planning baseline V1",
        ),
        "mep.rough_in": ProductivityRecord(
            "mep.rough_in",
            "gross_m2",
            Decimal("120"),
            10,
            "RESIDENTIAL",
            "coordinated rough-in",
            False,
            "GURSAR planning baseline V1",
        ),
        "finishes": ProductivityRecord(
            "finishes",
            "gross_m2",
            Decimal("75"),
            20,
            "RESIDENTIAL",
            "standard residential finish",
            False,
            "GURSAR planning baseline V1",
        ),
        "facade": ProductivityRecord(
            "facade",
            "facade_m2",
            Decimal("60"),
            10,
            "RESIDENTIAL",
            "standard facade",
            False,
            "GURSAR planning baseline V1",
        ),
    }

    def __init__(
        self,
        cpm: CpmEngine | None = None,
        productivity: dict[str, ProductivityRecord] | None = None,
    ) -> None:
        self._cpm = cpm or CpmEngine()
        self._productivity = productivity or self.PRODUCTIVITY

    def create(self, request: PlanningRequest) -> ScheduleResult:
        multiplier = {
            PlanningPriority.FAST: Decimal("0.82"),
            PlanningPriority.BALANCED: Decimal("1"),
            PlanningPriority.ECONOMIC: Decimal("1.18"),
        }[request.priority]
        crew_multiplier = {
            PlanningPriority.FAST: Decimal("1.25"),
            PlanningPriority.BALANCED: Decimal("1"),
            PlanningPriority.ECONOMIC: Decimal("0.85"),
        }[request.priority]
        masonry_area = request.gross_area_m2 * Decimal("0.65")
        facade_area = (request.gross_area_m2 / Decimal(request.floor_count)) * Decimal("1.30")

        def days(quantity: Decimal, code: str) -> Decimal:
            productivity = self._productivity[code].output_per_crew_day
            return max(
                Decimal("1"),
                (quantity / productivity * multiplier).quantize(
                    Decimal("1"), rounding=ROUND_CEILING
                ),
            )

        def crew(code: str) -> int:
            baseline = Decimal(self._productivity[code].crew_size) * crew_multiplier
            planned = int(baseline.quantize(Decimal("1"), rounding=ROUND_CEILING))
            return min(planned, request.available_crew) if request.available_crew else planned

        structure_days = max(
            Decimal("1"),
            (Decimal(request.floor_count) * Decimal("7") * multiplier).quantize(
                Decimal("1"), rounding=ROUND_CEILING
            ),
        )
        activities = (
            Activity(
                "A100",
                "Taşıyıcı sistem",
                "1.1",
                structure_days,
                "structure.floor",
                crew("structure.floor"),
                element_types=("COLUMN", "BEAM", "SLAB", "SHEAR_WALL"),
            ),
            Activity(
                "A200",
                "Duvar imalatları",
                "2.1",
                days(masonry_area, "masonry"),
                "masonry",
                crew("masonry"),
                (ActivityDependency("A100", DependencyType.START_START, Decimal("14")),),
                ("WALL",),
            ),
            Activity(
                "A300",
                "MEP kaba tesisat",
                "3.1",
                days(request.gross_area_m2, "mep.rough_in"),
                "mep.rough_in",
                crew("mep.rough_in"),
                (ActivityDependency("A200", DependencyType.START_START, Decimal("7")),),
            ),
            Activity(
                "A400",
                "Cephe ve dış kabuk",
                "4.1",
                days(facade_area, "facade"),
                "facade",
                crew("facade"),
                (ActivityDependency("A100"),),
                ("FACADE", "WINDOW"),
            ),
            Activity(
                "A500",
                "İnce işler",
                "5.1",
                days(request.gross_area_m2, "finishes"),
                "finishes",
                crew("finishes"),
                (ActivityDependency("A300"),),
            ),
            Activity(
                "A600",
                "Test, devreye alma ve teslim",
                "6.1",
                Decimal("10"),
                "commissioning",
                6,
                (ActivityDependency("A400"), ActivityDependency("A500")),
            ),
        )
        calculated = self._cpm.calculate(activities)
        assumptions = (
            "Klasik betonarme konut ve normal çalışma koşulları esas alındı.",
            "Süreler yaklaşık planlama değeridir; onaylı proje metrajı değildir.",
            "Temelin tamamlandığı ve ekiplerin kesintisiz çalışabildiği varsayıldı.",
        )
        return ScheduleResult(
            activities=calculated.activities,
            duration_days=calculated.duration_days,
            peak_crew=calculated.peak_crew,
            critical_path=calculated.critical_path,
            assumptions=assumptions,
        )
