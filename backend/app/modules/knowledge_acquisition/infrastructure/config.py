from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.modules.knowledge_acquisition.domain.models import (
    KnowledgeSourceClass,
    KnowledgeSourceDefinition,
    RedistributionPolicy,
    Taxonomy,
)


class KnowledgeConfig:
    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or Path(__file__).resolve().parents[1] / "config"
        self.trusted = self._load("trusted_sources.yaml")
        self.pipeline = self._load("pipeline.yaml")
        self.catalog = self._load("source_catalog.yaml")

    def sources(self) -> tuple[KnowledgeSourceDefinition, ...]:
        return tuple(self._source(item) for item in self.catalog["sources"])

    def _source(self, item: dict[str, Any]) -> KnowledgeSourceDefinition:
        taxonomy = Taxonomy(
            discipline=str(item["discipline"]),
            subdiscipline=str(item["subdiscipline"]),
            topic=str(item["topic"]),
            document_type=str(item["document_type"]),
            source_class=KnowledgeSourceClass(item["source_class"]),
            building_type=str(item.get("building_type", "all")),
            jurisdiction=str(item.get("jurisdiction", "TR")),
        )
        return KnowledgeSourceDefinition(
            key=str(item["key"]),
            name=str(item["name"]),
            authority=str(item["authority"]),
            source_url=str(item["source_url"]),
            taxonomy=taxonomy,
            quality_score=int(item["quality_score"]),
            license=str(item["license"]),
            copyright_status=str(item["copyright_status"]),
            redistribution_policy=RedistributionPolicy(item["redistribution_policy"]),
            published_date=self._date(item.get("published_date")),
            effective_from=self._date(item.get("effective_from")),
            effective_until=self._date(item.get("effective_until")),
            version_label=str(item["version_label"]) if item.get("version_label") else None,
            discover_links=bool(item.get("discover_links", False)),
            metadata=dict(item.get("metadata", {})),
        )

    @staticmethod
    def _date(value: object) -> date | None:
        if value is None:
            return None
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    def _load(self, filename: str) -> dict[str, Any]:
        with (self.config_dir / filename).open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid knowledge config: {filename}")
        return payload
