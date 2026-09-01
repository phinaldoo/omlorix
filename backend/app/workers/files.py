from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any
import uuid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.files.models import FileProcessingArtifact, Files
from app.files.pdf_preview import (
    PDF_PREVIEW_MAX_PAGES,
    extract_pdf_preview_page,
    inspect_pdf_preview_document,
    render_pdf_preview_page_png,
)
from app.files.schemas import SUPPORTED_EXTRACT_TEXT_MIME_TYPES
from app.files.utils import materialize_file_record, resolve_accessible_file_record
from app.paths import DATA_DIR
from app.users.models import User
from app.workers.models import (
    DurableWorkerJob,
    QUEUE_FILES,
    WorkerJobFailed,
    WorkerJobSnapshot,
    enqueue_worker_job,
    lock_unreconciled_terminal_jobs,
    utcnow,
    wait_for_worker_job,
)
from app.workers.runtime import (
    DurableQueueWorker,
    FatalJobError,
    JobCancelled,
    WorkerContext,
    run_worker_cli,
)


logger = logging.getLogger(__name__)
PROCESSOR_VERSION = 1
FILE_PROCESSING_CACHE_DIR = Path(
    os.getenv("FILE_PROCESSING_CACHE_DIR") or (DATA_DIR / "file-processing-cache")
)
OCR_IMAGE_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/tiff",
        "image/bmp",
    }
)
_TERMINAL_ARTIFACT_STATUSES = {"succeeded", "failed"}
_REQUESTER_INVALIDATED_ARTIFACT_ERRORS = {
    "authorization_changed",
    "file_unavailable",
    "user_unavailable",
}
_PDF_PAGE_OPERATIONS = {"pdf_page", "pdf_page_image"}


def external_file_processing_enabled() -> bool:
    return str(os.getenv("FILE_PROCESSING_WORKER_MODE", "inline") or "inline").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "external",
        "worker",
    }


def _source_fingerprint(file_record: Files) -> str:
    meta = file_record.meta if isinstance(file_record.meta, dict) else {}
    source_hash = str(meta.get("sha256") or "").strip().lower()
    if re.fullmatch(r"[a-f0-9]{64}", source_hash):
        return source_hash
    updated = getattr(file_record, "last_updated_at", None)
    updated_value = updated.isoformat() if updated is not None else ""
    material = f"{file_record.id}:{file_record.file_size}:{updated_value}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _operation_cache_key(file_record: Files, operation: str, params: dict[str, Any]) -> str:
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(params_json.encode("utf-8")).hexdigest()[:24]
    return f"{_source_fingerprint(file_record)}:{digest}"[:200]


