from __future__ import annotations

from datetime import timedelta
from functools import partial
import os
import logging
import uuid
from typing import Any

import anyio
from fastapi.encoders import jsonable_encoder

from app.database import AuditSessionLocal, SessionLocal
from app.utils.blocking_io import run_blocking_io
from app.workers.models import (
    QUEUE_INGESTION,
    DurableWorkerJob,
    WorkerJobSnapshot,
    enqueue_worker_job,
    utcnow,
    wait_for_worker_job,
    wait_for_worker_job_async,
)
from app.workers.runtime import DurableQueueWorker, FatalJobError, WorkerContext, run_worker_cli


_ENABLE_VALUES = {"1", "true", "yes", "on", "external", "worker"}
logger = logging.getLogger(__name__)


def external_ingestion_enabled() -> bool:
    return (
        str(os.getenv("CONNECTOR_WORKER_MODE", "inline") or "inline")
        .strip()
        .lower()
        in _ENABLE_VALUES
    )


def enqueue_google_drive_import(
    *,
    user_id: str,
    file_ids: list[str],
    audit_ip_address: str | None = None,
    audit_user_agent: str | None = None,
) -> DurableWorkerJob:
    session = SessionLocal()
    try:
        return enqueue_worker_job(
            session,
            queue=QUEUE_INGESTION,
            kind="google_drive_import",
            user_id=str(user_id),
            payload={
                "file_ids": [str(value) for value in file_ids],
                "audit_ip_address": audit_ip_address,
                "audit_user_agent": audit_user_agent,
            },
            idempotency_key=f"google-drive-import:{user_id}:{uuid.uuid4().hex}",
            priority=10,
            # A crash can happen after one of several files was imported. The
            # existing per-file deduplication remains available to a deliberate
            # user retry, but the queue never guesses and repeats side effects.
            max_attempts=1,
            expires_at=utcnow() + timedelta(hours=24),
            commit=True,
        )
    finally:
        session.close()


async def enqueue_google_drive_import_async(**kwargs: Any) -> DurableWorkerJob:
    """Commit a connector job without blocking the request event loop."""

    return await run_blocking_io(partial(enqueue_google_drive_import, **kwargs))


def _ingestion_wait_timeout() -> float:
    try:
        timeout = float(os.getenv("CONNECTOR_REQUEST_WAIT_SECONDS", "900") or "900")
    except (TypeError, ValueError):
        timeout = 900.0
    return max(1.0, min(timeout, 3600.0))


def wait_for_ingestion_job(job: DurableWorkerJob) -> dict[str, Any]:
    return wait_for_worker_job(job.id, timeout_seconds=_ingestion_wait_timeout())


async def wait_for_ingestion_job_async(job: DurableWorkerJob) -> dict[str, Any]:
    return await wait_for_worker_job_async(
        job.id,
        timeout_seconds=_ingestion_wait_timeout(),
    )


def _active_user(session, user_id: str):
    from app.users.models import User

    user = session.query(User).filter(User.id == str(user_id)).first()
    if (
        user is None
        or getattr(user, "deleted_at", None) is not None
        or not bool(getattr(user, "is_active", False))
        or str(getattr(user, "role", "")).strip().lower() == "pending"
    ):
        raise FatalJobError("user_unavailable")
    return user


def _handle_google_drive_import(
    job: WorkerJobSnapshot,
    context: WorkerContext,
) -> dict[str, Any]:
    from app.files.google_drive import import_google_drive_files_payload

    session = SessionLocal()
    try:
        user = _active_user(session, str(job.user_id or ""))
        file_ids = job.payload.get("file_ids")
        if not isinstance(file_ids, list):
            raise FatalJobError("invalid_payload")
        context.raise_if_cancelled()

        async def run_import():
            return await import_google_drive_files_payload(
                session,
                user_id=user.id,
                file_ids=[str(value) for value in file_ids],
            )

        result = anyio.run(run_import)
        context.raise_if_cancelled()
        try:
            from app.logging.models import create_audit_log

            create_audit_log(
                db_log=AuditSessionLocal(),
                user_id=user.id,
                action="GOOGLE_DRIVE_FILES_IMPORTED",
                details={
                    "requested_file_count": len(file_ids),
                    "imported_file_count": int(result.get("imported_count") or 0),
                    "error_count": len(result.get("errors") or []),
                },
                ip_address=job.payload.get("audit_ip_address"),
                user_agent=job.payload.get("audit_user_agent"),
                category="files",
            )
        except Exception:
            logger.exception("Could not enqueue Google Drive import audit event")
        return jsonable_encoder(result)
    finally:
        session.close()


def build_worker() -> DurableQueueWorker:
    return DurableQueueWorker(
        queue=QUEUE_INGESTION,
        handlers={"google_drive_import": _handle_google_drive_import},
        default_lease_seconds=300,
        env_prefix="connector",
    )


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
