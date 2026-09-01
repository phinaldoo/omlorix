from __future__ import annotations

import logging
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urldefrag, urlsplit

from app.tools.deep_research.artifacts import (
    clean_unresolved_artifact_references,
    localize_artifact_references,
    remove_remote_image_embeds,
)
from app.tools.deep_research.editing import (
    apply_article_revision,
    article_revision_repair_context,
    validate_article_revision,
)
from app.tools.deep_research.models import (
    DeepResearchRun,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    utc_now,
)
from app.tools.deep_research.prompts import (
    FINALIZER_INSTRUCTIONS,
    PLANNER_INSTRUCTIONS,
    RESEARCHER_INSTRUCTIONS,
    REVIEWER_INSTRUCTIONS,
    finalizer_input,
    planner_input,
    research_input,
    review_input,
)
from app.tools.deep_research.providers import (
    DeepResearchCancelled,
    DeepResearchEmptyResponse,
    DeepResearchIncompleteStream,
    DeepResearchStructuredOutputError,
    PhaseResult,
    parse_structured_output,
    public_error_code,
    run_model_phase,
    structured_output_repair_request,
)
from app.tools.deep_research.schemas import (
    ArticleRevision,
    QualityReview,
    ResearchBrief,
    ReviewIssue,
)
from app.tools.deep_research.storage import (
    artifact_manifest,
    create_workspace_archive,
    get_deep_research_workspace_dir,
    list_run_artifacts,
    materialize_deep_research_artifact,
    persist_generated_files,
    save_run_artifacts,
    upload_deep_research_artifacts,
    write_session_metadata,
    write_workspace_json,
    write_workspace_text,
)


logger = logging.getLogger(__name__)

PublicEventCallback = Callable[[dict[str, Any]], None]

_MARKDOWN_LINK_RE = re.compile(
    r"(?<!!)\[[^\]]+\]\((?P<url>https?://[^\s)]+)(?:\s+\"[^\"]*\")?\)",
    re.IGNORECASE,
)
_BARE_URL_RE = re.compile(r"(?<![\(\"'])https?://[^\s<>)\]]+", re.IGNORECASE)
_REMOTE_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(\s*https?://[^)]+\)",
    re.IGNORECASE,
)
_RAW_HTML_RE = re.compile(
    r"<\s*(?:script|iframe|object|embed|img|video|audio|svg)\b", re.IGNORECASE
)
_EMPTY_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*\)")
_ARTIFACT_PATH_RE = re.compile(r"(?:^|[\s(])(artifacts/[A-Za-z0-9._/-]+)")
_PROVIDER_STREAM_MAX_ATTEMPTS = 3
_STRUCTURED_REPAIR_MAX_ATTEMPTS = 2


def _safe_dict(value: Any) -> dict[str, Any]:
    """Return a detached dictionary without leaking model objects."""

    return dict(value) if isinstance(value, dict) else {}


def _merge_phase_results(
    previous: PhaseResult | None,
    current: PhaseResult,
) -> PhaseResult:
    """Merge retry accounting while keeping only the newest assistant text."""

    if previous is None:
        return current
    return PhaseResult(
        text=current.text,
        generated_files=[*previous.generated_files, *current.generated_files],
        tool_calls=[*previous.tool_calls, *current.tool_calls],
        sources=[*previous.sources, *current.sources],
        usage=[*previous.usage, *current.usage],
        raw_event_count=previous.raw_event_count + current.raw_event_count,
        duration_seconds=round(
            previous.duration_seconds + current.duration_seconds,
            3,
        ),
        structured_output=current.structured_output,
    )


def _record_phase_diagnostic(
    db,
    run: DeepResearchRun,
    *,
    phase: str,
    error_code: str,
    attempt: int,
    response_chars: int = 0,
    validation_summary: str = "",
) -> None:
    """Persist bounded, secret-free retry diagnostics on the owning run."""

    if not all(
        callable(getattr(db, name, None)) for name in ("add", "commit", "refresh")
    ):
        return
    result_meta = _safe_dict(getattr(run, "result_meta", None))
    diagnostics = list(result_meta.get("phase_diagnostics") or [])
    diagnostics.append(
        {
            "phase": str(phase),
            "error_code": str(error_code),
            "attempt": max(1, int(attempt)),
            "response_chars": max(0, int(response_chars)),
            "validation_summary": str(validation_summary or "")[:1_000],
            "created_at": utc_now().isoformat(),
        }
    )
    run.result_meta = {**result_meta, "phase_diagnostics": diagnostics[-20:]}
    run.updated_at = utc_now()
    db.add(run)
    db.commit()
    db.refresh(run)


def _record_degraded_report_completion(
    db,
    run: DeepResearchRun,
    *,
    phase: str,
    exc: BaseException,
    callback: PublicEventCallback | None,
) -> dict[str, Any]:
    """Checkpoint a recoverable finalization failure without losing the report."""

    warning = {
        "degraded": True,
        "warning_code": public_error_code(exc),
        "warning_phase": str(phase),
        "message_key": "deep_research_completed_with_warnings",
    }
    run.result_meta = {**_safe_dict(run.result_meta), "completion_warning": warning}
    run.updated_at = utc_now()
    db.add(run)
    db.commit()
    db.refresh(run)
    _emit(
        db,
        run,
        event_type="partial_report_available",
        phase=phase,
        message_key="deep_research_completed_with_warnings",
        payload={
            "warning_code": warning["warning_code"],
            "warning_phase": warning["warning_phase"],
        },
        callback=callback,
    )
    return warning


