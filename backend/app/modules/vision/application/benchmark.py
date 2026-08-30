from __future__ import annotations

import json
from collections import Counter, defaultdict
from importlib.resources import files
from typing import Any

from sqlalchemy.orm import Session

from app.core.persistence import utc_now
from app.modules.vision.application.fusion import DetectionFusionEngine
from app.modules.vision.infrastructure.models import (
    VisionBenchmarkRunORM,
    VisionModelVersionORM,
)


class GoldenVisionBenchmark:
    def __init__(self, session: Session, targets: dict[str, float]) -> None:
        self._session = session
        self._targets = targets

    def evaluate(
        self,
        *,
        model_version_id: str,
        dataset_version_id: str | None,
        predictions: list[dict[str, Any]],
        golden_version: str = "v1",
    ) -> VisionBenchmarkRunORM:
        model = self._session.get(VisionModelVersionORM, model_version_id)
        if model is None:
            raise LookupError(model_version_id)
        golden = self._load(golden_version)
        cases = list(golden["cases"])
        by_id = {str(row["id"]): row for row in predictions}
        sheet_cases = [row for row in cases if row["task"] == "SHEET_CLASSIFICATION"]
        detection_cases = [row for row in cases if row["task"] != "SHEET_CLASSIFICATION"]
        sheet_metrics = self._classification(sheet_cases, by_id)
        detection_metrics = self._detection(detection_cases, by_id)
        metrics = {**sheet_metrics, **detection_metrics}
        passed = (
            metrics["sheet_macro_f1"] >= self._targets["sheet_macro_f1"]
            and metrics["detection_map50"] >= self._targets["title_block_map50"]
            and metrics["count_error"] <= self._targets["count_error"]
        )
        row = VisionBenchmarkRunORM(
            model_version_id=model.id,
            dataset_version_id=dataset_version_id,
            golden_set_version=str(golden["version"]),
            metrics_json=metrics,
            comparison_json={},
            passed=passed,
            completed_at=utc_now(),
        )
        self._session.add(row)
        self._session.flush()
        return row

    @staticmethod
    def _classification(
        cases: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        labels = sorted({str(row["class_name"]) for row in cases})
        confusion: dict[str, Counter[str]] = defaultdict(Counter)
        correct = 0
        for row in cases:
            truth = str(row["class_name"])
            predicted = str(predictions.get(str(row["id"]), {}).get("class_name", "MISSING"))
            confusion[truth][predicted] += 1
            correct += truth == predicted
        f1_scores = []
        for label in labels:
            tp = confusion[label][label]
            fp = sum(confusion[other][label] for other in confusion if other != label)
            fn = sum(count for predicted, count in confusion[label].items() if predicted != label)
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            f1_scores.append(
                2 * precision * recall / (precision + recall) if precision + recall else 0
            )
        return {
            "sheet_accuracy": correct / len(cases) if cases else 0,
            "sheet_macro_f1": sum(f1_scores) / len(f1_scores) if f1_scores else 0,
            "confusion_matrix": {key: dict(value) for key, value in confusion.items()},
        }

    @staticmethod
    def _detection(
        cases: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        ious: list[float] = []
        true_positive = 0
        sensitive = {"DOOR", "WINDOW", "COLUMN"}
        truth_counts = Counter(
            str(row["class_name"]) for row in cases if row["class_name"] in sensitive
        )
        prediction_counts: Counter[str] = Counter()
        for row in cases:
            predicted = predictions.get(str(row["id"]))
            if not predicted:
                continue
            prediction_counts[str(predicted.get("class_name"))] += 1
            if predicted.get("class_name") != row["class_name"]:
                continue
            iou = DetectionFusionEngine._iou(list(row["bbox"]), list(predicted.get("bbox", [])))
            ious.append(iou)
            true_positive += iou >= 0.5
        precision = true_positive / len(predictions) if predictions else 0
        recall = true_positive / len(cases) if cases else 0
        count_errors = [
            abs(prediction_counts[name] - count) / count for name, count in truth_counts.items()
        ]
        return {
            "detection_precision": precision,
            "detection_recall": recall,
            "detection_f1": 2 * precision * recall / (precision + recall)
            if precision + recall
            else 0,
            "mean_iou": sum(ious) / len(ious) if ious else 0,
            "detection_map50": true_positive / len(cases) if cases else 0,
            "count_error": sum(count_errors) / len(count_errors) if count_errors else 0,
        }

    @staticmethod
    def _load(version: str) -> dict[str, Any]:
        path = files("app.modules.vision").joinpath(f"golden/{version}.json")
        return json.loads(path.read_text(encoding="utf-8"))