def _normalize_operation_params(operation: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """Whitelist and bound caller-controlled parameters before queueing them."""

    if operation not in _PDF_PAGE_OPERATIONS:
        return {}
    raw_page = (params or {}).get("page")
    if isinstance(raw_page, bool) or not isinstance(raw_page, (int, str)):
        raise TypeError("Invalid PDF page")
    if isinstance(raw_page, str) and not raw_page.isdigit():
        raise ValueError("Invalid PDF page")
    try:
        page = int(raw_page)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid PDF page") from exc
    if page < 1 or page > PDF_PREVIEW_MAX_PAGES:
        raise ValueError("Invalid PDF page")
    return {"page": page}


def _get_or_create_artifact(db, *, file_record: Files, operation: str, cache_key: str) -> FileProcessingArtifact:
    existing = (
        db.query(FileProcessingArtifact)
        .filter(
            FileProcessingArtifact.file_id == file_record.id,
            FileProcessingArtifact.operation == operation,
            FileProcessingArtifact.processor_version == PROCESSOR_VERSION,
            FileProcessingArtifact.cache_key == cache_key,
        )
        .first()
    )
    if existing is not None:
        return existing
    row = FileProcessingArtifact(
        file_id=file_record.id,
        operation=operation,
        processor_version=PROCESSOR_VERSION,
        cache_key=cache_key,
        status="pending",
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        row = (
            db.query(FileProcessingArtifact)
            .filter(
                FileProcessingArtifact.file_id == file_record.id,
                FileProcessingArtifact.operation == operation,
                FileProcessingArtifact.processor_version == PROCESSOR_VERSION,
                FileProcessingArtifact.cache_key == cache_key,
            )
            .one()
        )
    return row


def enqueue_file_processing(
    db,
    *,
    user_id: str,
    file_id: str,
    operation: str,
    params: dict[str, Any] | None = None,
) -> tuple[FileProcessingArtifact, DurableWorkerJob | None]:
    normalized_operation = str(operation or "").strip().lower()
    if normalized_operation not in {"extract_text", "pdf_inspect", "pdf_page", "pdf_page_image"}:
        raise ValueError("Unsupported file processing operation")
    file_record, _owner_id = resolve_accessible_file_record(db, str(user_id), str(file_id))
    if file_record is None:
        raise HTTPException(status_code=404, detail="File not found")
    normalized_params = _normalize_operation_params(normalized_operation, params)
    cache_key = _operation_cache_key(file_record, normalized_operation, normalized_params)
    artifact = _get_or_create_artifact(
        db,
        file_record=file_record,
        operation=normalized_operation,
        cache_key=cache_key,
    )
    if artifact.status == "succeeded" and normalized_operation == "pdf_page_image":
        try:
            if not artifact.cache_path:
                raise FileNotFoundError
            resolve_cached_preview_path(artifact.cache_path)
        except (HTTPException, OSError):
            # Derived cache files are regenerable. Give a missing cache entry a
            # fresh artifact/job identity while retaining the terminal history
            # of the original durable job for observability.
            db.delete(artifact)
            db.flush()
            artifact = _get_or_create_artifact(
                db,
                file_record=file_record,
                operation=normalized_operation,
                cache_key=cache_key,
            )
    if artifact.status == "succeeded":
        db.commit()
        db.refresh(artifact)
        return artifact, None
    if artifact.status == "failed":
        db.commit()
        db.refresh(artifact)
        return artifact, None
    job = enqueue_worker_job(
        db,
        queue=QUEUE_FILES,
        kind=normalized_operation,
        user_id=str(user_id),
        payload={
            "artifact_id": artifact.id,
            "file_id": str(file_record.id),
            "params": normalized_params,
        },
        idempotency_key=f"file-artifact:{artifact.id}",
        priority=20 if normalized_operation == "extract_text" else 30,
        max_attempts=2,
        commit=True,
    )
    db.refresh(artifact)
    return artifact, job


def wait_for_file_processing(artifact_id: str, *, timeout_seconds: float | None = None) -> FileProcessingArtifact:
    if timeout_seconds is None:
        try:
            timeout_seconds = float(os.getenv("FILE_PROCESSING_WAIT_SECONDS", "60") or "60")
        except (TypeError, ValueError):
            timeout_seconds = 60.0
    deadline = time.monotonic() + max(1.0, min(float(timeout_seconds), 300.0))
    delay = 0.2
    while True:
        session = SessionLocal()
        try:
            row = session.query(FileProcessingArtifact).filter(FileProcessingArtifact.id == artifact_id).first()
            if row is None:
                raise HTTPException(status_code=404, detail="File processing result not found")
            if row.status in _TERMINAL_ARTIFACT_STATUSES:
                session.expunge(row)
                return row
        finally:
            session.close()
        if time.monotonic() >= deadline:
            raise HTTPException(
                status_code=503,
                detail={"code": "file_processing_pending", "artifact_id": artifact_id},
                headers={"Retry-After": "2"},
            )
        time.sleep(delay)
        delay = min(1.0, delay * 1.5)


def process_file_and_wait(
    db,
    *,
    user_id: str,
    file_id: str,
    operation: str,
    params: dict[str, Any] | None = None,
) -> FileProcessingArtifact:
    artifact, _job = enqueue_file_processing(
        db,
        user_id=user_id,
        file_id=file_id,
        operation=operation,
        params=params,
    )
    if artifact.status == "succeeded":
        return artifact
    return wait_for_file_processing(artifact.id)


def _valid_source_descriptor(value: str) -> bool:
    from app.agents.utils import parse_agent_asset_descriptor
    from app.skills.models import parse_skill_file_descriptor

    return bool(
        parse_agent_asset_descriptor(value)
        or parse_skill_file_descriptor(value)
    )


def process_descriptor_text_and_wait(
    db,
    *,
    user_id: str,
    descriptor: str,
    file_info: dict[str, Any],
) -> str | None:
    """Extract an authorized agent/skill asset entirely in the file worker."""

    normalized_descriptor = str(descriptor or "").strip()
    if not _valid_source_descriptor(normalized_descriptor):
        return None
    try:
        path_stat = Path(str(file_info.get("path") or "")).stat()
    except OSError:
        return None
    fingerprint = hashlib.sha256(
        (
            f"{user_id}:{normalized_descriptor}:{path_stat.st_size}:"
            f"{path_stat.st_mtime_ns}:v{PROCESSOR_VERSION}"
        ).encode("utf-8")
    ).hexdigest()
    job = enqueue_worker_job(
        db,
        queue=QUEUE_FILES,
        kind="extract_descriptor_text",
        user_id=str(user_id),
        payload={"descriptor": normalized_descriptor},
        idempotency_key=f"descriptor-text:{fingerprint}",
        priority=20,
        max_attempts=2,
        expires_at=utcnow() + timedelta(days=7),
        commit=True,
    )
    try:
        timeout = float(os.getenv("FILE_PROCESSING_WAIT_SECONDS", "60") or "60")
    except (TypeError, ValueError):
        timeout = 60.0
    try:
        result = wait_for_worker_job(job.id, timeout_seconds=timeout)
    except WorkerJobFailed:
        return None
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "file_processing_pending", "job_id": job.id},
            headers={"Retry-After": "2"},
        ) from exc
    content = result.get("content")
    return str(content).strip() if isinstance(content, str) and content.strip() else None


