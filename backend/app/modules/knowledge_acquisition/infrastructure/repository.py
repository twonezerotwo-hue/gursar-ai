from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.persistence import utc_now
from app.modules.knowledge_acquisition.domain.models import (
    KnowledgeCollection,
    KnowledgeSourceClass,
    KnowledgeSourceDefinition,
    KnowledgeStatus,
    SemanticChunk,
)
from app.modules.knowledge_acquisition.infrastructure.models import (
    KnowledgeAcquisitionRunORM,
    KnowledgeChangeORM,
    KnowledgeChunkORM,
    KnowledgeConflictORM,
    KnowledgeDocumentVersionORM,
    KnowledgeFormulaORM,
    KnowledgeRuleORM,
    KnowledgeSourceORM,
)


def _collection_for(source_class: KnowledgeSourceClass) -> KnowledgeCollection:
    if source_class in {KnowledgeSourceClass.LAW, KnowledgeSourceClass.REGULATION}:
        return KnowledgeCollection.REGULATIONS
    if source_class in {
        KnowledgeSourceClass.MANDATORY_STANDARD,
        KnowledgeSourceClass.REFERENCE_STANDARD,
    }:
        return KnowledgeCollection.STANDARDS_METADATA
    if source_class is KnowledgeSourceClass.OFFICIAL_UNIT_PRICE:
        return KnowledgeCollection.YFK_UNIT_PRICES
    if source_class is KnowledgeSourceClass.ACADEMIC_REFERENCE:
        return KnowledgeCollection.ACADEMIC
    if source_class in {
        KnowledgeSourceClass.VERIFIED_PROJECT,
        KnowledgeSourceClass.SITE_EXPERIENCE,
    }:
        return KnowledgeCollection.PROJECT_KNOWLEDGE
    if source_class in {
        KnowledgeSourceClass.MANUFACTURER_DATA,
        KnowledgeSourceClass.PROFESSIONAL_REFERENCE,
    }:
        return KnowledgeCollection.COMPANY_KNOWLEDGE
    return KnowledgeCollection.TECHNICAL_SPECIFICATIONS


class KnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start_run(self) -> KnowledgeAcquisitionRunORM:
        row = KnowledgeAcquisitionRunORM(status="RUNNING", counters_json={}, failures_json=[])
        self.session.add(row)
        self.session.flush()
        return row

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        counters: dict[str, Any],
        failures: list[dict[str, str]],
    ) -> None:
        self.session.execute(
            update(KnowledgeAcquisitionRunORM)
            .where(KnowledgeAcquisitionRunORM.id == run_id)
            .values(
                status=status,
                counters_json=counters,
                failures_json=failures,
                completed_at=utc_now(),
            )
        )

    def upsert_source(self, definition: KnowledgeSourceDefinition) -> KnowledgeSourceORM:
        row = self.session.scalar(
            select(KnowledgeSourceORM).where(KnowledgeSourceORM.source_key == definition.key)
        )
        taxonomy = {
            "discipline": definition.taxonomy.discipline,
            "subdiscipline": definition.taxonomy.subdiscipline,
            "topic": definition.taxonomy.topic,
            "document_type": definition.taxonomy.document_type,
            "source_class": definition.taxonomy.source_class.value,
            "building_type": definition.taxonomy.building_type,
            "jurisdiction": definition.taxonomy.jurisdiction,
        }
        if row is None:
            row = KnowledgeSourceORM(
                source_key=definition.key,
                name=definition.name,
                authority=definition.authority,
                source_url=definition.source_url,
                source_class=definition.taxonomy.source_class.value,
                collection=_collection_for(definition.taxonomy.source_class).value,
                quality_score=definition.quality_score,
                license=definition.license,
                copyright_status=definition.copyright_status,
                redistribution_policy=definition.redistribution_policy.value,
                taxonomy_json=taxonomy,
                metadata_json=definition.metadata,
            )
            self.session.add(row)
            self.session.flush()
            return row
        row.name = definition.name
        row.authority = definition.authority
        row.source_url = definition.source_url
        row.source_class = definition.taxonomy.source_class.value
        row.collection = _collection_for(definition.taxonomy.source_class).value
        row.quality_score = definition.quality_score
        row.license = definition.license
        row.copyright_status = definition.copyright_status
        row.redistribution_policy = definition.redistribution_policy.value
        row.taxonomy_json = taxonomy
        row.metadata_json = definition.metadata
        row.updated_at = utc_now()
        return row

    def latest_version(self, source_id: str) -> KnowledgeDocumentVersionORM | None:
        return self.session.scalar(
            select(KnowledgeDocumentVersionORM)
            .where(KnowledgeDocumentVersionORM.source_id == source_id)
            .order_by(KnowledgeDocumentVersionORM.version_number.desc())
            .limit(1)
        )

    def active_version(
        self, source_id: str, as_of: date | None = None
    ) -> KnowledgeDocumentVersionORM | None:
        target = as_of or datetime.now(UTC).date()
        return self.session.scalar(
            select(KnowledgeDocumentVersionORM)
            .where(
                KnowledgeDocumentVersionORM.source_id == source_id,
                KnowledgeDocumentVersionORM.status == KnowledgeStatus.ACTIVE.value,
                or_(
                    KnowledgeDocumentVersionORM.effective_from.is_(None),
                    KnowledgeDocumentVersionORM.effective_from <= target,
                ),
                or_(
                    KnowledgeDocumentVersionORM.effective_until.is_(None),
                    KnowledgeDocumentVersionORM.effective_until >= target,
                ),
            )
            .order_by(KnowledgeDocumentVersionORM.version_number.desc())
            .limit(1)
        )

    def find_by_checksum(self, checksum: str) -> KnowledgeDocumentVersionORM | None:
        return self.session.scalar(
            select(KnowledgeDocumentVersionORM)
            .where(KnowledgeDocumentVersionORM.checksum_sha256 == checksum)
            .limit(1)
        )

    def create_version(
        self,
        *,
        source: KnowledgeSourceORM,
        definition: KnowledgeSourceDefinition,
        checksum: str,
        content_type: str,
        parser_name: str,
        parsed_text: str,
        ocr_derived: bool,
        chunks: Sequence[SemanticChunk],
        rules: Sequence[dict[str, Any]],
        status: KnowledgeStatus,
        etag: str | None = None,
        last_modified: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeDocumentVersionORM:
        previous = self.latest_version(source.id)
        version_number = 1 if previous is None else previous.version_number + 1
        row = KnowledgeDocumentVersionORM(
            source_id=source.id,
            version_number=version_number,
            version_label=definition.version_label or str(version_number),
            status=status.value,
            published_date=definition.published_date,
            effective_from=definition.effective_from,
            effective_until=definition.effective_until,
            source_url=definition.source_url,
            checksum_sha256=checksum,
            content_type=content_type,
            parser_name=parser_name,
            parsed_text=parsed_text,
            char_count=len(parsed_text),
            ocr_derived=ocr_derived,
            etag=etag,
            last_modified=last_modified,
            metadata_json=metadata or {},
        )
        self.session.add(row)
        self.session.flush()
        self.session.add_all(
            [
                KnowledgeChunkORM(
                    document_version_id=row.id,
                    chunk_index=chunk.index,
                    hierarchy_type=chunk.hierarchy_type,
                    heading=chunk.heading,
                    content=chunk.content,
                    normalized_content=chunk.normalized_content,
                    embedding_json=list(chunk.embedding),
                    taxonomy_json={
                        "discipline": chunk.taxonomy.discipline,
                        "subdiscipline": chunk.taxonomy.subdiscipline,
                        "topic": chunk.taxonomy.topic,
                        "document_type": chunk.taxonomy.document_type,
                        "source_class": chunk.taxonomy.source_class.value,
                        "building_type": chunk.taxonomy.building_type,
                        "jurisdiction": chunk.taxonomy.jurisdiction,
                    },
                    checksum_sha256=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                )
                for chunk in chunks
            ]
        )
        versioned_rules = []
        for rule in rules:
            payload = dict(rule)
            payload["rule_id"] = f"{payload['rule_id']}-v{version_number}"
            versioned_rules.append(KnowledgeRuleORM(document_version_id=row.id, **payload))
        self.session.add_all(versioned_rules)
        if previous is not None and previous.parsed_text != parsed_text:
            from app.modules.knowledge_acquisition.application.processing import section_diff

            self.session.add_all(
                [
                    KnowledgeChangeORM(
                        source_id=source.id,
                        old_version_id=previous.id,
                        new_version_id=row.id,
                        **change,
                    )
                    for change in section_diff(previous.parsed_text, parsed_text)
                ]
            )
        return row

    def approve(self, version_id: str) -> KnowledgeDocumentVersionORM:
        self.session.flush()
        row = self.session.get(KnowledgeDocumentVersionORM, version_id)
        if row is None:
            raise LookupError(version_id)
        if row.status not in {
            KnowledgeStatus.VALIDATED.value,
            KnowledgeStatus.APPROVED.value,
            KnowledgeStatus.METADATA_ONLY.value,
        }:
            raise ValueError(f"Version cannot be approved from status {row.status}")
        if row.status == KnowledgeStatus.METADATA_ONLY.value:
            return row
        source_versions = self.session.scalars(
            select(KnowledgeDocumentVersionORM).where(
                KnowledgeDocumentVersionORM.source_id == row.source_id
            )
        ).all()
        version_ids = [version.id for version in source_versions]
        for version in source_versions:
            if version.status == KnowledgeStatus.ACTIVE.value:
                version.status = KnowledgeStatus.SUPERSEDED.value
        all_rules = self.session.scalars(
            select(KnowledgeRuleORM).where(KnowledgeRuleORM.document_version_id.in_(version_ids))
        ).all()
        for rule in all_rules:
            rule.status = (
                KnowledgeStatus.ACTIVE.value
                if rule.document_version_id == row.id
                else KnowledgeStatus.SUPERSEDED.value
            )
        row.status = KnowledgeStatus.ACTIVE.value
        row.activated_at = utc_now()
        self.session.flush()
        return row

    def bridge_to_legal_knowledge(self, version_id: str) -> None:
        from app.modules.legal_knowledge.infrastructure.repository import LegalKnowledgeRepository

        row = self.session.get(KnowledgeDocumentVersionORM, version_id)
        if row is None or row.status != KnowledgeStatus.ACTIVE.value:
            return
        source = self.session.get(KnowledgeSourceORM, row.source_id)
        if source is None or source.source_class not in {
            "LAW",
            "REGULATION",
            "MANDATORY_STANDARD",
            "TECHNICAL_SPECIFICATION",
            "OFFICIAL_GUIDANCE",
        }:
            return
        chunks = self.session.scalars(
            select(KnowledgeChunkORM)
            .where(KnowledgeChunkORM.document_version_id == row.id)
            .order_by(KnowledgeChunkORM.chunk_index)
        ).all()
        LegalKnowledgeRepository(self.session).replace_document(
            source_key=f"knowledge:{source.source_key}:{row.version_number}",
            title=source.name,
            category=source.taxonomy_json.get("document_type", "knowledge"),
            authority=source.authority,
            source_url=row.source_url,
            published_on=row.published_date,
            official_gazette_number=row.version_label
            if source.source_class in {"LAW", "REGULATION"}
            else None,
            checksum_sha256=row.checksum_sha256,
            content_type=row.content_type,
            text=row.parsed_text,
            chunks=[(chunk.heading, chunk.content) for chunk in chunks],
            metadata={
                **source.taxonomy_json,
                "knowledge_document_version_id": row.id,
                "effective_from": row.effective_from.isoformat() if row.effective_from else None,
                "source_quality_score": source.quality_score,
            },
        )

    def detect_conflicts(self, version_id: str) -> int:
        source_class = self.session.scalar(
            select(KnowledgeSourceORM.source_class)
            .join(
                KnowledgeDocumentVersionORM,
                KnowledgeDocumentVersionORM.source_id == KnowledgeSourceORM.id,
            )
            .where(KnowledgeDocumentVersionORM.id == version_id)
        )
        normative_classes = {
            "LAW",
            "REGULATION",
            "MANDATORY_STANDARD",
            "TECHNICAL_SPECIFICATION",
        }
        if source_class not in normative_classes:
            return 0
        current_rules = self.session.scalars(
            select(KnowledgeRuleORM).where(KnowledgeRuleORM.document_version_id == version_id)
        ).all()
        created = 0
        for rule in current_rules:
            candidates = self.session.scalars(
                select(KnowledgeRuleORM)
                .where(
                    KnowledgeRuleORM.id != rule.id,
                    KnowledgeRuleORM.topic == rule.topic,
                    KnowledgeRuleORM.building_type == rule.building_type,
                    KnowledgeRuleORM.article == rule.article,
                    KnowledgeRuleORM.document_version_id != version_id,
                    KnowledgeRuleORM.status == KnowledgeStatus.ACTIVE.value,
                )
                .limit(100)
            ).all()
            for candidate in candidates:
                if candidate.requirement == rule.requirement:
                    continue
                conflict_key = "|".join(sorted((rule.rule_id, candidate.rule_id)))
                exists = self.session.scalar(
                    select(KnowledgeConflictORM.id).where(
                        KnowledgeConflictORM.conflict_key == conflict_key
                    )
                )
                if exists:
                    continue
                self.session.add(
                    KnowledgeConflictORM(
                        conflict_key=conflict_key,
                        left_rule_id=candidate.id,
                        right_rule_id=rule.id,
                        detail_json={
                            "type": "SOURCE_CONFLICT",
                            "topic": rule.topic,
                            "left": candidate.requirement,
                            "right": rule.requirement,
                        },
                    )
                )
                created += 1
        self.session.flush()
        return created

    def seed_formulas(self) -> None:
        formulas = (
            (
                "gross-area-v1",
                "Brüt alan",
                "quantity",
                "A_gross = A_footprint × n_floors",
                [{"name": "A_footprint"}, {"name": "n_floors"}],
                {"A_gross": "m2", "A_footprint": "m2"},
            ),
            (
                "raft-concrete-v1",
                "Radye beton hacmi",
                "structural",
                "V = A_foundation × t_raft",
                [{"name": "A_foundation"}, {"name": "t_raft"}],
                {"V": "m3", "A_foundation": "m2", "t_raft": "m"},
            ),
            (
                "facade-area-v1",
                "Brüt cephe alanı",
                "architecture",
                "A_facade = perimeter × floor_height × floors",
                [{"name": "perimeter"}, {"name": "floor_height"}, {"name": "floors"}],
                {"A_facade": "m2", "perimeter": "m", "floor_height": "m"},
            ),
            (
                "wall-area-v1",
                "Duvar hacminden alan",
                "quantity",
                "A_wall = V_wall / t_wall",
                [{"name": "V_wall"}, {"name": "t_wall"}],
                {"A_wall": "m2", "V_wall": "m3", "t_wall": "m"},
            ),
        )
        for formula_id, name, discipline, formula, variables, units in formulas:
            if self.session.scalar(
                select(KnowledgeFormulaORM.id).where(KnowledgeFormulaORM.formula_id == formula_id)
            ):
                continue
            self.session.add(
                KnowledgeFormulaORM(
                    formula_id=formula_id,
                    name=name,
                    discipline=discipline,
                    formula=formula,
                    variables_json=variables,
                    units_json=units,
                    conditions_json=["Deterministic calculation", "Validated units required"],
                    source="GURSAR deterministic estimation engine",
                    source_class="HEURISTIC",
                    version="1.0",
                )
            )

    def dashboard(self) -> dict[str, Any]:
        active_filter = KnowledgeDocumentVersionORM.status == KnowledgeStatus.ACTIVE.value

        def scalar_count(statement: Any) -> int:
            return int(self.session.scalar(statement) or 0)

        last_update = self.session.scalar(
            select(func.max(KnowledgeDocumentVersionORM.retrieved_at))
        )
        return {
            "total_documents": scalar_count(
                select(func.count()).select_from(KnowledgeDocumentVersionORM)
            ),
            "active_documents": scalar_count(
                select(func.count()).select_from(KnowledgeDocumentVersionORM).where(active_filter)
            ),
            "active_regulations": scalar_count(
                select(func.count())
                .select_from(KnowledgeDocumentVersionORM)
                .join(KnowledgeSourceORM)
                .where(active_filter, KnowledgeSourceORM.source_class == "REGULATION")
            ),
            "standards": scalar_count(
                select(func.count())
                .select_from(KnowledgeSourceORM)
                .where(
                    KnowledgeSourceORM.source_class.in_(
                        ("MANDATORY_STANDARD", "REFERENCE_STANDARD")
                    )
                )
            ),
            "technical_specifications": scalar_count(
                select(func.count())
                .select_from(KnowledgeSourceORM)
                .where(KnowledgeSourceORM.source_class == "TECHNICAL_SPECIFICATION")
            ),
            "yfk_records": scalar_count(
                select(func.count())
                .select_from(KnowledgeSourceORM)
                .where(KnowledgeSourceORM.source_class == "OFFICIAL_UNIT_PRICE")
            ),
            "academic_sources": scalar_count(
                select(func.count())
                .select_from(KnowledgeSourceORM)
                .where(KnowledgeSourceORM.source_class == "ACADEMIC_REFERENCE")
            ),
            "failed_downloads": sum(
                len(row.failures_json)
                for row in self.session.scalars(select(KnowledgeAcquisitionRunORM))
            ),
            "duplicate_files": sum(
                int(row.counters_json.get("duplicates", 0))
                for row in self.session.scalars(select(KnowledgeAcquisitionRunORM))
            ),
            "source_conflicts": scalar_count(
                select(func.count())
                .select_from(KnowledgeConflictORM)
                .where(KnowledgeConflictORM.status == "OPEN")
            ),
            "unverified_sources": scalar_count(
                select(func.count())
                .select_from(KnowledgeDocumentVersionORM)
                .where(
                    KnowledgeDocumentVersionORM.status.in_(
                        ("DISCOVERED", "DOWNLOADED", "PARSED", "VALIDATED")
                    )
                )
            ),
            "metadata_only": scalar_count(
                select(func.count())
                .select_from(KnowledgeDocumentVersionORM)
                .where(KnowledgeDocumentVersionORM.status == "METADATA_ONLY")
            ),
            "chunks": scalar_count(select(func.count()).select_from(KnowledgeChunkORM)),
            "structured_rules": scalar_count(select(func.count()).select_from(KnowledgeRuleORM)),
            "formulas": scalar_count(select(func.count()).select_from(KnowledgeFormulaORM)),
            "last_update": last_update.isoformat() if last_update else None,
        }
