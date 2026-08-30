from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.modules.document_intelligence.domain.models import DocumentAnalysis


class DocumentAnalyzer(Protocol):
    formats: tuple[str, ...]

    def analyze(self, *, path: Path, filename: str, detected_format: str) -> DocumentAnalysis: ...


class OcrEngine(Protocol):
    tool_id: str

    def read(self, image: object) -> tuple[str, float | None]: ...


class DocumentAnalysisRepository(Protocol):
    def find(self, *, conversation_id: str, sha256: str) -> DocumentAnalysis | None: ...

    def save(self, analysis: DocumentAnalysis) -> DocumentAnalysis: ...

    def list_for_conversation(self, conversation_id: str) -> list[DocumentAnalysis]: ...