def enqueue_text_extraction_if_supported(db, *, user_id: str, file_id: str) -> None:
    if not external_file_processing_enabled():
        return
    record = db.query(Files).filter(Files.id == str(file_id), Files.user_id == str(user_id)).first()
    if record is None:
        return
    mime = str(record.file_type or "").split(";", 1)[0].strip().lower()
    if mime not in SUPPORTED_EXTRACT_TEXT_MIME_TYPES and mime not in OCR_IMAGE_MIME_TYPES:
        return
    enqueue_file_processing(
        db,
        user_id=user_id,
        file_id=file_id,
        operation="extract_text",
    )


def _artifact_and_file(job: WorkerJobSnapshot):
    artifact_id = str(job.payload.get("artifact_id") or "").strip()
    file_id = str(job.payload.get("file_id") or "").strip()
    if not artifact_id or not file_id:
        raise FatalJobError("invalid_payload")
    session = SessionLocal()
    try:
        _active_worker_user(session, job.user_id)
    except Exception:
        session.close()
        raise
    artifact = session.query(FileProcessingArtifact).filter(FileProcessingArtifact.id == artifact_id).first()
    file_record, _owner_user_id = resolve_accessible_file_record(
        session,
        str(job.user_id or ""),
        file_id,
    )
    if artifact is None or file_record is None or artifact.file_id != file_record.id:
        session.close()
        raise FatalJobError("file_unavailable")
    artifact.status = "running"
    artifact.error_code = None
    from datetime import datetime, timezone

    artifact.updated_at = datetime.now(timezone.utc)
    session.commit()
    return session, artifact, file_record


def _active_worker_user(session, user_id: str | None) -> User:
    user = session.query(User).filter(User.id == str(user_id or "")).first()
    if (
        user is None
        or user.deleted_at is not None
        or not bool(user.is_active)
        or str(user.role or "").strip().lower() == "pending"
    ):
        raise FatalJobError("user_unavailable")
    return user


def _handle_descriptor_text(
    job: WorkerJobSnapshot,
    context: WorkerContext,
) -> dict[str, Any]:
    from app.files.utils import get_file_info

    descriptor = str(job.payload.get("descriptor") or "").strip()
    if not _valid_source_descriptor(descriptor):
        raise FatalJobError("invalid_payload")
    session = SessionLocal()
    try:
        user = _active_worker_user(session, job.user_id)
        context.raise_if_cancelled()
        file_info = get_file_info(user.id, descriptor)
        if not isinstance(file_info, dict):
            raise FatalJobError("file_unavailable")
        file_path = Path(str(file_info.get("path") or ""))
        if not file_path.is_file():
            raise FatalJobError("file_unavailable")
        mime = str(file_info.get("file_type") or "").split(";", 1)[0].strip().lower()
        if mime not in SUPPORTED_EXTRACT_TEXT_MIME_TYPES and mime not in OCR_IMAGE_MIME_TYPES:
            raise FatalJobError("unsupported_file_type")
        content, method = _extract_text(file_path, mime, context)
        return {"content": content, "method": method, "mime_type": mime}
    finally:
        session.close()


def _max_text_chars() -> int:
    try:
        value = int(os.getenv("FILE_EXTRACTED_TEXT_MAX_CHARS", str(4 * 1024 * 1024)))
    except (TypeError, ValueError):
        value = 4 * 1024 * 1024
    return max(10_000, min(value, 16 * 1024 * 1024))


def _ocr_max_input_bytes() -> int:
    try:
        max_input_bytes = int(
            os.getenv("FILE_OCR_MAX_INPUT_BYTES", str(50 * 1024 * 1024))
            or str(50 * 1024 * 1024)
        )
    except (TypeError, ValueError):
        max_input_bytes = 50 * 1024 * 1024
    return max(1024 * 1024, min(max_input_bytes, 200 * 1024 * 1024))


