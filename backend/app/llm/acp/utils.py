"""Backward-compatible public API for the ACP provider.

Focused chat orchestration lives in ``chat.py``. Imports remain here as
intentional compatibility and monkeypatch seams.
"""

# ruff: noqa: F401, E402

from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path, PurePosixPath
import queue
import threading
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified

from app.chats.models import Chats, create_chat_message
from app.chats.streaming import cancel_registry
from app.files.utils import extract_text_from_file_info, get_file_info
from app.llm.acp.runtime import (
    build_process_environment,
    extract_acp_session_controls,
    run_acp_turn,
    serialize_acp_session_controls,
)
from app.llm.acp.schemas import AcpModelSettings
from app.llm.helper import merge_settings
from app.llm.models import get_llm_provider
from app.llmstats.models import create_llm_generation_statistic
from app.remote_connections.models import SshConnection, UserAcpProfile
from app.remote_connections.policy import require_custom_acp_connections
from app.users.roles import is_admin_role


logger = logging.getLogger(__name__)

ACP_ATTACHMENT_FIELDS = ("images", "documents", "audios", "videos")
ACP_MAX_ATTACHMENTS = 20
ACP_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
ACP_MAX_TOTAL_ATTACHMENT_BYTES = 40 * 1024 * 1024
ACP_MAX_EXTRACTED_TEXT_CHARS = 200_000


