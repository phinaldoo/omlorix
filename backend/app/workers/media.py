from __future__ import annotations

import base64
from datetime import timedelta
from functools import partial
import hashlib
import logging
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from typing import Any, BinaryIO

import anyio
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from starlette.datastructures import Headers

from app.database import AuditSessionLocal, SessionLocal
from app.paths import DATA_DIR
from app.utils.blocking_io import run_blocking_io
from app.utils.db import release_db_session_before_long_wait
from app.utils.encryption import get_cipher_suite
from app.workers.models import (
    QUEUE_MEDIA,
    DurableWorkerJob,
    WorkerJobSnapshot,
    enqueue_worker_job,
    lock_unreconciled_terminal_jobs,
    utcnow,
    wait_for_worker_job,
    wait_for_worker_job_async,
)
from app.workers.runtime import DurableQueueWorker, FatalJobError, WorkerContext, run_worker_cli
from app.workers.tool_jobs import execute_tool_job


logger = logging.getLogger(__name__)
MEDIA_STAGING_DIR = Path(os.getenv("MEDIA_STAGING_DIR") or (DATA_DIR / "media-staging"))
_STAGED_MEDIA_RE = re.compile(r"^[a-f0-9]{32}\.(?:audio|meeting)$")
_MEETING_STAGING_MAGIC = b"OMLORIX-MEETING-1\x00"
_MEETING_STAGING_NONCE_BYTES = 12
_MEETING_STAGING_TAG_BYTES = 16
_MEETING_STAGING_CHUNK_BYTES = 1024 * 1024


class MeetingUploadTooLarge(ValueError):
    pass


class MeetingUploadEmpty(ValueError):
    pass


def stage_transcription_audio(content: bytes) -> str:
    """Encrypt an upload before handing its reference to the media worker."""

    MEDIA_STAGING_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.audio"
    target = MEDIA_STAGING_DIR / name
    temporary = MEDIA_STAGING_DIR / f".{name}.{os.getpid()}.part"
    try:
        ciphertext = get_cipher_suite().encrypt(bytes(content))
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(ciphertext)
        os.replace(temporary, target)
        return name
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _media_staging_key() -> bytes:
    encoded = str(os.getenv("ENCRYPTION_KEY") or "").strip().encode("ascii")
    try:
        root_key = base64.urlsafe_b64decode(encoded)
    except Exception as exc:
        raise ValueError("ENCRYPTION_KEY is invalid") from exc
    if len(root_key) != 32:
        raise ValueError("ENCRYPTION_KEY is invalid")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"omlorix-media-worker-staging-v1",
    ).derive(root_key)


