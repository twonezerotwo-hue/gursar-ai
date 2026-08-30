from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


class EstimationRegistry:
    def __init__(self, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir or Path(__file__).resolve().parents[1] / "config"
        self.empirical = self._load("empirical_reference.yaml")
        self.buildings = self._load("building_profiles.yaml")
        self.modifiers = self._load("structural_modifiers.yaml")
        self.learning = self._load("learning_thresholds.yaml")
        self.confidence = self._load("confidence_rules.yaml")

    def _load(self, filename: str) -> dict[str, Any]:
        path = self._config_dir / filename
        with path.open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid estimation registry: {path}")
        return payload

    def building_profile(self, building_use: str) -> dict[str, Any]:
        profiles: dict[str, dict[str, Any]] = self.buildings["profiles"]
        profile = deepcopy(profiles.get(building_use, profiles["unknown"]))
        inherited = profile.pop("inherit", None)
        if inherited:
            return deepcopy(profiles[inherited])
        return profile

    def learning_threshold(self, count: int) -> dict[str, Any]:
        for threshold in self.learning["thresholds"]:
            maximum = threshold["max_count"]
            if count >= threshold["min_count"] and (maximum is None or count <= maximum):
                return dict(threshold)
        raise ValueError(f"No learning threshold configured for sample count {count}")
