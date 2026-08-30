from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import func, inspect, or_, select, text

from app.modules.knowledge_acquisition.application.processing import (
    cosine_similarity,
    hashed_embedding,
    normalize_text,
)
from app.modules.knowledge_acquisition.domain.models import (
    KnowledgeSearchHit,
    KnowledgeSourceClass,
    KnowledgeStatus,
)
from app.modules.knowledge_acquisition.infrastructure.config import KnowledgeConfig
from app.modules.knowledge_acquisition.infrastructure.models import (
    KnowledgeChunkORM,
    KnowledgeDocumentVersionORM,
    KnowledgeSourceORM,
)
from app.modules.knowledge_acquisition.infrastructure.repository import KnowledgeRepository
from app.modules.legal_knowledge.domain.models import LegalContextChunk
from app.modules.legal_knowledge.infrastructure.repository import LegalKnowledgeRepository


class HybridKnowledgeSearch:
    def __init__(self, repository: KnowledgeRepository, config: KnowledgeConfig) -> None:
        self._repository = repository
        self._config = config

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        filters: dict[str, str] | None = None,
        as_of: date | None = None,
    ) -> list[KnowledgeSearchHit]:
        normalized = normalize_text(query)
        terms = [term for term in normalized.split() if len(term) >= 2]
        if not terms:
            return []
        target_date = as_of or date.today()
        base_filters = (
            select(KnowledgeChunkORM, KnowledgeDocumentVersionORM, KnowledgeSourceORM)
            .join(
                KnowledgeDocumentVersionORM,
                KnowledgeDocumentVersionORM.id == KnowledgeChunkORM.document_version_id,
            )
            .join(
                KnowledgeSourceORM, KnowledgeSourceORM.id == KnowledgeDocumentVersionORM.source_id
            )
            .where(
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
        )
        filters = filters or {}
        for key in (
            "collection",
            "discipline",
            "subdiscipline",
            "topic",
            "document_type",
            "source_class",
            "building_type",
            "jurisdiction",
        ):
            if value := filters.get(key):
                if key == "source_class":
                    base_filters = base_filters.where(KnowledgeSourceORM.source_class == value)
                elif key == "collection":
                    base_filters = base_filters.where(KnowledgeSourceORM.collection == value)
                else:
                    base_filters = base_filters.where(
                        KnowledgeChunkORM.taxonomy_json[key].as_string() == value
                    )
        candidate_limit = min(500, max(100, limit * 40))
        connection = self._repository.session.connection()
        dialect = connection.dialect.name
        if dialect == "postgresql":
            lexical_filter = func.to_tsvector("simple", KnowledgeChunkORM.normalized_content).op(
                "@@"
            )(func.plainto_tsquery("simple", " ".join(terms[:8])))
        elif dialect == "sqlite" and inspect(connection).has_table("knowledge_chunks_fts"):
            fts_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:8])
            fts_ids = (
                self._repository.session.execute(
                    text(
                        "SELECT chunk_id FROM knowledge_chunks_fts "
                        "WHERE normalized_content MATCH :query LIMIT :limit"
                    ),
                    {"query": fts_query, "limit": candidate_limit * 4},
                )
                .scalars()
                .all()
            )
            lexical_filter = KnowledgeChunkORM.id.in_(fts_ids)
        else:
            lexical_filter = or_(
                *(
                    KnowledgeChunkORM.normalized_content.contains(term, autoescape=True)
                    for term in terms[:8]
                )
            )
        candidate_ids = self._repository.session.scalars(
            base_filters.with_only_columns(KnowledgeChunkORM.id)
            .where(lexical_filter)
            .order_by(KnowledgeSourceORM.quality_score.desc(), KnowledgeChunkORM.id)
            .limit(candidate_limit)
        ).all()
        if not candidate_ids:
            # OCR and legacy encodings can defeat exact lexical matching. This
            # fallback still selects only IDs and remains strictly bounded.
            candidate_ids = self._repository.session.scalars(
                base_filters.with_only_columns(KnowledgeChunkORM.id)
                .order_by(KnowledgeSourceORM.quality_score.desc(), KnowledgeChunkORM.id)
                .limit(candidate_limit)
            ).all()
        if not candidate_ids:
            return []
        rows = self._repository.session.execute(
            base_filters.where(KnowledgeChunkORM.id.in_(candidate_ids))
        ).all()
        if not rows:
            return []
        documents = [row[0].normalized_content.split() for row in rows]
        document_count = len(documents)
        average_length = sum(map(len, documents)) / max(document_count, 1)
        document_frequency = {
            term: sum(1 for document in documents if term in set(document)) for term in terms
        }
        config = self._config.pipeline
        dimensions = int(config["embedding"]["dimensions"])
        query_vector = hashed_embedding(query, dimensions)
        retrieval = config["retrieval"]
        scored: list[
            tuple[float, KnowledgeChunkORM, KnowledgeDocumentVersionORM, KnowledgeSourceORM]
        ] = []
        for tokens, (chunk, version, source) in zip(documents, rows, strict=True):
            lexical = self._bm25(
                terms,
                tokens,
                document_count=document_count,
                document_frequency=document_frequency,
                average_length=average_length,
                k1=float(retrieval["k1"]),
                b=float(retrieval["b"]),
            )
            vector = max(0.0, cosine_similarity(chunk.embedding_json, query_vector))
            authority = source.quality_score / 100
            score = (
                lexical * float(retrieval["lexical_weight"])
                + vector * float(retrieval["vector_weight"])
                + authority * float(retrieval["authority_weight"])
            )
            if score > 0:
                scored.append((score, chunk, version, source))
        scored.sort(key=lambda item: (-item[0], -item[3].quality_score, item[2].version_number))
        return [
            KnowledgeSearchHit(
                document_id=version.id,
                document_name=source.name,
                article=chunk.heading,
                version=version.version_label,
                effective_from=version.effective_from,
                source_url=version.source_url,
                source_class=KnowledgeSourceClass(source.source_class),
                source_quality_score=source.quality_score,
                source_confidence=source.quality_score,
                data_confidence=max(0, source.quality_score - (15 if version.ocr_derived else 0)),
                content=chunk.content,
                score=score,
            )
            for score, chunk, version, source in scored[:limit]
        ]

    @staticmethod
    def _bm25(
        terms: list[str],
        tokens: list[str],
        *,
        document_count: int,
        document_frequency: dict[str, int],
        average_length: float,
        k1: float,
        b: float,
    ) -> float:
        frequencies = Counter(tokens)
        score = 0.0
        for term in terms:
            frequency = frequencies[term]
            if frequency == 0:
                continue
            df = document_frequency[term]
            inverse = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1 - b + b * len(tokens) / max(average_length, 1.0))
            score += inverse * frequency * (k1 + 1) / denominator
        return score


