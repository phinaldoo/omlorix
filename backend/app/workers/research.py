from __future__ import annotations

from datetime import timedelta
import json
import os
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from app.chats.streaming import stream_hub
from app.database import SessionLocal
from app.llm.models import Models
from app.redis_client import get_redis_client, redis_enabled
from app.tools.deep_research.models import (
    RUN_STATUS_CANCELLED,
    RUN_STATUS_FAILED,
    TERMINAL_RUN_STATUSES,
    get_deep_research_run,
    utc_now,
)
from app.users.models import User
from app.workers.models import (
    DurableWorkerJob,
    JOB_CANCELLED,
    QUEUE_RESEARCH,
    WorkerJobSnapshot,
    enqueue_worker_job,
    lock_unreconciled_terminal_jobs,
    utcnow,
)
from app.workers.runtime import (
    DurableQueueWorker,
    FatalJobError,
    JobCancelled,
    RetryableJobError,
    WorkerContext,
    run_worker_cli,
)


_ENABLE_VALUES = {"1", "true", "yes", "on", "external", "worker"}


def external_research_enabled() -> bool:
    configured = (
        str(os.getenv("RESEARCH_WORKER_MODE", "inline") or "inline").strip().lower()
        in _ENABLE_VALUES
    )
    return configured and redis_enabled()


def enqueue_research_job(
    db,
    *,
    run_id: str,
    user_id: str,
) -> DurableWorkerJob:
    if not redis_enabled() or get_redis_client() is None:
        raise RuntimeError("The external Research Worker requires shared Redis")
    return enqueue_worker_job(
        db,
        queue=QUEUE_RESEARCH,
        kind="deep_research",
        user_id=user_id,
        payload={"run_id": str(run_id)},
        idempotency_key=f"deep-research:{run_id}",
        priority=20,
        max_attempts=3,
        expires_at=utcnow() + timedelta(days=2),
        commit=True,
    )


def enqueue_subagent_job(
    db,
    *,
    run_id: str,
    user_id: str,
    tool_arguments: dict[str, Any],
    project_id: str | None,
    model_settings: dict[str, Any] | None,
    chat_id: str | None,
    chat_history: list | None,
    parent_generation_id: str | None,
) -> DurableWorkerJob:
    if not redis_enabled() or get_redis_client() is None:
        raise RuntimeError("The external Research Worker requires shared Redis")
    return enqueue_worker_job(
        db,
        queue=QUEUE_RESEARCH,
        kind="subagent",
        user_id=user_id,
        payload={
            "run_id": str(run_id),
            "tool_arguments": jsonable_encoder(tool_arguments),
            "project_id": str(project_id) if project_id else None,
            "model_settings": jsonable_encoder(model_settings),
            "chat_id": str(chat_id) if chat_id else None,
            "chat_history": jsonable_encoder(chat_history),
            "parent_generation_id": (
                str(parent_generation_id) if parent_generation_id else None
            ),
        },
        idempotency_key=f"subagent:{run_id}",
        priority=15,
        # A nested provider may have produced text or files before a hard
        # process failure. Never replay that unknown side effect automatically.
        max_attempts=1,
        expires_at=utcnow() + timedelta(days=1),
        commit=True,
    )


