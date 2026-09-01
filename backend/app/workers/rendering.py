from __future__ import annotations

from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import re
import shutil
import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.chats.streaming import stream_hub
from app.database import AuditSessionLocal, SessionLocal
from app.paths import DATA_DIR
from app.redis_client import get_redis_client, redis_enabled
from app.utils.encryption import get_cipher_suite
from app.workers.models import (
    QUEUE_RENDERING,
    DurableWorkerJob,
    WorkerJobSnapshot,
    enqueue_worker_job,
    lock_unreconciled_terminal_jobs,
    utcnow,
    wait_for_worker_job,
)
from app.workers.runtime import DurableQueueWorker, FatalJobError, WorkerContext, run_worker_cli
from app.workers.tool_jobs import execute_tool_job


logger = logging.getLogger(__name__)
RENDERING_STAGING_DIR = Path(
    os.getenv("RENDERING_STAGING_DIR") or (DATA_DIR / "rendering-staging")
)
_STAGED_RENDER_RE = re.compile(r"^[a-f0-9]{32}\.(?:md|pdf)$")


def _write_encrypted_staged(content: bytes, *, extension: str) -> str:
    RENDERING_STAGING_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{extension}"
    target = RENDERING_STAGING_DIR / name
    temporary = RENDERING_STAGING_DIR / f".{name}.{os.getpid()}.part"
    try:
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(get_cipher_suite().encrypt(bytes(content)))
        os.replace(temporary, target)
        return name
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _staged_render_path(name: str) -> Path:
    normalized = str(name or "").strip().lower()
    if not _STAGED_RENDER_RE.fullmatch(normalized):
        raise FatalJobError("invalid_staging_reference")
    candidate = (RENDERING_STAGING_DIR / normalized).resolve()
    root = RENDERING_STAGING_DIR.resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise FatalJobError("render_staging_unavailable")
    return candidate


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


def _enqueue_rendering_job(
    *,
    kind: str,
    user_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
    priority: int = 20,
    retry_terminal: bool = False,
) -> DurableWorkerJob:
    session = SessionLocal()
    try:
        return enqueue_worker_job(
            session,
            queue=QUEUE_RENDERING,
            kind=kind,
            user_id=str(user_id),
            payload=jsonable_encoder(payload),
            idempotency_key=idempotency_key,
            priority=priority,
            max_attempts=1,
            expires_at=utcnow() + timedelta(hours=24),
            retry_terminal=retry_terminal,
            commit=True,
        )
    finally:
        session.close()


def wait_for_rendering_job(job: DurableWorkerJob) -> dict[str, Any]:
    try:
        timeout = float(os.getenv("RENDERING_REQUEST_WAIT_SECONDS", "900") or "900")
    except (TypeError, ValueError):
        timeout = 900.0
    return wait_for_worker_job(job.id, timeout_seconds=max(1.0, min(timeout, 3600.0)))


def enqueue_markdown_pdf(
    *,
    user_id: str,
    markdown: str,
    filename: str | None,
    source_file_id: str | None,
) -> DurableWorkerJob:
    input_name = _write_encrypted_staged(str(markdown).encode("utf-8"), extension="md")
    result_name = f"{uuid.uuid4().hex}.pdf"
    try:
        return _enqueue_rendering_job(
            kind="canvas_markdown_pdf",
            user_id=user_id,
            payload={
                "input_name": input_name,
                "result_name": result_name,
                "filename": filename,
                "source_file_id": source_file_id,
            },
            idempotency_key=f"markdown-pdf:{input_name}:{result_name}",
            priority=20,
        )
    except Exception:
        _staged_render_path(input_name).unlink(missing_ok=True)
        raise


def read_markdown_pdf_result(result: dict[str, Any]) -> tuple[str, bytes]:
    name = str(result.get("result_name") or "")
    path = _staged_render_path(name)
    try:
        content = get_cipher_suite().decrypt(path.read_bytes())
    finally:
        path.unlink(missing_ok=True)
    return str(result.get("filename") or "canvas.pdf"), content


