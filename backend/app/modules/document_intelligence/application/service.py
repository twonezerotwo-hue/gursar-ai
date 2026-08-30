from __future__ import annotations

import hashlib
import importlib.metadata
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import IO

from app.modules.document_intelligence.domain.models import DocumentAnalysis, ToolCapability
from app.modules.document_intelligence.domain.ports import (
    DocumentAnalysisRepository,
    DocumentAnalyzer,
)


class EmptyAnalysisUploadError(ValueError):
    pass


class AnalysisUploadTooLargeError(ValueError):
    pass


class UnsupportedAnalysisFormatError(ValueError):
    pass


class InvalidAnalysisDocumentError(ValueError):
    pass


_TOOL_SPECS = (
    (
        "pdfplumber",
        "pdfplumber",
        "PDF metni, çizgi geometrisi ve tablo tespiti",
        "https://github.com/jsvine/pdfplumber",
        (".pdf",),
    ),
    (
        "opencv",
        "opencv-python",
        "Görüntü temizleme, kenar ve çizim yoğunluğu analizi",
        "https://github.com/opencv/opencv-python",
        (".png", ".jpg", ".jpeg", ".pdf"),
    ),
    (
        "rapidocr",
        "rapidocr",
        "Taranmış proje paftalarında yerel OCR",
        "https://github.com/RapidAI/RapidOCR",
        (".png", ".jpg", ".jpeg", ".pdf"),
    ),
    (
        "ezdxf",
        "ezdxf",
        "DXF katman, varlık ve geometri envanteri",
        "https://github.com/mozman/ezdxf",
        (".dxf",),
    ),
    (
        "ifcopenshell",
        "ifcopenshell",
        "IFC/BIM mekân, kat ve eleman envanteri",
        "https://github.com/IfcOpenShell/IfcOpenShell",
        (".ifc",),
    ),
)


class DocumentIntelligenceService:
    def __init__(
        self,
        *,
        analyzers: list[DocumentAnalyzer],
        max_upload_bytes: int,
    ) -> None:
        self._analyzers = analyzers
        self._max_upload_bytes = max_upload_bytes

    def capabilities(self) -> list[ToolCapability]:
        return [
            ToolCapability(
                id=tool_id,
                name=package,
                purpose=purpose,
                github_url=github_url,
                formats=formats,
                available=self._package_version(package) is not None,
                version=self._package_version(package),
            )
            for tool_id, package, purpose, github_url, formats in _TOOL_SPECS
        ]

    def supported_extensions(self) -> list[str]:
        return sorted({extension for analyzer in self._analyzers for extension in analyzer.formats})

    def analyze(
        self,
        *,
        filename: str,
        source: IO[bytes],
        conversation_id: str | None = None,
        repository: DocumentAnalysisRepository | None = None,
    ) -> DocumentAnalysis:
        safe_name = self._safe_name(filename)
        suffix = Path(safe_name).suffix.lower()
        analyzer = next(
            (candidate for candidate in self._analyzers if suffix in candidate.formats),
            None,
        )
        if analyzer is None:
            raise UnsupportedAnalysisFormatError(suffix)

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            size = 0
            digest = hashlib.sha256()
            source.seek(0)
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > self._max_upload_bytes:
                    temporary.close()
                    temporary_path.unlink(missing_ok=True)
                    raise AnalysisUploadTooLargeError(str(self._max_upload_bytes))
                digest.update(chunk)
                temporary.write(chunk)
        source.seek(0)
        if size == 0:
            temporary_path.unlink(missing_ok=True)
            raise EmptyAnalysisUploadError("empty upload")
        sha256 = digest.hexdigest()
        try:
            detected_format = self._detect_format(temporary_path)
            equivalent_formats = {detected_format, suffix}
            if detected_format != suffix and equivalent_formats != {".jpg", ".jpeg"}:
                raise UnsupportedAnalysisFormatError(
                    f"Extension {suffix!r} does not match {detected_format!r} content"
                )
            if conversation_id is not None and repository is not None:
                cached = repository.find(conversation_id=conversation_id, sha256=sha256)
                if cached is not None:
                    return cached
            result = analyzer.analyze(
                path=temporary_path,
                filename=safe_name,
                detected_format=detected_format,
            )
            result = replace(
                result,
                conversation_id=conversation_id,
                sha256=sha256,
                size_bytes=size,
            )
            if conversation_id is not None and repository is not None:
                return repository.save(result)
            return result
        except UnsupportedAnalysisFormatError:
            raise
        except Exception as exc:
            raise InvalidAnalysisDocumentError(str(exc)) from exc
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _detect_format(path: Path) -> str:
        with path.open("rb") as source:
            header = source.read(4096)
        stripped = header.lstrip()
        if header.startswith(b"%PDF-"):
            return ".pdf"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if header.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if header.startswith(b"AutoCAD Binary DXF\r\n\x1a\x00"):
            return ".dxf"
        normalized = stripped.replace(b"\r\n", b"\n")
        if normalized.startswith(b"0\nSECTION"):
            return ".dxf"
        if stripped.startswith(b"ISO-10303-21;"):
            return ".ifc"
        raise UnsupportedAnalysisFormatError("Unknown file signature")

    @staticmethod
    def _safe_name(filename: str) -> str:
        name = Path(filename.replace("\\", "/")).name.strip()
        return name[:255] or "project-file"

    @staticmethod
    def _package_version(package: str) -> str | None:
        try:
            return importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            return None