def _validate_current_research_policy(session, *, user: User, run) -> str | None:
    """Recheck mutable tool, project, and model grants before provider work."""

    config = run.config_snapshot if isinstance(run.config_snapshot, dict) else {}
    project_id = str(config.get("project_id") or "").strip() or None
    try:
        if project_id:
            from app.projects.models import get_project_with_access

            get_project_with_access(session, str(user.id), project_id)

        authorization = config.get("execution_authorization")
        if not isinstance(authorization, dict):
            raise FatalJobError("authorization_context_unavailable")
        runtime_tools = authorization.get("runtime_enabled_tools")
        if not isinstance(runtime_tools, list) or "deep_research" not in {
            str(value or "").strip() for value in runtime_tools
        }:
            raise FatalJobError("authorization_context_unavailable")

        origin_kind = str(authorization.get("origin_kind") or "").strip().lower()
        origin_model_id = str(
            authorization.get("origin_model_id") or ""
        ).strip()
        byok_context = None
        origin_settings = None
        raw_tools = runtime_tools
        if origin_kind == "model":
            if not origin_model_id:
                raise FatalJobError("authorization_context_unavailable")
            from app.llm.utils import ensure_user_access_to_model

            ensure_user_access_to_model(str(user.id), origin_model_id, session)
            origin_model = (
                session.query(Models)
                .filter(
                    Models.id == origin_model_id,
                    Models.is_active.is_(True),
                )
                .first()
            )
            if origin_model is None or "tools" not in set(
                getattr(origin_model, "capabilities", None) or []
            ):
                raise FatalJobError("tool_no_longer_enabled")
            raw_tools = getattr(origin_model, "tools", None) or []
            origin_settings = (
                origin_model.settings
                if isinstance(origin_model.settings, dict)
                else None
            )
        elif origin_kind == "byok":
            # Only a truthy marker is passed: resolver needs BYOK mode to
            # reapply the user's current group allowlist, never credentials.
            byok_context = {"authorization_check": True}
        else:
            raise FatalJobError("authorization_context_unavailable")

        from app.tools.utils import resolve_enabled_tools

        resolved = resolve_enabled_tools(
            raw_tools,
            db=session,
            model_settings=origin_settings,
            user_id=str(user.id),
            byok=byok_context,
            project_id=project_id,
        )
        if "deep_research" not in set(resolved.get("tool_list") or []):
            raise FatalJobError("tool_no_longer_enabled")

        model_id = str(getattr(run, "model_id", "") or "").strip()
        execution_mode = str(
            getattr(run, "execution_mode", "custom") or "custom"
        ).strip().lower()
        if execution_mode != "native" and model_id:
            from app.llm.utils import ensure_user_access_to_model

            ensure_user_access_to_model(str(user.id), model_id, session)
    except FatalJobError:
        raise
    except HTTPException as exc:
        if int(exc.status_code) >= 500:
            raise RetryableJobError(
                "research_policy_unavailable",
                delay_seconds=30,
            ) from exc
        raise FatalJobError("authorization_changed") from exc
    return project_id


def _execute(job: WorkerJobSnapshot, context: WorkerContext) -> dict[str, Any]:
    from app.tools.deep_research.utils import (
        _activity_snapshot,
        _append_activity_snapshot_event,
        _event_to_widget_payload,
        _publish_research_event,
        execute_research_run,
    )
    from app.tools.deep_research.providers import DeepResearchCancelled

    if not redis_enabled() or get_redis_client() is None:
        raise FatalJobError("shared_stream_unavailable")
    run_id = str(job.payload.get("run_id") or "").strip()
    if not run_id:
        raise FatalJobError("invalid_payload")
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == str(job.user_id or "")).first()
        if (
            user is None
            or user.deleted_at is not None
            or not bool(user.is_active)
            or str(user.role or "").strip().lower() == "pending"
        ):
            raise FatalJobError("user_unavailable")
        run = get_deep_research_run(session, run_id)
        if run is None or str(run.user_id) != str(job.user_id):
            raise FatalJobError("run_unavailable")
        if run.status in TERMINAL_RUN_STATUSES:
            return {"run_id": run.id, "status": run.status}
        project_id = _validate_current_research_policy(
            session,
            user=user,
            run=run,
        )

        events: list[dict[str, Any]] = []

        def publish(event: dict[str, Any]) -> None:
            if context.cancelled():
                # Provider adapters already persist a complete cancelled run;
                # use their domain exception instead of letting a queue-level
                # cancellation be misclassified as a research failure.
                raise DeepResearchCancelled("Research worker job was cancelled")
            _append_activity_snapshot_event(
                events,
                {"t": "deep_research_evt", **_event_to_widget_payload(event)},
            )
            _publish_research_event(run.generation_id, event)

        execute_research_run(
            session,
            run,
            project_id=project_id,
            # Authorization-sensitive context is refreshed at execution time;
            # a queued snapshot must not preserve permissions after a downgrade.
            user_role=user.role,
            callback=publish,
        )
        session.refresh(run)
        if run.status == RUN_STATUS_CANCELLED:
            raise JobCancelled()
        result_meta = dict(run.result_meta or {})
        result_meta["activity_snapshot"] = _activity_snapshot(events)
        run.result_meta = result_meta
        session.add(run)
        session.commit()
        session.refresh(run)
        return {"run_id": run.id, "status": run.status}
    finally:
        session.close()