def enqueue_canvas_latex_render(
    *,
    actor_user_id: str,
    source_file_id: str,
    expected_revision: int | None,
    audit_ip_address: str | None = None,
    audit_user_agent: str | None = None,
) -> DurableWorkerJob:
    revision = str(expected_revision) if expected_revision is not None else uuid.uuid4().hex
    return _enqueue_rendering_job(
        kind="canvas_latex",
        user_id=actor_user_id,
        payload={
            "source_file_id": source_file_id,
            "expected_revision": expected_revision,
            "audit_ip_address": audit_ip_address,
            "audit_user_agent": audit_user_agent,
        },
        idempotency_key=f"canvas-latex:{actor_user_id}:{source_file_id}:{revision}",
        priority=10,
        retry_terminal=True,
    )


def enqueue_presentation_rerender(
    *,
    user_id: str,
    presentation_id: str,
    expected_revision: int,
    generation_id: str | None = None,
    audit_ip_address: str | None = None,
    audit_user_agent: str | None = None,
    audit_success_action: str | None = None,
    audit_failure_action: str | None = None,
) -> DurableWorkerJob:
    return _enqueue_rendering_job(
        kind="presentation_rerender",
        user_id=user_id,
        payload={
            "presentation_id": presentation_id,
            "expected_revision": int(expected_revision),
            "generation_id": generation_id,
            "audit_ip_address": audit_ip_address,
            "audit_user_agent": audit_user_agent,
            "audit_success_action": audit_success_action,
            "audit_failure_action": audit_failure_action,
        },
        idempotency_key=(
            f"presentation-rerender:{user_id}:{presentation_id}:{int(expected_revision)}"
        ),
        priority=5,
        retry_terminal=True,
    )


def consume_presentation_rerender_result(result: dict[str, Any]):
    if not result.get("streamed"):
        for event in result.get("events") or []:
            if isinstance(event, str):
                yield event
    payload = result.get("result")
    return payload if isinstance(payload, dict) else {}


def _audit_rendering_event(
    job: WorkerJobSnapshot,
    *,
    action: str | None,
    details: dict[str, Any],
    category: str,
) -> None:
    normalized_action = str(action or "").strip()
    if not normalized_action:
        return
    try:
        from app.logging.models import create_audit_log

        create_audit_log(
            db_log=AuditSessionLocal(),
            user_id=str(job.user_id or ""),
            action=normalized_action,
            details=details,
            ip_address=job.payload.get("audit_ip_address"),
            user_agent=job.payload.get("audit_user_agent"),
            category=category,
        )
    except Exception:
        logger.exception("Could not enqueue rendering-worker audit event action=%s", action)


def _handle_markdown_pdf(job: WorkerJobSnapshot, context: WorkerContext) -> dict[str, Any]:
    from app.tools.canvas_markdown.pdf import render_canvas_markdown_pdf

    input_path = _staged_render_path(str(job.payload.get("input_name") or ""))
    result_name = str(job.payload.get("result_name") or "")
    if not re.fullmatch(r"[a-f0-9]{32}\.pdf", result_name):
        raise FatalJobError("invalid_staging_reference")
    session = SessionLocal()
    try:
        user = _active_user(session, str(job.user_id or ""))
        context.raise_if_cancelled()
        try:
            markdown = get_cipher_suite().decrypt(input_path.read_bytes()).decode("utf-8")
        except Exception as exc:
            raise FatalJobError("render_input_invalid") from exc
        rendered = render_canvas_markdown_pdf(
            session,
            user_id=user.id,
            markdown_text=markdown,
            filename=job.payload.get("filename"),
            source_file_id=job.payload.get("source_file_id"),
        )
        context.raise_if_cancelled()
        written_name = _write_encrypted_staged(rendered.content, extension="pdf")
        generated = _staged_render_path(written_name)
        target = RENDERING_STAGING_DIR / result_name
        os.replace(generated, target)
        return {"result_name": result_name, "filename": rendered.filename}
    finally:
        input_path.unlink(missing_ok=True)
        session.close()