def _ocr_bytes(image_bytes: bytes) -> str:
    if not image_bytes or len(image_bytes) > _ocr_max_input_bytes():
        raise FatalJobError("ocr_input_too_large")
    executable = shutil.which("tesseract")
    if not executable:
        raise FatalJobError("ocr_unavailable")
    language = str(os.getenv("FILE_OCR_LANGUAGES", "eng") or "eng").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{2,16}(?:\+[A-Za-z0-9_]{2,16}){0,7}", language):
        language = "eng"
    timeout = max(10, min(int(os.getenv("FILE_OCR_TIMEOUT_SECONDS", "120") or "120"), 600))
    completed = subprocess.run(
        [executable, "stdin", "stdout", "-l", language],
        input=image_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise FatalJobError("ocr_failed")
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _ocr_pdf(file_path: Path, context: WorkerContext) -> str:
    from app.files.pdfium import inspect_pdf_document, render_pdf_page

    metadata = inspect_pdf_document(file_path, max_file_bytes=100 * 1024 * 1024, max_pages=1000)
    try:
        max_pages = int(os.getenv("FILE_OCR_MAX_PAGES", "50") or "50")
    except (TypeError, ValueError):
        max_pages = 50
    page_count = min(int(metadata.get("page_count") or 0), max(1, min(max_pages, 250)))
    pages: list[str] = []
    for page in range(1, page_count + 1):
        context.raise_if_cancelled()
        png = render_pdf_page(
            file_path,
            page_number=page,
            scale=2.0,
            max_file_bytes=100 * 1024 * 1024,
            max_document_pages=1000,
            max_side_pixels=4096,
            max_pixels=16_000_000,
            max_page_png_bytes=10 * 1024 * 1024,
        )
        text = _ocr_bytes(png)
        if text:
            pages.append(f"\n\n## Page {page}\n\n{text}")
        if sum(len(value) for value in pages) >= _max_text_chars():
            break
    return "".join(pages).strip()


def _extract_text(file_path: Path, mime: str, context: WorkerContext) -> tuple[str, str]:
    from app.files.utils import _extract_text_from_path_inline

    if mime in OCR_IMAGE_MIME_TYPES:
        try:
            if file_path.stat().st_size > _ocr_max_input_bytes():
                raise FatalJobError("ocr_input_too_large")
        except OSError as exc:
            raise FatalJobError("file_unavailable") from exc
        text = _ocr_bytes(file_path.read_bytes())
        method = "ocr"
    else:
        text = _extract_text_from_path_inline({"path": str(file_path), "file_type": mime}) or ""
        method = "document"
        if mime == "application/pdf" and len(text.strip()) < 40:
            ocr_text = _ocr_pdf(file_path, context)
            if ocr_text:
                text = ocr_text
                method = "ocr"
    normalized = str(text).strip()
    if not normalized:
        raise FatalJobError("text_not_found")
    limit = _max_text_chars()
    return normalized[:limit], method


def _finish_artifact(
    session,
    artifact: FileProcessingArtifact,
    *,
    data: dict[str, Any] | None = None,
    cache_path: str | None = None,
) -> None:
    from datetime import datetime, timezone

    current = datetime.now(timezone.utc)
    artifact.status = "succeeded"
    artifact.data = data
    artifact.cache_path = cache_path
    artifact.error_code = None
    artifact.updated_at = current
    artifact.finished_at = current
    session.add(artifact)
    session.commit()


def _handle(job: WorkerJobSnapshot, context: WorkerContext) -> dict[str, Any]:
    session, artifact, file_record = _artifact_and_file(job)
    try:
        context.raise_if_cancelled()
        file_path = materialize_file_record(file_record, file_record.user_id)
        params = job.payload.get("params") if isinstance(job.payload.get("params"), dict) else {}
        if job.kind == "extract_text":
            mime = str(file_record.file_type or "").split(";", 1)[0].strip().lower()
            text, method = _extract_text(file_path, mime, context)
            _finish_artifact(
                session,
                artifact,
                data={"content": text, "method": method, "mime_type": mime},
            )
        elif job.kind == "pdf_inspect":
            _finish_artifact(session, artifact, data=inspect_pdf_preview_document(file_path))
        elif job.kind == "pdf_page":
            try:
                page = _normalize_operation_params(job.kind, params)["page"]
            except (TypeError, ValueError) as exc:
                raise FatalJobError("invalid_page") from exc
            _finish_artifact(session, artifact, data=extract_pdf_preview_page(file_path, page))
        elif job.kind == "pdf_page_image":
            try:
                page = _normalize_operation_params(job.kind, params)["page"]
            except (TypeError, ValueError) as exc:
                raise FatalJobError("invalid_page") from exc
            png = render_pdf_preview_page_png(file_path, page)
            FILE_PROCESSING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            directory = FILE_PROCESSING_CACHE_DIR / hashlib.sha256(file_record.id.encode("utf-8")).hexdigest()
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            target = directory / f"{artifact.id}.png"
            temporary = directory / f".{artifact.id}.{uuid.uuid4().hex}.tmp"
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as output:
                    output.write(png)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            relative = str(target.relative_to(FILE_PROCESSING_CACHE_DIR))
            _finish_artifact(
                session,
                artifact,
                data={"size_bytes": len(png), "sha256": hashlib.sha256(png).hexdigest()},
                cache_path=relative,
            )
        else:
            raise FatalJobError("unsupported_kind")
        return {"artifact_id": artifact.id}
    except Exception as exc:
        from datetime import datetime, timezone

        session.rollback()
        current = datetime.now(timezone.utc)
        artifact = session.query(FileProcessingArtifact).filter(FileProcessingArtifact.id == artifact.id).first()
        if artifact is not None:
            requester_local_failure = isinstance(exc, JobCancelled) or (
                isinstance(exc, FatalJobError)
                and exc.code in _REQUESTER_INVALIDATED_ARTIFACT_ERRORS
            )
            if requester_local_failure:
                # The artifact identity is shared by source and operation, not
                # by requester. Cancellation or a requester's lost grant must
                # never publish a global terminal failure for that shared
                # derived cache entry; deleting it lets an authorized caller
                # create a fresh artifact/job pair.
                session.delete(artifact)
                session.commit()
                raise
            terminal = isinstance(exc, FatalJobError) or job.attempt_count >= job.max_attempts
            artifact.status = "failed" if terminal else "pending"
            artifact.error_code = getattr(exc, "code", "processing_failed")[:64]
            artifact.data = None
            artifact.cache_path = None
            artifact.updated_at = current
            artifact.finished_at = current if terminal else None
            session.commit()
        raise
    finally:
        session.close()


def resolve_cached_preview_path(relative_path: str) -> Path:
    candidate = (FILE_PROCESSING_CACHE_DIR / str(relative_path or "")).resolve()
    root = FILE_PROCESSING_CACHE_DIR.resolve()
    if candidate == root or root not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="File preview not found")
    return candidate