def _stage_meeting_media_file(
    source: BinaryIO,
    *,
    max_bytes: int,
) -> tuple[str, int]:
    """Encrypt one upload on a worker thread without blocking the event loop."""

    MEDIA_STAGING_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.meeting"
    target = MEDIA_STAGING_DIR / name
    temporary = MEDIA_STAGING_DIR / f".{name}.{os.getpid()}.part"
    nonce = os.urandom(_MEETING_STAGING_NONCE_BYTES)
    encryptor = Cipher(
        algorithms.AES(_media_staging_key()),
        modes.GCM(nonce),
    ).encryptor()
    encryptor.authenticate_additional_data(_MEETING_STAGING_MAGIC + nonce)
    written = 0
    try:
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(_MEETING_STAGING_MAGIC)
            handle.write(nonce)
            while True:
                chunk = source.read(_MEETING_STAGING_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > max(1, int(max_bytes)):
                    raise MeetingUploadTooLarge()
                handle.write(encryptor.update(chunk))
            if written <= 0:
                raise MeetingUploadEmpty()
            handle.write(encryptor.finalize())
            handle.write(encryptor.tag)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return name, written
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


async def stage_meeting_media_upload(
    media: UploadFile,
    *,
    max_bytes: int,
) -> tuple[str, int]:
    """Stage a large encrypted upload without doing file or crypto I/O on ASGI."""

    return await run_blocking_io(
        partial(
            _stage_meeting_media_file,
            media.file,
            max_bytes=max_bytes,
        )
    )


def _staged_media_path(name: str) -> Path:
    normalized = str(name or "").strip().lower()
    if not _STAGED_MEDIA_RE.fullmatch(normalized):
        raise FatalJobError("invalid_staging_reference")
    candidate = (MEDIA_STAGING_DIR / normalized).resolve()
    root = MEDIA_STAGING_DIR.resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise FatalJobError("staged_audio_unavailable")
    return candidate


def _staged_audio_path(name: str) -> Path:
    path = _staged_media_path(name)
    if path.suffix != ".audio":
        raise FatalJobError("invalid_staging_reference")
    return path


def discard_media_staging(name: str) -> None:
    try:
        _staged_media_path(name).unlink(missing_ok=True)
    except FatalJobError:
        return


def _decrypt_staged_meeting(source: Path, target: Path) -> None:
    minimum_size = (
        len(_MEETING_STAGING_MAGIC)
        + _MEETING_STAGING_NONCE_BYTES
        + _MEETING_STAGING_TAG_BYTES
    )
    size = source.stat().st_size
    if size <= minimum_size:
        raise FatalJobError("staged_meeting_invalid")
    with source.open("rb") as encrypted:
        magic = encrypted.read(len(_MEETING_STAGING_MAGIC))
        nonce = encrypted.read(_MEETING_STAGING_NONCE_BYTES)
        if magic != _MEETING_STAGING_MAGIC or len(nonce) != _MEETING_STAGING_NONCE_BYTES:
            raise FatalJobError("staged_meeting_invalid")
        encrypted.seek(-_MEETING_STAGING_TAG_BYTES, os.SEEK_END)
        tag = encrypted.read(_MEETING_STAGING_TAG_BYTES)
        ciphertext_bytes = size - minimum_size
        encrypted.seek(len(_MEETING_STAGING_MAGIC) + _MEETING_STAGING_NONCE_BYTES)
        decryptor = Cipher(
            algorithms.AES(_media_staging_key()),
            modes.GCM(nonce, tag),
        ).decryptor()
        decryptor.authenticate_additional_data(_MEETING_STAGING_MAGIC + nonce)
        remaining = ciphertext_bytes
        try:
            with target.open("xb") as plaintext:
                os.chmod(target, 0o600)
                while remaining:
                    chunk = encrypted.read(min(_MEETING_STAGING_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise FatalJobError("staged_meeting_invalid")
                    remaining -= len(chunk)
                    plaintext.write(decryptor.update(chunk))
                plaintext.write(decryptor.finalize())
                plaintext.flush()
                os.fsync(plaintext.fileno())
        except Exception:
            target.unlink(missing_ok=True)
            raise


def enqueue_transcription_job(
    *,
    user_id: str,
    audio_bytes: bytes,
    filename: str,
    provider_id: str,
    model_name: str,
    measured_duration: float | None,
    admission_id: str | None,
    audit_ip_address: str | None,
    audit_user_agent: str | None,
) -> DurableWorkerJob:
    staged_name = stage_transcription_audio(audio_bytes)
    session = SessionLocal()
    try:
        return enqueue_worker_job(
            session,
            queue=QUEUE_MEDIA,
            kind="transcribe",
            user_id=str(user_id),
            payload={
                "staged_name": staged_name,
                "filename": str(filename or "audio")[:255],
                "provider_id": str(provider_id),
                "model_name": str(model_name),
                "measured_duration": measured_duration,
                "admission_id": admission_id,
                "audit_ip_address": audit_ip_address,
                "audit_user_agent": audit_user_agent,
            },
            idempotency_key=f"transcribe:{staged_name}:{admission_id or 'none'}",
            priority=5,
            max_attempts=1,
            expires_at=utcnow() + timedelta(hours=24),
            commit=True,
        )
    except Exception:
        try:
            _staged_audio_path(staged_name).unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        session.close()


async def enqueue_transcription_job_async(**kwargs: Any) -> DurableWorkerJob:
    """Stage and enqueue a potentially large transcription away from ASGI."""

    return await run_blocking_io(partial(enqueue_transcription_job, **kwargs))


def _media_wait_timeout() -> float:
    try:
        timeout = float(os.getenv("MEDIA_REQUEST_WAIT_SECONDS", "1200") or "1200")
    except (TypeError, ValueError):
        timeout = 1200.0
    return max(1.0, min(timeout, 3600.0))


def wait_for_transcription(job: DurableWorkerJob) -> dict[str, Any]:
    return wait_for_worker_job(job.id, timeout_seconds=_media_wait_timeout())


async def wait_for_transcription_async(job: DurableWorkerJob) -> dict[str, Any]:
    return await wait_for_worker_job_async(
        job.id,
        timeout_seconds=_media_wait_timeout(),
    )


def enqueue_read_aloud_job(
    *,
    user_id: str,
    message_id: str,
    expected_text_sha256: str,
    audit_ip_address: str | None,
    audit_user_agent: str | None,
) -> DurableWorkerJob:
    return _enqueue_media_job(
        kind="read_aloud",
        user_id=user_id,
        payload={
            "message_id": str(message_id),
            "expected_text_sha256": str(expected_text_sha256),
            "audit_ip_address": audit_ip_address,
            "audit_user_agent": audit_user_agent,
        },
        idempotency_key=f"read-aloud:{user_id}:{message_id}:{uuid.uuid4().hex}",
        priority=10,
    )


def enqueue_meeting_transcript_job(
    *,
    user_id: str,
    staged_name: str,
    filename: str,
    content_type: str | None,
    chat_id: str | None,
    project_id: str | None,
    browser_date_iso: str | None,
    browser_date_label: str | None,
    consent_confirmed: bool,
    legal_basis: str | None,
    legal_basis_details: str | None,
    retention_days: int | None,
    audit_ip_address: str | None,
    audit_user_agent: str | None,
) -> DurableWorkerJob:
    return _enqueue_media_job(
        kind="meeting_transcript",
        user_id=user_id,
        payload={
            "staged_name": staged_name,
            "filename": str(filename or "meeting")[:255],
            "content_type": str(content_type or "")[:255] or None,
            "chat_id": str(chat_id or "").strip() or None,
            "project_id": str(project_id or "").strip() or None,
            "browser_date_iso": str(browser_date_iso or "")[:255] or None,
            "browser_date_label": str(browser_date_label or "")[:255] or None,
            "consent_confirmed": bool(consent_confirmed),
            "legal_basis": str(legal_basis or "")[:64] or None,
            "legal_basis_details": str(legal_basis_details or "") or None,
            "retention_days": retention_days,
            "audit_ip_address": audit_ip_address,
            "audit_user_agent": audit_user_agent,
        },
        idempotency_key=f"meeting-transcript:{staged_name}",
        priority=5,
    )


async def enqueue_meeting_transcript_job_async(**kwargs: Any) -> DurableWorkerJob:
    """Commit a meeting job without blocking the request event loop."""

    return await run_blocking_io(partial(enqueue_meeting_transcript_job, **kwargs))


def _enqueue_media_job(
    *,
    kind: str,
    user_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
    priority: int,
) -> DurableWorkerJob:
    session = SessionLocal()
    try:
        return enqueue_worker_job(
            session,
            queue=QUEUE_MEDIA,
            kind=kind,
            user_id=str(user_id),
            payload=payload,
            idempotency_key=idempotency_key,
            priority=priority,
            # Provider media calls can complete before the process records its
            # result. Never guess and replay an ambiguous external side effect.
            max_attempts=1,
            expires_at=utcnow() + timedelta(hours=24),
            commit=True,
        )
    finally:
        session.close()


def wait_for_media_job(job: DurableWorkerJob) -> dict[str, Any]:
    return wait_for_transcription(job)


async def wait_for_media_job_async(job: DurableWorkerJob) -> dict[str, Any]:
    return await wait_for_transcription_async(job)


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


def _audit_media_event(
    *,
    user_id: str,
    action: str,
    details: dict[str, Any],
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    try:
        from app.logging.models import create_audit_log

        create_audit_log(
            db_log=AuditSessionLocal(),
            user_id=user_id,
            action=action,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
            category="llm" if action == "AUDIO_TRANSCRIBED" else "chats",
        )
    except Exception:
        logger.exception("Could not enqueue media-worker audit event action=%s", action)


def _handle_transcription(job: WorkerJobSnapshot, context: WorkerContext) -> dict[str, Any]:
    from app.llm.models import (
        RATE_LIMIT_ADMISSION_COMPLETED,
        RATE_LIMIT_ADMISSION_FAILED,
        finalize_duration_rate_limit_admission,
        renew_dictation_duration_rate_limit_lease,
    )
    from app.llm.speech import (
        get_transcription_runtime_for_provider,
        snapshot_transcription_provider,
        transcribe_audio_bytes_for_provider,
    )
    from app.settings.models import get_settings_page

    staged_name = str(job.payload.get("staged_name") or "")
    path = _staged_audio_path(staged_name)
    admission_id = str(job.payload.get("admission_id") or "").strip() or None
    session = SessionLocal()
    admission_finalized = False
    try:
        user = _active_user(session, str(job.user_id or ""))
        user_id = str(user.id)
        settings = get_settings_page(session, "dictation")
        data = settings.data if settings and isinstance(settings.data, dict) else {}
        provider_id = str(job.payload.get("provider_id") or "").strip()
        model_name = str(job.payload.get("model_name") or "").strip()
        if (
            not bool(data.get("transcription_enabled"))
            or str(data.get("transcription_provider_id") or "").strip() != provider_id
            or str(data.get("transcription_model") or "").strip() != model_name
        ):
            raise FatalJobError("transcription_configuration_changed")
        runtime = get_transcription_runtime_for_provider(session, provider_id)
        if model_name not in runtime["models"]:
            raise FatalJobError("transcription_configuration_changed")
        filename = str(job.payload.get("filename") or "audio")[:255]
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in runtime["allowed_formats"]:
            raise FatalJobError("unsupported_audio_format")
        provider = snapshot_transcription_provider(runtime["provider"])
        release_db_session_before_long_wait(session)
        try:
            audio_bytes = get_cipher_suite().decrypt(path.read_bytes())
        except Exception as exc:
            raise FatalJobError("staged_audio_invalid") from exc
        context.raise_if_cancelled()

        async def transcribe() -> str:
            async with anyio.create_task_group() as task_group:
                if admission_id:
                    task_group.start_soon(
                        renew_dictation_duration_rate_limit_lease,
                        admission_id,
                    )
                try:
                    return await transcribe_audio_bytes_for_provider(
                        provider,
                        model_name=model_name,
                        audio_bytes=audio_bytes,
                        filename=filename,
                    )
                finally:
                    task_group.cancel_scope.cancel()

        text = anyio.run(transcribe)
        context.raise_if_cancelled()
        measured_duration = float(job.payload.get("measured_duration") or 0)
        if admission_id:
            finalize_duration_rate_limit_admission(
                session,
                admission_id,
                consumed_seconds=max(1, int(math.ceil(measured_duration))),
                final_status=RATE_LIMIT_ADMISSION_COMPLETED,
            )
            admission_finalized = True

        _audit_media_event(
            user_id=user_id,
            action="AUDIO_TRANSCRIBED",
            details={
                "provider_id": provider_id,
                "model_name": model_name,
                "filename": filename,
                "audio_bytes": len(audio_bytes),
                "audio_duration_seconds": round(measured_duration, 3),
            },
            ip_address=job.payload.get("audit_ip_address"),
            user_agent=job.payload.get("audit_user_agent"),
        )
        return {"text": text}
    except Exception:
        session.rollback()
        if admission_id and not admission_finalized:
            try:
                finalize_duration_rate_limit_admission(
                    session,
                    admission_id,
                    consumed_seconds=0,
                    final_status=RATE_LIMIT_ADMISSION_FAILED,
                )
                admission_finalized = True
            except Exception:
                session.rollback()
                logger.exception("Could not finalize failed dictation admission")
        raise
    finally:
        path.unlink(missing_ok=True)
        session.close()


def _handle_read_aloud(job: WorkerJobSnapshot, context: WorkerContext) -> dict[str, Any]:
    from app.chats.read_aloud import (
        get_or_create_read_aloud_file,
        get_owned_assistant_message_read_aloud_text,
    )

    session = SessionLocal()
    try:
        user = _active_user(session, str(job.user_id or ""))
        message_id = str(job.payload.get("message_id") or "").strip()
        expected_hash = str(job.payload.get("expected_text_sha256") or "").strip()
        if not message_id or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
            raise FatalJobError("invalid_payload")
        try:
            canonical_text = get_owned_assistant_message_read_aloud_text(
                session,
                user_id=user.id,
                message_id=message_id,
            )
        except ValueError as exc:
            raise FatalJobError("read_aloud_message_unavailable") from exc
        if hashlib.sha256(canonical_text.encode("utf-8")).hexdigest() != expected_hash:
            raise FatalJobError("read_aloud_message_changed")
        context.raise_if_cancelled()
        try:
            file_record, was_cached = get_or_create_read_aloud_file(
                session,
                user_id=user.id,
                message_id=message_id,
                text=canonical_text,
            )
        except (HTTPException, ValueError, RuntimeError) as exc:
            raise FatalJobError("read_aloud_unavailable") from exc
        context.raise_if_cancelled()
        _audit_media_event(
            user_id=user.id,
            action="CHAT_MESSAGE_READ_ALOUD",
            details={"message_id": message_id, "file_id": file_record.id},
            ip_address=job.payload.get("audit_ip_address"),
            user_agent=job.payload.get("audit_user_agent"),
        )
        return {"file_id": str(file_record.id), "cached": bool(was_cached)}
    finally:
        session.close()


def _handle_meeting_transcript(
    job: WorkerJobSnapshot,
    context: WorkerContext,
) -> dict[str, Any]:
    from app.chats.meeting_transcripts import create_meeting_transcript

    staged_name = str(job.payload.get("staged_name") or "")
    staged_path = _staged_media_path(staged_name)
    if staged_path.suffix != ".meeting":
        raise FatalJobError("invalid_staging_reference")
    session = SessionLocal()
    try:
        user = _active_user(session, str(job.user_id or ""))
        context.raise_if_cancelled()
        with tempfile.TemporaryDirectory(prefix="meeting-worker-") as temp_dir:
            plaintext_path = Path(temp_dir) / "upload.media"
            try:
                _decrypt_staged_meeting(staged_path, plaintext_path)
            except FatalJobError:
                raise
            except Exception as exc:
                raise FatalJobError("staged_meeting_invalid") from exc
            context.raise_if_cancelled()
            with plaintext_path.open("rb") as source:
                upload = UploadFile(
                    file=source,
                    filename=str(job.payload.get("filename") or "meeting")[:255],
                    headers=Headers(
                        {"content-type": str(job.payload.get("content_type") or "")}
                    ),
                )

                async def run_workflow() -> dict[str, Any]:
                    return await create_meeting_transcript(
                        db=session,
                        user_id=user.id,
                        user_role=user.role,
                        media=upload,
                        chat_id=job.payload.get("chat_id"),
                        project_id=job.payload.get("project_id"),
                        browser_date_iso=job.payload.get("browser_date_iso"),
                        browser_date_label=job.payload.get("browser_date_label"),
                        consent_confirmed=job.payload.get("consent_confirmed"),
                        legal_basis=job.payload.get("legal_basis"),
                        legal_basis_details=job.payload.get("legal_basis_details"),
                        retention_days=job.payload.get("retention_days"),
                    )

                try:
                    result = anyio.run(run_workflow)
                except HTTPException as exc:
                    session.rollback()
                    if exc.status_code < 500:
                        raise FatalJobError("meeting_request_invalid") from exc
                    raise FatalJobError("meeting_transcription_failed") from exc
        context.raise_if_cancelled()
        _audit_media_event(
            user_id=user.id,
            action="MEETING_TRANSCRIBED",
            details={
                "chat_id": result.get("chat_id"),
                "project_id": result.get("project_id"),
                "filename": job.payload.get("filename"),
            },
            ip_address=job.payload.get("audit_ip_address"),
            user_agent=job.payload.get("audit_user_agent"),
        )
        return jsonable_encoder(result)
    finally:
        staged_path.unlink(missing_ok=True)
        session.close()


def reconcile_terminal_media_jobs(*, batch_size: int = 1000) -> int:
    from app.llm.models import RATE_LIMIT_ADMISSION_FAILED, finalize_duration_rate_limit_admission

    session = SessionLocal()
    try:
        rows = lock_unreconciled_terminal_jobs(
            session,
            queue=QUEUE_MEDIA,
            kinds=("transcribe", "meeting_transcript"),
            batch_size=batch_size,
        )
        current = utcnow()
        for row in rows:
            parts = str(row.idempotency_key or "").split(":", 2)
            staged_name = ""
            admission_id = None
            if row.kind == "transcribe" and len(parts) == 3 and parts[0] == "transcribe":
                staged_name = parts[1]
                admission_id = parts[2] if parts[2] != "none" else None
            elif (
                row.kind == "meeting_transcript"
                and len(parts) >= 2
                and parts[0] == "meeting-transcript"
            ):
                staged_name = parts[1]
            if staged_name:
                try:
                    _staged_media_path(staged_name).unlink(missing_ok=True)
                except FatalJobError:
                    pass
            if admission_id:
                finalize_duration_rate_limit_admission(
                    session,
                    admission_id,
                    consumed_seconds=0,
                    final_status=RATE_LIMIT_ADMISSION_FAILED,
                )
            row.reconciled_at = current
            row.updated_at = current
        session.commit()
        return len(rows)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def clear_media_staging_after_restore() -> int:
    removed = 0
    if not MEDIA_STAGING_DIR.exists():
        return removed
    for path in MEDIA_STAGING_DIR.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        removed += 1
    return removed


def build_worker() -> DurableQueueWorker:
    return DurableQueueWorker(
        queue=QUEUE_MEDIA,
        handlers={
            "tool_call": execute_tool_job,
            "transcribe": _handle_transcription,
            "read_aloud": _handle_read_aloud,
            "meeting_transcript": _handle_meeting_transcript,
        },
        reconciler=reconcile_terminal_media_jobs,
        default_lease_seconds=900,
    )


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
