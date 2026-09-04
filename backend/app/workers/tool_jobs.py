from __future__ import annotations

from datetime import timedelta
import hashlib
import os
import time
import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.chats.streaming import cancel_registry, stream_hub
from app.database import SessionLocal
from app.redis_client import get_redis_client, redis_enabled
from app.users.models import User
from app.workers.models import (
    DurableWorkerJob,
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_SUCCEEDED,
    QUEUE_MEDIA,
    QUEUE_RENDERING,
    WorkerJobFailed,
    WorkerJobSnapshot,
    enqueue_worker_job,
    request_worker_job_cancellation,
    utcnow,
)
from app.workers.runtime import FatalJobError, JobCancelled, WorkerContext


MEDIA_TOOL_NAMES = frozenset(
    {"image_generation", "video_generation", "audio_generation", "music_generation"}
)
RENDERING_TOOL_NAMES = frozenset({"slide_presentation", "latex_pdf"})
_ENABLE_VALUES = {"1", "true", "yes", "on", "external", "worker"}


def _enabled(name: str) -> bool:
    return str(os.getenv(name, "inline") or "inline").strip().lower() in _ENABLE_VALUES


def external_media_enabled() -> bool:
    return _enabled("MEDIA_WORKER_MODE")


def external_rendering_enabled() -> bool:
    return _enabled("RENDERING_WORKER_MODE")


def external_queue_for_tool(tool_name: str) -> str | None:
    normalized = str(tool_name or "").strip()
    if normalized in MEDIA_TOOL_NAMES and external_media_enabled():
        return QUEUE_MEDIA
    if normalized in RENDERING_TOOL_NAMES and external_rendering_enabled():
        return QUEUE_RENDERING
    return None


def _wait_seconds(queue: str) -> float:
    name = "MEDIA_REQUEST_WAIT_SECONDS" if queue == QUEUE_MEDIA else "RENDERING_REQUEST_WAIT_SECONDS"
    default = 1200 if queue == QUEUE_MEDIA else 900
    try:
        value = float(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        value = float(default)
    return max(1.0, min(value, 3600.0))


def _tool_job_key(
    *,
    queue: str,
    tool_name: str,
    user_id: str,
    generation_id: str | None,
    tool_call_id: str | None,
) -> str:
    stable_call = str(tool_call_id or "").strip()
    stable_generation = str(generation_id or "").strip()
    if not stable_call:
        stable_call = uuid.uuid4().hex
    material = f"{queue}:{tool_name}:{user_id}:{stable_generation}:{stable_call}"
    return f"tool:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def enqueue_tool_job(
    *,
    queue: str,
    tool_name: str,
    tool_arguments: dict[str, Any],
    user_id: str,
    group_id: str | None,
    project_id: str | None,
    model_settings: dict[str, Any] | None,
    byok: dict[str, Any] | None,
    chat_id: str | None,
    chat_history: list[Any] | None,
    generation_id: str | None,
    user_role: str | None,
    tool_call_id: str | None,
) -> DurableWorkerJob:
    session = SessionLocal()
    try:
        payload = jsonable_encoder(
            {
                "tool_name": tool_name,
                "tool_arguments": tool_arguments,
                "group_id": group_id,
                "project_id": project_id,
                "model_settings": model_settings,
                # The generic durable job encrypts and terminally erases BYOK
                # credentials and any reference-bearing chat history.
                "byok": byok,
                "chat_id": chat_id,
                "chat_history": chat_history,
                "generation_id": generation_id,
                "user_role": user_role,
                "tool_call_id": tool_call_id,
            }
        )
        return enqueue_worker_job(
            session,
            queue=queue,
            kind="tool_call",
            user_id=str(user_id),
            payload=payload,
            idempotency_key=_tool_job_key(
                queue=queue,
                tool_name=tool_name,
                user_id=str(user_id),
                generation_id=generation_id,
                tool_call_id=tool_call_id,
            ),
            priority=10,
            # Media generation and artifact creation have externally visible
            # side effects, so an ambiguous provider outcome is never replayed.
            max_attempts=1,
            expires_at=utcnow() + timedelta(hours=24),
            commit=True,
        )
    finally:
        session.close()


