from __future__ import annotations

from datetime import timedelta
import json
import logging
import os
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.chats.schemas import RegenerateMessageRequest, SendChatRequest
from app.chats.streaming import cancel_registry, stream_hub
from app.database import SessionLocal
from app.llm.provider_request import iterate_sync_stream_async
from app.redis_client import get_redis_client, redis_enabled
from app.users.models import User
from app.workers.models import (
    DurableWorkerJob,
    JOB_CANCELLED,
    QUEUE_GENERATION,
    WorkerJobSnapshot,
    enqueue_worker_job,
    lock_unreconciled_terminal_jobs,
    request_worker_job_cancellation,
    utcnow,
)
from app.workers.runtime import DurableQueueWorker, FatalJobError, JobCancelled, WorkerContext, run_worker_cli


logger = logging.getLogger(__name__)
_ENABLE_VALUES = {"1", "true", "yes", "on", "external", "worker"}


def external_generation_enabled() -> bool:
    configured = (
        str(os.getenv("GENERATION_WORKER_MODE", "inline") or "inline").strip().lower()
        in _ENABLE_VALUES
    )
    # Redis-off installations retain the existing in-process streaming path.
    # A configured external worker still fails closed on a live Redis outage
    # when enqueue_generation_job performs its reachability check.
    return configured and redis_enabled()


def require_shared_generation_stream() -> None:
    if not redis_enabled() or get_redis_client() is None:
        raise RuntimeError("The external generation worker requires a reachable shared Redis service")


def enqueue_generation_job(
    db,
    *,
    kind: str,
    user_id: str,
    generation_id: str,
    request_payload: dict[str, Any],
    custom_settings: dict[str, Any] | None,
    byok: dict[str, Any] | None,
    normalized_project_id: str | None = None,
    subagent_targets: list[dict[str, str]] | None = None,
) -> DurableWorkerJob:
    require_shared_generation_stream()
    payload = {
        "generation_id": str(generation_id),
        "request": jsonable_encoder(request_payload),
        "custom_settings": jsonable_encoder(custom_settings),
        # BYOK can contain the resolved short-lived API key. DurableWorkerJob
        # encrypts the full payload and erases it at terminal state.
        "byok": jsonable_encoder(byok),
        "normalized_project_id": normalized_project_id,
        "subagent_targets": jsonable_encoder(subagent_targets),
    }
    return enqueue_worker_job(
        db,
        queue=QUEUE_GENERATION,
        kind=kind,
        user_id=user_id,
        payload=payload,
        idempotency_key=f"generation:{generation_id}",
        priority=10,
        max_attempts=1,
        expires_at=utcnow() + timedelta(hours=24),
        commit=True,
    )


def cancel_queued_generation(db, *, generation_id: str, user_id: str) -> bool:
    row = (
        db.query(DurableWorkerJob)
        .filter(
            DurableWorkerJob.queue == QUEUE_GENERATION,
            DurableWorkerJob.idempotency_key == f"generation:{generation_id}",
            DurableWorkerJob.user_id == str(user_id),
        )
        .first()
    )
    if row is None:
        return False
    cancelled = request_worker_job_cancellation(
        db,
        job_id=row.id,
        user_id=user_id,
        commit=True,
    )
    if cancelled:
        # End the browser stream immediately. A processing worker still sees
        # the durable/Redis cancellation flags and persists any partial state.
        stream_hub.mark_done(generation_id, status="cancelled")
    return cancelled


def _safe_failure(generation_id: str, *, code: str = "generation_failed") -> None:
    try:
        stream_hub.publish_line(
            generation_id,
            json.dumps(
                {
                    "t": "e",
                    "d": "Assistant response failed",
                    "code": code,
                    "i18n_key": "chat_sr_response_failed",
                },
                separators=(",", ":"),
            ),
        )
    finally:
        stream_hub.mark_done(generation_id, status="failed")
        cancel_registry.clear(generation_id)


def _active_user(db, user_id: str) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if (
        user is None
        or getattr(user, "deleted_at", None) is not None
        or not bool(getattr(user, "is_active", False))
        or str(getattr(user, "role", "")) == "pending"
    ):
        raise FatalJobError("user_unavailable")
    return user


def _publish_worker_only_line(generation_id: str, line: str) -> None:
    """Publish setup events that the legacy HTTP consumer used to forward.

    Provider tokens, IDs, errors, titles, and completion events are already
    published by the shared chat generator.  A newly-created chat notification
    is emitted before that generator starts its hub, so the external worker is
    now the only process able to forward it.
    """

    try:
        payload = json.loads(line)
    except (TypeError, json.JSONDecodeError):
        return
    if isinstance(payload, dict) and payload.get("t") == "n_c":
        stream_hub.publish_line(generation_id, line)


