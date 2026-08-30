from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.documents.domain.models import (
    DocumentUploadResult,
    DocumentVersion,
    DocumentVersionStatus,
    PDFMetadata,
    ProjectDocument,
    Sheet,
    SheetDiscipline,
)
from app.modules.documents.infrastructure.models import (
    DocumentVersionORM,
    ProjectDocumentORM,
    SheetORM,
)


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_duplicate(self, *, project_id: str, sha256: str) -> DocumentVersion | None:
        row = self._session.scalar(
            select(DocumentVersionORM).where(
                DocumentVersionORM.project_id == project_id,
                DocumentVersionORM.sha256 == sha256,
            )
        )
        return self._to_version(row) if row is not None else None

    def find_document(self, *, project_id: str, original_name: str) -> ProjectDocument | None:
        row = self._session.scalar(
            select(ProjectDocumentORM).where(
                ProjectDocumentORM.project_id == project_id,
                ProjectDocumentORM.original_name == original_name,
            )
        )
        return self._to_document(row) if row is not None else None

    def create_document(self, *, project_id: str, original_name: str) -> ProjectDocument:
        row = ProjectDocumentORM(project_id=project_id, original_name=original_name)
        self._session.add(row)
        self._session.flush()
        return self._to_document(row)

    def next_version(self, document_id: str) -> int:
        latest = self._session.scalar(
            select(func.max(DocumentVersionORM.version)).where(
                DocumentVersionORM.document_id == document_id
            )
        )
        return int(latest or 0) + 1

    def save_version(
        self,
        *,
        version_id: str,
        document: ProjectDocument,
        version: int,
        sha256: str,
        size_bytes: int,
        storage_key: str,
        metadata: PDFMetadata,
        media_type: str = "application/pdf",
    ) -> DocumentUploadResult:
        version_row = DocumentVersionORM(
            id=version_id,
            document_id=document.id,
            project_id=document.project_id,
            version=version,
            sha256=sha256,
            size_bytes=size_bytes,
            media_type=media_type,
            storage_key=storage_key,
            page_count=len(metadata.pages),
            status=DocumentVersionStatus.READY.value,
        )
        self._session.add(version_row)
        sheet_rows = [
            SheetORM(
                document_version_id=version_id,
                page_number=page.page_number,
                title=None,
                discipline=SheetDiscipline.UNKNOWN.value,
                width_points=page.width_points,
                height_points=page.height_points,
            )
            for page in metadata.pages
        ]
        self._session.add_all(sheet_rows)
        self._session.flush()
        return DocumentUploadResult(
            document=document,
            version=self._to_version(version_row),
            sheets=[self._to_sheet(row) for row in sheet_rows],
        )

    def get_version(self, version_id: str) -> DocumentVersion | None:
        row = self._session.get(DocumentVersionORM, version_id)
        return self._to_version(row) if row is not None else None

    def get_document(self, document_id: str) -> ProjectDocument | None:
        row = self._session.get(ProjectDocumentORM, document_id)
        return self._to_document(row) if row is not None else None

    def latest_version(self, document_id: str) -> DocumentVersion | None:
        row = self._session.scalar(
            select(DocumentVersionORM)
            .where(DocumentVersionORM.document_id == document_id)
            .order_by(DocumentVersionORM.version.desc())
            .limit(1)
        )
        return self._to_version(row) if row is not None else None

    def list_sheets(self, document_version_id: str) -> list[Sheet]:
        rows = self._session.scalars(
            select(SheetORM)
            .where(SheetORM.document_version_id == document_version_id)
            .order_by(SheetORM.page_number)
        ).all()
        return [self._to_sheet(row) for row in rows]

    @staticmethod
    def _to_document(row: ProjectDocumentORM) -> ProjectDocument:
        return ProjectDocument(
            id=row.id,
            project_id=row.project_id,
            original_name=row.original_name,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_version(row: DocumentVersionORM) -> DocumentVersion:
        return DocumentVersion(
            id=row.id,
            document_id=row.document_id,
            project_id=row.project_id,
            version=row.version,
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            media_type=row.media_type,
            storage_key=row.storage_key,
            page_count=row.page_count,
            status=DocumentVersionStatus(row.status),
            created_at=row.created_at,
        )

    @staticmethod
    def _to_sheet(row: SheetORM) -> Sheet:
        return Sheet(
            id=row.id,
            document_version_id=row.document_version_id,
            page_number=row.page_number,
            title=row.title,
            discipline=SheetDiscipline(row.discipline),
            width_points=Decimal(row.width_points),
            height_points=Decimal(row.height_points),
            sheet_number=row.sheet_number,
            sheet_name=row.sheet_name,
            revision=row.revision,
            drawing_type=row.drawing_type,
            level_name=row.level_name,
            classification_confidence=(
                Decimal(row.classification_confidence)
                if row.classification_confidence is not None
                else None
            ),
        )