def _handle_canvas_latex(job: WorkerJobSnapshot, context: WorkerContext) -> dict[str, Any]:
    from app.files.access import resolve_file_for_edit
    from app.files.canvas_assets import CanvasAssetAccessError
    from app.tools.latex_pdf.utils import (
        LatexCompileError,
        LatexRenderOutputLimitError,
        LatexSourceRevisionConflict,
        render_latex_canvas,
    )

    session = SessionLocal()
    try:
        user = _active_user(session, str(job.user_id or ""))
        source_id = str(job.payload.get("source_file_id") or "").strip()
        access = resolve_file_for_edit(session, user.id, source_id)
        if access is None:
            raise FatalJobError("latex_canvas_unavailable")
        context.raise_if_cancelled()
        try:
            result = render_latex_canvas(
                session,
                user_id=access.storage_owner_user_id,
                asset_actor_user_id=user.id,
                source_file_id=source_id,
                expected_revision=job.payload.get("expected_revision"),
                audit_ip_address=job.payload.get("audit_ip_address"),
                audit_user_agent=job.payload.get("audit_user_agent"),
            )
        except LatexSourceRevisionConflict as exc:
            raise FatalJobError("latex_revision_conflict") from exc
        except LatexRenderOutputLimitError as exc:
            raise FatalJobError("latex_output_limit") from exc
        except CanvasAssetAccessError as exc:
            raise FatalJobError("latex_asset_forbidden") from exc
        except LatexCompileError as exc:
            raise FatalJobError("latex_compile_failed") from exc
        except ValueError as exc:
            raise FatalJobError("latex_invalid") from exc
        return jsonable_encoder(result)
    finally:
        session.close()


def _mark_presentation_failed(
    session,
    presentation_id: str,
    user_id: str,
    revision: int,
    *,
    commit: bool = True,
) -> None:
    from app.files.models import get_file

    source = get_file(session, presentation_id, user_id)
    if source is None:
        return
    meta = dict(source.meta) if isinstance(source.meta, dict) else {}
    if int(meta.get("canvas_revision") or 0) != int(revision):
        return
    meta["presentation_render_status"] = "failed"
    source.meta = meta
    session.add(source)
    if commit:
        session.commit()