def reconcile_terminal_file_jobs(*, batch_size: int = 1000) -> int:
    """Close file artifacts left active by terminal queue jobs."""

    session = SessionLocal()
    try:
        rows = lock_unreconciled_terminal_jobs(
            session,
            queue=QUEUE_FILES,
            kinds=("extract_text", "pdf_inspect", "pdf_page", "pdf_page_image"),
            batch_size=batch_size,
        )
        current = utcnow()
        for row in rows:
            prefix = "file-artifact:"
            key = str(row.idempotency_key or "")
            artifact_id = key[len(prefix) :] if key.startswith(prefix) else ""
            artifact = (
                session.query(FileProcessingArtifact)
                .filter(FileProcessingArtifact.id == artifact_id)
                .first()
                if artifact_id
                else None
            )
            if artifact is not None and artifact.status in {"pending", "running"}:
                error_code = str(row.error_code or "processing_failed")[:64]
                if error_code in _REQUESTER_INVALIDATED_ARTIFACT_ERRORS:
                    # Authorization belongs to the requester that happened to
                    # create this globally keyed cache entry.  Losing that
                    # authorization must not poison the source owner's derived
                    # cache; deleting the regenerable row gives the next
                    # authorized caller a fresh artifact and job identity.
                    session.delete(artifact)
                else:
                    artifact.status = "failed"
                    artifact.error_code = error_code
                    artifact.data = None
                    artifact.cache_path = None
                    artifact.finished_at = current
                    artifact.updated_at = current
                    session.add(artifact)
            row.reconciled_at = current
            row.updated_at = current
        session.commit()
        return len(rows)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def build_worker() -> DurableQueueWorker:
    handlers = {name: _handle for name in ("extract_text", "pdf_inspect", "pdf_page", "pdf_page_image")}
    handlers["extract_descriptor_text"] = _handle_descriptor_text
    return DurableQueueWorker(
        queue=QUEUE_FILES,
        handlers=handlers,
        reconciler=reconcile_terminal_file_jobs,
        default_lease_seconds=180,
    )


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