def _sensitive_text_summary(value: str) -> dict[str, Any]:
    """Return reproducible diagnostics for text without logging the text itself."""
    text = str(value or "")
    encoded = text.encode("utf-8")
    return {
        "characters": len(text),
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _log_acp_chat_step(
    generation_id: str | None,
    chat_id: str | None,
    step: str,
    event: str,
    **details: Any,
) -> None:
    """Write a correlated, content-safe record for the ACP chat adapter."""
    payload = {
        "component": "acp_chat",
        "generation_id": str(generation_id or ""),
        "chat_id": str(chat_id or ""),
        "step": step,
        "event": event,
        **details,
    }
    logger.info("[ACP] %s", json.dumps(payload, sort_keys=True, default=str))


def _resolve_remote_path(root_value: str, relative_value: str) -> str:
    """Resolve a POSIX remote path without allowing traversal above its root."""
    root = PurePosixPath(str(root_value or ""))
    if not root.is_absolute():
        raise HTTPException(
            status_code=422, detail="Remote ACP workspace root must be absolute"
        )
    relative = PurePosixPath(str(relative_value or "."))
    if relative.is_absolute():
        raise HTTPException(status_code=422, detail="Remote ACP paths must be relative")
    parts: list[str] = []
    for part in relative.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise HTTPException(
                    status_code=422,
                    detail="Remote ACP path escapes the configured workspace",
                )
            parts.pop()
            continue
        parts.append(part)
    return str(root.joinpath(*parts))


def _message_role(message: Any) -> str:
    """Read a chat role from either an ORM row or a normalized mapping."""
    return str(
        message.get("role")
        if isinstance(message, dict)
        else getattr(message, "role", "") or ""
    )


def _message_content(message: Any) -> Any:
    """Decode persisted message content into a provider-neutral Python value."""
    content = (
        message.get("content")
        if isinstance(message, dict)
        else getattr(message, "content", "")
    )
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (TypeError, ValueError):
            return content
    return content


def _content_text(content: Any) -> str:
    """Extract model-visible text from Omlorix content blocks."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content or "").strip()

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            if block.strip():
                parts.append(block.strip())
            continue
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        block_content = block.get("content")
        if (
            block_type in {"user", "content", "reasoning", "tool_call_result"}
            and block_content
        ):
            parts.append(str(block_content).strip())
        elif block_type == "tool_call":
            name = (
                block.get("meta", {}).get("tool_name")
                if isinstance(block.get("meta"), dict)
                else None
            )
            arguments = (
                block.get("meta", {}).get("arguments")
                if isinstance(block.get("meta"), dict)
                else None
            )
            parts.append(f"Tool call: {name or 'tool'}({arguments or '{}'})")
    return "\n\n".join(part for part in parts if part)


def _attachment_id(value: Any) -> str:
    """Normalize the attachment representations accepted by Omlorix messages."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("id", "file_id", "fileId"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
    return ""


def _content_attachment_ids(content: Any) -> list[tuple[str, str]]:
    """Collect ordered ``(file_id, category)`` pairs from message blocks."""
    blocks = content if isinstance(content, list) else [content]
    attachments: list[tuple[str, str]] = []
    field_categories = {
        "images": "image",
        "documents": "document",
        "audios": "audio",
        "videos": "video",
    }
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for field in ACP_ATTACHMENT_FIELDS:
            values = block.get(field)
            if not isinstance(values, list):
                continue
            for value in values:
                file_id = _attachment_id(value)
                if file_id:
                    attachments.append((file_id, field_categories[field]))
    return attachments


def _history_attachment_ids(
    chat_history: list[Any],
    *,
    replay: bool,
) -> list[tuple[str, str]]:
    """Select latest-turn files or all user files needed for session replay."""
    user_messages = [
        message for message in chat_history or [] if _message_role(message) == "user"
    ]
    if not replay and user_messages:
        user_messages = user_messages[-1:]

    ordered: dict[str, tuple[str, str]] = {}
    for message in user_messages:
        for file_id, category in _content_attachment_ids(_message_content(message)):
            # Moving a repeated file to the end ensures a file reattached on
            # the newest turn cannot be displaced by the replay limit.
            ordered.pop(file_id, None)
            ordered[file_id] = (file_id, category)

    selected = list(ordered.values())[-ACP_MAX_ATTACHMENTS:]
    # Replayed sessions prioritize the newest binaries when enforcing the
    # aggregate byte budget. ACP resources do not require transcript order.
    return list(reversed(selected)) if replay else selected


def _safe_file_size(value: Any, path: Path) -> int | None:
    """Return a non-negative attachment size without trusting malformed metadata."""
    try:
        size = int(value)
        if size >= 0:
            return size
    except (TypeError, ValueError):
        pass
    try:
        return path.stat().st_size
    except OSError:
        return None


def _prepare_acp_attachments(
    chat_history: list[Any],
    user_id: str,
    *,
    replay: bool,
) -> list[dict[str, Any]]:
    """Resolve accessible Omlorix files into bounded ACP attachment payloads.

    Resolution is performed with the requesting user's identity, so another
    user's private file ID cannot be smuggled into an ACP prompt. Only the
    resulting bytes/text and safe display metadata leave Omlorix's file layer;
    storage keys and local filesystem paths are never included in ACP blocks.
    """
    prepared: list[dict[str, Any]] = []
    total_binary_bytes = 0

    for file_id, hinted_category in _history_attachment_ids(
        chat_history, replay=replay
    ):
        try:
            file_info = get_file_info(str(user_id or ""), file_id)
        except Exception:
            logger.exception(
                "Failed to resolve an ACP attachment",
                extra={"event": "acp_attachment_resolution_failed", "user_id": user_id},
            )
            file_info = None

        if not isinstance(file_info, dict):
            prepared.append(
                {
                    "file_id": file_id,
                    "name": "attachment",
                    "category": hinted_category,
                    "mime_type": "application/octet-stream",
                    "omission_reason": "The attachment is unavailable or no longer accessible.",
                }
            )
            continue

        meta = file_info.get("meta") if isinstance(file_info.get("meta"), dict) else {}
        path = Path(str(file_info.get("path") or ""))
        mime_type = str(
            file_info.get("file_type") or "application/octet-stream"
        ).strip()
        category = (
            str(file_info.get("file_category") or hinted_category).strip().lower()
        )
        category = (
            category[:-1]
            if category in {"images", "documents", "audios", "videos"}
            else category
        )
        name = str(
            meta.get("original_filename")
            or meta.get("original_name")
            or file_info.get("file_name")
            or "attachment"
        ).strip()
        size = _safe_file_size(file_info.get("file_size"), path)
        attachment: dict[str, Any] = {
            "file_id": file_id,
            "name": name or "attachment",
            "category": category or hinted_category,
            "mime_type": mime_type or "application/octet-stream",
            "size": size,
        }

        # Documents get a broadly compatible text representation. Avoid
        # attempting lossy UTF-8 decoding of image, audio, or video payloads.
        if attachment["category"] not in {"image", "audio", "video"}:
            try:
                extracted_text = extract_text_from_file_info(file_info)
            except Exception:
                logger.exception(
                    "Failed to extract text for an ACP attachment",
                    extra={
                        "event": "acp_attachment_text_extraction_failed",
                        "user_id": user_id,
                    },
                )
                extracted_text = None
            if extracted_text:
                if len(extracted_text) > ACP_MAX_EXTRACTED_TEXT_CHARS:
                    extracted_text = (
                        extracted_text[:ACP_MAX_EXTRACTED_TEXT_CHARS]
                        + "\n\n[Attachment text truncated by Omlorix]"
                    )
                attachment["text"] = extracted_text

        can_embed_binary = (
            path.is_file()
            and size is not None
            and size <= ACP_MAX_ATTACHMENT_BYTES
            and total_binary_bytes + size <= ACP_MAX_TOTAL_ATTACHMENT_BYTES
        )
        if can_embed_binary:
            try:
                binary = path.read_bytes()
                # Recheck the actual byte count because persisted size metadata
                # may be stale after an external storage migration.
                if len(binary) <= ACP_MAX_ATTACHMENT_BYTES and (
                    total_binary_bytes + len(binary) <= ACP_MAX_TOTAL_ATTACHMENT_BYTES
                ):
                    attachment["data"] = base64.b64encode(binary).decode("ascii")
                    total_binary_bytes += len(binary)
                else:
                    attachment["omission_reason"] = (
                        "The binary attachment exceeds the ACP transfer limit."
                    )
            except OSError:
                attachment["omission_reason"] = (
                    "The attachment bytes could not be read."
                )
        elif not attachment.get("text"):
            attachment["omission_reason"] = (
                "The binary attachment exceeds the ACP transfer limit."
                if size is not None and size > ACP_MAX_ATTACHMENT_BYTES
                else "The binary attachment could not be embedded."
            )

        prepared.append(attachment)

    return prepared


def _latest_user_prompt(chat_history: list[Any]) -> str:
    """Return the newest user-authored text in the normalized transcript."""
    for message in reversed(chat_history or []):
        if _message_role(message) == "user":
            text = _content_text(_message_content(message))
            if text:
                return text
    return ""


def _transcript_prompt(chat_history: list[Any]) -> str:
    """Replay Omlorix history when the ACP agent cannot resume its saved session."""
    lines: list[str] = []
    for message in chat_history or []:
        role = _message_role(message)
        if role not in {"user", "assistant", "system"}:
            continue
        text = _content_text(_message_content(message))
        if text:
            lines.append(f"{role.title()}:\n{text}")
    return "\n\n".join(lines)


def _compose_prompt(
    chat_history: list[Any],
    system_instruction_sections: list[dict[str, str]] | None,
    *,
    replay: bool,
    has_attachments: bool = False,
) -> str:
    """Compose one ACP text block while preserving Omlorix instruction layering."""
    sections: list[str] = []
    for section in system_instruction_sections or []:
        if not isinstance(section, dict):
            continue
        content = str(section.get("content") or "").strip()
        if content:
            label = str(
                section.get("title") or section.get("name") or "Instructions"
            ).strip()
            sections.append(f"[{label}]\n{content}")

    user_text = (
        _transcript_prompt(chat_history)
        if replay
        else _latest_user_prompt(chat_history)
    )
    if not user_text and has_attachments:
        user_text = "Please work with the attached file or files."
    if not user_text:
        raise HTTPException(status_code=400, detail="ACP prompt is empty")
    if sections:
        return (
            "Follow these Omlorix instructions for this turn:\n\n"
            + "\n\n".join(sections)
            + "\n\n"
            + user_text
        )
    return user_text


def _effective_acp_system_instruction(
    model_settings: dict | None,
    settings_override: dict | None,
) -> str:
    """Resolve the request-level system instruction over the ACP model default."""
    settings, _ = merge_settings(
        model_settings,
        settings_override,
        getattr(AcpModelSettings, "model_fields", None),
    )
    return str(settings.get("system_instruction") or "").strip()


def _get_saved_session(
    chat: Chats | None, model_id: str, provider_id: str, cwd: str
) -> str | None:
    """Read a compatible ACP session ID from durable chat metadata."""
    meta = chat.meta if chat and isinstance(chat.meta, dict) else {}
    sessions = (
        meta.get("acp_sessions") if isinstance(meta.get("acp_sessions"), dict) else {}
    )
    entry = (
        sessions.get(str(model_id))
        if isinstance(sessions.get(str(model_id)), dict)
        else {}
    )
    if entry.get("provider_id") != provider_id or entry.get("cwd") != cwd:
        return None
    session_id = str(entry.get("session_id") or "").strip()
    return session_id or None


def _get_saved_control(
    chat: Chats | None,
    model_id: str,
    provider_id: str,
    cwd: str,
    key: str,
) -> str | None:
    """Read one selected control value from compatible ACP session metadata."""
    meta = chat.meta if chat and isinstance(chat.meta, dict) else {}
    sessions = (
        meta.get("acp_sessions") if isinstance(meta.get("acp_sessions"), dict) else {}
    )
    entry = (
        sessions.get(str(model_id))
        if isinstance(sessions.get(str(model_id)), dict)
        else {}
    )
    if entry.get("provider_id") != provider_id or entry.get("cwd") != cwd:
        return None
    selected_value = str(entry.get(key) or "").strip()
    return selected_value or None


def _get_saved_model(
    chat: Chats | None, model_id: str, provider_id: str, cwd: str
) -> str | None:
    """Read the selected agent-side model for a compatible ACP session."""
    return _get_saved_control(
        chat,
        model_id,
        provider_id,
        cwd,
        "selected_model_id",
    )


def _save_session(
    chat: Chats | None,
    db,
    model_id: str,
    provider_id: str,
    cwd: str,
    session_id: str,
    selected_model_id: str | None = None,
    selected_security_level: str | None = None,
    selected_reasoning_effort: str | None = None,
) -> None:
    """Persist the agent-owned session and explicitly validated controls."""
    if chat is None:
        logger.info(
            "[ACP] Skipping ACP session metadata persistence because no durable chat row exists"
        )
        return
    meta = deepcopy(chat.meta) if isinstance(chat.meta, dict) else {}
    sessions = (
        meta.get("acp_sessions") if isinstance(meta.get("acp_sessions"), dict) else {}
    )
    entry = {
        "session_id": session_id,
        "provider_id": provider_id,
        "cwd": cwd,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    selections = {
        "selected_model_id": selected_model_id,
        "selected_security_level": selected_security_level,
        "selected_reasoning_effort": selected_reasoning_effort,
    }
    for key, selected_value in selections.items():
        effective_value = str(selected_value or "").strip()
        if effective_value:
            entry[key] = effective_value
    sessions[str(model_id)] = entry
    meta["acp_sessions"] = sessions
    chat.meta = meta
    flag_modified(chat, "meta")
    logger.info(
        "[ACP] Persisting ACP session metadata to chats table: %s",
        json.dumps(
            {
                "chat_id": str(getattr(chat, "id", "") or ""),
                "model_id": str(model_id),
                "provider_id": str(provider_id),
                "session_id": str(session_id),
                "selected_model_id": entry.get("selected_model_id"),
                "selected_security_level": entry.get("selected_security_level"),
                "selected_reasoning_effort": entry.get("selected_reasoning_effort"),
                "table": "chats",
                "column": "meta",
            },
            sort_keys=True,
        ),
    )
    db.add(chat)
    db.commit()
    logger.info(
        "[ACP] ACP session metadata commit completed: chat_id=%s model_id=%s session_id=%s",
        str(getattr(chat, "id", "") or ""),
        str(model_id),
        str(session_id),
    )


def _plan_markdown(update: dict[str, Any]) -> str:
    """Render an ACP plan update as compact reasoning Markdown."""
    lines = []
    for entry in update.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "pending")
        marker = "x" if status == "completed" else " "
        content = str(entry.get("content") or "").strip()
        if content:
            lines.append(f"- [{marker}] {content}\n")
    return "".join(lines)


def _record_acp_generation_statistic(
    db,
    *,
    db_model: Any,
    provider: Any,
    model_name: str,
    metadata: dict[str, Any],
    user_id: str | None,
    success: bool,
    error: BaseException | None = None,
) -> None:
    """Store ACP turns in the same usage statistics table as other providers."""
    error_classification = ""
    if error is not None:
        summary = _sensitive_text_summary(str(error))
        error_classification = f"{type(error).__name__}:{str(summary['sha256'])[:16]}"
    statistic_metadata = {
        key: value for key, value in metadata.items() if value not in (None, "", [], {})
    }
    model_metadata = (
        db_model.meta if isinstance(getattr(db_model, "meta", None), dict) else {}
    )
    if model_metadata.get("user_managed") is True:
        # Keep the privacy classification with the durable statistic. Provider
        # rows can be deleted later, while historical statistics remain.
        statistic_metadata["user_managed"] = True

    try:
        create_llm_generation_statistic(
            db,
            model_name=model_name,
            # The Omlorix model row remains the billable/rate-limit identity;
            # model_name and message metadata expose the agent-side model ID.
            model_id=str(getattr(db_model, "id", None) or model_name or "acp"),
            provider="acp",
            provider_id=str(getattr(provider, "id", None) or "acp"),
            success=success,
            error=not success,
            error_message=error_classification,
            error_type=type(error).__name__ if error is not None else "",
            category="chat",
            meta=statistic_metadata,
            user_id=user_id,
            is_byok=False,
        )
    except Exception as statistic_error:
        # Generation statistics must never turn a successful ACP response into
        # a failed chat turn, matching the behavior of the other adapters.
        logger.warning(
            "Failed to record ACP generation statistics: %s",
            type(statistic_error).__name__,
        )


def acp_chat(*args, **kwargs):
    """Delegate chat handling while retaining historical patch seams."""
    from . import chat as _implementation

    _implementation._sync_compat_dependencies("acp_chat", globals())
    return _implementation._impl_acp_chat(*args, **kwargs)
