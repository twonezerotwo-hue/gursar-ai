from __future__ import annotations

from typing import Any, Never, Protocol

import cv2
import numpy as np
from PIL import Image


class VisionInferenceProvider(Protocol):
    provider_name: str
    model_name: str
    model_version: str

    def classify_sheet(self, image: Image.Image) -> dict[str, Any]: ...

    def detect_title_block(self, image: Image.Image) -> list[dict[str, Any]]: ...

    def detect_view_regions(self, image: Image.Image) -> list[dict[str, Any]]: ...

    def detect_elements(self, image: Image.Image, classes: set[str]) -> list[dict[str, Any]]: ...

    def segment_elements(self, image: Image.Image, classes: set[str]) -> list[dict[str, Any]]: ...


class HeuristicVisionProvider:
    """Free local baseline; outputs review-only semantic candidates, never quantities."""

    provider_name = "heuristic-local"
    model_name = "opencv-layout-baseline"
    model_version = "VISION_INTELLIGENCE_V1"

    def classify_sheet(self, image: Image.Image) -> dict[str, Any]:
        del image
        return {"class_name": "UNKNOWN", "confidence": 0.0}

    def detect_title_block(self, image: Image.Image) -> list[dict[str, Any]]:
        gray = np.asarray(image.convert("L"))
        height, width = gray.shape
        region = gray[int(height * 0.6) :, int(width * 0.55) :]
        ink_ratio = float(np.mean(region < 210))
        if ink_ratio < 0.01:
            return []
        return [
            {
                "class_name": "TITLE_BLOCK",
                "geometry": {"bbox": [0.55, 0.6, 1.0, 1.0]},
                "geometry_type": "BBOX",
                "confidence": min(0.85, 0.45 + ink_ratio),
            }
        ]

    def detect_view_regions(self, image: Image.Image) -> list[dict[str, Any]]:
        del image
        return [
            {
                "class_name": "PLAN_REGION",
                "geometry": {"bbox": [0.0, 0.0, 1.0, 0.9]},
                "geometry_type": "BBOX",
                "confidence": 0.55,
            }
        ]

    def detect_elements(self, image: Image.Image, classes: set[str]) -> list[dict[str, Any]]:
        if "ROOM" not in classes:
            return []
        gray = np.asarray(image.convert("L"))
        binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
        contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        height, width = gray.shape
        results: list[dict[str, Any]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            ratio = (w * h) / (width * height)
            if 0.02 <= ratio <= 0.45 and w > 40 and h > 40:
                results.append(
                    {
                        "class_name": "ROOM",
                        "geometry": {
                            "bbox": [x / width, y / height, (x + w) / width, (y + h) / height]
                        },
                        "geometry_type": "BBOX",
                        "confidence": 0.42,
                    }
                )
            if len(results) >= 100:
                break
        return results

    def segment_elements(self, image: Image.Image, classes: set[str]) -> list[dict[str, Any]]:
        del image, classes
        return []


class LocalVisionProvider:
    provider_name = "local-model-adapter"

    def __init__(self, *, model_name: str, model_version: str, device: str) -> None:
        self.model_name = model_name
        self.model_version = model_version
        self.device = device

    def _unavailable(self) -> Never:
        raise RuntimeError("LOCAL_VISION_MODEL_NOT_CONFIGURED")

    def classify_sheet(self, image: Image.Image) -> dict[str, Any]:
        del image
        self._unavailable()

    def detect_title_block(self, image: Image.Image) -> list[dict[str, Any]]:
        del image
        self._unavailable()

    def detect_view_regions(self, image: Image.Image) -> list[dict[str, Any]]:
        del image
        self._unavailable()

    def detect_elements(self, image: Image.Image, classes: set[str]) -> list[dict[str, Any]]:
        del image, classes
        self._unavailable()

    def segment_elements(self, image: Image.Image, classes: set[str]) -> list[dict[str, Any]]:
        del image, classes
        self._unavailable()


class RemoteVisionProvider(LocalVisionProvider):
    provider_name = "remote-model-adapter"
