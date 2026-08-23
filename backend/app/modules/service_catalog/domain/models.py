from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    code: str
    labels: dict[str, str]
    version: int
    is_active: bool

    def label(self, locale: str) -> str:
        return self.labels.get(locale) or self.labels.get("en") or self.code