def _wait_for_tool_job(
    job: DurableWorkerJob,
    *,
    generation_id: str | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    delay = 0.15
    while True:
        session = SessionLocal()
        try:
            row = session.query(DurableWorkerJob).filter(DurableWorkerJob.id == job.id).first()
            if row is None:
                raise WorkerJobFailed("job_unavailable", status=JOB_FAILED)
            if row.status == JOB_SUCCEEDED:
                return dict(row.result or {})
            if row.status in (JOB_FAILED, JOB_CANCELLED):
                if row.status == JOB_CANCELLED:
                    raise JobCancelled()
                raise WorkerJobFailed(str(row.error_code or "tool_execution_failed"), status=row.status)
            if generation_id and cancel_registry.is_cancelled(generation_id):
                request_worker_job_cancellation(session, job_id=job.id, commit=True)
                raise JobCancelled()
        finally:
            session.close()
        if time.monotonic() >= deadline:
            raise TimeoutError("Tool worker did not complete before the request deadline")
        time.sleep(delay)
        delay = min(1.5, delay * 1.4)


def delegate_tool_call(
    *,
    queue: str,
    tool_name: str,
    tool_arguments: dict[str, Any],
    user_id: str,
    group_id: str | None,
    project_id: str | None,
    model_settings: dict[str, Any] | None,
    byok: dict[str, Any] | None,
    chat_id: str | None,
    chat_history: list[Any] | None,
    generation_id: str | None,
    user_role: str | None,
    tool_call_id: str | None,
):
    job = enqueue_tool_job(
        queue=queue,
        tool_name=tool_name,
        tool_arguments=tool_arguments,
        user_id=user_id,
        group_id=group_id,
        project_id=project_id,
        model_settings=model_settings,
        byok=byok,
        chat_id=chat_id,
        chat_history=chat_history,
        generation_id=generation_id,
        user_role=user_role,
        tool_call_id=tool_call_id,
    )
    result = _wait_for_tool_job(
        job,
        generation_id=generation_id,
        timeout_seconds=_wait_seconds(queue),
    )
    if not result.get("streamed"):
        for line in result.get("events") or []:
            if isinstance(line, str):
                yield line
    payload = result.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("Tool worker returned an invalid result")
    return payload


def _active_user(session, user_id: str) -> User:
    user = session.query(User).filter(User.id == str(user_id)).first()
    if (
        user is None
        or getattr(user, "deleted_at", None) is not None
        or not bool(getattr(user, "is_active", False))
        or str(getattr(user, "role", "")).strip().lower() == "pending"
    ):
        raise FatalJobError("user_unavailable")
    return user


def _validate_current_tool_policy(
    session,
    *,
    tool_name: str,
    user: User,
    model_settings: dict[str, Any] | None,
    byok: dict[str, Any] | None,
    project_id: str | None,
) -> None:
    settings = model_settings if isinstance(model_settings, dict) else {}
    originally_enabled = settings.get("_runtime_enabled_tools")
    if not isinstance(originally_enabled, (list, tuple, set, dict, str)):
        return
    from app.tools.utils import resolve_enabled_tools

    resolved = resolve_enabled_tools(
        originally_enabled,
        db=session,
        model_settings=settings,
        user_id=user.id,
        byok=byok,
        project_id=project_id,
    )
    currently_enabled = set(resolved.get("tool_list") or [])
    policy_name = "canvas" if tool_name == "latex_pdf" else tool_name
    if policy_name not in currently_enabled:
        raise FatalJobError("tool_no_longer_enabled")


def execute_tool_job(job: WorkerJobSnapshot, context: WorkerContext) -> dict[str, Any]:
    tool_name = str(job.payload.get("tool_name") or "").strip()
    if tool_name not in MEDIA_TOOL_NAMES | RENDERING_TOOL_NAMES:
        raise FatalJobError("unsupported_tool")
    session = SessionLocal()
    try:
        user = _active_user(session, str(job.user_id or ""))
        context.raise_if_cancelled()
        _validate_current_tool_policy(
            session,
            tool_name=tool_name,
            user=user,
            model_settings=job.payload.get("model_settings"),
            byok=job.payload.get("byok"),
            project_id=job.payload.get("project_id"),
        )
        generation_id = str(job.payload.get("generation_id") or "").strip() or None
        publish_live = bool(
            generation_id and redis_enabled() and get_redis_client() is not None
        )
        from app.tools.helper import resolve_tool_call

        runner = resolve_tool_call(
            session,
            tool_name,
            job.payload.get("tool_arguments") or {},
            user.id,
            user.group_id,
            job.payload.get("project_id"),
            model_settings=job.payload.get("model_settings"),
            byok=job.payload.get("byok"),
            chat_id=job.payload.get("chat_id"),
            chat_history=job.payload.get("chat_history"),
            generation_id=generation_id,
            user_role=user.role,
            tool_call_id=job.payload.get("tool_call_id"),
            _skip_rate_limit=True,
            _execution_queue=job.queue,
        )
        events: list[str] = []
        total_event_bytes = 0
        try:
            while True:
                line = next(runner)
                if line is None:
                    continue
                context.raise_if_cancelled()
                normalized = str(line)
                if publish_live:
                    stream_hub.publish_line(generation_id, normalized)
                else:
                    total_event_bytes += len(normalized.encode("utf-8"))
                    if total_event_bytes > 2 * 1024 * 1024:
                        raise FatalJobError("tool_stream_too_large")
                    events.append(normalized)
        except StopIteration as completed:
            payload = completed.value or {}
        context.raise_if_cancelled()
        return {
            "payload": jsonable_encoder(dict(payload)),
            "events": events,
            "streamed": publish_live,
        }
    finally:
        session.close()
