from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class KnowledgeSourceClass(StrEnum):
    LAW = "LAW"
    REGULATION = "REGULATION"
    MANDATORY_STANDARD = "MANDATORY_STANDARD"
    REFERENCE_STANDARD = "REFERENCE_STANDARD"
    TECHNICAL_SPECIFICATION = "TECHNICAL_SPECIFICATION"
    OFFICIAL_UNIT_PRICE = "OFFICIAL_UNIT_PRICE"
    OFFICIAL_GUIDANCE = "OFFICIAL_GUIDANCE"
    ACADEMIC_REFERENCE = "ACADEMIC_REFERENCE"
    PROFESSIONAL_REFERENCE = "PROFESSIONAL_REFERENCE"
    MANUFACTURER_DATA = "MANUFACTURER_DATA"
    VERIFIED_PROJECT = "VERIFIED_PROJECT"
    SITE_EXPERIENCE = "SITE_EXPERIENCE"
    HEURISTIC = "HEURISTIC"
    AI_INFERENCE = "AI_INFERENCE"


class KnowledgeCollection(StrEnum):
    REGULATIONS = "REGULATIONS"
    STANDARDS_METADATA = "STANDARDS_METADATA"
    TECHNICAL_SPECIFICATIONS = "TECHNICAL_SPECIFICATIONS"
    YFK_UNIT_PRICES = "YFK_UNIT_PRICES"
    ACADEMIC = "ACADEMIC"
    COMPANY_KNOWLEDGE = "COMPANY_KNOWLEDGE"
    PROJECT_KNOWLEDGE = "PROJECT_KNOWLEDGE"


class KnowledgeStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    DOWNLOADED = "DOWNLOADED"
    PARSED = "PARSED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"
    METADATA_ONLY = "METADATA_ONLY"


class RuleApplicabilityStatus(StrEnum):
    APPLICABLE = "APPLICABLE"
    UNVERIFIED = "UNVERIFIED"
    CONFLICT = "CONFLICT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RedistributionPolicy(StrEnum):
    ALLOWED = "ALLOWED"
    METADATA_ONLY = "METADATA_ONLY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Taxonomy:
    discipline: str
    subdiscipline: str
    topic: str
    document_type: str
    source_class: KnowledgeSourceClass
    building_type: str = "all"
    jurisdiction: str = "TR"


@dataclass(frozen=True, slots=True)
class KnowledgeSourceDefinition:
    key: str
    name: str
    authority: str
    source_url: str
    taxonomy: Taxonomy
    quality_score: int
    license: str
    copyright_status: str
    redistribution_policy: RedistributionPolicy
    published_date: date | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    version_label: str | None = None
    discover_links: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    text: str
    content_type: str
    parser_name: str
    ocr_derived: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SemanticChunk:
    index: int
    hierarchy_type: str
    heading: str | None
    content: str
    normalized_content: str
    embedding: tuple[float, ...]
    taxonomy: Taxonomy


@dataclass(frozen=True, slots=True)
class KnowledgeSearchHit:
    document_id: str
    document_name: str
    article: str | None
    version: str
    effective_from: date | None
    source_url: str
    source_class: KnowledgeSourceClass
    source_quality_score: int
    source_confidence: int
    data_confidence: int
    content: str
    score: float
    collection: str = "TECHNICAL_SPECIFICATIONS"
    jurisdiction: str = "TR"
    verification_status: str = "ACTIVE"


@dataclass(frozen=True, slots=True)
class RuleApplicabilityContext:
    query: str
    jurisdiction: str = "TR"
    building_type: str = "all"
    topic: str | None = None
    as_of: date | None = None


@dataclass(frozen=True, slots=True)
class RuleEvidence:
    rule_id: str
    document_version_id: str
    document_name: str
    source_url: str
    source_class: KnowledgeSourceClass
    authority: str
    article: str | None
    requirement: str
    condition: str
    version: str
    jurisdiction: str
    building_type: str
    effective_from: date | None
    effective_until: date | None
    source_quality_score: int
    authority_rank: int


@dataclass(frozen=True, slots=True)
class RuleApplicabilityResult:
    status: RuleApplicabilityStatus
    context: RuleApplicabilityContext
    selected: RuleEvidence | None
    evidence: tuple[RuleEvidence, ...]
    conflict_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    engine_version: str
    evaluation_checksum: str
    requires_professional_review: bool


@dataclass(slots=True)
class AcquisitionReport:
    run_id: str
    discovered: int = 0
    downloaded: int = 0
    ingested: int = 0
    active: int = 0
    metadata_only: int = 0
    unchanged: int = 0
    duplicates: int = 0
    rejected: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    chunks_created: int = 0
    rules_created: int = 0
    conflicts_created: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