def _execute_subagent(job: WorkerJobSnapshot, context: WorkerContext) -> dict[str, Any]:
    from app.tools.subagents.runtime import _execute_subagent_tool_inline

    if not redis_enabled() or get_redis_client() is None:
        raise FatalJobError("shared_stream_unavailable")
    run_id = str(job.payload.get("run_id") or "").strip()
    if not run_id:
        raise FatalJobError("invalid_payload")
    stream_id = f"research-subagent:{run_id}"
    session = SessionLocal()
    stream_status = "failed"
    try:
        user = session.query(User).filter(User.id == str(job.user_id or "")).first()
        if (
            user is None
            or user.deleted_at is not None
            or not bool(user.is_active)
            or str(user.role or "").strip().lower() == "pending"
        ):
            raise FatalJobError("user_unavailable")
        context.raise_if_cancelled()
        generator = _execute_subagent_tool_inline(
            session,
            tool_arguments=job.payload.get("tool_arguments"),
            user_id=user.id,
            group_id=user.group_id,
            project_id=job.payload.get("project_id"),
            model_settings=job.payload.get("model_settings"),
            chat_id=job.payload.get("chat_id"),
            chat_history=job.payload.get("chat_history"),
            generation_id=job.payload.get("parent_generation_id"),
            user_role=user.role,
        )
        result: dict[str, Any] = {}
        try:
            while True:
                line = next(generator)
                if line is not None:
                    stream_hub.publish_line(stream_id, str(line))
                context.raise_if_cancelled()
        except StopIteration as completed:
            if isinstance(completed.value, dict):
                result = completed.value
        context.raise_if_cancelled()
        stream_status = "done"
        return result
    except JobCancelled:
        stream_status = "cancelled"
        stream_hub.publish_line(
            stream_id,
            json.dumps(
                {
                    "t": "subagent_evt",
                    "event": "cancelled",
                    "run_id": run_id,
                    "data": {"status": "cancelled", "code": "subagent_cancelled"},
                },
                separators=(",", ":"),
            ),
        )
        raise
    except Exception:
        stream_hub.publish_line(
            stream_id,
            json.dumps(
                {
                    "t": "subagent_evt",
                    "event": "error",
                    "run_id": run_id,
                    "data": {"status": "error", "code": "subagent_failed"},
                },
                separators=(",", ":"),
            ),
        )
        raise
    finally:
        stream_hub.mark_done(stream_id, status=stream_status)
        session.close()


def reconcile_terminal_research_jobs(*, batch_size: int = 1000) -> int:
    """Make an interrupted durable Research run terminal for its waiter."""

    if not redis_enabled() or get_redis_client() is None:
        return 0
    session = SessionLocal()
    try:
        rows = lock_unreconciled_terminal_jobs(
            session,
            queue=QUEUE_RESEARCH,
            kinds=("deep_research", "subagent"),
            batch_size=batch_size,
        )
        current = utc_now()
        for row in rows:
            if row.kind == "subagent":
                prefix = "subagent:"
                key = str(row.idempotency_key or "")
                run_id = key[len(prefix) :] if key.startswith(prefix) else ""
                if run_id:
                    stream_id = f"research-subagent:{run_id}"
                    event = "cancelled" if row.status == JOB_CANCELLED else "error"
                    stream_hub.publish_line(
                        stream_id,
                        json.dumps(
                            {
                                "t": "subagent_evt",
                                "event": event,
                                "run_id": run_id,
                                "data": {
                                    "status": event,
                                    "code": str(row.error_code or "subagent_failed")[:64],
                                },
                            },
                            separators=(",", ":"),
                        ),
                    )
                    stream_hub.mark_done(stream_id, status=event)
                row.reconciled_at = current
                row.updated_at = current
                continue

            prefix = "deep-research:"
            key = str(row.idempotency_key or "")
            run_id = key[len(prefix) :] if key.startswith(prefix) else ""
            run = get_deep_research_run(session, run_id) if run_id else None
            if run is not None and run.status not in TERMINAL_RUN_STATUSES:
                if row.status == JOB_CANCELLED:
                    run.status = RUN_STATUS_CANCELLED
                    run.phase = "cancelled"
                    run.error_code = "run_cancelled"
                    run.error_message_key = "deep_research_cancelled"
                else:
                    run.status = RUN_STATUS_FAILED
                    run.phase = "failed"
                    run.error_code = str(row.error_code or "internal_research_error")[:64]
                    run.error_message_key = "deep_research_failed"
                run.completed_at = current
                run.updated_at = current
                session.add(run)
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
        queue=QUEUE_RESEARCH,
        handlers={"deep_research": _execute, "subagent": _execute_subagent},
        reconciler=reconcile_terminal_research_jobs,
        default_lease_seconds=300,
    )


def main(argv: list[str] | None = None) -> int:
    return run_worker_cli(build_worker(), argv)


if __name__ == "__main__":
    raise SystemExit(main())