def _handle_presentation_rerender(
    job: WorkerJobSnapshot,
    context: WorkerContext,
) -> dict[str, Any]:
    from app.tools.slide_presentation.pipeline import (
        PresentationRevisionConflict,
        rerender_presentation_source,
    )
    from app.tools.slide_presentation.router import (
        _owned_editor_records,
        _read_editor_source,
    )

    session = SessionLocal()
    presentation_id = str(job.payload.get("presentation_id") or "").strip()
    revision = int(job.payload.get("expected_revision") or 0)
    try:
        user = _active_user(session, str(job.user_id or ""))
        presentation, source, meta = _owned_editor_records(session, user.id, presentation_id)
        if int(meta.get("canvas_revision") or 0) != revision:
            raise FatalJobError("presentation_revision_conflict")
        html = _read_editor_source(
            source,
            user.id,
            presentation_id,
            str(presentation.storage_provider or "local"),
            str(presentation.storage_prefix or ""),
        )
        generation_id = str(job.payload.get("generation_id") or "").strip() or None
        publish_live = bool(
            generation_id and redis_enabled() and get_redis_client() is not None
        )
        events: list[str] = []
        total_bytes = 0
        rerender = rerender_presentation_source(
            db=session,
            user_id=user.id,
            html_file_id=presentation_id,
            html=html,
            expected_revision=revision,
        )
        try:
            while True:
                line = next(rerender)
                context.raise_if_cancelled()
                normalized = str(line)
                try:
                    event = json.loads(normalized.strip())
                except (TypeError, ValueError, json.JSONDecodeError):
                    event = None
                # Canvas emits its own authoritative terminal event after this
                # derivative refresh. Publishing the presentation completion
                # as well would create two result cards for one edit.
                if (
                    isinstance(event, dict)
                    and event.get("t") == "slide_presentation_evt"
                    and event.get("event") == "complete"
                ):
                    continue
                if publish_live:
                    stream_hub.publish_line(generation_id, normalized)
                else:
                    total_bytes += len(normalized.encode("utf-8"))
                    if total_bytes > 2 * 1024 * 1024:
                        raise FatalJobError("render_stream_too_large")
                    events.append(normalized)
        except StopIteration as completed:
            result = completed.value or {}
        response = {
            "result": jsonable_encoder(result),
            "events": events,
            "streamed": publish_live,
        }
        _audit_rendering_event(
            job,
            action=job.payload.get("audit_success_action"),
            details={
                "presentation_id": presentation_id,
                "canvas_revision": revision,
                "slide_count": int(result.get("slide_count") or 1),
            },
            category="presentations",
        )
        return response
    except PresentationRevisionConflict as exc:
        session.rollback()
        raise FatalJobError("presentation_revision_conflict") from exc
    except FatalJobError:
        session.rollback()
        raise
    except ValueError as exc:
        session.rollback()
        _mark_presentation_failed(session, presentation_id, str(job.user_id or ""), revision)
        _audit_rendering_event(
            job,
            action=job.payload.get("audit_failure_action"),
            details={"presentation_id": presentation_id, "canvas_revision": revision},
            category="presentations",
        )
        raise FatalJobError("presentation_invalid") from exc
    except Exception as exc:
        session.rollback()
        _mark_presentation_failed(session, presentation_id, str(job.user_id or ""), revision)
        _audit_rendering_event(
            job,
            action=job.payload.get("audit_failure_action"),
            details={"presentation_id": presentation_id, "canvas_revision": revision},
            category="presentations",
        )
        raise FatalJobError("presentation_render_failed") from exc
    finally:
        session.close()


def reconcile_terminal_rendering_jobs(*, batch_size: int = 1000) -> int:
    session = SessionLocal()
    try:
        rows = lock_unreconciled_terminal_jobs(
            session,
            queue=QUEUE_RENDERING,
            kinds=("canvas_markdown_pdf", "presentation_rerender"),
            batch_size=batch_size,
        )
        current = utcnow()
        for row in rows:
            if row.kind == "canvas_markdown_pdf":
                parts = str(row.idempotency_key or "").split(":", 2)
                for name in parts[1:] if len(parts) == 3 else ():
                    try:
                        _staged_render_path(name).unlink(missing_ok=True)
                    except FatalJobError:
                        pass
            elif row.kind == "presentation_rerender":
                parts = str(row.idempotency_key or "").rsplit(":", 2)
                if len(parts) == 3:
                    try:
                        _mark_presentation_failed(
                            session,
                            parts[1],
                            str(row.user_id or ""),
                            int(parts[2]),
                            commit=False,
                        )
                    except (TypeError, ValueError):
                        pass
            row.reconciled_at = current
            row.updated_at = current
        session.commit()
        return len(rows)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def clear_rendering_staging_after_restore() -> int:
    removed = 0
    if not RENDERING_STAGING_DIR.exists():
        return removed
    for path in RENDERING_STAGING_DIR.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        removed += 1
    return removed


def build_worker() -> DurableQueueWorker:
    return DurableQueueWorker(
        queue=QUEUE_RENDERING,
        handlers={
            "tool_call": execute_tool_job,
            "canvas_markdown_pdf": _handle_markdown_pdf,
            "canvas_latex": _handle_canvas_latex,
            "presentation_rerender": _handle_presentation_rerender,
        },
        reconciler=reconcile_terminal_rendering_jobs,
        default_lease_seconds=600,
    )


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
