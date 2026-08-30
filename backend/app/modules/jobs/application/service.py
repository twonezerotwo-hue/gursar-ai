from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, cast

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.persistence import utc_now
from app.modules.jobs.infrastructure.models import AnalysisJobORM

logger = structlog.get_logger(__name__)
JOB_TYPES = {
    "PDF_ANALYSIS",
    "DXF_ANALYSIS",
    "IFC_ANALYSIS",
    "VISION_INFERENCE",
    "DATASET_EXPORT",
    "MODEL_EVALUATION",
    "VECTOR_PDF",
    "RASTER_VISION",
    "HYBRID_ANALYSIS",
    "IFC",
    "DXF",
    "BENCHMARK",
    "AUTONOMOUS_DATA_ACQUISITION",
    "SYNTHETIC_PROJECT_GENERATION",
    "RESPLAN_IMPORT",
    "BIM_DATASET_IMPORT",
    "IFC_2D_RENDER",
    "REAL_DATA_READINESS",
}


class JobQueueProvider(Protocol):
    def enqueue(
        self,
        *,
        job_type: str,
        project_id: str,
        document_version_id: str | None,
        operation_version: str,
        payload: dict[str, Any],
    ) -> tuple[AnalysisJobORM, bool]: ...

    def claim_next(self, *, worker_version: str) -> AnalysisJobORM | None: ...


class DatabaseJobQueueProvider:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self,
        *,
        job_type: str,
        project_id: str,
        document_version_id: str | None,
        operation_version: str,
        payload: dict[str, Any],
    ) -> tuple[AnalysisJobORM, bool]:
        if job_type not in JOB_TYPES:
            raise ValueError("Unsupported job type")
        existing = self._session.scalar(
            select(AnalysisJobORM).where(
                AnalysisJobORM.document_version_id == document_version_id,
                AnalysisJobORM.job_type == job_type,
                AnalysisJobORM.operation_version == operation_version,
            )
        )
        if existing:
            return existing, True
        row = AnalysisJobORM(
            job_type=job_type,
            project_id=project_id,
            document_version_id=document_version_id,
            operation_version=operation_version,
            payload_json=payload,
            status="QUEUED",
            progress=0,
            current_stage="QUEUED",
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            existing = self._session.scalar(
                select(AnalysisJobORM).where(
                    AnalysisJobORM.document_version_id == document_version_id,
                    AnalysisJobORM.job_type == job_type,
                    AnalysisJobORM.operation_version == operation_version,
                )
            )
            if existing is None:
                raise
            return existing, True
        return row, False

    def claim_next(
        self, *, worker_version: str, job_id: str | None = None
    ) -> AnalysisJobORM | None:
        query = select(AnalysisJobORM).where(AnalysisJobORM.status == "QUEUED")
        if job_id is not None:
            query = query.where(AnalysisJobORM.id == job_id)
        row = self._session.scalar(
            query
            .order_by(AnalysisJobORM.created_at, AnalysisJobORM.id)
            .limit(1)
        )
        if row is None:
            return None
        now = utc_now()
        claimed = self._session.execute(
            update(AnalysisJobORM)
            .where(AnalysisJobORM.id == row.id, AnalysisJobORM.status == "QUEUED")
            .values(
                status="RUNNING",
                progress=1,
                current_stage="VALIDATING",
                started_at=now,
                worker_version=worker_version,
                claimed_by=worker_version,
                heartbeat_at=now,
            )
        )
        if cast(Any, claimed).rowcount != 1:
            return None
        self._session.refresh(row)
        self._session.flush()
        return row

    def recover_stale(
        self, *, stale_before: datetime, max_rows: int = 100
    ) -> list[AnalysisJobORM]:
        rows = list(
            self._session.scalars(
                select(AnalysisJobORM)
                .where(
                    AnalysisJobORM.status == "RUNNING",
                    or_(
                        AnalysisJobORM.heartbeat_at.is_(None),
                        AnalysisJobORM.heartbeat_at < stale_before,
                    ),
                )
                .order_by(AnalysisJobORM.started_at)
                .limit(max_rows)
            )
        )
        recovered: list[AnalysisJobORM] = []
        for row in rows:
            if row.retry_count >= row.max_retries:
                row.status = "FAILED"
                row.current_stage = "FAILED"
                row.error_code = "STALE_JOB_RETRY_EXHAUSTED"
                row.error_message = "Worker heartbeat expired and retry limit was reached"
                row.completed_at = utc_now()
            else:
                row.retry_count += 1
                row.status = "QUEUED"
                row.current_stage = "QUEUED"
                row.progress = 0
                row.claimed_by = None
                row.started_at = None
                row.heartbeat_at = None
                recovered.append(row)
        self._session.flush()
        return recovered


class JobWorker:
    def __init__(
        self,
        session: Session,
        *,
        handlers: dict[str, Callable[[AnalysisJobORM], dict[str, Any]]],
        worker_version: str = "db-worker-v1",
    ) -> None:
        self._session = session
        self._handlers = handlers
        self._worker_version = worker_version

    def run_one(self, *, job_id: str | None = None) -> AnalysisJobORM | None:
        row = DatabaseJobQueueProvider(self._session).claim_next(
            worker_version=self._worker_version, job_id=job_id
        )
        if row is None:
            return None
        started = time.monotonic()
        cpu_started = time.process_time()
        try:
            handler = self._handlers.get(row.job_type)
            if handler is None:
                raise RuntimeError("JOB_HANDLER_UNAVAILABLE")
            row.result_json = handler(row)
            row.status = "COMPLETED"
            row.progress = 100
            row.current_stage = "COMPLETED"
        except Exception as exc:
            row.status = "FAILED"
            row.error_code = type(exc).__name__
            row.error_message = str(exc)[:2000]
            row.progress = 100
            row.current_stage = "FAILED"
        row.completed_at = utc_now()
        row.duration_seconds = time.monotonic() - started
        row.cpu_seconds = time.process_time() - cpu_started
        row.peak_memory_mb = self._memory_mb()
        self._session.flush()
        logger.info(
            "analysis_job_finished",
            job_id=row.id,
            job_type=row.job_type,
            status=row.status,
            duration=row.duration_seconds,
            cpu=row.cpu_seconds,
            memory_mb=row.peak_memory_mb,
            result=row.result_json,
        )
        return row

    def cancel(self, job_id: str) -> AnalysisJobORM:
        row = self._session.get(AnalysisJobORM, job_id)
        if row is None:
            raise LookupError(job_id)
        if row.status in {"QUEUED", "RUNNING"}:
            row.status = "CANCELLED"
            row.current_stage = "CANCELLED"
            row.completed_at = utc_now()
            row.progress = 100
            self._session.flush()
        return row

    def retry_failed(self, job_id: str) -> AnalysisJobORM:
        row = self._session.get(AnalysisJobORM, job_id)
        if row is None:
            raise LookupError(job_id)
        if row.status != "FAILED" or row.retry_count >= row.max_retries:
            raise ValueError("Job cannot be retried")
        row.retry_count += 1
        row.status = "QUEUED"
        row.current_stage = "QUEUED"
        row.progress = 0
        row.error_code = None
        row.error_message = None
        row.started_at = None
        row.completed_at = None
        row.claimed_by = None
        self._session.flush()
        return row

    @staticmethod
    def _memory_mb() -> float | None:
        return None
