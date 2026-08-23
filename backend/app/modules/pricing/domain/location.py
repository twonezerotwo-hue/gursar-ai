from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class NormalizedLocation:
    code: str
    display_name: str


class LocationNormalizer(Protocol):
    def normalize(self, raw_value: str | None, default_code: str) -> NormalizedLocation: ...


def location_fallback_chain(code: str) -> list[str]:
    normalized = code.strip("/").upper()
    parts = [part for part in normalized.split("/") if part]
    chain = ["/".join(parts[:index]) for index in range(len(parts), 0, -1)]
    if "GLOBAL" not in chain:
        chain.append("GLOBAL")
    return chain
