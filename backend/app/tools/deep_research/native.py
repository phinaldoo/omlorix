from __future__ import annotations

import logging
import time
from typing import Any, Callable
from urllib.parse import urlparse

from app.llm.models import LLMProvider
from app.tools.deep_research.models import (
    DeepResearchRun,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    utc_now,
)
from app.tools.deep_research.artifacts import (
    clean_unresolved_artifact_references,
    remove_remote_image_embeds,
)
from app.tools.deep_research.providers import (
    DeepResearchCancelled,
    public_error_code,
)
from app.tools.deep_research.storage import (
    create_workspace_archive,
    get_deep_research_workspace_dir,
    upload_deep_research_artifacts,
    write_session_metadata,
    write_workspace_json,
    write_workspace_text,
)


logger = logging.getLogger(__name__)

NativeEventCallback = Callable[[dict[str, Any]], None]
_TERMINAL_STATUSES = {
    "completed",
    "succeeded",
    "failed",
    "cancelled",
    "canceled",
    "error",
    "incomplete",
}
_DEFAULT_NATIVE_TIMEOUT_SECONDS = 60 * 60
_MAX_NATIVE_TIMEOUT_SECONDS = 24 * 60 * 60


def _native_timeout_seconds(config: dict[str, Any]) -> int:
    """Return a bounded wall-clock limit for one native interaction."""

    try:
        configured = int(
            config.get("native_timeout_seconds")
            or _DEFAULT_NATIVE_TIMEOUT_SECONDS
        )
    except (TypeError, ValueError):
        configured = _DEFAULT_NATIVE_TIMEOUT_SECONDS
    return max(1, min(configured, _MAX_NATIVE_TIMEOUT_SECONDS))


def _safe_dict(value: Any) -> dict[str, Any]:
    """Convert SDK objects to their public dictionary representation."""

    if isinstance(value, dict):
        return value
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                result = method(exclude_none=True)
            except TypeError:
                result = method()
            if isinstance(result, dict):
                return result
    return {}


def _safe_list(value: Any) -> list[Any]:
    """Normalize SDK tuple/list collections."""

    return list(value) if isinstance(value, (list, tuple)) else []


def _emit(
    db,
    run: DeepResearchRun,
    *,
    event_type: str,
    phase: str,
    message_key: str,
    payload: dict[str, Any] | None = None,
    callback: NativeEventCallback | None = None,
) -> None:
    """Send one native-adapter event directly to the chat generation."""

    if callback is not None:
        sequence = int(getattr(run, "_stream_event_sequence", 0) or 0) + 1
        setattr(run, "_stream_event_sequence", sequence)
        callback(
            {
                "run_id": run.id,
                "sequence": sequence,
                "event_type": event_type,
                "phase": phase,
                "message_key": message_key,
                "payload": payload or {},
                "created_at": utc_now().isoformat(),
            }
        )


def _create_interaction(client: Any, model_name: str, query: str):
    """Start a Google native Deep Research interaction across SDK variants."""

    interactions = getattr(client, "interactions", None)
    create = getattr(interactions, "create", None)
    if not callable(create):
        raise RuntimeError(
            "Google AI Studio client does not expose the Interactions API."
        )
    variants = [
        {
            "input": query,
            "agent": model_name,
            "background": True,
            "agent_config": {
                "type": "deep-research",
                "thinking_summaries": "auto",
            },
        },
        {"input": query, "agent": model_name, "background": True},
    ]
    last_error: Exception | None = None
    for arguments in variants:
        try:
            return create(**arguments)
        except TypeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to create the native Deep Research interaction.")


def _response_text(response: Any) -> str:
    """Extract report text from current and earlier Google SDK response shapes."""

    for field in ("output_text", "text"):
        value = getattr(response, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    chunks: list[str] = []
    for candidate in _safe_list(getattr(response, "candidates", None)):
        content = getattr(candidate, "content", None)
        for part in _safe_list(getattr(content, "parts", None)):
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    payload = _safe_dict(response)
    if not chunks:
        for field in ("output_text", "text"):
            value = payload.get(field)
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip())
                break
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _visible_thinking_text(response: Any) -> str:
    """Extract only provider-published thought summaries from an interaction.

    The Interactions API deliberately distinguishes visible ``thought`` summary
    steps from hidden model reasoning. Signatures and every non-text summary
    block are ignored so the public stream cannot expose private reasoning.
    """

    payload = _safe_dict(response)
    steps = _safe_list(getattr(response, "steps", None))
    if not steps:
        steps = _safe_list(payload.get("steps"))
    summaries: list[str] = []
    for step in steps:
        step_payload = _safe_dict(step) if not isinstance(step, dict) else step
        if str(step_payload.get("type") or "").strip().lower() != "thought":
            continue
        for block in _safe_list(step_payload.get("summary")):
            block_payload = _safe_dict(block) if not isinstance(block, dict) else block
            if str(block_payload.get("type") or "text").strip().lower() != "text":
                continue
            text = str(block_payload.get("text") or "").strip()
            if text:
                summaries.append(text)
    return "\n\n".join(summaries)


