from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SOURCE_PRIORITY = {
    "VERIFIED_MANUAL": 100,
    "IFC": 90,
    "DXF": 80,
    "VECTOR_PDF": 70,
    "VISION": 50,
    "HEURISTIC": 30,
}


@dataclass(frozen=True, slots=True)
class DetectionCandidate:
    source_id: str
    source_type: str
    class_name: str
    geometry: dict[str, Any]
    confidence: float


@dataclass(frozen=True, slots=True)
class FusedDetection:
    class_name: str
    geometry: dict[str, Any]
    confidence: float
    source_ids: tuple[str, ...]
    source_types: tuple[str, ...]
    agreement: bool
    conflict: bool


class DetectionFusionEngine:
    def __init__(self, *, iou_threshold: float, distance_threshold: float) -> None:
        self._iou_threshold = iou_threshold
        self._distance_threshold = distance_threshold

    def fuse(
        self,
        structured: list[DetectionCandidate],
        vision: list[DetectionCandidate],
    ) -> tuple[list[FusedDetection], list[tuple[DetectionCandidate, DetectionCandidate]]]:
        results: list[FusedDetection] = []
        conflicts: list[tuple[DetectionCandidate, DetectionCandidate]] = []
        matched_vision: set[str] = set()
        for source in structured:
            match = max(
                (candidate for candidate in vision if candidate.source_id not in matched_vision),
                key=lambda candidate: self._match_score(source, candidate),
                default=None,
            )
            score = self._match_score(source, match) if match else 0.0
            if match and score >= self._iou_threshold:
                matched_vision.add(match.source_id)
                agrees = source.class_name == match.class_name
                if not agrees:
                    conflicts.append((source, match))
                winner = self._winner(source, match)
                results.append(
                    FusedDetection(
                        class_name=winner.class_name,
                        geometry=winner.geometry,
                        confidence=(
                            min(0.99, max(source.confidence, match.confidence) + 0.08)
                            if agrees
                            else winner.confidence
                        ),
                        source_ids=(source.source_id, match.source_id),
                        source_types=(source.source_type, match.source_type),
                        agreement=agrees,
                        conflict=not agrees,
                    )
                )
            else:
                results.append(self._single(source))
        for candidate in vision:
            if candidate.source_id not in matched_vision:
                results.append(self._single(candidate))
        return results, conflicts

    def _match_score(self, first: DetectionCandidate, second: DetectionCandidate | None) -> float:
        if second is None:
            return 0.0
        iou = self._iou(self._bbox(first.geometry), self._bbox(second.geometry))
        if iou:
            return iou
        a = self._center(self._bbox(first.geometry))
        b = self._center(self._bbox(second.geometry))
        distance = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        return self._iou_threshold if distance <= self._distance_threshold else 0.0

    @staticmethod
    def _winner(first: DetectionCandidate, second: DetectionCandidate) -> DetectionCandidate:
        return max(
            (first, second),
            key=lambda row: (SOURCE_PRIORITY.get(row.source_type, 0), row.confidence),
        )

    @staticmethod
    def _single(candidate: DetectionCandidate) -> FusedDetection:
        return FusedDetection(
            class_name=candidate.class_name,
            geometry=candidate.geometry,
            confidence=candidate.confidence,
            source_ids=(candidate.source_id,),
            source_types=(candidate.source_type,),
            agreement=False,
            conflict=False,
        )

    @staticmethod
    def _bbox(geometry: dict[str, Any]) -> list[float]:
        bbox = geometry.get("bbox")
        if bbox and len(bbox) == 4:
            return [float(value) for value in bbox]
        points = geometry.get("points", [])
        if points:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            return [min(xs), min(ys), max(xs), max(ys)]
        return [0.0, 0.0, 0.0, 0.0]

    @staticmethod
    def _center(bbox: list[float]) -> tuple[float, float]:
        return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    @staticmethod
    def _iou(first: list[float], second: list[float]) -> float:
        x0, y0 = max(first[0], second[0]), max(first[1], second[1])
        x1, y1 = min(first[2], second[2]), min(first[3], second[3])
        intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        area_a = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
        area_b = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
        union = area_a + area_b - intersection
        return intersection / union if union else 0.0
