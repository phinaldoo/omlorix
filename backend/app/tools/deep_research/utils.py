from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import quote, urlsplit

from app.database import AuditSessionLocal
from app.llm.helper import build_widget_block_meta
from app.llm.models import LLMProvider, Models
from app.logging.models import create_audit_log
from app.settings.models import get_settings_page
from app.tools.deep_research.models import (
    RUN_STATUS_CANCELLED,
    RUN_STATUS_FAILED,
    TERMINAL_RUN_STATUSES,
    create_deep_research_run,
    get_deep_research_run,
    request_deep_research_cancellation,
    utc_now,
)
from app.tools.deep_research.native import run_native_research
from app.tools.deep_research.orchestrator import run_custom_research
from app.tools.deep_research.providers import DeepResearchCancelled, public_error_code
from app.tools.deep_research.storage import (
    get_deep_research_workspace_dir,
    get_deep_research_run_storage_provider,
    list_workspace_files,
    materialize_deep_research_artifact,
)

logger = logging.getLogger(__name__)
_LOCAL_MARKDOWN_TARGET_RE = re.compile(
    r"(?P<prefix>!?\[[^\]]*\]\()(?P<path>artifacts/[A-Za-z0-9._/-]+)"
)
_PUBLIC_WORKSPACE_FILES = {
    "citations.json",
    "manifest.json",
    "session.json",
    "workspace.zip",
}


def get_deep_research_config(db) -> dict[str, Any]:
    """Load and normalize the greenfield v2 Deep Research configuration."""

    config: dict[str, Any] = {
        "execution_mode": "custom",
        "model_id": "",
        "native_provider_id": "",
        "native_model_name": "deep-research-preview-04-2026",
        "max_revision_rounds": 2,
        "websearch_search_provider": "",
        "websearch_scrape_provider": "",
    }
    record = get_settings_page(db, "deep_research")
    if record is not None and isinstance(record.data, dict):
        config.update(record.data)

    execution_mode = str(config.get("execution_mode") or "custom").strip().lower()
    if execution_mode not in {"custom", "native"}:
        execution_mode = "custom"
    try:
        max_rounds = int(config.get("max_revision_rounds") or 2)
    except (TypeError, ValueError):
        max_rounds = 2

    return {
        "execution_mode": execution_mode,
        "model_id": str(config.get("model_id") or "").strip(),
        "native_provider_id": str(config.get("native_provider_id") or "").strip(),
        "native_model_name": str(
            config.get("native_model_name") or "deep-research-preview-04-2026"
        ).strip(),
        "max_revision_rounds": max(1, min(max_rounds, 3)),
        "websearch_search_provider": str(
            config.get("websearch_search_provider") or ""
        ).strip(),
        "websearch_scrape_provider": str(
            config.get("websearch_scrape_provider") or ""
        ).strip(),
    }