def route_knowledge_query(query: str) -> dict[str, Any]:
    normalized = normalize_text(query)
    routes: list[str] = []
    filters: dict[str, str] = {}
    for pattern, route, discipline in (
        (r"kacis|yangin merdiv|sprinkler|duman", "FIRE", "fire"),
        (r"radye|kazik|zemin|spt|cpt|iksa", "GEOTECHNICAL", "geotechnical"),
        (r"demir|beton|kalip|statik|donati", "STRUCTURAL", "structural"),
        (r"mantolama|poz|rayic|birim fiyat", "YFK_COST", "cost"),
        (r"emsal|imar|taks|kaks|ruhsat", "ZONING", "architecture"),
        (r"ifc|bim|ifcwall|ifcslab", "BIM", "bim"),
        (r"cephe|mimari|plan", "ARCHITECTURE", "architecture"),
    ):
        if re.search(pattern, normalized):
            routes.append(route)
            filters.setdefault("discipline", discipline)
    if re.search(r"ne kadar|metraj|m2|m²|ton", normalized):
        routes.append("ESTIMATION")
    collections: list[str] = []
    if any(route in routes for route in ("FIRE", "ZONING")):
        collections.append("REGULATIONS")
    if "YFK_COST" in routes:
        collections.append("YFK_UNIT_PRICES")
    if any(route in routes for route in ("STRUCTURAL", "ARCHITECTURE", "BIM")):
        collections.append("TECHNICAL_SPECIFICATIONS")
    if not routes:
        routes.append("GENERAL_CONSTRUCTION")
    if len(set(collections)) == 1:
        filters["collection"] = collections[0]
    return {
        "routes": list(dict.fromkeys(routes)),
        "collections": list(dict.fromkeys(collections)),
        "filters": filters,
    }


class UnifiedConstructionKnowledgeSearch:
    """Compatibility adapter that adds approved hybrid knowledge to the existing chat RAG."""

    def __init__(self, repository: KnowledgeRepository, config: KnowledgeConfig) -> None:
        self._hybrid = HybridKnowledgeSearch(repository, config)
        self._legacy = LegalKnowledgeRepository(repository.session)

    def search(self, query: str, *, limit: int = 3) -> list[LegalContextChunk]:
        routing = route_knowledge_query(query)
        hybrid = self._hybrid.search(query, limit=limit, filters=routing["filters"])
        results = [
            LegalContextChunk(
                document_key=hit.document_id,
                title=hit.document_name,
                category=hit.source_class.value,
                authority=f"SOURCE_CONFIDENCE={hit.source_confidence}",
                source_url=hit.source_url,
                fetched_at=(hit.effective_from or date.today()).isoformat(),
                chunk_index=index,
                content=(
                    f"[Version: {hit.version}; Effective: {hit.effective_from}; "
                    f"Article: {hit.article or 'N/A'}]\n{hit.content}"
                ),
                score=hit.score,
            )
            for index, hit in enumerate(hybrid)
        ]
        seen_urls = {result.source_url for result in results}
        for legacy in self._legacy.search(query, limit=limit * 2):
            if legacy.source_url in seen_urls:
                continue
            results.append(legacy)
            seen_urls.add(legacy.source_url)
            if len(results) >= limit:
                break
        return results[:limit]
