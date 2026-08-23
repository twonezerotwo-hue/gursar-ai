from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.persistence import utc_now
from app.modules.legal_knowledge.domain.models import LegalContextChunk
from app.modules.legal_knowledge.infrastructure.models import LegalChunkORM, LegalDocumentORM

STOP_WORDS = {
    "acaba",
    "gore",
    "icin",
    "ile",
    "midir",
    "nedir",
    "olan",
    "olarak",
    "var",
    "ve",
    "veya",
    "yapi",
}


def normalize_legal_text(value: str) -> str:
    translated = value.casefold().translate(
        str.maketrans({"ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"})
    )
    decomposed = unicodedata.normalize("NFKD", translated)
    ascii_like = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", ascii_like))


class LegalKnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def replace_document(
        self,
        *,
        source_key: str,
        title: str,
        category: str,
        authority: str,
        source_url: str,
        published_on: date | None,
        official_gazette_number: str | None,
        checksum_sha256: str,
        content_type: str,
        text: str,
        chunks: Sequence[tuple[str | None, str]],
        metadata: dict[str, object],
    ) -> tuple[LegalDocumentORM, bool]:
        document = self._session.scalar(
            select(LegalDocumentORM).where(LegalDocumentORM.source_key == source_key)
        )
        if document is not None and document.checksum_sha256 == checksum_sha256:
            document.fetched_at = utc_now()
            return document, False

        if document is None:
            document = LegalDocumentORM(
                source_key=source_key,
                title=title,
                category=category,
                authority=authority,
                source_url=source_url,
                published_on=published_on,
                official_gazette_number=official_gazette_number,
                checksum_sha256=checksum_sha256,
                content_type=content_type,
                version=1,
                is_active=True,
                char_count=len(text),
                metadata_json=metadata,
            )
            self._session.add(document)
            self._session.flush()
        else:
            document.title = title
            document.category = category
            document.authority = authority
            document.source_url = source_url
            document.published_on = published_on
            document.official_gazette_number = official_gazette_number
            document.fetched_at = utc_now()
            document.checksum_sha256 = checksum_sha256
            document.content_type = content_type
            document.version += 1
            document.is_active = True
            document.char_count = len(text)
            document.metadata_json = metadata
            self._session.execute(
                delete(LegalChunkORM).where(LegalChunkORM.document_id == document.id)
            )

        self._session.add_all(
            [
                LegalChunkORM(
                    document_id=document.id,
                    chunk_index=index,
                    heading=heading,
                    content=content,
                    normalized_content=normalize_legal_text(content),
                )
                for index, (heading, content) in enumerate(chunks)
            ]
        )
        return document, True

    def has_document(self, source_key: str) -> bool:
        return (
            self._session.scalar(
                select(LegalDocumentORM.id).where(LegalDocumentORM.source_key == source_key)
            )
            is not None
        )

    def search(self, query: str, *, limit: int = 3) -> list[LegalContextChunk]:
        self._session.flush()
        normalized_query = normalize_legal_text(query)
        terms = [
            term for term in normalized_query.split() if len(term) >= 3 and term not in STOP_WORDS
        ]
        if not terms:
            return []

        rows = self._session.execute(
            select(LegalChunkORM, LegalDocumentORM)
            .join(LegalDocumentORM, LegalDocumentORM.id == LegalChunkORM.document_id)
            .where(
                LegalDocumentORM.is_active.is_(True),
                or_(*(LegalChunkORM.normalized_content.contains(term) for term in terms)),
            )
        ).all()
        scored: list[LegalContextChunk] = []
        for chunk, document in rows:
            title = normalize_legal_text(document.title)
            content = chunk.normalized_content
            matched_terms = sum(1 for term in terms if term in content)
            if matched_terms == 0:
                continue
            frequency = sum(min(content.count(term), 4) for term in terms)
            title_hits = sum(1 for term in terms if term in title)
            phrase_bonus = 3 if normalized_query in content else 0
            score = matched_terms * 4 + frequency + title_hits * 3 + phrase_bonus
            scored.append(
                LegalContextChunk(
                    document_key=document.source_key,
                    title=document.title,
                    category=document.category,
                    authority=document.authority,
                    source_url=document.source_url,
                    fetched_at=document.fetched_at.isoformat(),
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    score=float(score),
                )
            )
        scored.sort(key=lambda item: (-item.score, item.title, item.chunk_index))
        return scored[:limit]

    def counts(self) -> tuple[int, int]:
        self._session.flush()
        documents = self._session.scalar(select(func.count()).select_from(LegalDocumentORM)) or 0
        chunks = self._session.scalar(select(func.count()).select_from(LegalChunkORM)) or 0
        return documents, chunks
