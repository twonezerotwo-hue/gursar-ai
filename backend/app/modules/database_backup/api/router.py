from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.security import require_admin_key
from app.modules.database_backup.application.service import (
    BackupNotSupportedError,
    BackupValidationError,
    DatabaseBackupService,
)

router = APIRouter(
    prefix="/api/v1/admin/backups",
    tags=["database-backup"],
    dependencies=[Depends(require_admin_key)],
)


@router.post("")
def create_backup(
    request: Request,
    dry_run: bool = False,
    session: Session = Depends(get_session),
) -> dict:
    settings = request.app.state.settings
    try:
        result = DatabaseBackupService(
            session=session,
            database_url=settings.database_url,
            backup_root=settings.database_backup_root,
        ).create(dry_run=dry_run)
    except BackupNotSupportedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except BackupValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return asdict(result)