def _run_phase_call_with_transport_retries(
    db,
    run: DeepResearchRun,
    *,
    phase: str,
    phase_call: dict[str, Any],
    callback: PublicEventCallback | None,
) -> PhaseResult:
    """Retry only silently interrupted provider streams with a fresh request."""

    accumulated: PhaseResult | None = None
    for attempt in range(1, _PROVIDER_STREAM_MAX_ATTEMPTS + 1):
        try:
            current = run_model_phase(db, **phase_call)
            return _merge_phase_results(accumulated, current)
        except DeepResearchIncompleteStream as exc:
            partial = exc.partial_result
            if isinstance(partial, PhaseResult):
                accumulated = _merge_phase_results(accumulated, partial)
            _record_phase_diagnostic(
                db,
                run,
                phase=phase,
                error_code="provider_incomplete_response",
                attempt=attempt,
                response_chars=len(partial.text)
                if isinstance(partial, PhaseResult)
                else 0,
            )
            if attempt >= _PROVIDER_STREAM_MAX_ATTEMPTS:
                exc.partial_result = accumulated
                raise
            _emit(
                db,
                run,
                event_type="phase_retry",
                phase=phase,
                message_key="deep_research_stream_interrupted_retrying",
                payload={
                    "reason": "provider_incomplete_response",
                    "attempt": attempt + 1,
                    "max_attempts": _PROVIDER_STREAM_MAX_ATTEMPTS,
                },
                callback=callback,
            )

    raise AssertionError("The bounded provider retry loop did not terminate.")


def _emit(
    db,
    run: DeepResearchRun,
    *,
    event_type: str,
    phase: str | None = None,
    message_key: str | None = None,
    payload: dict[str, Any] | None = None,
    callback: PublicEventCallback | None = None,
) -> None:
    """Send one lifecycle event directly to the active chat generation."""

    if callback is not None:
        sequence = int(getattr(run, "_stream_event_sequence", 0) or 0) + 1
        setattr(run, "_stream_event_sequence", sequence)
        callback(
            {
                "run_id": run.id,
                "sequence": sequence,
                "event_type": event_type,
                "phase": phase or run.phase,
                "message_key": message_key,
                "payload": payload or {},
                "created_at": utc_now().isoformat(),
            }
        )


def _set_phase(
    db,
    run: DeepResearchRun,
    phase: str,
    *,
    callback: PublicEventCallback | None,
) -> None:
    """Checkpoint a phase transition before the provider call begins."""

    now = utc_now()
    run.status = RUN_STATUS_RUNNING
    run.phase = phase
    run.updated_at = now
    if run.started_at is None:
        run.started_at = now
    db.add(run)
    db.commit()
    db.refresh(run)
    _emit(
        db,
        run,
        event_type="phase_started",
        phase=phase,
        message_key=f"deep_research_phase_{phase.replace('-', '_')}",
        callback=callback,
    )


def _cancellation_requested(db, run: DeepResearchRun) -> bool:
    """Refresh the cancellation flag written by the shared chat endpoint."""

    try:
        db.expire(run, ["cancel_requested"])
    except Exception:
        pass
    return bool(run.cancel_requested)


def _checkpoint_phase(
    db,
    run: DeepResearchRun,
    workspace: Path,
    *,
    phase: str,
    relative_paths: list[str],
) -> None:
    """Persist a completed phase and upload its restart-critical files."""

    result_meta = _safe_dict(run.result_meta)
    checkpoints = dict(result_meta.get("checkpoints") or {})
    checkpoints[phase] = {
        "completed_at": utc_now().isoformat(),
        "files": list(dict.fromkeys(relative_paths)),
    }

    # Upload and verify the phase outputs before recording the checkpoint as
    # complete. Persisting the provider here also ensures interrupted runs stay
    # readable and migratable after the global storage provider changes.
    upload_result = upload_deep_research_artifacts(
        workspace_dir=workspace,
        user_id=run.user_id,
        session_id=run.id,
        relative_paths=relative_paths,
    )
    existing_storage = _safe_dict(result_meta.get("storage"))
    existing_provider = str(existing_storage.get("provider") or "").strip().lower()
    uploaded_provider = str(upload_result.get("provider") or "").strip().lower()
    if existing_provider and uploaded_provider and existing_provider != uploaded_provider:
        raise RuntimeError(
            "Deep Research checkpoint storage provider changed during the run."
        )

    uploaded_files = list(
        dict.fromkeys(
            [
                *(
                    existing_storage.get("uploaded_files")
                    if isinstance(existing_storage.get("uploaded_files"), list)
                    else []
                ),
                *(
                    upload_result.get("uploaded_files")
                    if isinstance(upload_result.get("uploaded_files"), list)
                    else []
                ),
            ]
        )
    )
    objects_by_path: dict[str, dict[str, Any]] = {}
    for item in [
        *(
            existing_storage.get("objects")
            if isinstance(existing_storage.get("objects"), list)
            else []
        ),
        *(
            upload_result.get("objects")
            if isinstance(upload_result.get("objects"), list)
            else []
        ),
    ]:
        if not isinstance(item, dict):
            continue
        relative_path = str(item.get("relative_path") or "").strip()
        if relative_path:
            objects_by_path[relative_path] = dict(item)

    storage_meta = {
        **existing_storage,
        **upload_result,
        "uploaded_files": uploaded_files,
        "objects": list(objects_by_path.values()),
    }
    run.result_meta = {
        **result_meta,
        "checkpoints": checkpoints,
        "storage": storage_meta,
    }
    run.updated_at = utc_now()
    db.add(run)
    db.commit()
    db.refresh(run)


def _checkpoint_text(
    run: DeepResearchRun,
    phase: str,
    relative_path: str,
) -> str | None:
    """Read a phase result only when its durable checkpoint is complete."""

    checkpoints = _safe_dict(run.result_meta).get("checkpoints") or {}
    checkpoint = checkpoints.get(phase) if isinstance(checkpoints, dict) else None
    files = checkpoint.get("files") if isinstance(checkpoint, dict) else []
    if relative_path not in (files or []):
        return None
    try:
        path = materialize_deep_research_artifact(
            run.user_id,
            run.id,
            relative_path,
        )
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, ValueError):
        logger.warning(
            "Deep Research checkpoint file is unavailable; phase will be retried",
            extra={"run_id": run.id, "phase": phase, "path": relative_path},
        )
        return None