async def _consume_send(job: WorkerJobSnapshot, context: WorkerContext) -> dict:
    from app.chats.utils import send_message

    generation_id = str(job.payload.get("generation_id") or "")
    if not generation_id:
        raise FatalJobError("invalid_payload")
    try:
        require_shared_generation_stream()
    except RuntimeError as exc:
        raise FatalJobError("shared_stream_unavailable") from exc
    def _stream():
        # Construct, use, and close the synchronous Session on one bounded
        # compatibility thread. Provider implementations are not yet safe to
        # move a live Session between the worker event loop and that thread.
        session = SessionLocal()
        try:
            context.raise_if_cancelled()
            if cancel_registry.is_cancelled(generation_id):
                raise JobCancelled()
            user = _active_user(session, str(job.user_id or ""))
            request = SendChatRequest.model_validate(job.payload.get("request") or {})
            yield from send_message(
                user.id,
                user.group_id,
                str(request.chat_id or "").strip(),
                request.message,
                request.image_ids,
                request.video_ids,
                request.audio_ids,
                request.document_ids,
                job.payload.get("normalized_project_id"),
                request.temp_chat,
                request.model_id,
                job.payload.get("byok"),
                job.payload.get("custom_settings"),
                session,
                skill_id=request.skill_id,
                skill_ids=request.skill_ids,
                note_ids=request.note_ids,
                prompt_ids=request.prompt_ids,
                reference_parts=request.reference_parts,
                chat_reference_ids=request.chat_reference_ids,
                user_role=user.role,
                generation_id=generation_id,
                subagent_targets=job.payload.get("subagent_targets"),
            )
        finally:
            session.close()

    try:
        context.raise_if_cancelled()
        if cancel_registry.is_cancelled(generation_id):
            raise JobCancelled()
        async for line in iterate_sync_stream_async(_stream):
            _publish_worker_only_line(generation_id, line)
            context.raise_if_cancelled()
        return {"generation_id": generation_id}
    except JobCancelled:
        cancel_registry.cancel(generation_id)
        stream_hub.mark_done(generation_id, status="cancelled")
        cancel_registry.clear(generation_id)
        raise
    except Exception:
        _safe_failure(generation_id)
        raise


async def _consume_regenerate(job: WorkerJobSnapshot, context: WorkerContext) -> dict:
    from app.chats.utils import regenerate_message

    generation_id = str(job.payload.get("generation_id") or "")
    if not generation_id:
        raise FatalJobError("invalid_payload")
    try:
        require_shared_generation_stream()
    except RuntimeError as exc:
        raise FatalJobError("shared_stream_unavailable") from exc
    def _stream():
        session = SessionLocal()
        try:
            context.raise_if_cancelled()
            if cancel_registry.is_cancelled(generation_id):
                raise JobCancelled()
            user = _active_user(session, str(job.user_id or ""))
            request = RegenerateMessageRequest.model_validate(job.payload.get("request") or {})
            yield from regenerate_message(
                user.id,
                user.group_id,
                request.chat_id,
                request.user_message_id,
                request.model_id,
                job.payload.get("byok"),
                job.payload.get("custom_settings"),
                session,
                skill_id=request.skill_id,
                skill_ids=request.skill_ids,
                note_ids=request.note_ids,
                prompt_ids=request.prompt_ids,
                chat_reference_ids=request.chat_reference_ids,
                retry_guidance=request.retry_guidance,
                user_role=user.role,
                generation_id=generation_id,
                subagent_targets=job.payload.get("subagent_targets"),
            )
        finally:
            session.close()

    try:
        context.raise_if_cancelled()
        if cancel_registry.is_cancelled(generation_id):
            raise JobCancelled()
        async for _line in iterate_sync_stream_async(_stream):
            context.raise_if_cancelled()
        return {"generation_id": generation_id}
    except JobCancelled:
        cancel_registry.cancel(generation_id)
        stream_hub.mark_done(generation_id, status="cancelled")
        cancel_registry.clear(generation_id)
        raise
    except Exception:
        _safe_failure(generation_id)
        raise


def reconcile_terminal_generation_jobs(*, batch_size: int = 1000) -> int:
    """Close shared streams whose at-most-once generation job terminated."""

    if not redis_enabled() or get_redis_client() is None:
        return 0
    session = SessionLocal()
    try:
        rows = lock_unreconciled_terminal_jobs(
            session,
            queue=QUEUE_GENERATION,
            kinds=("send", "regenerate"),
            batch_size=batch_size,
        )
        current = utcnow()
        for row in rows:
            prefix = "generation:"
            key = str(row.idempotency_key or "")
            generation_id = key[len(prefix) :] if key.startswith(prefix) else ""
            if generation_id:
                if row.status == JOB_CANCELLED:
                    stream_hub.mark_done(generation_id, status="cancelled")
                    cancel_registry.clear(generation_id)
                else:
                    _safe_failure(
                        generation_id,
                        code=str(row.error_code or "generation_failed"),
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


def build_worker() -> DurableQueueWorker:
    return DurableQueueWorker(
        queue=QUEUE_GENERATION,
        handlers={"send": _consume_send, "regenerate": _consume_regenerate},
        reconciler=reconcile_terminal_generation_jobs,
        default_lease_seconds=120,
    )


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
