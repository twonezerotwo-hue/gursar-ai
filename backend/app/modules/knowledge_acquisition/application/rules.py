from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import or_, select

from app.modules.knowledge_acquisition.application.processing import normalize_text
from app.modules.knowledge_acquisition.domain.models import (
    KnowledgeSourceClass,
    KnowledgeStatus,
    RuleApplicabilityContext,
    RuleApplicabilityResult,
    RuleApplicabilityStatus,
    RuleEvidence,
)
from app.modules.knowledge_acquisition.infrastructure.config import KnowledgeConfig
from app.modules.knowledge_acquisition.infrastructure.models import (
    KnowledgeConflictORM,
    KnowledgeDocumentVersionORM,
    KnowledgeRuleORM,
    KnowledgeSourceORM,
)
from app.modules.knowledge_acquisition.infrastructure.repository import KnowledgeRepository

RULE_APPLICABILITY_VERSION = "KNOWLEDGE_RULE_APPLICABILITY_V1"


class RuleApplicabilityEngine:
    """Deterministically evaluates active rules without claiming legal certainty."""

    def __init__(self, repository: KnowledgeRepository, config: KnowledgeConfig) -> None:
        self._repository = repository
        self._config = config

    def evaluate(self, context: RuleApplicabilityContext) -> RuleApplicabilityResult:
        target_date = context.as_of or date.today()
        settings = self._config.brain["applicability"]
        rows = self._repository.session.execute(
            select(KnowledgeRuleORM, KnowledgeDocumentVersionORM, KnowledgeSourceORM)
            .join(
                KnowledgeDocumentVersionORM,
                KnowledgeDocumentVersionORM.id == KnowledgeRuleORM.document_version_id,
            )
            .join(
                KnowledgeSourceORM, KnowledgeSourceORM.id == KnowledgeDocumentVersionORM.source_id
            )
            .where(
                KnowledgeRuleORM.status == KnowledgeStatus.ACTIVE.value,
                KnowledgeDocumentVersionORM.status == KnowledgeStatus.ACTIVE.value,
                or_(
                    KnowledgeDocumentVersionORM.effective_from.is_(None),
                    KnowledgeDocumentVersionORM.effective_from <= target_date,
                ),
                or_(
                    KnowledgeDocumentVersionORM.effective_until.is_(None),
                    KnowledgeDocumentVersionORM.effective_until >= target_date,
                ),
            )
            .order_by(KnowledgeSourceORM.quality_score.desc(), KnowledgeRuleORM.id)
            .limit(int(settings["maximum_rule_candidates"]))
        ).all()
        query_terms = set(normalize_text(context.query).split())
        requested_topic = normalize_text(context.topic or "")
        ranked: list[
            tuple[int, int, int, KnowledgeRuleORM, KnowledgeDocumentVersionORM, KnowledgeSourceORM]
        ] = []
        semantic_seen = False
        for rule, version, source in rows:
            searchable = normalize_text(
                " ".join(
                    filter(None, (rule.topic, rule.condition, rule.requirement, rule.article or ""))
                )
            )
            overlap = len(query_terms & set(searchable.split()))
            topic_matches = bool(requested_topic and normalize_text(rule.topic) == requested_topic)
            if not topic_matches and overlap < int(settings["minimum_lexical_overlap"]):
                continue
            semantic_seen = True
            taxonomy = source.taxonomy_json or {}
            jurisdiction = str(taxonomy.get("jurisdiction", "UNKNOWN")).upper()
            requested_jurisdiction = context.jurisdiction.upper()
            if jurisdiction not in {requested_jurisdiction, "INTERNATIONAL", "GLOBAL"}:
                continue
            if rule.building_type.casefold() not in {
                "all",
                "*",
                context.building_type.casefold(),
            }:
                continue
            authority_rank = self._authority_rank(source.source_class)
            ranked.append(
                (
                    int(topic_matches),
                    overlap,
                    authority_rank,
                    rule,
                    version,
                    source,
                )
            )
        ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], -item[5].quality_score))
        evidence = tuple(self._evidence(*item[3:]) for item in ranked)
        conflict_ids = self._conflicts(ranked)
        warnings: list[str] = []
        selected = evidence[0] if evidence else None
        if not evidence:
            status = (
                RuleApplicabilityStatus.NOT_APPLICABLE
                if semantic_seen
                else RuleApplicabilityStatus.UNVERIFIED
            )
            warnings.append("NO_APPLICABLE_VERIFIED_RULE")
        elif conflict_ids:
            status = RuleApplicabilityStatus.CONFLICT
            warnings.append("CONFLICT_REQUIRES_EXPERT_REVIEW")
        elif selected is not None and (
            selected.source_quality_score < int(settings["minimum_source_quality"])
            or selected.source_class
            in {KnowledgeSourceClass.HEURISTIC, KnowledgeSourceClass.AI_INFERENCE}
        ):
            status = RuleApplicabilityStatus.UNVERIFIED
            warnings.append("SOURCE_BELOW_VERIFICATION_THRESHOLD")
        else:
            status = RuleApplicabilityStatus.APPLICABLE
        if selected is not None and selected.jurisdiction in {"INTERNATIONAL", "GLOBAL"}:
            warnings.append("INTERNATIONAL_REFERENCE_REQUIRES_LOCAL_APPLICABILITY_CHECK")
        checksum = self._checksum(context, status, evidence, conflict_ids)
        return RuleApplicabilityResult(
            status=status,
            context=context,
            selected=selected,
            evidence=evidence,
            conflict_ids=conflict_ids,
            warnings=tuple(warnings),
            engine_version=RULE_APPLICABILITY_VERSION,
            evaluation_checksum=checksum,
            requires_professional_review=status
            in {
                RuleApplicabilityStatus.CONFLICT,
                RuleApplicabilityStatus.UNVERIFIED,
                RuleApplicabilityStatus.NOT_APPLICABLE,
            },
        )

    def _authority_rank(self, source_class: str) -> int:
        return int(self._config.brain["source_hierarchy"].get(source_class, 0))

    def _evidence(
        self,
        rule: KnowledgeRuleORM,
        version: KnowledgeDocumentVersionORM,
        source: KnowledgeSourceORM,
    ) -> RuleEvidence:
        taxonomy = source.taxonomy_json or {}
        return RuleEvidence(
            rule_id=rule.id,
            document_version_id=version.id,
            document_name=source.name,
            source_url=version.source_url,
            source_class=KnowledgeSourceClass(source.source_class),
            authority=source.authority,
            article=rule.article,
            requirement=rule.requirement,
            condition=rule.condition,
            version=version.version_label,
            jurisdiction=str(taxonomy.get("jurisdiction", "UNKNOWN")).upper(),
            building_type=rule.building_type,
            effective_from=version.effective_from,
            effective_until=version.effective_until,
            source_quality_score=source.quality_score,
            authority_rank=self._authority_rank(source.source_class),
        )

    def _conflicts(
        self,
        ranked: list[
            tuple[int, int, int, KnowledgeRuleORM, KnowledgeDocumentVersionORM, KnowledgeSourceORM]
        ],
    ) -> tuple[str, ...]:
        rule_ids = {item[3].id for item in ranked}
        if not rule_ids:
            return ()
        stored = list(
            self._repository.session.scalars(
                select(KnowledgeConflictORM).where(
                    KnowledgeConflictORM.status == "OPEN",
                    KnowledgeConflictORM.left_rule_id.in_(rule_ids),
                    KnowledgeConflictORM.right_rule_id.in_(rule_ids),
                )
            )
        )
        conflict_ids = {row.id for row in stored}
        by_scope: dict[tuple[str, str, str | None], list[KnowledgeRuleORM]] = defaultdict(list)
        for item in ranked:
            rule = item[3]
            by_scope[(rule.topic, rule.building_type, rule.article)].append(rule)
        for scope, rules in by_scope.items():
            requirements = {normalize_text(rule.requirement) for rule in rules}
            if len(requirements) <= 1:
                continue
            digest = hashlib.sha256(
                json.dumps(
                    {"scope": scope, "rules": sorted(rule.id for rule in rules)},
                    ensure_ascii=True,
                ).encode()
            ).hexdigest()[:20]
            conflict_ids.add(f"LIVE-{digest}")
        return tuple(sorted(conflict_ids))

    @staticmethod
    def _checksum(
        context: RuleApplicabilityContext,
        status: RuleApplicabilityStatus,
        evidence: tuple[RuleEvidence, ...],
        conflict_ids: tuple[str, ...],
    ) -> str:
        payload: dict[str, Any] = {
            "context": {
                "query": context.query,
                "jurisdiction": context.jurisdiction,
                "building_type": context.building_type,
                "topic": context.topic,
                "as_of": context.as_of.isoformat() if context.as_of else None,
            },
            "status": status.value,
            "rules": [item.rule_id for item in evidence],
            "conflicts": conflict_ids,
            "engine_version": RULE_APPLICABILITY_VERSION,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
