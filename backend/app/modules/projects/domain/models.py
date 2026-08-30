from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    name: str
    locale: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime
