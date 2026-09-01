from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
import logging
import os
from pathlib import Path
import re
import shutil
import threading
import uuid
from typing import Any, BinaryIO

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, text

from app.workers.models import QUEUE_OPERATIONS, WorkerJobSnapshot
from app.workers.models import (
    DurableWorkerJob,
    ImportStagingReservation,
    JOB_TERMINAL_STATUSES,
    WorkerJobFailed,
    enqueue_worker_job,
    lock_unreconciled_terminal_jobs,
    release_import_staging_reservations,
    utcnow,
    wait_for_worker_job,
    wait_for_worker_job_async,
)
from app.workers.runtime import (
    DurableQueueWorker,
    FatalJobError,
    WorkerContext,
    run_worker_cli,
)
from app.database import AuditSessionLocal, SessionLocal
from app.paths import DATA_DIR
from app.utils.blocking_io import run_blocking_io


logger = logging.getLogger(__name__)
OPERATIONS_IMPORT_DIR = Path(
    os.getenv("OPERATIONS_IMPORT_STAGING_DIR") or (DATA_DIR / "operations-imports")
)
OPERATIONS_RESULT_DIR = Path(
    os.getenv("OPERATIONS_RESULT_STAGING_DIR") or (DATA_DIR / "operations-results")
)
_STAGED_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(?:json|zip|csv|xlsx)$")
_IMPORT_KINDS = frozenset(
    {
        "import_user_self",
        "import_chatgpt",
        "import_openwebui_single",
        "import_openwebui_bulk",
        "import_admin_users",
        "import_bulk_users",
    }
)
_IMPORT_WRITE_CHUNK_BYTES = 8 * 1024 * 1024
_LOCAL_IMPORT_QUOTA_LOCK = threading.RLock()


def external_operations_enabled() -> bool:
    return str(os.getenv("OPERATIONS_WORKER_MODE", "inline") or "inline").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "external",
        "worker",
    }


def _max_import_bytes() -> int:
    try:
        value = int(os.getenv("OPERATIONS_IMPORT_MAX_BYTES", str(512 * 1024 * 1024)))
    except (TypeError, ValueError):
        value = 512 * 1024 * 1024
    return max(1024 * 1024, min(value, 2 * 1024 * 1024 * 1024))


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _global_import_staging_bytes() -> int:
    return _bounded_env_int(
        "OPERATIONS_IMPORT_STAGING_GLOBAL_MAX_BYTES",
        8 * 1024 * 1024 * 1024,
        1024 * 1024,
        8 * 1024 * 1024 * 1024 * 1024,
    )


def _principal_import_staging_bytes() -> int:
    return _bounded_env_int(
        "OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_BYTES",
        1024 * 1024 * 1024,
        1024 * 1024,
        2 * 1024 * 1024 * 1024 * 1024,
    )


def _global_import_staging_slots() -> int:
    return _bounded_env_int(
        "OPERATIONS_IMPORT_STAGING_GLOBAL_MAX_SLOTS", 1000, 1, 100_000
    )


def _principal_import_staging_slots() -> int:
    return _bounded_env_int(
        "OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_SLOTS", 10, 1, 1000
    )


def _import_staging_retention_hours() -> int:
    return _bounded_env_int("OPERATIONS_STAGING_RETENTION_HOURS", 96, 72, 336)


def _import_quota_lock_id(scope: str) -> int:
    raw = int.from_bytes(
        hashlib.sha256(f"omlorix:import-staging:{scope}".encode("utf-8")).digest()[:8],
        "big",
    )
    return raw - (1 << 64) if raw >= (1 << 63) else raw


@contextmanager
def _serialized_import_quota(db, principal_id: str):
    """Serialize aggregate quota admission across replicas and local tests."""

    if db.get_bind().dialect.name == "postgresql":
        # Always acquire global before principal to keep concurrent principals
        # on one deterministic lock order.
        for scope in ("global", f"principal:{principal_id}"):
            db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _import_quota_lock_id(scope)},
            )
        yield
        return
    with _LOCAL_IMPORT_QUOTA_LOCK:
        yield


def _staging_limit_error(code: str) -> HTTPException:
    headers = {"Retry-After": "60"} if code != "import_file_too_large" else None
    return HTTPException(status_code=413, detail={"code": code}, headers=headers)


def _normalize_import_identity(*, principal_id: str, import_kind: str) -> tuple[str, str]:
    normalized_principal = str(principal_id or "").strip()
    normalized_kind = str(import_kind or "").strip().lower()
    if not normalized_principal:
        raise ValueError("Import staging principal is required")
    if normalized_kind not in _IMPORT_KINDS:
        raise ValueError("Unsupported import staging kind")
    return normalized_principal, normalized_kind


def _create_import_staging_reservation(
    *,
    staged_name: str,
    principal_id: str,
    import_kind: str,
) -> str:
    db = SessionLocal()
    try:
        with _serialized_import_quota(db, principal_id):
            global_slots = int(db.query(func.count(ImportStagingReservation.id)).scalar() or 0)
            principal_slots = int(
                db.query(func.count(ImportStagingReservation.id))
                .filter(ImportStagingReservation.principal_id == principal_id)
                .scalar()
                or 0
            )
            if global_slots >= _global_import_staging_slots():
                raise _staging_limit_error("import_staging_capacity_exceeded")
            if principal_slots >= _principal_import_staging_slots():
                raise _staging_limit_error("import_staging_quota_exceeded")
            reservation = ImportStagingReservation(
                id=uuid.uuid4().hex,
                staged_name=staged_name,
                principal_id=principal_id,
                import_kind=import_kind,
                size_bytes=0,
                expires_at=utcnow() + timedelta(hours=_import_staging_retention_hours()),
            )
            db.add(reservation)
            db.flush()
            reservation_id = str(reservation.id)
            db.commit()
            return reservation_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _reserve_import_staging_bytes(reservation_id: str, additional_bytes: int) -> None:
    increment = int(additional_bytes)
    if increment <= 0:
        return
    db = SessionLocal()
    try:
        reservation = (
            db.query(ImportStagingReservation)
            .filter(ImportStagingReservation.id == reservation_id)
            .first()
        )
        if reservation is None:
            raise RuntimeError("Import staging reservation is unavailable")
        principal_id = str(reservation.principal_id)
        with _serialized_import_quota(db, principal_id):
            reservation = (
                db.query(ImportStagingReservation)
                .filter(ImportStagingReservation.id == reservation_id)
                .with_for_update()
                .first()
            )
            if reservation is None:
                raise RuntimeError("Import staging reservation is unavailable")
            current_size = int(reservation.size_bytes or 0)
            if current_size + increment > _max_import_bytes():
                raise _staging_limit_error("import_file_too_large")
            global_bytes = int(
                db.query(func.coalesce(func.sum(ImportStagingReservation.size_bytes), 0)).scalar()
                or 0
            )
            principal_bytes = int(
                db.query(func.coalesce(func.sum(ImportStagingReservation.size_bytes), 0))
                .filter(ImportStagingReservation.principal_id == principal_id)
                .scalar()
                or 0
            )
            if global_bytes + increment > _global_import_staging_bytes():
                raise _staging_limit_error("import_staging_capacity_exceeded")
            if principal_bytes + increment > _principal_import_staging_bytes():
                raise _staging_limit_error("import_staging_quota_exceeded")
            reservation.size_bytes = current_size + increment
            reservation.updated_at = utcnow()
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _release_import_staging_name(staged_name: str) -> bool:
    db = SessionLocal()
    try:
        release_import_staging_reservations(
            db,
            staged_names={staged_name},
            commit=True,
        )
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("Could not release import staging reservation name=%s", staged_name)
        return False
    finally:
        db.close()


def _import_staging_link_state(staged_name: str) -> bool | None:
    """Return linked/unlinked, or ``None`` when commit outcome is unknowable."""

    db = SessionLocal()
    try:
        row = (
            db.query(ImportStagingReservation.worker_job_id)
            .filter(ImportStagingReservation.staged_name == staged_name)
            .first()
        )
        return bool(row and row.worker_job_id)
    except Exception:
        logger.exception("Could not reconcile import enqueue outcome name=%s", staged_name)
        return None
    finally:
        db.close()


def discard_import_staging(staged_name: str) -> bool:
    """Remove staged bytes before releasing their aggregate quota."""

    normalized = str(staged_name or "").strip().lower()
    if not _STAGED_NAME_RE.fullmatch(normalized):
        return False
    target = OPERATIONS_IMPORT_DIR / normalized
    part_paths = list(OPERATIONS_IMPORT_DIR.glob(f".{normalized}.*.part"))
    try:
        target.unlink(missing_ok=True)
        for part_path in part_paths:
            part_path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Could not remove import staging bytes name=%s", normalized)
        return False
    if target.exists() or any(path.exists() for path in part_paths):
        return False
    return _release_import_staging_name(normalized)


def stage_import_stream(
    stream: BinaryIO,
    *,
    extension: str,
    principal_id: str,
    import_kind: str,
) -> str:
    normalized_extension = str(extension or "").strip().lower().lstrip(".")
    if normalized_extension not in {"json", "zip", "csv", "xlsx"}:
        raise ValueError("Unsupported import staging format")
    normalized_principal, normalized_kind = _normalize_import_identity(
        principal_id=principal_id,
        import_kind=import_kind,
    )
    OPERATIONS_IMPORT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{normalized_extension}"
    target = OPERATIONS_IMPORT_DIR / name
    temporary = OPERATIONS_IMPORT_DIR / f".{name}.{os.getpid()}.part"
    reservation_id = _create_import_staging_reservation(
        staged_name=name,
        principal_id=normalized_principal,
        import_kind=normalized_kind,
    )
    try:
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            while True:
                chunk = stream.read(_IMPORT_WRITE_CHUNK_BYTES)
                if not chunk:
                    break
                _reserve_import_staging_bytes(reservation_id, len(chunk))
                handle.write(chunk)
        os.replace(temporary, target)
        return name
    except Exception:
        discard_import_staging(name)
        raise


def stage_import_json(
    payload: Any,
    *,
    principal_id: str,
    import_kind: str,
) -> str:
    import json

    normalized_principal, normalized_kind = _normalize_import_identity(
        principal_id=principal_id,
        import_kind=import_kind,
    )
    OPERATIONS_IMPORT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.json"
    target = OPERATIONS_IMPORT_DIR / name
    temporary = OPERATIONS_IMPORT_DIR / f".{name}.{os.getpid()}.part"
    reservation_id = _create_import_staging_reservation(
        staged_name=name,
        principal_id=normalized_principal,
        import_kind=normalized_kind,
    )
    try:
        encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
        buffered = bytearray()
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            for fragment in encoder.iterencode(payload):
                # JSONEncoder can yield one very large string. Slice it before
                # UTF-8 encoding so staging remains memory-bounded.
                for offset in range(0, len(fragment), 1024 * 1024):
                    buffered.extend(fragment[offset : offset + 1024 * 1024].encode("utf-8"))
                    while len(buffered) >= _IMPORT_WRITE_CHUNK_BYTES:
                        chunk = bytes(buffered[:_IMPORT_WRITE_CHUNK_BYTES])
                        del buffered[:_IMPORT_WRITE_CHUNK_BYTES]
                        _reserve_import_staging_bytes(reservation_id, len(chunk))
                        handle.write(chunk)
            if buffered:
                _reserve_import_staging_bytes(reservation_id, len(buffered))
                handle.write(buffered)
        os.replace(temporary, target)
        return name
    except Exception:
        discard_import_staging(name)
        raise


def _staged_path(name: str) -> Path:
    normalized = str(name or "").strip().lower()
    if not _STAGED_NAME_RE.fullmatch(normalized):
        raise FatalJobError("invalid_staging_reference")
    candidate = (OPERATIONS_IMPORT_DIR / normalized).resolve()
    root = OPERATIONS_IMPORT_DIR.resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise FatalJobError("staged_import_unavailable")
    return candidate


def resolve_operations_result_path(name: str) -> Path:
    normalized = str(name or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}\.json", normalized):
        raise HTTPException(status_code=404, detail="Operation result not found")
    candidate = (OPERATIONS_RESULT_DIR / normalized).resolve()
    root = OPERATIONS_RESULT_DIR.resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Operation result not found")
    return candidate


def clear_operations_staging_after_restore(*, db=None) -> int:
    """Remove snapshot-replayed imports and ephemeral export responses."""

    def _clear_paths() -> int:
        removed = 0
        for root in (OPERATIONS_IMPORT_DIR, OPERATIONS_RESULT_DIR):
            if not root.exists():
                continue
            for path in root.iterdir():
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                removed += 1
        return removed

    if db is None:
        return _clear_paths()
    with _serialized_import_quota(db, "__restore__"):
        removed = _clear_paths()
        # Physical bytes must disappear before their quota rows. Otherwise a
        # failed cleanup followed by service restart could undercount disk.
        db.query(ImportStagingReservation).delete(synchronize_session=False)
        db.commit()
    return removed


def cleanup_import_staging_reservations(
    *,
    cutoff: datetime | None = None,
    batch_size: int = 1000,
) -> int:
    """Reconcile stale reservation rows and orphaned import staging files."""

    current = utcnow()
    missing_grace = current - timedelta(minutes=15)
    file_cutoff = cutoff or (
        current - timedelta(hours=_import_staging_retention_hours())
    )
    limit = max(10, min(int(batch_size), 5000))
    db = SessionLocal()
    removed = 0
    try:
        with _serialized_import_quota(db, "__maintenance__"):
            terminal_query = (
                db.query(ImportStagingReservation)
                .outerjoin(
                    DurableWorkerJob,
                    DurableWorkerJob.id == ImportStagingReservation.worker_job_id,
                )
                .filter(
                    ImportStagingReservation.worker_job_id.is_not(None),
                    or_(
                        DurableWorkerJob.id.is_(None),
                        DurableWorkerJob.status.in_(JOB_TERMINAL_STATUSES),
                    ),
                )
                .order_by(ImportStagingReservation.updated_at.asc())
                .limit(limit)
            )
            if db.get_bind().dialect.name == "postgresql":
                terminal_query = terminal_query.with_for_update(
                    skip_locked=True,
                    of=ImportStagingReservation,
                )
            terminal_rows = terminal_query.all()
            terminal_ids = {str(row.id) for row in terminal_rows}
            remaining = max(0, limit - len(terminal_rows))
            stale_rows = []
            if remaining:
                stale_query = (
                    db.query(ImportStagingReservation)
                    .filter(
                        or_(
                            ImportStagingReservation.expires_at <= current,
                            and_(
                                ImportStagingReservation.updated_at <= missing_grace,
                                ImportStagingReservation.worker_job_id.is_(None),
                            ),
                        ),
                        ~ImportStagingReservation.id.in_(terminal_ids),
                    )
                    .order_by(ImportStagingReservation.expires_at.asc())
                    .limit(remaining)
                )
                if db.get_bind().dialect.name == "postgresql":
                    stale_query = stale_query.with_for_update(
                        skip_locked=True,
                        of=ImportStagingReservation,
                    )
                stale_rows = stale_query.all()
            rows = terminal_rows + stale_rows
            job_ids = {
                str(row.worker_job_id)
                for row in rows
                if row.worker_job_id is not None
            }
            jobs = (
                {
                    str(job.id): str(job.status)
                    for job in db.query(DurableWorkerJob)
                    .filter(DurableWorkerJob.id.in_(job_ids))
                    .all()
                }
                if job_ids
                else {}
            )
            for row in rows:
                name = str(row.staged_name)
                target = OPERATIONS_IMPORT_DIR / name
                part_paths = list(OPERATIONS_IMPORT_DIR.glob(f".{name}.*.part"))
                has_file = target.is_file() or any(path.is_file() for path in part_paths)
                expires_at = row.expires_at
                updated_at = row.updated_at
                if expires_at is not None and expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if updated_at is not None and updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                job_status = jobs.get(str(row.worker_job_id)) if row.worker_job_id else None
                stale = bool(expires_at and expires_at <= current)
                terminal = job_status in JOB_TERMINAL_STATUSES
                missing_job = row.worker_job_id is not None and job_status is None
                missing_file = not has_file and bool(updated_at and updated_at <= missing_grace)
                if not (stale or terminal or missing_job or missing_file):
                    continue
                target.unlink(missing_ok=True)
                for part_path in part_paths:
                    part_path.unlink(missing_ok=True)
                db.delete(row)
                removed += 1
            db.commit()

        # Sweep bounded, old filesystem orphans after reservation reconciliation.
        if OPERATIONS_IMPORT_DIR.exists():
            candidates = []
            candidate_names: set[str] = set()
            for path in list(OPERATIONS_IMPORT_DIR.iterdir())[:limit]:
                try:
                    if (
                        path.is_file()
                        and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                        < file_cutoff
                    ):
                        candidates.append(path)
                        if _STAGED_NAME_RE.fullmatch(path.name):
                            candidate_names.add(path.name)
                        else:
                            part_match = re.fullmatch(
                                r"\.([a-f0-9]{32}\.(?:json|zip|csv|xlsx))\.\d+\.part",
                                path.name,
                            )
                            if part_match:
                                candidate_names.add(part_match.group(1))
                except OSError:
                    logger.debug("Could not inspect import staging file %s", path)
            reserved_names = (
                {
                    str(value)
                    for (value,) in db.query(ImportStagingReservation.staged_name)
                    .filter(ImportStagingReservation.staged_name.in_(candidate_names))
                    .all()
                }
                if candidate_names
                else set()
            )
            for path in candidates:
                staged_reference = path.name
                part_match = re.fullmatch(
                    r"\.([a-f0-9]{32}\.(?:json|zip|csv|xlsx))\.\d+\.part",
                    path.name,
                )
                if part_match:
                    staged_reference = part_match.group(1)
                if staged_reference not in reserved_names:
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed
    except Exception:
        db.rollback()
        logger.exception("Import staging reservation cleanup failed")
        return removed
    finally:
        db.close()


def enqueue_import_job(
    db,
    *,
    kind: str,
    staged_name: str,
    user_id: str,
    options: dict[str, Any] | None = None,
) -> DurableWorkerJob:
    normalized_principal, normalized_kind = _normalize_import_identity(
        principal_id=user_id,
        import_kind=kind,
    )
    normalized_name = str(staged_name or "").strip().lower()
    committed = False
    discard_if_unlinked = False
    try:
        staged_path = _staged_path(normalized_name)
        reservation_query = db.query(ImportStagingReservation).filter(
            ImportStagingReservation.staged_name == normalized_name
        )
        if db.get_bind().dialect.name == "postgresql":
            reservation_query = reservation_query.with_for_update()
        reservation = reservation_query.first()
        if reservation is None:
            # An unaccounted staged file must not survive a failed enqueue.
            discard_if_unlinked = True
            raise RuntimeError("Import staging reservation does not match the staged file")
        if (
            str(reservation.principal_id) != normalized_principal
            or str(reservation.import_kind) != normalized_kind
            or reservation.worker_job_id is not None
        ):
            # Never let a mismatched or duplicate caller discard another
            # principal's bytes or the input of an already queued job.
            raise RuntimeError("Import staging reservation does not match the staged file")
        discard_if_unlinked = True
        if int(reservation.size_bytes or 0) != int(staged_path.stat().st_size):
            raise RuntimeError("Import staging reservation does not match the staged file")
        expires_at = reservation.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is not None and expires_at <= utcnow():
            raise RuntimeError("Import staging reservation expired before enqueue")

        job = enqueue_worker_job(
            db,
            queue=QUEUE_OPERATIONS,
            kind=normalized_kind,
            user_id=normalized_principal,
            payload={"staged_name": normalized_name, "options": dict(options or {})},
            idempotency_key=f"import:{normalized_kind}:{normalized_name}",
            priority=10,
            max_attempts=1,
            expires_at=utcnow() + timedelta(days=3),
            commit=False,
        )
        reservation.worker_job_id = str(job.id)
        reservation.updated_at = utcnow()
        db.commit()
        # Keep the returned identifier usable after async callers close their
        # thread-owned Session.
        db.refresh(job)
        committed = True
        return job
    except Exception:
        if not committed:
            try:
                db.rollback()
            except Exception:
                logger.exception("Could not roll back import enqueue name=%s", normalized_name)
            # A connection can fail after PostgreSQL committed. Re-read with a
            # fresh session and never delete bytes for a possibly queued job.
            link_state = _import_staging_link_state(normalized_name)
            if discard_if_unlinked and link_state is False:
                discard_import_staging(normalized_name)
        raise


def _enqueue_import_job_with_thread_session(**kwargs: Any) -> DurableWorkerJob:
    session = SessionLocal()
    try:
        return enqueue_import_job(session, **kwargs)
    finally:
        session.close()


async def enqueue_import_job_async(**kwargs: Any) -> DurableWorkerJob:
    """Validate the staging reservation and enqueue using a thread-owned DB session."""

    return await run_blocking_io(
        partial(_enqueue_import_job_with_thread_session, **kwargs)
    )


def wait_for_operations_result(job: DurableWorkerJob) -> dict[str, Any]:
    try:
        timeout = float(os.getenv("OPERATIONS_REQUEST_WAIT_SECONDS", "600") or "600")
    except (TypeError, ValueError):
        timeout = 600.0
    try:
        return wait_for_worker_job(job.id, timeout_seconds=timeout)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "operation_still_processing", "job_id": job.id},
            headers={"Retry-After": "3"},
        ) from exc
    except WorkerJobFailed as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "job_id": job.id},
        ) from exc


async def wait_for_operations_result_async(
    job: DurableWorkerJob,
) -> dict[str, Any]:
    try:
        timeout = float(os.getenv("OPERATIONS_REQUEST_WAIT_SECONDS", "600") or "600")
    except (TypeError, ValueError):
        timeout = 600.0
    try:
        return await wait_for_worker_job_async(job.id, timeout_seconds=timeout)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "operation_still_processing", "job_id": job.id},
            headers={"Retry-After": "3"},
        ) from exc
    except WorkerJobFailed as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "job_id": job.id},
        ) from exc


def enqueue_user_data_export(db, *, user_id: str) -> DurableWorkerJob:
    request_id = uuid.uuid4().hex
    return enqueue_worker_job(
        db,
        queue=QUEUE_OPERATIONS,
        kind="user_data_export",
        user_id=str(user_id),
        payload={"result_name": f"{request_id}.json"},
        idempotency_key=f"user-data-export:{user_id}:{request_id}",
        priority=30,
        max_attempts=1,
        expires_at=utcnow() + timedelta(days=1),
        commit=True,
    )


def _require_active_user(
    db,
    user_id: str | None,
    *,
    administrative: bool = False,
    owner: bool = False,
    lock: bool = False,
) -> None:
    from app.users.models import User
    from app.users.roles import is_admin_role, is_owner_role

    normalized_user_id = str(user_id or "").strip()
    query = db.query(User).filter(User.id == normalized_user_id)
    if lock and db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    user = query.first()
    if (
        user is None
        or user.deleted_at is not None
        or not bool(user.is_active)
        or str(user.role or "").strip().lower() == "pending"
    ):
        raise FatalJobError("user_unavailable")
    if owner and not is_owner_role(user.role):
        raise FatalJobError("authorization_changed")
    if administrative and not is_admin_role(user.role):
        raise FatalJobError("authorization_changed")


def _require_user_data_control(db, user_id: str | None) -> None:
    """Revalidate self-service data authority at the execution boundary."""

    from app.groups.init import get_user_group_setting_value

    try:
        allowed = get_user_group_setting_value(
            str(user_id or ""),
            "data_controls",
            "allow_user_data",
            db,
            commit=False,
        )
    except Exception as exc:
        raise FatalJobError("authorization_changed") from exc
    if not bool(allowed):
        raise FatalJobError("authorization_changed")


def _load_staged_json(path: Path):
    import json

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _import(job: WorkerJobSnapshot, context: WorkerContext) -> dict:
    path = _staged_path(str(job.payload.get("staged_name") or ""))
    options = job.payload.get("options") if isinstance(job.payload.get("options"), dict) else {}
    db = SessionLocal()
    db_log = AuditSessionLocal()
    try:
        context.raise_if_cancelled()
        administrative = job.kind in {
            "import_openwebui_single",
            "import_openwebui_bulk",
            "import_admin_users",
            "import_bulk_users",
        }
        _require_active_user(
            db,
            job.user_id,
            administrative=administrative,
            owner=bool(
                job.kind == "import_admin_users"
                and options.get("allow_administrative_targets")
            ),
            lock=True,
        )
        if job.kind in {"import_user_self", "import_chatgpt"}:
            _require_user_data_control(db, job.user_id)
        if job.kind == "import_user_self":
            from app.users.data_import import import_user_data_for_existing_user

            result = import_user_data_for_existing_user(
                str(job.user_id),
                _load_staged_json(path),
                db,
                db_log,
            )
        elif job.kind == "import_chatgpt":
            from app.chats.chatgpt_import import import_chatgpt_export_archive

            with path.open("rb") as archive:
                result = import_chatgpt_export_archive(
                    db,
                    str(job.user_id),
                    archive,
                    archive_name=str(options.get("archive_name") or "chatgpt-export.zip")[:255],
                )
        elif job.kind == "import_openwebui_single":
            from app.admin.chat_imports.models import import_openwebui_chats

            payload = _load_staged_json(path)
            result = import_openwebui_chats(
                db,
                str(options.get("target_user_id") or ""),
                payload,
                force_archived=bool(options.get("force_archived")),
            )
        elif job.kind == "import_openwebui_bulk":
            from app.admin.chat_imports.models import import_openwebui_chats_bulk

            payload = _load_staged_json(path)
            result = import_openwebui_chats_bulk(
                db,
                str(payload.get("users_csv") or ""),
                payload.get("chats") if isinstance(payload.get("chats"), list) else [],
            )
        elif job.kind == "import_admin_users":
            import zipfile
            from app.admin.user_exports.utils import import_admin_users_archive

            with zipfile.ZipFile(path) as archive:
                result = import_admin_users_archive(
                    db,
                    archive,
                    selected_indices=options.get("selected_indices"),
                    import_options=options.get("import_options") or {},
                    allow_administrative_targets=bool(options.get("allow_administrative_targets")),
                )
        elif job.kind == "import_bulk_users":
            from app.admin.users.utils import create_users_from_csv, create_users_from_xlsx

            content = path.read_bytes()
            importer = create_users_from_xlsx if path.suffix == ".xlsx" else create_users_from_csv
            result = importer(
                content,
                db,
                default_password=str(options.get("default_password") or ""),
                force_password_change=bool(options.get("force_password_change", True)),
            )
            result["file_type"] = path.suffix.lstrip(".")
        else:
            raise FatalJobError("unsupported_import")
        if not isinstance(result, dict):
            raise FatalJobError("invalid_import_result")
        return result
    except FatalJobError:
        raise
    except Exception as exc:
        logger.exception("Operations import failed kind=%s", job.kind)
        raise FatalJobError("import_failed") from exc
    finally:
        db_log.close()
        db.close()
        discard_import_staging(path.name)


def _required_id(job: WorkerJobSnapshot, field: str) -> str:
    value = str(job.payload.get(field) or "").strip()
    if not value:
        raise FatalJobError("invalid_payload")
    return value


def _backup(job: WorkerJobSnapshot, context: WorkerContext) -> dict:
    from app.backups.service import run_backup_job_sync

    context.raise_if_cancelled()
    if job.user_id:
        db = SessionLocal()
        try:
            _require_active_user(db, job.user_id, administrative=True)
        finally:
            db.close()
    row = run_backup_job_sync(_required_id(job, "backup_job_id"))
    if str(row.status) != "success":
        raise FatalJobError("backup_failed")
    return {"backup_job_id": str(row.id), "status": str(row.status)}


def _restore(job: WorkerJobSnapshot, context: WorkerContext) -> dict:
    from app.backups.service import run_restore_job_sync

    context.raise_if_cancelled()
    if job.user_id:
        db = SessionLocal()
        try:
            _require_active_user(db, job.user_id, administrative=True)
        finally:
            db.close()
    row = run_restore_job_sync(_required_id(job, "restore_job_id"))
    if str(row.status) != "success":
        raise FatalJobError("restore_failed")
    return {"restore_job_id": str(row.id), "status": str(row.status)}


def _admin_user_export(job: WorkerJobSnapshot, context: WorkerContext) -> dict:
    from app.admin.user_exports.jobs.utils import run_admin_user_export_job_sync

    context.raise_if_cancelled()
    if job.user_id:
        db = SessionLocal()
        try:
            _require_active_user(db, job.user_id, administrative=True)
        finally:
            db.close()
    row = run_admin_user_export_job_sync(_required_id(job, "export_job_id"))
    if str(row.status) != "success":
        raise FatalJobError("admin_user_export_failed")
    return {"export_job_id": str(row.id), "status": str(row.status)}


def _user_data_export(job: WorkerJobSnapshot, context: WorkerContext) -> dict:
    from app.users.utils import build_user_data_export_json_file

    result_name = str(job.payload.get("result_name") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}\.json", result_name):
        raise FatalJobError("invalid_payload")
    OPERATIONS_RESULT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = OPERATIONS_RESULT_DIR / result_name
    temporary = OPERATIONS_RESULT_DIR / f".{result_name}.{os.getpid()}.part"
    db = SessionLocal()
    db_log = AuditSessionLocal()
    export_file = None
    try:
        context.raise_if_cancelled()
        _require_active_user(db, job.user_id)
        _require_user_data_control(db, job.user_id)
        export_file = build_user_data_export_json_file(
            str(job.user_id),
            db,
            db_log,
            include_file_contents=True,
            include_deleted_or_temp_chats=True,
        )
        with temporary.open("xb") as output:
            os.chmod(temporary, 0o600)
            while True:
                context.raise_if_cancelled()
                chunk = export_file.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        os.replace(temporary, target)
        return {"result_name": result_name, "size_bytes": target.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)
        if export_file is not None:
            export_file.close()
        db_log.close()
        db.close()


HANDLERS = {
    "backup": _backup,
    "restore": _restore,
    "admin_user_export": _admin_user_export,
    "user_data_export": _user_data_export,
    "import_user_self": _import,
    "import_chatgpt": _import,
    "import_openwebui_single": _import,
    "import_openwebui_bulk": _import,
    "import_admin_users": _import,
    "import_bulk_users": _import,
}


def reconcile_interrupted_operation_jobs(*, batch_size: int = 1000) -> int:
    """Close domain job rows left active after an at-most-once worker died.

    Backup and restore side effects cannot safely be replayed after an unknown
    process failure. The durable queue therefore records a terminal failure,
    while this reconciliation step ensures the operator-facing catalog cannot
    remain misleadingly queued or running forever.
    """

    from app.admin.export_jobs.models import AdminUserExportJob
    from app.backups.models import BackupJob, RestoreJob

    db = SessionLocal()
    reconciled = 0
    try:
        rows = lock_unreconciled_terminal_jobs(
            db,
            queue=QUEUE_OPERATIONS,
            kinds=("backup", "restore", "admin_user_export"),
            batch_size=batch_size,
        )
        now = utcnow()
        for worker_job in rows:
            prefix, _, domain_job_id = str(worker_job.idempotency_key or "").partition(":")
            error_code = str(worker_job.error_code or "worker_interrupted")[:64]
            if not domain_job_id:
                worker_job.reconciled_at = now
                worker_job.updated_at = now
                continue

            if prefix == "backup":
                domain_job = db.query(BackupJob).filter(BackupJob.id == domain_job_id).first()
                if domain_job is not None and domain_job.status in {"queued", "running"}:
                    domain_job.status = "failed"
                    domain_job.error = error_code
                    domain_job.finished_at = now
                    domain_job.updated_at = now
                    reconciled += 1
            elif prefix == "restore":
                domain_job = db.query(RestoreJob).filter(RestoreJob.id == domain_job_id).first()
                if domain_job is not None and domain_job.status in {"queued", "running"}:
                    domain_job.status = "failed"
                    domain_job.error = error_code
                    domain_job.finished_at = now
                    domain_job.updated_at = now
                    reconciled += 1
            elif prefix == "admin-user-export":
                domain_job = (
                    db.query(AdminUserExportJob)
                    .filter(AdminUserExportJob.id == domain_job_id)
                    .first()
                )
                if domain_job is not None and domain_job.status in {"queued", "running"}:
                    domain_job.status = "failed"
                    domain_job.error = error_code
                    domain_job.finished_at = now
                    domain_job.updated_at = now
                    reconciled += 1

            # Mark every inspected row so already-terminal domain jobs cannot
            # starve older reconciliation work in a large instance.
            worker_job.reconciled_at = now
            worker_job.updated_at = now

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Interrupted operations job reconciliation failed")
    finally:
        db.close()
    return reconciled


class OperationsWorker(DurableQueueWorker):
    """Operations queue plus its lightweight backup schedule coordinator."""

    def __init__(self) -> None:
        super().__init__(
            queue=QUEUE_OPERATIONS,
            handlers=HANDLERS,
            reconciler=reconcile_interrupted_operation_jobs,
            default_lease_seconds=300,
        )
        self._scheduler_started = False

    def run_forever(self) -> None:
        from app.backups.scheduler import start_backup_scheduler_worker, stop_backup_scheduler_worker

        start_backup_scheduler_worker()
        self._scheduler_started = True
        try:
            super().run_forever()
        finally:
            if self._scheduler_started:
                stop_backup_scheduler_worker()


def build_worker() -> OperationsWorker:
    return OperationsWorker()


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