def _record_usage(
    db,
    run: DeepResearchRun,
    *,
    phase: str,
    phase_result: PhaseResult,
) -> None:
    """Checkpoint provider usage and activity counters for one phase."""

    def reported_tokens(items: list[dict[str, Any]]) -> int:
        """Extract a conservative aggregate token count from provider events."""

        total = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            payload = item.get("d") if isinstance(item.get("d"), dict) else item
            explicit_total = payload.get("total_tokens")
            if isinstance(explicit_total, (int, float)):
                total += max(0, int(explicit_total))
                continue
            for key in (
                "input_tokens",
                "prompt_tokens",
                "output_tokens",
                "completion_tokens",
            ):
                value = payload.get(key)
                if isinstance(value, (int, float)):
                    total += max(0, int(value))
        return total

    phase_tokens = reported_tokens(phase_result.usage)
    tool_counts: dict[str, int] = {}
    for tool_call in phase_result.tool_calls:
        name = str(tool_call.get("name") or "unknown")
        tool_counts[name] = tool_counts.get(name, 0) + 1
    usage = _safe_dict(run.usage)
    usage[phase] = {
        "provider_events": phase_result.usage,
        "duration_seconds": max(0.0, float(phase_result.duration_seconds or 0)),
        "model_call_count": 1,
        "stream_event_count": phase_result.raw_event_count,
        "tool_call_count": len(phase_result.tool_calls),
        "tool_calls_by_name": tool_counts,
        "generated_file_count": len(phase_result.generated_files),
        "reported_tokens": phase_tokens,
    }
    run.usage = usage
    run.updated_at = utc_now()
    db.add(run)
    db.commit()
    db.refresh(run)


def _phase_event_bridge(
    db,
    run: DeepResearchRun,
    *,
    callback: PublicEventCallback | None,
) -> PublicEventCallback:
    """Translate one provider request stream into public sidebar events."""

    def bridge(event: dict[str, Any]) -> None:
        event_name = str(event.get("event") or "")
        if event_name in {
            "llm_request_started",
            "llm_request_completed",
            "llm_request_failed",
            "reasoning_delta",
            "reasoning_completed",
            "content_delta",
        }:
            payload = {
                key: event.get(key)
                for key in ("request_id", "delta", "duration_seconds", "replace")
                if event.get(key) is not None
            }
            _emit(
                db,
                run,
                event_type=event_name,
                phase=str(event.get("phase") or run.phase),
                payload=payload,
                callback=callback,
            )
        elif event_name == "tool_started":
            _emit(
                db,
                run,
                event_type="tool_started",
                phase=str(event.get("phase") or run.phase),
                message_key="deep_research_tool_started",
                payload={
                    "tool": event.get("name"),
                    "tool_call_id": event.get("id"),
                    "request_id": event.get("request_id"),
                    "arguments": event.get("arguments"),
                },
                callback=callback,
            )
        elif event_name in {"tool_completed", "tool_failed"}:
            succeeded = event_name == "tool_completed"
            _emit(
                db,
                run,
                event_type=event_name,
                phase=str(event.get("phase") or run.phase),
                message_key=(
                    "deep_research_tool_completed"
                    if succeeded
                    else "deep_research_tool_failed"
                ),
                payload={
                    "tool": event.get("name"),
                    "tool_call_id": event.get("id"),
                    "request_id": event.get("request_id"),
                    "success": succeeded,
                },
                callback=callback,
            )
        elif event_name == "artifact_created":
            # The file becomes a validated research artifact after the phase
            # returns and the normal user-file ownership check succeeds.
            _emit(
                db,
                run,
                event_type="artifact_detected",
                phase=str(event.get("phase") or run.phase),
                message_key="deep_research_artifact_detected",
                payload={"name": event.get("name")},
                callback=callback,
            )

    return bridge


def _run_phase(
    db,
    run: DeepResearchRun,
    *,
    phase: str,
    instructions: str,
    input_text: str,
    tools: list[str],
    project_id: str | None,
    user_role: str | None,
    callback: PublicEventCallback | None,
    structured_schema: type[Any] | None = None,
    structured_validator: Callable[[Any], None] | None = None,
    structured_repair_context: Callable[[Any | None, str], str] | None = None,
    model_id: str | None = None,
) -> PhaseResult:
    """Execute and checkpoint one provider-neutral model phase."""

    if _cancellation_requested(db, run):
        raise DeepResearchCancelled("Deep Research was cancelled.")
    _set_phase(db, run, phase, callback=callback)
    phase_call = dict(
        model_id=str(model_id or run.model_id),
        user_id=str(run.user_id),
        run_id=str(run.id),
        phase=phase,
        instructions=instructions,
        input_text=input_text,
        tools=tools,
        chat_id=run.chat_id,
        project_id=project_id,
        generation_id=run.generation_id,
        user_role=user_role,
        settings_override=_safe_dict(run.config_snapshot).get("model_settings_override")
        or {},
        event_callback=_phase_event_bridge(db, run, callback=callback),
        cancellation_check=lambda: _cancellation_requested(db, run),
    )
    result = _run_phase_call_with_transport_retries(
        db,
        run,
        phase=phase,
        phase_call=phase_call,
        callback=callback,
    )

    # Some provider endpoints can return a successful HTTP stream containing
    # only final metadata and no assistant output. No research phase can
    # produce a useful checkpoint from that, so retry once before presenting a
    # stable public error.
    if not result.text.strip():
        first_result = result
        _emit(
            db,
            run,
            event_type="phase_retry",
            phase=phase,
            message_key="deep_research_empty_response_retrying",
            payload={"reason": "empty_response", "attempt": 2},
            callback=callback,
        )
        result = _merge_phase_results(
            first_result,
            _run_phase_call_with_transport_retries(
                db,
                run,
                phase=phase,
                phase_call=phase_call,
                callback=callback,
            ),
        )
        if not result.text.strip():
            raise DeepResearchEmptyResponse(
                f"Deep Research phase '{phase}' returned no usable content twice."
            )

    if structured_schema is not None:

        def parse_and_validate(text: str) -> Any:
            """Parse one model response and run optional semantic validation."""

            parsed = parse_structured_output(text, structured_schema)
            if structured_validator is None:
                return parsed
            try:
                structured_validator(parsed)
            except ValueError as exc:
                raise DeepResearchStructuredOutputError(
                    structured_schema.__name__,
                    str(exc),
                    structured_value=parsed,
                ) from exc
            return parsed

        try:
            result.structured_output = parse_and_validate(result.text)
        except DeepResearchStructuredOutputError as validation_error:
            # Schema repairs are deliberately tool-free. A separate transport
            # retry budget protects each repair from a truncated provider
            # stream without repeating costly evidence searches.
            for repair_attempt in range(1, _STRUCTURED_REPAIR_MAX_ATTEMPTS + 1):
                _record_phase_diagnostic(
                    db,
                    run,
                    phase=phase,
                    error_code="structured_output_invalid",
                    attempt=repair_attempt,
                    response_chars=len(result.text),
                    validation_summary=validation_error.validation_summary,
                )
                focused_context = (
                    structured_repair_context(
                        validation_error.structured_value,
                        validation_error.validation_summary,
                    )
                    if structured_repair_context is not None
                    else ""
                )
                repair_instructions, repair_input = structured_output_repair_request(
                    schema_type=structured_schema,
                    original_input=input_text,
                    invalid_output=result.text,
                    validation_summary=validation_error.validation_summary,
                    repair_context=focused_context,
                )
                _emit(
                    db,
                    run,
                    event_type="phase_retry",
                    phase=phase,
                    message_key="deep_research_structured_output_retrying",
                    payload={
                        "reason": "invalid_structured_output",
                        "attempt": repair_attempt + 1,
                        "max_attempts": _STRUCTURED_REPAIR_MAX_ATTEMPTS + 1,
                        "schema": structured_schema.__name__,
                    },
                    callback=callback,
                )
                repair_call = {
                    **phase_call,
                    "instructions": repair_instructions,
                    "input_text": repair_input,
                    "tools": [],
                }
                repaired = _run_phase_call_with_transport_retries(
                    db,
                    run,
                    phase=phase,
                    phase_call=repair_call,
                    callback=callback,
                )
                result = _merge_phase_results(result, repaired)
                try:
                    result.structured_output = parse_and_validate(result.text)
                    break
                except DeepResearchStructuredOutputError as next_error:
                    validation_error = next_error
            else:
                raise validation_error

    _record_usage(db, run, phase=phase, phase_result=result)
    _persist_phase_evidence(
        db,
        run,
        phase=phase,
        sources=result.sources,
    )
    _emit(
        db,
        run,
        event_type="phase_progress",
        phase=phase,
        payload={"completed": True},
        callback=callback,
    )
    return result