def create_research_run(
    db,
    *,
    user_id: str,
    query: str,
    chat_id: str | None = None,
    generation_id: str | None = None,
    project_id: str | None = None,
    execution_mode: str | None = None,
    authorization_context: dict[str, Any] | None = None,
) -> Any:
    """Validate configuration and persist one Markdown Deep Research run."""

    query_text = str(query or "").strip()
    if not query_text:
        raise ValueError("deep_research tool requires a query argument")

    config = get_deep_research_config(db)
    requested_mode = str(execution_mode or config["execution_mode"]).strip().lower()
    if requested_mode not in {"custom", "native"}:
        raise ValueError("Unsupported Deep Research execution mode.")

    model = None
    provider_id: str | None = None
    model_id: str | None = None
    model_name: str
    if requested_mode == "native":
        provider_id = str(config.get("native_provider_id") or "").strip()
        provider = (
            db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
            if provider_id
            else None
        )
        if (
            provider is None
            or str(provider.provider).strip().lower() != "google_aistudio"
        ):
            raise RuntimeError(
                "A Google AI Studio native Deep Research provider is not configured."
            )
        model_name = str(config.get("native_model_name") or "").strip()
        if not model_name:
            raise RuntimeError("Native Deep Research model name is not configured.")
    else:
        model_id = str(config.get("model_id") or "").strip()
        if not model_id:
            raise RuntimeError(
                "Deep Research model is not configured in admin settings."
            )
        model = (
            db.query(Models)
            .filter(Models.id == model_id, Models.is_active == True)  # noqa: E712
            .first()
        )
        if model is None:
            raise RuntimeError("Configured Deep Research model no longer exists.")
        provider_id = str(getattr(model, "provider_id", "") or "").strip() or None
        model_name = str(model.name or model.model_name or model.id)

    model_settings_override = {
        "websearch_search_provider": config["websearch_search_provider"],
        "websearch_scrape_provider": config["websearch_scrape_provider"],
    }
    model_settings_override = {
        key: value for key, value in model_settings_override.items() if value
    }
    raw_authorization = (
        authorization_context if isinstance(authorization_context, dict) else {}
    )
    origin_kind = str(raw_authorization.get("origin_kind") or "").strip().lower()
    if origin_kind not in {"model", "byok"}:
        origin_kind = "unknown"
    runtime_enabled_tools: list[str] = []
    raw_runtime_tools = raw_authorization.get("runtime_enabled_tools")
    if isinstance(raw_runtime_tools, (list, tuple, set)):
        for value in raw_runtime_tools:
            name = str(value or "").strip()
            if name and name not in runtime_enabled_tools:
                runtime_enabled_tools.append(name)
    execution_authorization = {
        "schema_version": 1,
        "origin_kind": origin_kind,
        "origin_model_id": (
            str(raw_authorization.get("origin_model_id") or "").strip() or None
        ),
        "runtime_enabled_tools": runtime_enabled_tools,
    }

    run = create_deep_research_run(
        db,
        user_id=str(user_id),
        query=query_text,
        chat_id=str(chat_id).strip() if chat_id else None,
        generation_id=str(generation_id).strip() if generation_id else None,
        execution_mode=requested_mode,
        # Keep the persisted field stable for imports and historical rows, but
        # new runs now have exactly one canonical Markdown output.
        output_format="markdown",
        provider_id=provider_id,
        model_id=model_id,
        model_name=model_name,
        max_revision_rounds=config["max_revision_rounds"],
        config_snapshot={
            "schema_version": 2,
            "project_id": str(project_id).strip() if project_id else None,
            "execution_authorization": execution_authorization,
            "native_model_name": (
                config.get("native_model_name") if requested_mode == "native" else None
            ),
            "model_settings_override": model_settings_override,
            "shared_tool_pipelines": {
                "web_search": "app.tools.websearch.utils.web_search",
                "code_execution": "app.tools.code_execution.utils.execute_code_tool_call",
            },
        },
    )
    return run


def _known_pipeline_phases(run) -> list[str]:
    """Return the deterministic phases that can be shown before execution.

    Revision and release-gate phases are deliberately omitted because the
    quality loop decides at runtime how many of them are needed. The frontend
    adds those phases when their first lifecycle event arrives.
    """

    if str(getattr(run, "execution_mode", "custom") or "custom") == "native":
        return ["native-research"]
    return ["planning", "deep-research", "evidence-audit"]


