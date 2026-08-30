from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.document_intelligence.domain.models import DocumentAnalysis
from app.modules.document_intelligence.infrastructure.models import DocumentAnalysisORM


class SqlDocumentAnalysisRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, *, conversation_id: str, sha256: str) -> DocumentAnalysis | None:
        row = self._session.scalar(
            select(DocumentAnalysisORM).where(
                DocumentAnalysisORM.conversation_id == conversation_id,
                DocumentAnalysisORM.sha256 == sha256,
            )
        )
        return self._to_domain(row, cached=True) if row is not None else None

    def save(self, analysis: DocumentAnalysis) -> DocumentAnalysis:
        if analysis.conversation_id is None:
            raise ValueError("conversation_id is required for persisted analysis")
        row = DocumentAnalysisORM(
            conversation_id=analysis.conversation_id,
            filename=analysis.filename,
            format=analysis.format,
            media_type=analysis.media_type,
            sha256=analysis.sha256,
            size_bytes=analysis.size_bytes,
            tools_used=list(analysis.tools_used),
            summary=analysis.summary,
            extracted_text=analysis.extracted_text,
            metrics=analysis.metrics,
        )
        self._session.add(row)
        self._session.flush()
        return self._to_domain(row, cached=False)

    def list_for_conversation(self, conversation_id: str) -> list[DocumentAnalysis]:
        rows = self._session.scalars(
            select(DocumentAnalysisORM)
            .where(DocumentAnalysisORM.conversation_id == conversation_id)
            .order_by(DocumentAnalysisORM.created_at, DocumentAnalysisORM.id)
        ).all()
        return [self._to_domain(row, cached=True) for row in rows]

    @staticmethod
    def _to_domain(row: DocumentAnalysisORM, *, cached: bool) -> DocumentAnalysis:
        return DocumentAnalysis(
            filename=row.filename,
            format=row.format,
            media_type=row.media_type,
            tools_used=tuple(row.tools_used),
            summary=row.summary,
            extracted_text=row.extracted_text,
            metrics=dict(row.metrics),
            analysis_id=row.id,
            conversation_id=row.conversation_id,
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            cached=cached,
        )