def _snapshot_delta(previous: str, current: str) -> tuple[str, bool]:
    """Return an append delta, or a replacement when a snapshot was rewritten."""

    if not current or current == previous:
        return "", False
    if current.startswith(previous):
        return current[len(previous) :], False
    return current, True


def _cancel_interaction(interactions: Any, interaction_id: str) -> None:
    """Best-effort provider cancellation across current SDK signatures."""

    cancel = getattr(interactions, "cancel", None)
    if not interaction_id or not callable(cancel):
        return
    try:
        cancel(interaction_id)
    except TypeError:
        try:
            cancel(id=interaction_id)
        except Exception:
            logger.warning(
                "Native Deep Research provider cancellation failed",
                exc_info=True,
            )
    except Exception:
        logger.warning(
            "Native Deep Research provider cancellation failed",
            exc_info=True,
        )


def _citations(response: Any) -> list[dict[str, str]]:
    """Extract deduplicated HTTP(S) citations without trusting SDK field names."""

    result: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(node: Any, title: str = "") -> None:
        if not isinstance(node, (dict, list, tuple)):
            node = _safe_dict(node)
        if isinstance(node, dict):
            current_title = str(
                node.get("title") or node.get("name") or title or ""
            ).strip()
            for key in ("url", "uri", "source_url", "sourceUrl", "link"):
                value = node.get(key)
                if not isinstance(value, str):
                    continue
                url = value.strip()
                if not url.startswith(("http://", "https://")) or url in seen:
                    continue
                seen.add(url)
                result.append(
                    {
                        "url": url,
                        "title": current_title or urlparse(url).netloc or url,
                    }
                )
            for value in node.values():
                walk(value, current_title)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value, title)

    walk(response)
    return result


