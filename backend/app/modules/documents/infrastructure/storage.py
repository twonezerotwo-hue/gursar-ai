from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import IO


class LocalDocumentStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def save(self, *, storage_key: str, source: IO[bytes]) -> None:
        target = self._resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                dir=target.parent,
                prefix=".upload-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                source.seek(0)
                while chunk := source.read(1024 * 1024):
                    temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
        finally:
            source.seek(0)
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def delete(self, *, storage_key: str) -> None:
        target = self._resolve(storage_key)
        if target.exists():
            target.unlink()

    def path_for_read(self, *, storage_key: str) -> Path:
        target = self._resolve(storage_key)
        if not target.is_file():
            raise FileNotFoundError(storage_key)
        return target

    def _resolve(self, storage_key: str) -> Path:
        candidate = (self._root / storage_key).resolve()
        if candidate == self._root or self._root not in candidate.parents:
            raise ValueError("Storage key escapes the configured document root")
        return candidate
