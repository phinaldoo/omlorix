"""Dedicated durable memory extraction; independent of chat streams and Redis."""

from __future__ import annotations

from datetime import datetime
import os
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.database import SessionLocal
from app.memories.service import MAX_MEMORY_SOURCE_AGE
from app.workers.models import (
    QUEUE_MEMORY,
    DurableWorkerJob,
    WorkerJobSnapshot,
    enqueue_worker_job,
)
from app.workers.runtime import (
    DurableQueueWorker,
    FatalJobError,
    WorkerContext,
    run_worker_cli,
)


def external_memory_enabled() -> bool:
    return str(os.getenv("MEMORY_WORKER_MODE", "inline")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "external",
        "worker",
    }


def enqueue_memory_consolidation_job(
    db,
    *,
    user_id: str,
    source_message_id: str,
    source_at,
    source_text: str,
    current_model_id: str | None = None,
    byok: dict[str, Any] | None = None,
    commit: bool = True,
) -> DurableWorkerJob:
    """Persist one encrypted, retryable post-message memory job."""

    normalized_user_id = str(user_id or "").strip()
    normalized_message_id = str(source_message_id or "").strip()
    if not normalized_user_id or not normalized_message_id:
        raise ValueError("Memory consolidation requires a user and source message")
    payload = {
        "source_message_id": normalized_message_id,
        "source_at": jsonable_encoder(source_at),
        "source_text": str(source_text or ""),
        "current_model_id": str(current_model_id or "").strip() or None,
        # The entire durable payload is encrypted and erased at terminal state.
        "byok": jsonable_encoder(byok),
    }
    return enqueue_worker_job(
        db,
        queue=QUEUE_MEMORY,
        kind="memory_consolidation",
        user_id=normalized_user_id,
        payload=payload,
        idempotency_key=f"memory:{normalized_user_id}:{normalized_message_id}",
        priority=50,
        max_attempts=3,
        expires_at=source_at + MAX_MEMORY_SOURCE_AGE,
        commit=commit,
    )


def _consume_memory_consolidation(
    job: WorkerJobSnapshot,
    context: WorkerContext,
) -> dict:
    """Run memory extraction outside the request and streaming critical path."""

    from app.memories.consolidation import process_memory_consolidation

    payload = job.payload or {}
    source_at_raw = payload.get("source_at")
    if isinstance(source_at_raw, str):
        try:
            source_at = datetime.fromisoformat(source_at_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise FatalJobError("invalid_payload") from exc
    else:
        source_at = source_at_raw
    if not isinstance(source_at, datetime):
        raise FatalJobError("invalid_payload")

    session = SessionLocal()
    try:
        context.raise_if_cancelled()
        result = process_memory_consolidation(
            session,
            user_id=str(job.user_id or ""),
            source_message_id=str(payload.get("source_message_id") or ""),
            source_at=source_at,
            source_text=str(payload.get("source_text") or ""),
            current_model_id=(
                str(payload.get("current_model_id") or "").strip() or None
            ),
            byok=payload.get("byok") if isinstance(payload.get("byok"), dict) else None,
        )
        context.raise_if_cancelled()
        # Never retain source text or generated facts in the worker result.
        return {
            key: value
            for key, value in result.items()
            if key
            in {
                "status",
                "reason",
                "created_count",
                "updated_count",
                "confirmed_count",
                "deleted_count",
                "evicted_count",
                "skipped_count",
                "stale_count",
            }
        }
    finally:
        session.close()


def build_worker() -> DurableQueueWorker:
    return DurableQueueWorker(
        queue=QUEUE_MEMORY,
        handlers={"memory_consolidation": _consume_memory_consolidation},
        default_lease_seconds=120,
    )


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