def _activity_steps_for_widget(run) -> list[dict[str, Any]]:
    """Return a bounded, secret-free activity summary for chat history.

    These phase-only steps remain the fallback for historical or imported
    messages that predate the rich activity snapshot. New runs additionally
    persist the sanitized event timeline in the tool-result metadata. Usage
    entries are written in execution order; durable checkpoints fill any gaps
    left by an interrupted persistence cycle.
    """

    steps: list[dict[str, Any]] = []
    positions: dict[str, int] = {}

    def add_step(
        phase: Any,
        *,
        status: str = "completed",
        duration_seconds: Any = None,
    ) -> None:
        """Add or enrich one normalized phase without leaking raw metadata."""

        normalized = str(phase or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", normalized):
            return
        if normalized in {"starting", "queued", "completed"}:
            return
        normalized_status = (
            status
            if status in {"pending", "running", "completed", "failed", "cancelled"}
            else "completed"
        )
        duration: float | None = None
        try:
            candidate = float(duration_seconds)
            if 0 <= candidate <= 86_400:
                duration = round(candidate, 3)
        except (TypeError, ValueError):
            pass

        existing_index = positions.get(normalized)
        if existing_index is not None:
            existing = steps[existing_index]
            if duration is not None:
                existing["duration_seconds"] = duration
            if (
                existing.get("status") == "completed"
                or normalized_status != "completed"
            ):
                existing["status"] = normalized_status
            return
        if len(steps) >= 24:
            return
        item: dict[str, Any] = {"phase": normalized, "status": normalized_status}
        if duration is not None:
            item["duration_seconds"] = duration
        positions[normalized] = len(steps)
        steps.append(item)

    usage = getattr(run, "usage", None)
    if isinstance(usage, dict):
        for phase, phase_usage in usage.items():
            metadata = phase_usage if isinstance(phase_usage, dict) else {}
            add_step(phase, duration_seconds=metadata.get("duration_seconds"))

    result_meta = getattr(run, "result_meta", None)
    checkpoints = (
        result_meta.get("checkpoints") if isinstance(result_meta, dict) else None
    )
    if isinstance(checkpoints, dict):
        for phase in checkpoints:
            add_step(phase)

    run_status = str(getattr(run, "status", "running") or "running").lower()
    current_phase = str(getattr(run, "phase", "starting") or "starting").lower()
    if current_phase not in positions and current_phase not in {
        "starting",
        "queued",
        "completed",
    }:
        current_status = {
            "failed": "failed",
            "error": "failed",
            "cancelled": "cancelled",
        }.get(run_status, "running")
        add_step(current_phase, status=current_status)

    # Native runs do not use the custom provider usage recorder. Older custom
    # runs may also lack usage metadata, so reconstruct only deterministic work
    # for a successfully completed result.
    if not steps and run_status == "completed":
        phases = _known_pipeline_phases(run)
        if str(getattr(run, "execution_mode", "custom") or "custom") != "native":
            try:
                revision_round = max(
                    0,
                    min(int(getattr(run, "revision_round", 0) or 0), 3),
                )
            except (TypeError, ValueError):
                revision_round = 0
            for round_number in range(1, revision_round + 1):
                phases.extend(
                    [f"final-revision-{round_number}", f"release-gate-{round_number}"]
                )
        for phase in phases:
            add_step(phase)

    # Checkpoints can fill a missing usage entry after later phases have
    # already been recorded. Restore the canonical workflow order so history
    # never displays review or revision work out of sequence.
    canonical_order = ["planning", "deep-research", "evidence-audit"]
    for round_number in range(1, 4):
        canonical_order.extend(
            [f"final-revision-{round_number}", f"release-gate-{round_number}"]
        )
    canonical_order.append("native-research")
    order_by_phase = {phase: index for index, phase in enumerate(canonical_order)}
    original_order = {item["phase"]: index for index, item in enumerate(steps)}
    steps.sort(
        key=lambda item: (
            order_by_phase.get(item["phase"], len(canonical_order)),
            original_order[item["phase"]],
        )
    )
    return steps


def _widget_data(run, terminal_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the bounded view model used by the frontend research card."""

    payload = terminal_payload if isinstance(terminal_payload, dict) else {}
    status = str(
        payload.get("status") or getattr(run, "status", "running") or "running"
    )
    status = "running" if status == "queued" else status
    phase = str(payload.get("phase") or getattr(run, "phase", "starting") or "starting")
    phase = "starting" if phase == "queued" else phase
    terminal = status in TERMINAL_RUN_STATUSES
    completion_warning = (getattr(run, "result_meta", None) or {}).get(
        "completion_warning"
    )
    return {
        "schema_version": 1,
        "run_id": str(getattr(run, "id", "") or ""),
        "generation_id": str(
            payload.get("generation_id")
            or getattr(run, "generation_id", "")
            or ""
        ),
        "status": status,
        "phase": phase,
        "terminal": terminal,
        "query": str(getattr(run, "query", "") or ""),
        "model": str(getattr(run, "model_name", "") or ""),
        "execution_mode": str(
            getattr(run, "execution_mode", "custom") or "custom"
        ),
        "error_code": str(
            payload.get("error_code") or getattr(run, "error_code", "") or ""
        ),
        "warning_code": str(
            payload.get("warning_code")
            or (completion_warning or {}).get("warning_code")
            or ""
        ),
        "has_completion_warning": bool(completion_warning),
        "known_phases": _known_pipeline_phases(run),
        "activity_steps": _activity_steps_for_widget(run),
        "final_report_path": str(
            payload.get("final_report_path")
            or getattr(run, "final_report_path", "")
            or ""
        ),
        "archive_path": str(payload.get("archive_path") or ""),
        "files": payload.get("files") or [],
    }


def _artifact_url(run_id: str, relative_path: str) -> str:
    """Return the authenticated public URL for one validated run artifact."""

    safe_path = "/".join(
        quote(segment, safe="")
        for segment in str(relative_path or "").split("/")
        if segment
    )
    return f"/api/v1/deep-research/runs/{run_id}/files/{safe_path}"


def _read_run_artifact_text(run, relative_path: str | None) -> str:
    """Read a persisted run artifact without exposing storage implementation."""

    normalized = str(relative_path or "").strip()
    if not normalized:
        return ""
    try:
        path = materialize_deep_research_artifact(
            run.user_id,
            run.id,
            normalized,
            storage_provider=get_deep_research_run_storage_provider(run),
        )
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeError):
        return ""


def _normalize_citations(payload: Any) -> list[dict[str, str]]:
    """Normalize report citations for both the widget and model tool result."""

    if not isinstance(payload, list):
        return []
    citations: list[dict[str, str]] = []
    for item in payload:
        if isinstance(item, str):
            url = item.strip()
            parsed = urlsplit(url)
            if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
                citations.append({"url": url, "title": url, "snippet": ""})
            continue
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("canonical_url") or "").strip()
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            continue
        citations.append(
            {
                "url": url,
                "title": str(item.get("title") or url).strip(),
                "snippet": str(
                    item.get("snippet") or item.get("excerpt") or ""
                ).strip(),
            }
        )
    return citations


def _event_to_widget_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Translate an orchestrator callback into the public chat widget protocol."""

    event_type = str(event.get("event_type") or "")
    phase = str(event.get("phase") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    progress_by_phase = {
        "queued": 5,
        "planning": 12,
        "deep-research": 32,
        "native-research": 32,
        "evidence-audit": 52,
        "final-revision-1": 62,
        "release-gate-1": 72,
        "final-revision-2": 78,
        "release-gate-2": 88,
        "final-revision-3": 90,
        "release-gate-3": 96,
        "completed": 100,
        "cancelled": 100,
        "failed": 100,
    }
    base = {
        "run_id": event.get("run_id"),
        "widget_id": event.get("run_id"),
        "sequence": event.get("sequence"),
        "phase": phase,
        "message_key": event.get("message_key"),
        "progress": progress_by_phase.get(phase, 20),
    }
    if event.get("created_at"):
        base["created_at"] = event["created_at"]
    if event_type in {
        "llm_request_started",
        "llm_request_completed",
        "llm_request_failed",
        "reasoning_delta",
        "reasoning_completed",
        "content_delta",
    }:
        stream_payload = {
            **base,
            "event": event_type,
            "status": "running",
            "request_id": payload.get("request_id"),
        }
        if payload.get("delta") is not None:
            stream_payload["delta"] = payload["delta"]
        if payload.get("duration_seconds") is not None:
            stream_payload["duration_seconds"] = payload["duration_seconds"]
        if "replace" in payload:
            stream_payload["replace"] = bool(payload["replace"])
        return stream_payload
    if event_type == "tool_started":
        return {
            **base,
            "event": "tool_call",
            "name": payload.get("tool"),
            "tool_call_id": payload.get("tool_call_id"),
            "request_id": payload.get("request_id"),
            "arguments": payload.get("arguments"),
        }
    if event_type in {"tool_completed", "tool_failed"}:
        return {
            **base,
            "event": "tool_result",
            "name": payload.get("tool"),
            "tool_call_id": payload.get("tool_call_id"),
            "request_id": payload.get("request_id"),
            "success": event_type == "tool_completed",
        }
    if event_type in {"artifact_detected", "artifact_created"}:
        return {
            **base,
            "event": "status",
            "status": "running",
            "artifact": payload,
        }
    if event_type == "report_updated":
        return {
            **base,
            "event": "report_updated",
            "status": "running",
            "report": str(payload.get("report") or ""),
        }
    if event_type == "cancelled":
        return {**base, "event": "cancelled", "status": "cancelled", "progress": 100}
    if event_type == "failed":
        return {**base, "event": "error", "status": "failed", "progress": 100}
    return {
        **base,
        "event": "status",
        "status": "completed" if event_type == "completed" else "running",
    }


def _append_activity_snapshot_event(
    events: list[dict[str, Any]],
    event: dict[str, Any],
) -> None:
    """Append one public event while compacting adjacent streamed text.

    The snapshot stores the same safe event contract sent to the browser, not
    provider requests or tool results. Adjacent text chunks can be combined
    without changing replay semantics because no lifecycle or tool event occurs
    between them. This keeps long reports and reasoning streams compact inside
    the persisted chat message while preserving exact block boundaries.
    """

    public_event = {
        str(key): value
        for key, value in event.items()
        if key != "t" and value is not None
    }
    event_name = str(public_event.get("event") or "")

    # The canonical final report is already persisted as its own artifact. An
    # intermediate report patch is preview state rather than Activity-tab data,
    # and retaining it here would duplicate the complete article several times.
    if event_name == "report_updated":
        return

    if event_name in {"reasoning_delta", "content_delta", "output_delta"}:
        delta = str(public_event.get("delta") or "")
        public_event["delta"] = delta
        previous = events[-1] if events else None
        if (
            isinstance(previous, dict)
            and previous.get("event") == event_name
            and previous.get("request_id") == public_event.get("request_id")
            and previous.get("phase") == public_event.get("phase")
        ):
            if public_event.get("replace"):
                previous["delta"] = delta
                previous["replace"] = True
            else:
                previous["delta"] = str(previous.get("delta") or "") + delta
            previous["sequence"] = public_event.get(
                "sequence",
                previous.get("sequence"),
            )
            if public_event.get("created_at"):
                previous["created_at"] = public_event["created_at"]
            return

    events.append(public_event)


def _activity_snapshot(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the versioned display-only payload persisted with a tool result."""

    return {
        "schema_version": 1,
        "events": events,
    }


def _publish_research_event(generation_id: str | None, event: dict[str, Any]) -> None:
    """Publish progress immediately into the owning chat generation stream."""

    if not generation_id:
        return
    from app.chats.streaming import stream_hub

    stream_hub.publish_line(
        generation_id,
        json.dumps(
            {"t": "deep_research_evt", **_event_to_widget_payload(event)},
            ensure_ascii=False,
        ),
    )


def _audit_run(user_id: str, action: str, run) -> None:
    """Record one secret-free inline Research lifecycle transition."""

    session = AuditSessionLocal()
    try:
        create_audit_log(
            db_log=session,
            user_id=str(user_id),
            action=action,
            category="deep_research",
            details={
                "run_id": run.id,
                "status": run.status,
                "phase": run.phase,
                "execution_mode": run.execution_mode,
                "revision_round": run.revision_round,
            },
        )
    except Exception:
        logger.exception(
            "Failed to write Deep Research audit log",
            extra={"run_id": run.id, "action": action},
        )
    finally:
        session.close()


def _mark_inline_failure(db, run, exc: Exception) -> None:
    """Persist a terminal failure if an adapter failed before doing so itself."""

    # Provider and persistence failures can surface during a flush. Always
    # clear a failed transaction before inspecting or updating the run.
    db.rollback()
    try:
        db.refresh(run)
    except Exception:
        pass
    if run.status in TERMINAL_RUN_STATUSES:
        return
    run.status = "failed"
    run.phase = "failed"
    run.error_code = public_error_code(exc)
    run.error_message_key = "deep_research_failed"
    run.completed_at = utc_now()
    run.updated_at = utc_now()
    db.add(run)
    db.commit()
    db.refresh(run)


def _terminal_run_payload(run) -> dict[str, Any]:
    """Build the complete terminal widget and tool payload from durable files."""

    report = _read_run_artifact_text(run, run.final_report_path)
    report = _LOCAL_MARKDOWN_TARGET_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{_artifact_url(run.id, match.group('path'))}"
        ),
        report,
    )
    citations_raw: Any = []
    citations_text = _read_run_artifact_text(run, "citations.json")
    if citations_text:
        try:
            citations_raw = json.loads(citations_text)
        except json.JSONDecodeError:
            citations_raw = []
    citations = _normalize_citations(citations_raw)

    workspace = get_deep_research_workspace_dir(run.user_id, run.id)
    files = list_workspace_files(workspace) if workspace.exists() else []
    stored_files = (
        (run.result_meta or {})
        .get("storage", {})
        .get(
            "uploaded_files",
            [],
        )
    )
    if isinstance(stored_files, list):
        files.extend(str(path) for path in stored_files)
    files.extend(
        str(path)
        for path in (
            run.final_report_path,
            run.final_html_path,
            run.manifest_path,
            (run.result_meta or {}).get("archive_path"),
        )
        if path
    )
    files = sorted(
        {
            path
            for path in files
            if path.startswith("artifacts/")
            or path in _PUBLIC_WORKSPACE_FILES
            or path
            in {
                run.final_report_path,
                run.final_html_path,
                run.manifest_path,
            }
        }
    )
    completion_warning = (run.result_meta or {}).get("completion_warning")
    return {
        "event": "complete" if run.status == "completed" else run.status,
        "run_id": run.id,
        "widget_id": run.id,
        "generation_id": run.generation_id,
        "query": run.query,
        "status": run.status,
        "phase": run.phase,
        "message_key": (
            (
                "deep_research_completed_with_warnings"
                if completion_warning
                else "deep_research_completed"
            )
            if run.status == "completed"
            else run.error_message_key
        ),
        "progress": 100,
        "report": report,
        "citations": citations,
        "files": files,
        "final_report_path": run.final_report_path,
        "archive_path": (run.result_meta or {}).get("archive_path"),
        "report_url": _artifact_url(run.id, run.final_report_path)
        if run.final_report_path
        else None,
        "error_code": run.error_code,
        "degraded": bool(completion_warning),
        "warning_code": (
            completion_warning.get("warning_code")
            if isinstance(completion_warning, dict)
            else None
        ),
        "warning_phase": (
            completion_warning.get("warning_phase")
            if isinstance(completion_warning, dict)
            else None
        ),
    }


def execute_research_run(
    db,
    run,
    *,
    project_id: str | None = None,
    user_role: str | None = None,
    callback=None,
):
    """Execute one already-persisted run, resuming durable checkpoints.

    Both inline development mode and the dedicated Research Worker use this
    boundary.  Provider adapters remain the sole owners of phase persistence;
    this wrapper provides consistent audit and terminal failure handling.
    """

    _audit_run(run.user_id, "DEEP_RESEARCH_STARTED", run)
    try:
        if run.execution_mode == "native":
            run_native_research(db, run, callback=callback)
        else:
            run_custom_research(
                db,
                run,
                project_id=project_id,
                user_role=user_role,
                callback=callback,
            )
        db.refresh(run)
        if run.status == RUN_STATUS_CANCELLED:
            _audit_run(run.user_id, "DEEP_RESEARCH_CANCELLED", run)
        elif run.status == RUN_STATUS_FAILED:
            _audit_run(run.user_id, "DEEP_RESEARCH_FAILED", run)
        else:
            _audit_run(run.user_id, "DEEP_RESEARCH_COMPLETED", run)
    except DeepResearchCancelled:
        db.refresh(run)
        _audit_run(run.user_id, "DEEP_RESEARCH_CANCELLED", run)
    except Exception as exc:
        logger.exception("Deep Research execution failed", extra={"run_id": run.id})
        _mark_inline_failure(db, run, exc)
        _audit_run(run.user_id, "DEEP_RESEARCH_FAILED", run)
    return run


def _external_research_enabled() -> bool:
    from app.workers.research import external_research_enabled

    return external_research_enabled()


def deep_research(
    *,
    db,
    user_id: str,
    query: str,
    config_override: dict[str, Any] | None = None,
    generation_id: str | None = None,
    chat_id: str | None = None,
    project_id: str | None = None,
    user_role: str | None = None,
    authorization_context: dict[str, Any] | None = None,
):
    """Execute Research inline and return its report to the main conversation."""

    del config_override
    run = create_research_run(
        db,
        user_id=user_id,
        query=query,
        chat_id=chat_id,
        generation_id=generation_id,
        project_id=project_id,
        authorization_context=authorization_context,
    )
    widget_payload = {
        "type": "deep_research",
        "html": json.dumps(
            _widget_data(run), ensure_ascii=False, separators=(",", ":")
        ),
        "render_mode": "frontend",
        "run_id": run.id,
        "model_context": {
            "schema_version": 2,
            "run_id": run.id,
            "status": "running",
            "phase": "starting",
        },
    }
    activity_events: list[dict[str, Any]] = []
    yield (
        json.dumps(
            {
                "t": "wg",
                "c": widget_payload["html"],
                "widget_type": "deep_research",
                "meta": build_widget_block_meta(
                    widget_payload,
                    tool_name="deep_research",
                ),
            },
            ensure_ascii=False,
        )
        + "\n"
    )

    def publish_event(event: dict[str, Any]) -> None:
        """Capture and forward one safe event from the synchronous workflow."""

        _append_activity_snapshot_event(
            activity_events,
            {"t": "deep_research_evt", **_event_to_widget_payload(event)},
        )
        _publish_research_event(generation_id, event)

    if _external_research_enabled():
        try:
            from app.workers.research import enqueue_research_job

            enqueue_research_job(
                db,
                run_id=run.id,
                user_id=user_id,
            )
            poll_seconds = max(
                0.2,
                min(float(os.getenv("RESEARCH_RESULT_POLL_SECONDS", "1") or "1"), 10.0),
            )
            while True:
                db.expire_all()
                current = get_deep_research_run(db, run.id)
                if current is None:
                    raise RuntimeError("Deep Research run disappeared while queued.")
                run = current
                if generation_id:
                    from app.chats.streaming import cancel_registry

                    if cancel_registry.is_cancelled(generation_id) and not run.cancel_requested:
                        request_deep_research_cancellation(db, run)
                if run.status in TERMINAL_RUN_STATUSES:
                    break
                time.sleep(poll_seconds)
            stored_activity = (run.result_meta or {}).get("activity_snapshot")
            if isinstance(stored_activity, dict) and isinstance(stored_activity.get("events"), list):
                activity_events = list(stored_activity["events"])
        except Exception as exc:
            logger.exception("Failed waiting for external Deep Research", extra={"run_id": run.id})
            _mark_inline_failure(db, run, exc)
    else:
        execute_research_run(
            db,
            run,
            project_id=project_id,
            user_role=user_role,
            callback=publish_event,
        )

    result = _terminal_run_payload(run)
    # Live clients receive the terminal event below. Chat history instead keeps
    # this final card, so reopening a finished conversation needs no run API.
    widget_payload["html"] = json.dumps(
        _widget_data(run, result), ensure_ascii=False, separators=(",", ":")
    )
    widget_payload["model_context"] = {
        "schema_version": 2,
        "run_id": result["run_id"],
        "status": result["status"],
        "phase": result["phase"],
        "final_report_path": result.get("final_report_path"),
        "archive_path": result.get("archive_path"),
        "files": result.get("files") or [],
        "error_code": result.get("error_code"),
        "warning_code": result.get("warning_code"),
        "warning_phase": result.get("warning_phase"),
    }
    # Replay the terminal lifecycle row as well, but do not duplicate the
    # potentially large report, citations, or file payload in chat metadata.
    # The completed widget already persists the artifact paths needed to load
    # those sections. This event is solely for Activity-tab fidelity.
    _append_activity_snapshot_event(
        activity_events,
        {
            "event": result["event"],
            "run_id": result["run_id"],
            "widget_id": result["run_id"],
            "generation_id": result.get("generation_id"),
            "phase": result["phase"],
            "status": result["status"],
            "message_key": result.get("message_key"),
            "error_code": result.get("error_code"),
            "warning_code": result.get("warning_code"),
            "progress": 100,
        },
    )
    yield (
        json.dumps(
            {"t": "deep_research_evt", **result},
            ensure_ascii=False,
        )
        + "\n"
    )

    report = str(result.get("report") or "").strip()
    citation_lines = [
        f"- {citation['title']}: {citation['url']}"
        for citation in result.get("citations") or []
    ]
    content_parts = [
        "Deep Research has finished.",
        f"Run status: {result['status']}",
        f"Run ID: {result['run_id']}",
    ]
    if report:
        content_parts.extend(["", "Final report:", report])
    if citation_lines:
        content_parts.extend(["", "Sources:", *citation_lines])
    if result.get("files"):
        content_parts.extend(
            [
                "",
                "Generated files:",
                *[
                    f"- {path}: {_artifact_url(result['run_id'], path)}"
                    for path in result["files"]
                ],
            ]
        )
    if result.get("error_code"):
        content_parts.extend(["", f"Error code: {result['error_code']}"])
    if result.get("warning_code"):
        content_parts.extend(
            [
                "",
                "Completion warning: the final quality pass could not be completed; "
                "the report is the latest successfully checkpointed version.",
                f"Warning code: {result['warning_code']}",
            ]
        )

    return {
        "content": "\n".join(content_parts),
        "result": result,
        "documents": [],
        "images": [],
        "videos": [],
        "audios": [],
        "youtube": [],
        "webpages": [],
        "widget": widget_payload,
        "tool_meta": {
            "deep_research": True,
            "schema_version": 2,
            "run_id": run.id,
            "model_id": run.model_id,
            "status": result["status"],
            "error_code": result.get("error_code"),
            "warning_code": result.get("warning_code"),
            "warning_phase": result.get("warning_phase"),
            # Display-only replay data belongs to the persisted tool-result
            # metadata. It is not included in model_context and therefore does
            # not inflate subsequent LLM requests.
            "deep_research_activity": _activity_snapshot(activity_events),
        },
    }


__all__ = [
    "create_research_run",
    "deep_research",
    "execute_research_run",
    "get_deep_research_config",
]
