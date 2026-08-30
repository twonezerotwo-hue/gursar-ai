from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.modules.database_backup.infrastructure.models import DatabaseBackupORM


class BackupNotSupportedError(RuntimeError):
    pass


class BackupValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackupResult:
    dry_run: bool
    database_kind: str
    source: str
    target: str | None
    checksum_sha256: str | None
    size_bytes: int | None
    validation_status: str


class DatabaseBackupService:
    """Creates transactionally consistent SQLite backups and validates restore readability."""

    def __init__(self, *, session: Session, database_url: str, backup_root: Path) -> None:
        self._session = session
        self._url = make_url(database_url)
        self._backup_root = backup_root.resolve()

    def create(self, *, dry_run: bool) -> BackupResult:
        if not self._url.drivername.startswith("sqlite"):
            raise BackupNotSupportedError(
                "PostgreSQL backup adapter requires managed pg_dump credentials and is not enabled."
            )
        database = self._url.database
        if not database or database == ":memory:":
            raise BackupNotSupportedError("In-memory SQLite databases cannot be backed up")
        source = Path(database).resolve()
        if not source.is_file():
            raise BackupValidationError("SQLite database file does not exist")
        if dry_run:
            return BackupResult(
                dry_run=True,
                database_kind="sqlite",
                source=str(source),
                target=None,
                checksum_sha256=None,
                size_bytes=source.stat().st_size,
                validation_status="DRY_RUN_OK",
            )
        self._backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self._backup_root / f"gursar-{stamp}-{os.urandom(4).hex()}.sqlite3"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self._backup_root, prefix=".backup-", suffix=".tmp", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
            with (
                closing(sqlite3.connect(source)) as source_db,
                closing(sqlite3.connect(temporary_path)) as target_db,
            ):
                source_db.backup(target_db)
            self._validate_sqlite(temporary_path)
            os.replace(temporary_path, target)
            temporary_path = None
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        checksum = self._checksum(target)
        row = DatabaseBackupORM(
            database_kind="sqlite",
            storage_path=str(target),
            checksum_sha256=checksum,
            size_bytes=target.stat().st_size,
            validation_status="RESTORE_VALIDATED",
        )
        self._session.add(row)
        self._session.flush()
        return BackupResult(
            dry_run=False,
            database_kind="sqlite",
            source=str(source),
            target=str(target),
            checksum_sha256=checksum,
            size_bytes=target.stat().st_size,
            validation_status="RESTORE_VALIDATED",
        )

    @staticmethod
    def _validate_sqlite(path: Path) -> None:
        with closing(sqlite3.connect(path)) as database:
            result = database.execute("PRAGMA integrity_check").fetchone()
            tables = {
                row[0]
                for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        if not result or result[0] != "ok":
            raise BackupValidationError("SQLite integrity check failed")
        if not {"projects", "conversations", "price_book"}.issubset(tables):
            raise BackupValidationError("Backup is missing required application tables")

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