def _persist_terminal_workspace(
    db,
    run: DeepResearchRun,
    *,
    workspace,
    citations: list[dict[str, str]] | None = None,
) -> None:
    """Persist a restart-safe native manifest and archive for every outcome."""

    normalized_citations = list(citations or [])
    manifest_path = write_workspace_json(
        workspace,
        "manifest.json",
        {
            "schema_version": 2,
            "run_id": run.id,
            "query": run.query,
            "execution_mode": "native",
            "status": run.status,
            "phase": run.phase,
            "model": {
                "name": run.model_name,
                "provider_id": run.provider_id,
            },
            "usage": run.usage or {},
            "citations": normalized_citations,
            "error_code": run.error_code,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": (
                run.completed_at.isoformat() if run.completed_at else None
            ),
        },
    )
    run.manifest_path = manifest_path.relative_to(workspace).as_posix()
    write_session_metadata(
        workspace,
        {
            "schema_version": 2,
            "run_id": run.id,
            "status": run.status,
            "phase": run.phase,
            "query": run.query,
            "report_path": run.final_report_path,
            "citation_count": len(normalized_citations),
            "error_code": run.error_code,
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
    run.result_meta = {
        **_safe_dict(run.result_meta),
        "storage": upload_result,
    }
    db.add(run)
    db.commit()
    db.refresh(run)


def run_native_research(
    db,
    run: DeepResearchRun,
    *,
    callback: NativeEventCallback | None = None,
) -> dict[str, Any]:
    """Run Google native Deep Research behind the shared v2 run contract."""

    config = run.config_snapshot if isinstance(run.config_snapshot, dict) else {}
    provider_id = str(run.provider_id or "").strip()
    provider = (
        db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
        if provider_id
        else None
    )
    if provider is None or str(provider.provider).strip().lower() != "google_aistudio":
        raise RuntimeError("The configured native Deep Research provider is unavailable.")
    model_name = str(run.model_name or config.get("native_model_name") or "").strip()
    if not model_name:
        raise RuntimeError("The native Deep Research model name is not configured.")

    from app.llm.google_aistudio.utils import get_aistudio_client

    workspace = get_deep_research_workspace_dir(run.user_id, run.id)
    run.status = RUN_STATUS_RUNNING
    run.phase = "native-research"
    run.started_at = run.started_at or utc_now()
    run.updated_at = utc_now()
    db.add(run)
    db.commit()
    db.refresh(run)
    _emit(
        db,
        run,
        event_type="phase_started",
        phase=run.phase,
        message_key="deep_research_phase_native_research",
        callback=callback,
    )

    request_id = f"deep-research:{run.id}:native-research"
    request_started_at = time.monotonic()
    try:
        client = get_aistudio_client(db, aistudio_provider_id=provider.id)
        _emit(
            db,
            run,
            event_type="llm_request_started",
            phase=run.phase,
            message_key="deep_research_phase_native_research",
            payload={"request_id": request_id},
            callback=callback,
        )
        streamed_reasoning = ""
        streamed_content = ""

        def publish_response_stream(snapshot: Any) -> None:
            """Forward every newly visible native interaction snapshot."""

            nonlocal streamed_reasoning, streamed_content
            visible_reasoning = _visible_thinking_text(snapshot)
            reasoning_delta, replace_reasoning = _snapshot_delta(
                streamed_reasoning,
                visible_reasoning,
            )
            if reasoning_delta:
                _emit(
                    db,
                    run,
                    event_type="reasoning_delta",
                    phase=run.phase,
                    message_key="deep_research_phase_native_research",
                    payload={
                        "request_id": request_id,
                        "delta": reasoning_delta,
                        "replace": replace_reasoning,
                    },
                    callback=callback,
                )
                streamed_reasoning = visible_reasoning

            visible_content = _response_text(snapshot)
            content_delta, replace_content = _snapshot_delta(
                streamed_content,
                visible_content,
            )
            if content_delta:
                _emit(
                    db,
                    run,
                    event_type="content_delta",
                    phase=run.phase,
                    message_key="deep_research_phase_native_research",
                    payload={
                        "request_id": request_id,
                        "delta": content_delta,
                        "replace": replace_content,
                    },
                    callback=callback,
                )
                streamed_content = visible_content

        response = _create_interaction(client, model_name, run.query)
        publish_response_stream(response)
        interactions = getattr(client, "interactions", None)
        get_interaction = getattr(interactions, "get", None)
        response_payload = _safe_dict(response)
        interaction_id = str(
            getattr(response, "id", None) or response_payload.get("id") or ""
        ).strip()
        interaction_status = str(
            getattr(response, "status", None)
            or response_payload.get("status")
            or "in_progress"
        ).strip().lower()
        poll_seconds = max(
            1,
            min(int(config.get("native_poll_seconds") or 3), 30),
        )
        timeout_seconds = _native_timeout_seconds(config)
        interaction_deadline = request_started_at + timeout_seconds
        while interaction_status not in _TERMINAL_STATUSES:
            db.expire(run, ["cancel_requested"])
            if run.cancel_requested:
                _cancel_interaction(interactions, interaction_id)
                raise DeepResearchCancelled("Deep Research was cancelled.")
            if not interaction_id or not callable(get_interaction):
                raise RuntimeError("The native Deep Research interaction cannot be polled.")
            remaining_seconds = interaction_deadline - time.monotonic()
            if remaining_seconds <= 0:
                _cancel_interaction(interactions, interaction_id)
                raise TimeoutError(
                    "Native Deep Research exceeded its "
                    f"{timeout_seconds}-second runtime limit."
                )
            # Do not sleep past the deadline; the next loop iteration performs
            # the normal cancellation refresh before deciding the outcome.
            time.sleep(min(poll_seconds, remaining_seconds))
            response = get_interaction(interaction_id)
            publish_response_stream(response)
            response_payload = _safe_dict(response)
            interaction_status = str(
                getattr(response, "status", None)
                or response_payload.get("status")
                or interaction_status
            ).strip().lower()

        if interaction_status in {
            "failed",
            "cancelled",
            "canceled",
            "error",
            "incomplete",
        }:
            raise RuntimeError(
                str(
                    response_payload.get("error")
                    or getattr(response, "error", None)
                    or f"Native research ended with status {interaction_status}."
                )
            )

        report = _response_text(response)
        if not report:
            raise RuntimeError("Native Deep Research produced no report text.")
        # A terminal response can add its final output synchronously without a
        # preceding in-progress poll, so perform one final idempotent snapshot.
        publish_response_stream(response)
        report, removed_remote_images = remove_remote_image_embeds(report)
        report, unresolved_artifacts = clean_unresolved_artifact_references(report)
        if streamed_reasoning:
            _emit(
                db,
                run,
                event_type="reasoning_completed",
                phase=run.phase,
                message_key="deep_research_phase_native_research",
                payload={"request_id": request_id},
                callback=callback,
            )
        _emit(
            db,
            run,
            event_type="llm_request_completed",
            phase=run.phase,
            message_key="deep_research_phase_native_research",
            payload={
                "request_id": request_id,
                "duration_seconds": round(time.monotonic() - request_started_at, 3),
            },
            callback=callback,
        )
        citations = _citations(response)
        evidence = [
            dict(item) for item in run.evidence or [] if isinstance(item, dict)
        ]
        known_urls = {
            str(item.get("canonical_url") or "") for item in evidence
        }
        for citation in citations:
            url = citation["url"]
            if url not in known_urls:
                now = utc_now().isoformat()
                evidence.append(
                    {
                        "title": citation["title"],
                        "canonical_url": url,
                        "provider": "google_aistudio",
                        "source_type": "native_grounding",
                        "published_at": None,
                        "author": None,
                        "content_hash": None,
                        "excerpt": None,
                        "research_questions": [],
                        "meta": {},
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                known_urls.add(url)
        run.evidence = evidence
        run.updated_at = utc_now()
        db.add(run)
        db.commit()

        markdown_path = write_workspace_text(workspace, "final-report.md", report)
        write_workspace_json(workspace, "citations.json", citations)
        if removed_remote_images:
            write_workspace_json(
                workspace,
                "removed-remote-images.json",
                removed_remote_images,
            )
        if unresolved_artifacts:
            write_workspace_json(
                workspace,
                "unresolved-artifact-references.json",
                unresolved_artifacts,
            )
        run.status = RUN_STATUS_COMPLETED
        run.phase = "completed"
        run.final_report_path = markdown_path.relative_to(workspace).as_posix()
        run.final_html_path = None
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        run.result_meta = {
            **(_safe_dict(run.result_meta)),
            "title": run.query[:300],
            "citation_count": len(citations),
            "artifact_count": 0,
            "native_interaction_id": interaction_id,
        }
        db.add(run)
        db.commit()
        db.refresh(run)
        _persist_terminal_workspace(
            db,
            run,
            workspace=workspace,
            citations=citations,
        )
        _emit(
            db,
            run,
            event_type="completed",
            phase="completed",
            message_key="deep_research_completed",
            payload={
                "report_path": run.final_report_path,
                "citation_count": len(citations),
                "artifact_count": 0,
            },
            callback=callback,
        )
        return {
            "run_id": run.id,
            "status": run.status,
            "report": report,
            "report_path": run.final_report_path,
            "workspace": workspace,
            "citations": [citation["url"] for citation in citations],
            "artifacts": [],
        }
    except DeepResearchCancelled:
        _emit(
            db,
            run,
            event_type="llm_request_failed",
            phase=run.phase,
            message_key="deep_research_cancelled",
            payload={"request_id": request_id},
            callback=callback,
        )
        run.status = RUN_STATUS_CANCELLED
        run.phase = "cancelled"
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        db.add(run)
        db.commit()
        db.refresh(run)
        _persist_terminal_workspace(db, run, workspace=workspace)
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
        logger.exception("Native Deep Research adapter failed", extra={"run_id": run.id})
        _emit(
            db,
            run,
            event_type="llm_request_failed",
            phase=run.phase,
            message_key="deep_research_failed",
            payload={"request_id": request_id},
            callback=callback,
        )
        run.status = RUN_STATUS_FAILED
        run.phase = "failed"
        run.error_code = public_error_code(exc)
        run.error_message_key = "deep_research_failed"
        run.completed_at = utc_now()
        run.updated_at = utc_now()
        db.add(run)
        db.commit()
        db.refresh(run)
        _persist_terminal_workspace(db, run, workspace=workspace)
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