def _persist_phase_artifacts(
    db,
    run: DeepResearchRun,
    *,
    phase: str,
    phase_result: PhaseResult,
    workspace: Path,
    callback: PublicEventCallback | None,
) -> None:
    """Localize files created by the shared Code Execution pipeline."""

    saved = persist_generated_files(
        db,
        run_id=run.id,
        user_id=run.user_id,
        phase=phase,
        generated_files=phase_result.generated_files,
        workspace_dir=workspace,
    )
    for artifact in saved:
        _emit(
            db,
            run,
            event_type="artifact_created",
            phase=phase,
            message_key="deep_research_artifact_saved",
            payload={
                "stable_id": artifact.stable_id,
                "name": artifact.original_filename,
                "kind": artifact.kind,
            },
            callback=callback,
        )


def _synchronize_visual_metadata(
    db,
    run: DeepResearchRun,
    report: str,
) -> str:
    """Synchronize Markdown alt/caption data with validated image artifacts.

    Code Execution emits an owned file event, while the model supplies the
    semantic alt text in its Markdown. Joining those two trusted boundaries here
    makes generated charts portable and reviewable without a second runner.
    """

    synchronized = str(report or "")
    changed = False
    artifacts = list_run_artifacts(db, run.id)
    for artifact in artifacts:
        if artifact.kind != "image" or artifact.validation_status != "validated":
            continue
        pattern = re.compile(
            rf"!\[(?P<alt>[^\]]+)\]\(\s*{re.escape(artifact.relative_path)}"
            r'(?:\s+"(?P<title>[^"]+)")?\s*\)'
        )
        match = pattern.search(synchronized)
        if match is None:
            continue
        alt_text = str(match.group("alt") or "").strip()
        title = str(match.group("title") or "").strip()
        caption = str(artifact.caption or title or alt_text).strip()
        if not artifact.alt_text and alt_text:
            artifact.alt_text = alt_text[:1_000]
            changed = True
        if not artifact.caption and caption:
            artifact.caption = caption[:2_000]
            changed = True

        # A Markdown alt label is not a visible caption. Add the persisted
        # caption directly below the image unless nearby report text already
        # presents it. No language-specific prefix is injected.
        following = synchronized[match.end() : match.end() + 500]
        if caption and caption.casefold() not in following.casefold():
            synchronized = (
                synchronized[: match.end()]
                + f"\n\n*{caption}*"
                + synchronized[match.end() :]
            )
    if changed:
        save_run_artifacts(db, run, artifacts)
    return synchronized


def _canonical_url(value: str) -> str | None:
    """Normalize a public HTTP(S) source URL for run-level deduplication."""

    raw = str(value or "").strip().rstrip(".,;:")
    if not raw:
        return None
    normalized, _fragment = urldefrag(raw)
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return normalized


def _extract_report_urls(report: str) -> list[str]:
    """Extract and deduplicate cited URLs from final Markdown."""

    ordered: list[str] = []
    seen: set[str] = set()
    candidates = [match.group("url") for match in _MARKDOWN_LINK_RE.finditer(report)]
    candidates.extend(match.group(0) for match in _BARE_URL_RE.finditer(report))
    for candidate in candidates:
        canonical = _canonical_url(candidate)
        if canonical and canonical not in seen:
            seen.add(canonical)
            ordered.append(canonical)
    return ordered


