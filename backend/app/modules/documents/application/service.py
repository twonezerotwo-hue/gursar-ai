from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import IO

from ezdxf.filemanagement import readfile
from ezdxf.lldxf.const import DXFError
from sqlalchemy.orm import Session

from app.core.persistence import append_audit, append_outbox, new_id
from app.modules.documents.domain.models import DocumentUploadResult, PDFMetadata, Sheet
from app.modules.documents.domain.ports import DocumentStorage, PDFInspector
from app.modules.documents.infrastructure.pdf import EncryptedPDFError, InvalidPDFError
from app.modules.documents.infrastructure.repository import DocumentRepository
from app.modules.projects.infrastructure.repository import ProjectRepository


class ProjectNotFoundForUploadError(LookupError):
    pass


class DocumentNotFoundError(LookupError):
    pass


class EmptyUploadError(ValueError):
    pass


class UploadTooLargeError(ValueError):
    pass


class UnsupportedDocumentTypeError(ValueError):
    pass


class PDFPageLimitError(ValueError):
    pass


class DuplicateDocumentError(ValueError):
    def __init__(self, version_id: str) -> None:
        super().__init__(version_id)
        self.version_id = version_id


class DocumentService:
    def __init__(
        self,
        *,
        session: Session,
        projects: ProjectRepository,
        documents: DocumentRepository,
        storage: DocumentStorage,
        inspector: PDFInspector,
        max_upload_bytes: int,
        max_pdf_pages: int,
    ) -> None:
        self._session = session
        self._projects = projects
        self._documents = documents
        self._storage = storage
        self._inspector = inspector
        self._max_upload_bytes = max_upload_bytes
        self._max_pdf_pages = max_pdf_pages

    def upload_document(
        self,
        *,
        project_id: str,
        original_name: str,
        source: IO[bytes],
    ) -> DocumentUploadResult:
        if self._projects.get(project_id) is None:
            raise ProjectNotFoundForUploadError(project_id)
        safe_name = self._safe_original_name(original_name)
        spool_limit = min(self._max_upload_bytes, 8 * 1024 * 1024)
        with tempfile.SpooledTemporaryFile(max_size=spool_limit) as copy:
            size_bytes, sha256, signature = self._copy_and_hash(source, copy)
            if size_bytes == 0:
                raise EmptyUploadError("Upload is empty")
            if signature.startswith(b"%PDF-"):
                media_type = "application/pdf"
                extension = ".pdf"
                metadata = self._inspector.inspect(copy)
                if len(metadata.pages) > self._max_pdf_pages:
                    raise PDFPageLimitError(str(len(metadata.pages)))
            elif signature.lstrip().startswith(b"ISO-10303-21"):
                media_type = "application/x-step"
                extension = ".ifc"
                metadata = PDFMetadata(pages=[])
            elif self._looks_like_dxf(signature):
                self._validate_dxf(copy)
                media_type = "image/vnd.dxf"
                extension = ".dxf"
                metadata = PDFMetadata(pages=[])
            else:
                raise UnsupportedDocumentTypeError("Content is neither PDF, IFC STEP nor DXF")
            duplicate = self._documents.find_duplicate(project_id=project_id, sha256=sha256)
            if duplicate is not None:
                raise DuplicateDocumentError(duplicate.id)

            document = self._documents.find_document(
                project_id=project_id,
                original_name=safe_name,
            )
            if document is None:
                document = self._documents.create_document(
                    project_id=project_id,
                    original_name=safe_name,
                )
            version_number = self._documents.next_version(document.id)
            version_id = new_id()
            storage_key = (
                f"projects/{project_id}/documents/{document.id}/versions/{version_id}{extension}"
            )
            self._storage.save(storage_key=storage_key, source=copy)
            try:
                result = self._documents.save_version(
                    version_id=version_id,
                    document=document,
                    version=version_number,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    storage_key=storage_key,
                    metadata=metadata,
                    media_type=media_type,
                )
            except Exception:
                self._storage.delete(storage_key=storage_key)
                raise

        append_audit(
            self._session,
            action="DOCUMENT_VERSION_UPLOADED",
            aggregate_id=result.version.id,
            detail={
                "project_id": project_id,
                "document_id": result.document.id,
                "version": result.version.version,
                "sha256": result.version.sha256,
                "size_bytes": result.version.size_bytes,
            },
        )
        append_outbox(
            self._session,
            event_type="document.version.uploaded.v1",
            aggregate_id=result.version.id,
            payload={
                "project_id": project_id,
                "document_id": result.document.id,
                "document_version_id": result.version.id,
            },
        )
        return result

    def upload_pdf(
        self,
        *,
        project_id: str,
        original_name: str,
        source: IO[bytes],
    ) -> DocumentUploadResult:
        return self.upload_document(
            project_id=project_id,
            original_name=original_name,
            source=source,
        )

    def get_document(self, document_id: str) -> DocumentUploadResult:
        document = self._documents.get_document(document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)
        version = self._documents.latest_version(document_id)
        if version is None:
            raise DocumentNotFoundError(document_id)
        return DocumentUploadResult(
            document=document,
            version=version,
            sheets=self._documents.list_sheets(version.id),
        )

    def list_sheets(self, document_id: str) -> list[Sheet]:
        return self.get_document(document_id).sheets

    def _copy_and_hash(
        self,
        source: IO[bytes],
        destination: IO[bytes],
    ) -> tuple[int, str, bytes]:
        digest = hashlib.sha256()
        size_bytes = 0
        signature = b""
        source.seek(0)
        while chunk := source.read(1024 * 1024):
            size_bytes += len(chunk)
            if size_bytes > self._max_upload_bytes:
                raise UploadTooLargeError(str(self._max_upload_bytes))
            if len(signature) < 64:
                signature += chunk[: 64 - len(signature)]
            digest.update(chunk)
            destination.write(chunk)
        destination.seek(0)
        return size_bytes, digest.hexdigest(), signature

    @staticmethod
    def _safe_original_name(original_name: str) -> str:
        basename = Path(original_name.replace("\\", "/")).name
        cleaned = "".join(character for character in basename if ord(character) >= 32).strip()
        if not cleaned:
            cleaned = "project.pdf"
        if len(cleaned) > 255:
            suffix = Path(cleaned).suffix[:16]
            cleaned = f"{Path(cleaned).stem[: 255 - len(suffix)]}{suffix}"
        return cleaned

    @staticmethod
    def _looks_like_dxf(signature: bytes) -> bool:
        if signature.startswith(b"AutoCAD Binary DXF\r\n\x1a\x00"):
            return True
        normalized = signature.lstrip().replace(b"\r\n", b"\n")
        return normalized.startswith(b"0\nSECTION")

    @staticmethod
    def _validate_dxf(source: IO[bytes]) -> None:
        source.seek(0)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                shutil.copyfileobj(source, temporary)
            readfile(temporary_path).modelspace()
        except (DXFError, OSError, ValueError) as exc:
            raise UnsupportedDocumentTypeError("INVALID_DXF") from exc
        finally:
            source.seek(0)
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


__all__ = [
    "DocumentNotFoundError",
    "DocumentService",
    "DuplicateDocumentError",
    "EmptyUploadError",
    "EncryptedPDFError",
    "InvalidPDFError",
    "PDFPageLimitError",
    "ProjectNotFoundForUploadError",
    "UnsupportedDocumentTypeError",
    "UploadTooLargeError",
]
