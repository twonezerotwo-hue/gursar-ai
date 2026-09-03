from __future__ import annotations

from decimal import Decimal

from app.modules.architecture_intelligence.domain.models import (
    ArchitectureAnalysis,
    ArchitectureInput,
    ArchitectureIssue,
    ArchitectureMetric,
)


class ArchitectureAnalyzer:
    """Produces traceable ratios without inventing missing geometry."""

    def analyze(self, data: ArchitectureInput) -> ArchitectureAnalysis:
        if data.gross_area_m2 <= 0:
            raise ValueError("gross_area_m2 must be positive")
        metrics: list[ArchitectureMetric] = [
            ArchitectureMetric("GROSS_AREA", data.gross_area_m2, "m2", "PROJECT_CONTEXT")
        ]
        assumptions: list[str] = []
        issues: list[ArchitectureIssue] = []

        self._ratio(metrics, "NET_GROSS_RATIO", data.net_area_m2, data.gross_area_m2)
        self._ratio(metrics, "CIRCULATION_RATIO", data.circulation_area_m2, data.gross_area_m2)
        self._ratio(metrics, "CORE_RATIO", data.core_area_m2, data.gross_area_m2)
        self._ratio(metrics, "FACADE_OPENING_RATIO", data.opening_area_m2, data.facade_area_m2)

        if data.net_area_m2 is None:
            assumptions.append("Net alan verilmediği için net/brüt verimlilik hesaplanmadı.")
        elif data.net_area_m2 > data.gross_area_m2:
            issues.append(
                ArchitectureIssue(
                    "NET_EXCEEDS_GROSS", "HIGH", "Net alan brüt alandan büyük olamaz.", 1
                )
            )
        if data.circulation_area_m2 is not None:
            ratio = data.circulation_area_m2 / data.gross_area_m2
            if ratio > Decimal("0.25"):
                issues.append(
                    ArchitectureIssue(
                        "HIGH_CIRCULATION_RATIO",
                        "MEDIUM",
                        "Dolaşım alanı oranı %25'in üzerinde.",
                        1,
                    )
                )
        for code, count, message in (
            ("UNREACHABLE_ROOM", data.unreachable_room_count, "Erişilemeyen mahal bulundu."),
            (
                "INVALID_ROOM_BOUNDARY",
                data.invalid_room_boundary_count,
                "Geçersiz mahal sınırı bulundu.",
            ),
            ("DOOR_COLLISION", data.door_collision_count, "Kapı çakışması bulundu."),
        ):
            if count:
                issues.append(ArchitectureIssue(code, "HIGH", message, count))
        if data.room_count is not None:
            metrics.append(
                ArchitectureMetric("ROOM_COUNT", Decimal(data.room_count), "count", "PROJECT_GRAPH")
            )
        return ArchitectureAnalysis(tuple(metrics), tuple(issues), tuple(assumptions))

    @staticmethod
    def _ratio(
        metrics: list[ArchitectureMetric],
        code: str,
        numerator: Decimal | None,
        denominator: Decimal | None,
    ) -> None:
        if numerator is None or denominator is None or denominator <= 0:
            return
        metrics.append(
            ArchitectureMetric(
                code,
                (numerator / denominator).quantize(Decimal("0.0001")),
                "ratio",
                "PROJECT_GRAPH",
            )
        )
