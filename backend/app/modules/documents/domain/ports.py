from __future__ import annotations

from pathlib import Path
from typing import IO, Protocol

from app.modules.documents.domain.models import PDFMetadata


class PDFInspector(Protocol):
    def inspect(self, source: IO[bytes]) -> PDFMetadata: ...


class DocumentStorage(Protocol):
    def save(self, *, storage_key: str, source: IO[bytes]) -> None: ...

    def delete(self, *, storage_key: str) -> None: ...

    def path_for_read(self, *, storage_key: str) -> Path: ...