def _persist_phase_evidence(
    db,
    run: DeepResearchRun,
    *,
    phase: str,
    sources: list[dict[str, str]],
) -> None:
    """Persist the shared Websearch pipeline's source journal per phase."""

    now = utc_now()
    config = _safe_dict(run.config_snapshot)
    model_overrides = _safe_dict(config.get("model_settings_override"))
    provider = (
        str(model_overrides.get("websearch_search_provider") or "").strip()
        or str(run.provider_id or "").strip()
        or None
    )
    evidence = [dict(item) for item in run.evidence or [] if isinstance(item, dict)]
    evidence_by_url = {str(item.get("canonical_url") or ""): item for item in evidence}
    changed = False
    for source in sources:
        canonical = _canonical_url(source.get("url") or "")
        if canonical is None:
            continue
        title = str(source.get("title") or "").strip() or urlsplit(canonical).netloc
        excerpt = str(source.get("snippet") or "").strip()[:2_000] or None
        existing = evidence_by_url.get(canonical)
        if existing is not None:
            metadata = _safe_dict(existing.get("meta"))
            phases = list(metadata.get("phases") or [])
            if phase not in phases:
                phases.append(phase)
                metadata["phases"] = phases
                existing["meta"] = metadata
                existing["updated_at"] = now.isoformat()
                changed = True
            continue
        item = {
            "title": title,
            "canonical_url": canonical,
            "provider": provider,
            "source_type": "shared_web_search",
            "published_at": None,
            "author": None,
            "content_hash": hashlib.sha256(
                f"{canonical}\n{excerpt or ''}".encode("utf-8")
            ).hexdigest(),
            "excerpt": excerpt,
            "research_questions": [run.query],
            "meta": {
                "classification": "unclassified",
                "retrieved_at": now.isoformat(),
                "phases": [phase],
            },
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        evidence.append(item)
        evidence_by_url[canonical] = item
        changed = True
    if changed:
        run.evidence = evidence
        run.updated_at = now
        db.add(run)
        db.commit()


def _persist_evidence_index(db, run: DeepResearchRun, report: str) -> list[str]:
    """Persist a normalized source index derived from the publishable report."""

    urls = _extract_report_urls(report)
    now = utc_now()
    evidence = [dict(item) for item in run.evidence or [] if isinstance(item, dict)]
    existing_urls = {str(item.get("canonical_url") or "") for item in evidence}
    for url in urls:
        if url in existing_urls:
            continue
        host = urlsplit(url).netloc
        config = _safe_dict(run.config_snapshot)
        model_overrides = _safe_dict(config.get("model_settings_override"))
        evidence.append(
            {
                "title": host or url,
                "canonical_url": url,
                "provider": (
                    str(model_overrides.get("websearch_search_provider") or "").strip()
                    or None
                ),
                "source_type": "report_citation",
                "published_at": None,
                "author": None,
                "content_hash": hashlib.sha256(url.encode("utf-8")).hexdigest(),
                "excerpt": None,
                "research_questions": [],
                "meta": {
                    "classification": "unclassified",
                    "retrieved_at": now.isoformat(),
                },
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        )
        existing_urls.add(url)
    run.evidence = evidence
    run.updated_at = now
    db.add(run)
    db.commit()
    return urls


def _quality_gate_entry(
    review: QualityReview, phase: str, round_number: int
) -> dict[str, Any]:
    """Build a stable quality-gate summary without storing hidden reasoning."""

    severity_counts = {
        severity: sum(issue.severity == severity for issue in review.issues)
        for severity in ("critical", "major", "minor")
    }
    return {
        "phase": phase,
        "round": round_number,
        "ready_to_publish": review.ready_to_publish,
        "issue_count": len(review.issues),
        "severity_counts": severity_counts,
    }


def _accept_final_revision(
    review: QualityReview,
    *,
    round_number: int,
    max_revision_rounds: int,
) -> tuple[bool, bool]:
    """Apply the publication policy for the last configured revision.

    A review can request another revision while revision budget remains. The
    last configured revision is always the terminal report, however: reaching
    that boundary is a successful publication outcome rather than a separate
    terminal state.
    """

    accepted_after_final_round = (
        not review.ready_to_publish and round_number >= max_revision_rounds
    )
    return (
        review.ready_to_publish or accepted_after_final_round,
        accepted_after_final_round,
    )


def _enforce_release_invariants(
    review: QualityReview,
    *,
    report: str,
    brief: ResearchBrief,
    artifacts: list[dict[str, Any]],
) -> QualityReview:
    """Combine the independent model review with deterministic hard checks."""

    issues = list(review.issues)
    revision_instructions = list(review.revision_instructions)

    def add_issue(
        category: str,
        problem: str,
        required_fix: str,
    ) -> None:
        issues.append(
            ReviewIssue(
                severity="major",
                category=category,
                claim_or_section="Final report",
                problem=problem,
                required_fix=required_fix,
            )
        )
        revision_instructions.append(required_fix)

    if _REMOTE_IMAGE_RE.search(report):
        add_issue(
            "visualization",
            "The report embeds a remote image URL.",
            "Import the image through the secure web-image tool or remove it.",
        )
    if (
        "artifact://" in report
        or "sandbox:/mnt/data/" in report
        or "/tmp/output/" in report
    ):
        add_issue(
            "visualization",
            "The report contains an unresolved generated-artifact reference.",
            "Use a validated relative artifact path or remove the visual.",
        )
    if _RAW_HTML_RE.search(report):
        add_issue(
            "other",
            "The report contains an active or unsupported raw HTML block.",
            "Replace raw HTML with safe Markdown.",
        )
    if _EMPTY_LINK_RE.search(report):
        add_issue(
            "citation",
            "The report contains an empty Markdown link.",
            "Add a direct source URL or remove the empty citation.",
        )
    if not _extract_report_urls(report):
        add_issue(
            "citation",
            "The report contains no direct source links.",
            "Add claim-level direct links to the evidence used in the report.",
        )

    coverage_questions = {
        re.sub(r"\s+", " ", item.research_question).strip().casefold()
        for item in review.coverage
    }
    for research_question in brief.research_questions:
        normalized_question = (
            re.sub(
                r"\s+",
                " ",
                research_question.question,
            )
            .strip()
            .casefold()
        )
        if normalized_question not in coverage_questions:
            add_issue(
                "missing_context",
                f"The review omitted coverage for '{research_question.question}'.",
                "Audit every approved research question before publication.",
            )

    headings = {
        re.sub(r"\s+", " ", match.group(1)).strip().casefold()
        for match in re.finditer(r"^#{1,4}\s+(.+?)\s*$", report, re.MULTILINE)
    }
    for required in brief.required_sections:
        normalized = re.sub(r"\s+", " ", required).strip().casefold()
        if normalized and not any(
            normalized in heading or heading in normalized for heading in headings
        ):
            add_issue(
                "missing_context",
                f"The required section '{required}' is missing.",
                f"Add the required '{required}' section with supported content.",
            )

    known_paths = {
        str(item.get("relative_path") or "")
        for item in artifacts
        if item.get("validation_status") == "validated"
    }
    referenced_paths = {match.group(1) for match in _ARTIFACT_PATH_RE.finditer(report)}
    for missing_path in sorted(referenced_paths - known_paths):
        add_issue(
            "visualization",
            f"The report references missing artifact '{missing_path}'.",
            "Replace the reference with a validated artifact or remove it.",
        )
    artifacts_by_path = {
        str(item.get("relative_path") or ""): item for item in artifacts
    }
    for referenced_path in sorted(referenced_paths & known_paths):
        artifact = artifacts_by_path.get(referenced_path) or {}
        if artifact.get("kind") == "image" and (
            not str(artifact.get("alt_text") or "").strip()
            or not str(artifact.get("caption") or "").strip()
        ):
            add_issue(
                "visualization",
                f"The visual '{referenced_path}' lacks a caption or alt text.",
                "Add evidence-focused alt text and a caption before publication.",
            )

    has_severe_review_issue = any(
        issue.severity in {"critical", "major"} for issue in issues
    )
    if review.ready_to_publish and has_severe_review_issue:
        revision_instructions.append(
            "Resolve every critical and major issue before publication."
        )
    return review.model_copy(
        update={
            "ready_to_publish": bool(
                review.ready_to_publish and not has_severe_review_issue
            ),
            "issues": issues,
            "revision_instructions": list(dict.fromkeys(revision_instructions)),
        }
    )


def _record_quality_gate(
    db,
    run: DeepResearchRun,
    *,
    review: QualityReview,
    phase: str,
    round_number: int,
    accepted_for_publication: bool,
    accepted_after_final_round: bool,
    callback: PublicEventCallback | None,
) -> None:
    """Checkpoint one independent release audit and its public verdict."""

    entry = {
        **_quality_gate_entry(review, phase, round_number),
        # Preserve the independent review verdict while exposing the effective
        # publication decision used by the orchestrator.
        "review_ready_to_publish": review.ready_to_publish,
        "ready_to_publish": accepted_for_publication,
        "accepted_after_final_round": accepted_after_final_round,
    }
    previous = _safe_dict(run.quality_gate)
    audits = list(previous.get("audits") or [])
    audits.append(entry)
    run.quality_gate = {
        **entry,
        "max_revision_rounds": run.max_revision_rounds,
        "audits": audits,
    }
    run.updated_at = utc_now()
    db.add(run)
    db.commit()
    db.refresh(run)
    _emit(
        db,
        run,
        event_type="quality_gate_completed",
        phase=phase,
        message_key=(
            "deep_research_quality_gate_passed"
            if accepted_for_publication
            else "deep_research_quality_gate_revision_required"
        ),
        payload=entry,
        callback=callback,
    )


def _write_manifest(
    db,
    run: DeepResearchRun,
    workspace: Path,
    *,
    citations: list[str] | None = None,
    unresolved_artifacts: list[str] | None = None,
) -> Path:
    """Write the durable, secret-free run manifest used by export/import."""

    manifest = {
        "schema_version": 2,
        "run_id": run.id,
        "query": run.query,
        "execution_mode": run.execution_mode,
        "status": run.status,
        "phase": run.phase,
        "model": {
            "id": run.model_id,
            "name": run.model_name,
            "provider_id": run.provider_id,
        },
        "revision_round": run.revision_round,
        "max_revision_rounds": run.max_revision_rounds,
        "usage": run.usage or {},
        "quality_gate": run.quality_gate or {},
        "artifacts": artifact_manifest(db, run.id),
        "citations": citations or [],
        "unresolved_artifact_references": unresolved_artifacts or [],
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
    path = write_workspace_json(workspace, "manifest.json", manifest)
    run.manifest_path = "manifest.json"
    run.updated_at = utc_now()
    db.add(run)
    db.commit()
    db.refresh(run)
    return path


def run_custom_research(
    db,
    run: DeepResearchRun,
    *,
    project_id: str | None = None,
    user_role: str | None = None,
    callback: PublicEventCallback | None = None,
) -> dict[str, Any]:
    """Run the v2 plan → research → audit → revise → release workflow.

    All model calls use the ordinary Omlorix provider pipeline. Evidence discovery
    and page retrieval use the normal ``web_search`` tool; calculations and charts
    use the normal ``code_execution`` tool. This orchestrator adds only durable
    checkpoints, quality gates, artifact localization, and report publication.
    """

    if not run.model_id:
        raise RuntimeError("Deep Research model is not configured.")

    workspace = get_deep_research_workspace_dir(run.user_id, run.id)
    write_workspace_json(
        workspace,
        "request.json",
        {
            "query": run.query,
            "execution_mode": run.execution_mode,
        },
    )
    _emit(
        db,
        run,
        event_type="run_started",
        phase="starting",
        message_key="deep_research_run_started",
        callback=callback,
    )

    try:
        brief_json = _checkpoint_text(run, "planning", "research-brief.json")
        if brief_json is not None:
            brief = ResearchBrief.model_validate_json(brief_json)
        else:
            planning = _run_phase(
                db,
                run,
                phase="planning",
                instructions=PLANNER_INSTRUCTIONS,
                input_text=planner_input(run.query),
                tools=[],
                project_id=project_id,
                user_role=user_role,
                callback=callback,
                structured_schema=ResearchBrief,
            )
            brief = planning.structured_output
            write_workspace_json(workspace, "research-brief.json", brief)
            write_workspace_text(
                workspace,
                "research-instructions.md",
                brief.final_research_instruction,
            )
            _checkpoint_phase(
                db,
                run,
                workspace,
                phase="planning",
                relative_paths=[
                    "research-brief.json",
                    "research-instructions.md",
                ],
            )

        report = _checkpoint_text(
            run,
            "deep-research",
            "draft-report.md",
        )
        if report is None:
            research = _run_phase(
                db,
                run,
                phase="deep-research",
                instructions=RESEARCHER_INSTRUCTIONS,
                input_text=research_input(run.query, brief),
                tools=[
                    "web_search",
                    "code_execution",
                    "deep_research_import_web_image",
                ],
                project_id=project_id,
                user_role=user_role,
                callback=callback,
            )
            if not research.text.strip():
                raise RuntimeError("The Deep Research phase produced no report.")
            _persist_phase_artifacts(
                db,
                run,
                phase="deep-research",
                phase_result=research,
                workspace=workspace,
                callback=callback,
            )
            report = localize_artifact_references(
                research.text,
                list_run_artifacts(db, run.id),
            )
            report = _synchronize_visual_metadata(db, run, report)
            write_workspace_text(workspace, "draft-report.md", report)
            _checkpoint_phase(
                db,
                run,
                workspace,
                phase="deep-research",
                relative_paths=[
                    "draft-report.md",
                    *[
                        str(item.get("relative_path"))
                        for item in artifact_manifest(db, run.id)
                        if item.get("relative_path")
                    ],
                ],
            )

        audit_json = _checkpoint_text(
            run,
            "evidence-audit",
            "evidence-audit-review.json",
        )
        if audit_json is not None:
            review = QualityReview.model_validate_json(audit_json)
        else:
            audit = _run_phase(
                db,
                run,
                phase="evidence-audit",
                instructions=REVIEWER_INSTRUCTIONS,
                input_text=review_input(
                    run.query,
                    brief,
                    report,
                    artifact_manifest(db, run.id),
                ),
                tools=["web_search"],
                project_id=project_id,
                user_role=user_role,
                callback=callback,
                structured_schema=QualityReview,
            )
            review = audit.structured_output
            write_workspace_json(
                workspace,
                "evidence-audit-review.json",
                review,
            )
            _checkpoint_phase(
                db,
                run,
                workspace,
                phase="evidence-audit",
                relative_paths=["evidence-audit-review.json"],
            )

        max_revision_rounds = max(1, int(run.max_revision_rounds))
        completion_warning: dict[str, Any] | None = None
        for round_number in range(1, max_revision_rounds + 1):
            run.revision_round = round_number
            run.updated_at = utc_now()
            db.add(run)
            db.commit()
            db.refresh(run)

            revision_phase = f"final-revision-{round_number}"
            revision_path = f"{revision_phase}-report.md"
            revision_edits_path = f"{revision_phase}-edits.json"
            _emit(
                db,
                run,
                event_type="revision_started",
                phase=revision_phase,
                message_key=(f"deep_research_phase_{revision_phase.replace('-', '_')}"),
                payload={"round": round_number},
                callback=callback,
            )
            checkpoint_report = _checkpoint_text(
                run,
                revision_phase,
                revision_path,
            )
            if checkpoint_report is not None:
                report = checkpoint_report
            else:
                try:
                    revision = _run_phase(
                        db,
                        run,
                        phase=revision_phase,
                        instructions=FINALIZER_INSTRUCTIONS,
                        input_text=finalizer_input(
                            run.query,
                            brief,
                            report,
                            review,
                            artifact_manifest(db, run.id),
                        ),
                        tools=[
                            "web_search",
                            "code_execution",
                            "deep_research_import_web_image",
                        ],
                        project_id=project_id,
                        user_role=user_role,
                        callback=callback,
                        structured_schema=ArticleRevision,
                        structured_validator=lambda candidate: (
                            validate_article_revision(
                                report,
                                candidate,
                            )
                        ),
                        structured_repair_context=lambda candidate, error: (
                            article_revision_repair_context(
                                report,
                                candidate
                                if isinstance(candidate, ArticleRevision)
                                else None,
                                error,
                            )
                        ),
                    )
                except (
                    DeepResearchEmptyResponse,
                    DeepResearchIncompleteStream,
                    DeepResearchStructuredOutputError,
                    TimeoutError,
                ) as exc:
                    completion_warning = _record_degraded_report_completion(
                        db,
                        run,
                        phase=revision_phase,
                        exc=exc,
                        callback=callback,
                    )
                    break
                revision_plan = revision.structured_output
                if not isinstance(revision_plan, ArticleRevision):
                    raise DeepResearchStructuredOutputError("ArticleRevision")
                _persist_phase_artifacts(
                    db,
                    run,
                    phase=revision_phase,
                    phase_result=revision,
                    workspace=workspace,
                    callback=callback,
                )
                report = apply_article_revision(report, revision_plan)
                report = localize_artifact_references(
                    report,
                    list_run_artifacts(db, run.id),
                    append_unreferenced=False,
                )
                report = _synchronize_visual_metadata(db, run, report)
                write_workspace_json(workspace, revision_edits_path, revision_plan)
                write_workspace_text(workspace, revision_path, report)
                _checkpoint_phase(
                    db,
                    run,
                    workspace,
                    phase=revision_phase,
                    relative_paths=[
                        revision_edits_path,
                        revision_path,
                        *[
                            str(item.get("relative_path"))
                            for item in artifact_manifest(db, run.id)
                            if item.get("relative_path")
                        ],
                    ],
                )
                _emit(
                    db,
                    run,
                    event_type="report_updated",
                    phase=revision_phase,
                    payload={"report": report},
                    callback=callback,
                )

            release_phase = f"release-gate-{round_number}"
            release_path = f"{release_phase}-review.json"
            release_json = _checkpoint_text(run, release_phase, release_path)
            if release_json is not None:
                review = QualityReview.model_validate_json(release_json)
            else:
                try:
                    release = _run_phase(
                        db,
                        run,
                        phase=release_phase,
                        instructions=REVIEWER_INSTRUCTIONS,
                        input_text=review_input(
                            run.query,
                            brief,
                            report,
                            artifact_manifest(db, run.id),
                        ),
                        tools=["web_search"],
                        project_id=project_id,
                        user_role=user_role,
                        callback=callback,
                        structured_schema=QualityReview,
                    )
                except (
                    DeepResearchEmptyResponse,
                    DeepResearchIncompleteStream,
                    DeepResearchStructuredOutputError,
                    TimeoutError,
                ) as exc:
                    completion_warning = _record_degraded_report_completion(
                        db,
                        run,
                        phase=release_phase,
                        exc=exc,
                        callback=callback,
                    )
                    break
                review = release.structured_output
            review = _enforce_release_invariants(
                review,
                report=report,
                brief=brief,
                artifacts=artifact_manifest(db, run.id),
            )
            (
                accepted_for_publication,
                accepted_after_final_round,
            ) = _accept_final_revision(
                review,
                round_number=round_number,
                max_revision_rounds=max_revision_rounds,
            )
            if release_json is None:
                write_workspace_json(workspace, release_path, review)
                _record_quality_gate(
                    db,
                    run,
                    review=review,
                    phase=release_phase,
                    round_number=round_number,
                    accepted_for_publication=accepted_for_publication,
                    accepted_after_final_round=accepted_after_final_round,
                    callback=callback,
                )
                _checkpoint_phase(
                    db,
                    run,
                    workspace,
                    phase=release_phase,
                    relative_paths=[release_path],
                )
            if accepted_for_publication:
                break

        report = localize_artifact_references(
            report,
            list_run_artifacts(db, run.id),
            append_unreferenced=False,
        )
        report = _synchronize_visual_metadata(db, run, report)
        report, removed_remote_images = remove_remote_image_embeds(report)
        report, unresolved = clean_unresolved_artifact_references(report)
        if removed_remote_images:
            write_workspace_json(
                workspace,
                "removed-remote-images.json",
                removed_remote_images,
            )
        if unresolved:
            write_workspace_json(
                workspace,
                "unresolved-artifact-references.json",
                unresolved,
            )
        citations = _persist_evidence_index(db, run, report)
        markdown_path = write_workspace_text(workspace, "final-report.md", report)
        write_workspace_json(workspace, "citations.json", citations)
        write_workspace_json(workspace, "artifacts.json", artifact_manifest(db, run.id))

        run.status = RUN_STATUS_COMPLETED
        run.phase = "completed"
        run.final_report_path = markdown_path.relative_to(workspace).as_posix()
        run.final_html_path = None
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        run.result_meta = {
            **_safe_dict(run.result_meta),
            "title": brief.title,
            "output_language": brief.output_language,
            "citation_count": len(citations),
            "artifact_count": len(list_run_artifacts(db, run.id)),
            "completion_warning": completion_warning,
        }
        db.add(run)
        db.commit()
        db.refresh(run)
        _write_manifest(
            db,
            run,
            workspace,
            citations=citations,
            unresolved_artifacts=unresolved,
        )
        write_session_metadata(
            workspace,
            {
                "schema_version": 2,
                "run_id": run.id,
                "status": run.status,
                "phase": run.phase,
                "query": run.query,
                "title": brief.title,
                "report_path": run.final_report_path,
                "artifact_count": run.result_meta["artifact_count"],
                "citation_count": run.result_meta["citation_count"],
                "completion_warning": completion_warning,
            },
        )
        archive_path = create_workspace_archive(workspace)
        run.result_meta = {
            **_safe_dict(run.result_meta),
            "archive_path": archive_path.relative_to(workspace).as_posix(),
        }
        db.add(run)
        db.commit()
        db.refresh(run)
        upload_result = upload_deep_research_artifacts(
            workspace_dir=workspace,
            user_id=run.user_id,
            session_id=run.id,
        )
        run.result_meta = {**_safe_dict(run.result_meta), "storage": upload_result}
        db.add(run)
        db.commit()
        db.refresh(run)
        _emit(
            db,
            run,
            event_type="completed",
            phase="completed",
            message_key=(
                "deep_research_completed_with_warnings"
                if completion_warning
                else "deep_research_completed"
            ),
            payload={
                "report_path": run.final_report_path,
                "citation_count": len(citations),
                "artifact_count": run.result_meta.get("artifact_count", 0),
                "degraded": bool(completion_warning),
                "warning_code": (
                    completion_warning.get("warning_code")
                    if completion_warning
                    else None
                ),
            },
            callback=callback,
        )
        return {
            "run_id": run.id,
            "status": run.status,
            "report": report,
            "report_path": run.final_report_path,
            "workspace": workspace,
            "citations": citations,
            "artifacts": artifact_manifest(db, run.id),
            "completion_warning": completion_warning,
        }
    except DeepResearchCancelled:
        run.status = RUN_STATUS_CANCELLED
        run.phase = "cancelled"
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        db.add(run)
        db.commit()
        db.refresh(run)
        _write_manifest(db, run, workspace)
        archive_path = create_workspace_archive(workspace)
        upload_result = upload_deep_research_artifacts(
            workspace_dir=workspace,
            user_id=run.user_id,
            session_id=run.id,
        )
        run.result_meta = {
            **_safe_dict(run.result_meta),
            "archive_path": archive_path.relative_to(workspace).as_posix(),
            "storage": upload_result,
        }
        db.add(run)
        db.commit()
        db.refresh(run)
        _emit(
            db,
            run,
            event_type="cancelled",
            phase="cancelled",
            message_key="deep_research_cancelled",
            callback=callback,
        )
        raise
    except Exception as exc:
        logger.exception("Deep Research v2 execution failed", extra={"run_id": run.id})
        run.status = RUN_STATUS_FAILED
        run.phase = "failed"
        run.error_code = public_error_code(exc)
        run.error_message_key = "deep_research_failed"
        run.completed_at = datetime.now(timezone.utc)
        run.updated_at = utc_now()
        db.add(run)
        db.commit()
        db.refresh(run)
        _write_manifest(db, run, workspace)
        archive_path = create_workspace_archive(workspace)
        upload_result = upload_deep_research_artifacts(
            workspace_dir=workspace,
            user_id=run.user_id,
            session_id=run.id,
        )
        run.result_meta = {
            **_safe_dict(run.result_meta),
            "archive_path": archive_path.relative_to(workspace).as_posix(),
            "storage": upload_result,
        }
        db.add(run)
        db.commit()
        db.refresh(run)
        _emit(
            db,
            run,
            event_type="failed",
            phase="failed",
            message_key="deep_research_failed",
            payload={"error_code": run.error_code},
            callback=callback,
        )
        raise
