from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Generator
import json
import logging
import os
import time
import uuid

from fastapi import HTTPException

from app.agents.utils import list_accessible_agents, resolve_selected_model_for_user
from app.chats.streaming import cancel_registry, stream_hub
from app.database import SessionLocal
from app.files.models import get_file
from app.groups.init import get_user_group_setting_value
from app.llm.models import (
    Models,
    RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
    RATE_LIMIT_ADMISSION_COMPLETED,
    RATE_LIMIT_ADMISSION_FAILED,
    finalize_rate_limit_admission,
    list_models,
)
from app.llm.schemas import normalize_provider_value
from app.llm.provider_request import ProviderRequest, REQUEST_TYPE_CHAT, call_provider_chat
from app.llm.utils import ensure_user_access_to_model
from app.tools.subagents.schemas import (
    SUBAGENT_RUNTIME_TARGETS_SETTING,
    SUBAGENT_TARGET_PAGE_MAX,
)


logger = logging.getLogger(__name__)

SUBAGENT_TOOL_NAME = "subagent"
SUBAGENT_MAX_ACTIVE_PER_PARENT_GENERATION = 6
SUBAGENT_MAX_PROMPT_CHARS = 50000
SUBAGENT_MAX_CONTEXT_CHARS = 50000


def _bounded_subagent_wait_seconds(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> float:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return float(max(minimum, min(maximum, value)))

# Nested providers stream these events to render durable file deliverables in
# the normal chat UI.  Most nested events belong only in the subagent activity
# transcript, but swallowing these artifact events would make files created by
# a subagent visible only after manually finding them in workspace storage.
SUBAGENT_PARENT_ARTIFACT_EVENT_TYPES = frozenset(
    {
        "canvas_evt",
        "f",
        "latex_pdf_evt",
        "slide_presentation_evt",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_content(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _structured_error(
    code: str,
    message: str,
    *,
    attachments: dict[str, list[str]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    run: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a provider-safe error and embed a completed run when one exists."""
    payload = {
        "status": "error",
        "code": code,
        "message": message,
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    if artifacts:
        payload["artifacts"] = artifacts
    tool_meta = {
        "subagent": run
        if run is not None
        else {"status": "error", "code": code},
    }
    return {
        "content": _json_content(payload),
        "result": payload,
        "documents": list((attachments or {}).get("documents") or []),
        "images": list((attachments or {}).get("images") or []),
        "videos": list((attachments or {}).get("videos") or []),
        "audios": list((attachments or {}).get("audios") or []),
        "youtube": [],
        "webpages": [],
        "tool_meta": tool_meta,
    }


def _append_run_event(
    run: dict[str, Any],
    event_type: str,
    *,
    role: str | None = None,
    name: str | None = None,
    content: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Append one exact event without duplicating data already in its raw payload."""
    raw = meta or {}
    event: dict[str, Any] = {"type": event_type}
    if role:
        event["role"] = role
    if name:
        event["name"] = name
    if raw:
        event["raw"] = raw
    # Provider text and reasoning events already carry the token in ``raw.d``.
    # Avoid storing it twice while retaining explicit content for lifecycle and
    # non-JSON events.
    if content is not None and raw.get("d") != content:
        event["content"] = content
    run["events"].append(event)


def _finish_run(
    run: dict[str, Any],
    *,
    status: str,
    result: str | None = None,
    error: str | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> None:
    """Finalize the run object that will be persisted in the chat message part."""
    run["status"] = status
    run["result"] = result
    run["error"] = error
    run["artifacts"] = artifacts or []
    run["completed_at"] = _now_iso()


def _normalize_text(value: Any, *, max_chars: int, field_name: str, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{field_name} is required")
    if len(text) > max_chars:
        raise ValueError(f"{field_name} must be {max_chars} characters or fewer")
    return text


def _model_can_be_used_for_chat(model: Models) -> bool:
    capabilities = getattr(model, "capabilities", None)
    if not isinstance(capabilities, list):
        return True
    excluded = {"image_generation", "video_generation", "audio_generation", "music_generation", "transcription", "tts"}
    return not excluded.intersection({str(item) for item in capabilities})


def _serialize_model_target(model: Models) -> dict[str, Any]:
    """Build the narrow model record shared by target discovery and its API."""
    return {
        "type": "model",
        "id": model.id,
        "name": model.name,
        "description": model.description,
        "provider": normalize_provider_value(model.provider),
        "base_model_id": model.id,
        "base_model_name": model.name,
        "is_shared": False,
    }


def _serialize_legacy_model_target(model: Models) -> dict[str, Any]:
    """Preserve the richer response returned by the legacy list_models action."""
    capabilities = getattr(model, "capabilities", None)
    return {
        **_serialize_model_target(model),
        "provider_id": model.provider_id,
        "model_name": model.model_name,
        "capabilities": capabilities if isinstance(capabilities, list) else [],
        "status": model.status,
    }


def _agents_enabled_for_user(db, user_id: str) -> bool:
    """Return whether saved Agents are enabled by the user's effective group policy."""
    return bool(get_user_group_setting_value(user_id, "agents", "allow_agents", db))


def _normalize_target_ref(value: Any) -> tuple[str, str] | None:
    """Normalize a target reference from Pydantic, request, or runtime settings data."""
    if isinstance(value, dict):
        target_type = str(value.get("type") or "").strip().lower()
        target_id = str(value.get("id") or "").strip()
    else:
        target_type = str(getattr(value, "type", "") or "").strip().lower()
        target_id = str(getattr(value, "id", "") or "").strip()
    if target_type not in {"model", "agent"} or not target_id:
        return None
    return target_type, target_id


def _runtime_target_allowlist(model_settings: dict[str, Any] | None) -> set[tuple[str, str]] | None:
    """Read the server-injected per-generation delegation allowlist.

    A missing setting preserves the historical "any accessible target" behavior.
    An explicit empty list is intentionally restrictive and disables every run.
    """
    if not isinstance(model_settings, dict) or SUBAGENT_RUNTIME_TARGETS_SETTING not in model_settings:
        return None
    raw_targets = model_settings.get(SUBAGENT_RUNTIME_TARGETS_SETTING)
    if not isinstance(raw_targets, list):
        return set()
    return {
        normalized
        for item in raw_targets
        if (normalized := _normalize_target_ref(item)) is not None
    }


def _serialize_agent_target(agent_payload: dict[str, Any], base_model: Models) -> dict[str, Any]:
    """Build the deliberately narrow Agent discovery record exposed to a model or UI.

    ``list_accessible_agents`` also powers Agent management and contains private
    execution configuration. Discovery must never copy that response wholesale.
    """
    return {
        "type": "agent",
        "id": str(agent_payload.get("id") or agent_payload.get("agent_id") or ""),
        "name": str(agent_payload.get("name") or ""),
        "description": str(getattr(base_model, "name", None) or getattr(base_model, "model_name", None) or "")[:100],
        "provider": normalize_provider_value(getattr(base_model, "provider", None)),
        "base_model_id": str(getattr(base_model, "id", None) or ""),
        "base_model_name": str(getattr(base_model, "name", None) or getattr(base_model, "model_name", None) or ""),
        "is_shared": bool(agent_payload.get("is_shared")),
    }


def _target_matches_query(target: dict[str, Any], query: str) -> bool:
    normalized_query = str(query or "").strip().casefold()
    if not normalized_query:
        return True
    searchable = " ".join(
        str(target.get(field) or "")
        for field in ("name", "description", "provider", "base_model_name")
    ).casefold()
    return normalized_query in searchable


def list_accessible_subagent_targets(
    db,
    *,
    user_id: str,
    query: str = "",
    target_type: str = "all",
    limit: int = SUBAGENT_TARGET_PAGE_MAX,
    cursor: str | None = None,
    allowed_targets: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Search authorized model and Agent targets using one safe result contract."""
    normalized_type = str(target_type or "all").strip().lower()
    if normalized_type not in {"all", "model", "agent"}:
        raise ValueError("target_type must be 'all', 'model', or 'agent'")
    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > SUBAGENT_TARGET_PAGE_MAX:
        raise ValueError(f"limit must be between 1 and {SUBAGENT_TARGET_PAGE_MAX}")
    try:
        offset = int(str(cursor or "0").strip() or "0")
    except (TypeError, ValueError) as exc:
        raise ValueError("cursor is invalid") from exc
    if offset < 0:
        raise ValueError("cursor is invalid")

    accessible_models: dict[str, Models] = {}
    targets: list[dict[str, Any]] = []
    for model in list_models(db):
        if not bool(getattr(model, "is_active", True)) or not _model_can_be_used_for_chat(model):
            continue
        try:
            ensure_user_access_to_model(user_id, model.id, db)
        except HTTPException:
            continue
        accessible_models[str(model.id)] = model
        if normalized_type in {"all", "model"}:
            targets.append(_serialize_model_target(model))

    if normalized_type in {"all", "agent"} and _agents_enabled_for_user(db, user_id):
        for agent_payload in list_accessible_agents(
            db,
            user_id,
            accessible_base_models=accessible_models,
        ):
            if not isinstance(agent_payload, dict):
                continue
            base_model = accessible_models.get(str(agent_payload.get("base_model_id") or ""))
            if base_model is None:
                continue
            targets.append(_serialize_agent_target(agent_payload, base_model))

    if allowed_targets is not None:
        targets = [
            target
            for target in targets
            if (str(target.get("type") or ""), str(target.get("id") or "")) in allowed_targets
        ]
    targets = [target for target in targets if _target_matches_query(target, query)]
    targets.sort(
        key=lambda target: (
            str(target.get("name") or "").casefold(),
            str(target.get("type") or ""),
            str(target.get("id") or ""),
        )
    )
    total = len(targets)
    page = targets[offset:offset + normalized_limit]
    next_offset = offset + len(page)
    return {
        "status": "ok",
        "targets": page,
        "models": [target for target in page if target.get("type") == "model"],
        "agents": [target for target in page if target.get("type") == "agent"],
        "count": len(page),
        "total": total,
        "limit": normalized_limit,
        "next_cursor": str(next_offset) if next_offset < total else None,
    }


def validate_subagent_target_selection(
    db,
    *,
    user_id: str,
    targets: list[Any] | None,
) -> list[dict[str, str]] | None:
    """Authorize and canonicalize the browser's optional strict target selection."""
    if targets is None:
        return None

    canonical: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_target in targets:
        normalized = _normalize_target_ref(raw_target)
        if normalized is None:
            raise HTTPException(status_code=400, detail="Invalid subagent target")
        target_type, target_id = normalized
        if normalized in seen:
            continue

        if target_type == "model":
            ensure_user_access_to_model(user_id, target_id, db)
            model = db.query(Models).filter(Models.id == target_id, Models.is_active.is_(True)).first()
            if model is None or not _model_can_be_used_for_chat(model):
                raise HTTPException(status_code=404, detail="Subagent target not found")
        else:
            if not _agents_enabled_for_user(db, user_id):
                raise HTTPException(status_code=403, detail="Agents are disabled for your group")
            resolved = resolve_selected_model_for_user(db, user_id=user_id, model_id=target_id)
            if resolved.model_kind != "agent" or resolved.agent is None or not _model_can_be_used_for_chat(resolved.base_model):
                raise HTTPException(status_code=404, detail="Subagent target not found")

        seen.add(normalized)
        canonical.append({"type": target_type, "id": target_id})
    return canonical


def list_accessible_subagent_models(
    db,
    *,
    user_id: str,
) -> dict[str, Any]:
    targets: list[dict[str, Any]] = []
    for model in list_models(db):
        if not bool(getattr(model, "is_active", True)) or not _model_can_be_used_for_chat(model):
            continue
        try:
            ensure_user_access_to_model(user_id, model.id, db)
        except HTTPException:
            continue
        targets.append(_serialize_legacy_model_target(model))
    return {
        "status": "ok",
        "models": targets,
        "count": len(targets),
        "limit": len(targets),
    }


def _configured_subagent_tools(model: Models) -> list[Any]:
    """Return the selected model's saved tools without recursive delegation.

    A subagent model is an independently configured execution target.  Its
    privileges must therefore come from its own persisted tool list rather
    than from the parent conversation's runtime overrides.  Removing only the
    subagent tool prevents unbounded nesting while preserving every other tool
    explicitly assigned to the selected model.
    """
    raw = getattr(model, "tools", None)
    if isinstance(raw, (list, tuple, set)):
        configured_tools = [deepcopy(item) for item in raw]
    elif isinstance(raw, (str, dict)):
        configured_tools = [deepcopy(raw)]
    else:
        configured_tools = []

    return [
        entry
        for entry in configured_tools
        if _tool_entry_name(entry) != SUBAGENT_TOOL_NAME
    ]


def _tool_entry_name(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict) and isinstance(entry.get("name"), str):
        return entry["name"].strip() or None
    return None


def _clone_model_with_subagent_tools(model: Models, subagent_tools: list[Any]) -> Any:
    """Clone a persisted model with its recursion-safe saved tool list."""
    payload = {
        "id": model.id,
        "name": model.name,
        "description": model.description,
        "model_icon": model.model_icon,
        "provider": model.provider,
        "provider_id": model.provider_id,
        "model_name": model.model_name,
        "settings": deepcopy(model.settings or {}),
        "capabilities": deepcopy(model.capabilities or []),
        "tools": deepcopy(subagent_tools),
        "access": deepcopy(model.access or {}),
        "meta": deepcopy(model.meta or {}),
        "status": model.status,
        "is_active": model.is_active,
        "created_at": model.created_at,
    }
    if subagent_tools and "tools" not in payload["capabilities"]:
        payload["capabilities"].append("tools")
    return SimpleNamespace(**payload)


def _build_subagent_user_message(
    *,
    prompt: str,
    context: str | None,
    attachments: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    parts = ["Subagent task:", prompt.strip()]
    if context:
        parts.extend(["", "Additional context:", context.strip()])
    content_block: dict[str, Any] = {
        "type": "content",
        "content": "\n".join(parts).strip(),
    }
    for field in ("images", "videos", "audios", "documents"):
        values = list((attachments or {}).get(field) or [])
        if values:
            content_block[field] = values
    return {
        "id": f"subagent-task-{uuid.uuid4()}",
        "role": "user",
        "content": [content_block],
    }


def _append_task_to_history(
    parent_history: list | None,
    *,
    prompt: str,
    context: str | None,
    attachments: dict[str, list[str]] | None = None,
) -> list:
    history = list(parent_history or [])
    history.append(_build_subagent_user_message(prompt=prompt, context=context, attachments=attachments))
    return history


def _merge_reference_attachments(*groups: dict[str, list[str]] | None) -> dict[str, list[str]]:
    """Merge categorized reference descriptors without duplicating them."""
    merged = {"images": [], "videos": [], "audios": [], "documents": []}
    for group in groups:
        if not isinstance(group, dict):
            continue
        for field in merged:
            for value in group.get(field) or []:
                normalized = str(value or "").strip()
                if normalized and normalized not in merged[field]:
                    merged[field].append(normalized)
    return merged


def _build_target_instruction_and_attachment_context(
    db,
    *,
    user_id: str,
    db_model: Models,
    resolved_selection=None,
) -> tuple[list[dict[str, str]] | None, dict[str, list[str]]]:
    """Apply the same fixed-Skill and saved-Agent context contract as primary chat."""
    from app.chats.utils import (
        _build_system_instruction_sections,
        _collect_skill_file_attachment_ids,
        _compose_skill_content,
        _extract_model_skill_ids,
        _resolve_generation_skill_ids,
        _resolve_trusted_admin_skill_ids,
    )

    agent_skill_ids = list(getattr(resolved_selection, "agent_skill_ids", None) or [])
    model_skill_ids = _extract_model_skill_ids(
        db_model.settings if isinstance(getattr(db_model, "settings", None), dict) else {}
    )
    effective_skill_ids = _resolve_generation_skill_ids(
        requested_skill_ids=[],
        model_skill_ids=model_skill_ids,
        agent_skill_ids=agent_skill_ids,
    )
    trusted_admin_skill_ids = _resolve_trusted_admin_skill_ids(
        model_skill_ids=model_skill_ids,
        agent_skill_ids=agent_skill_ids,
    )
    skill_content = _compose_skill_content(
        db,
        user_id,
        effective_skill_ids,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )
    system_sections = _build_system_instruction_sections(
        skill_content=skill_content,
        agent_instruction=getattr(resolved_selection, "agent_instruction", None),
    )
    skill_attachments = _collect_skill_file_attachment_ids(
        db,
        user_id,
        effective_skill_ids,
        trusted_admin_skill_ids=trusted_admin_skill_ids,
    )

    agent_attachments = {"images": [], "videos": [], "audios": [], "documents": []}
    descriptor_map = getattr(resolved_selection, "asset_descriptors_by_category", None) or {}
    for source, target in {
        "image": "images",
        "video": "videos",
        "audio": "audios",
        "document": "documents",
    }.items():
        agent_attachments[target] = list(descriptor_map.get(source) or [])

    return system_sections or None, _merge_reference_attachments(skill_attachments, agent_attachments)


def _dispatch_nested_provider(
    *,
    db,
    provider: str,
    chat_id: str | None,
    chat_history: list,
    db_model,
    user_id: str,
    project_id: str | None,
    generation_id: str,
    settings_override: dict[str, Any],
    system_instruction_sections: list[dict[str, str]] | None,
    user_role: str | None,
) -> Generator[str, None, Any]:
    normalized_provider = normalize_provider_value(provider)
    return call_provider_chat(
        ProviderRequest(
            request_type=REQUEST_TYPE_CHAT,
            db=db,
            provider=normalized_provider,
            model=db_model,
            chat_history=chat_history,
            user_id=user_id,
            project_id=project_id,
            generation_id=generation_id,
            temp_request_flag=True,
            settings_override=settings_override,
            system_instruction_sections=system_instruction_sections,
            assistant_metadata={"subagent": True},
            user_role=user_role,
            extra={"chat_id": chat_id},
        )
    )


def _event_from_nested_line(raw_line: str) -> tuple[str, str | None, dict[str, Any]]:
    try:
        payload = json.loads(str(raw_line or "").strip())
    except Exception:
        return "raw", str(raw_line or ""), {}
    if not isinstance(payload, dict):
        return "raw", str(raw_line or ""), {}
    stream_type = payload.get("t") or payload.get("type") or "stream"
    if stream_type == "c":
        return "message_delta", payload.get("d") if isinstance(payload.get("d"), str) else "", payload
    if stream_type == "r":
        return "reasoning_delta", payload.get("d") if isinstance(payload.get("d"), str) else "", payload
    if stream_type == "t_c":
        descriptor = payload.get("d")
        name = descriptor if isinstance(descriptor, str) else descriptor.get("name") if isinstance(descriptor, dict) else None
        return "tool_call", None, {"name": name, "payload": payload}
    if stream_type == "t_cd":
        return "tool_delta", None, payload
    if stream_type == "wg":
        return "widget", None, payload
    if stream_type == "e":
        return "error", payload.get("d") if isinstance(payload.get("d"), str) else None, payload
    if stream_type == "d":
        return "done", None, payload
    if stream_type in {"s", "a_id", "uf", "w", "r_f", "f"}:
        return "stream", None, payload
    return str(stream_type), payload.get("d") if isinstance(payload.get("d"), str) else None, payload


def _subagent_stream_line(run_id: str, event: str, data: dict[str, Any]) -> str:
    return json.dumps(
        {
            "t": "subagent_evt",
            "event": event,
            "run_id": run_id,
            "data": data,
        },
        ensure_ascii=False,
    ) + "\n"


def _record_subagent_artifact(
    db,
    *,
    file_id: str,
    fallback_name: str | None,
    run_id: str,
    user_id: str,
) -> tuple[str, dict[str, Any]]:
    """Classify and annotate one file emitted by a nested provider.

    Generated files already use the normal ``Files`` persistence path inside
    their producing tool.  This helper only adds subagent provenance and maps
    the record back into the attachment field expected by the parent provider.
    Unknown or already-removed records remain downloadable document references
    instead of being silently discarded.
    """
    normalized_file_id = str(file_id or "").strip()
    record = get_file(db, normalized_file_id, str(user_id)) if normalized_file_id else None
    category = str(getattr(record, "file_category", "") or "").strip().lower()
    attachment_field = {
        "image": "images",
        "video": "videos",
        "audio": "audios",
    }.get(category, "documents")

    record_meta = dict(getattr(record, "meta", None) or {})
    if record is not None:
        # Assignment, rather than in-place mutation, ensures SQLAlchemy notices
        # the JSON change on every supported database backend.
        record_meta.update(
            {
                "generated_by": "subagent",
                "subagent_id": run_id,
            }
        )
        record.meta = record_meta

    original_name = str(
        record_meta.get("original_filename")
        or fallback_name
        or getattr(record, "file_name", "")
        or normalized_file_id
    ).strip()
    artifact = {
        "file_id": normalized_file_id,
        "name": original_name,
        "file_category": category or "unknown",
        "file_type": str(getattr(record, "file_type", "") or ""),
    }
    return attachment_field, artifact


def _execute_subagent_tool_inline(
    db,
    *,
    tool_arguments: dict[str, Any] | None,
    user_id: str,
    group_id: str | None,
    project_id: str | None,
    model_settings: dict[str, Any] | None,
    chat_id: str | None,
    chat_history: list | None,
    generation_id: str | None,
    user_role: str | None,
) -> Generator[str, None, dict[str, Any]]:
    args = tool_arguments if isinstance(tool_arguments, dict) else {}
    action = str(args.get("action") or "").strip()
    runtime_allowlist = _runtime_target_allowlist(model_settings)
    if action in {"list_models", "list_targets"}:
        try:
            # Preserve the legacy model-only response contract. New callers use
            # ``list_targets`` for bounded model and Agent discovery.
            if action == "list_models":
                payload = list_accessible_subagent_models(db, user_id=user_id)
                if runtime_allowlist is not None:
                    payload["models"] = [
                        model
                        for model in payload["models"]
                        if ("model", str(model.get("id") or "")) in runtime_allowlist
                    ]
                    payload["count"] = len(payload["models"])
            else:
                payload = list_accessible_subagent_targets(
                    db,
                    user_id=user_id,
                    query=_normalize_text(args.get("query"), max_chars=200, field_name="query"),
                    target_type=str(args.get("target_type") or "all"),
                    limit=(
                        SUBAGENT_TARGET_PAGE_MAX
                        if args.get("limit") is None
                        else int(args.get("limit"))
                    ),
                    cursor=str(args.get("cursor") or "").strip() or None,
                    allowed_targets=runtime_allowlist,
                )
        except (TypeError, ValueError) as exc:
            return _structured_error("invalid_arguments", str(exc))
        return {
            "content": _json_content(payload),
            "result": payload,
            "documents": [],
            "images": [],
            "videos": [],
            "audios": [],
            "youtube": [],
            "webpages": [],
            "tool_meta": {"subagent": {"action": action, "count": payload["count"]}},
        }

    if action != "run":
        return _structured_error(
            "invalid_action",
            "subagent action must be 'list_targets', 'list_models', or 'run'.",
        )

    model_id = str(args.get("model_id") or "").strip()
    agent_id = str(args.get("agent_id") or "").strip()
    if bool(model_id) == bool(agent_id):
        return _structured_error(
            "invalid_target",
            "Provide exactly one of model_id or agent_id for action='run'.",
        )

    requested_target = ("agent", agent_id) if agent_id else ("model", model_id)
    if runtime_allowlist is not None and requested_target not in runtime_allowlist:
        return _structured_error(
            "subagent_target_not_selected",
            "The requested subagent target is not in the user's selected target list.",
        )

    prompt_field = "task" if agent_id else "prompt"
    try:
        prompt = _normalize_text(
            args.get(prompt_field),
            max_chars=SUBAGENT_MAX_PROMPT_CHARS,
            field_name=prompt_field,
            required=True,
        )
        context = _normalize_text(
            args.get("context"),
            max_chars=SUBAGENT_MAX_CONTEXT_CHARS,
            field_name="context",
            required=False,
        )
    except ValueError as exc:
        return _structured_error("invalid_arguments", str(exc))

    # ``model_settings`` belongs to the parent conversation.  It remains in
    # this public call signature for compatibility with the common tool
    # dispatcher, but must not grant tools or MCP selections to the subagent.
    nested_generation_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    run: dict[str, Any] | None = None
    rate_limit_admission = None
    generated_attachments: dict[str, list[str]] = {
        "documents": [],
        "images": [],
        "videos": [],
        "audios": [],
    }
    generated_artifacts: list[dict[str, Any]] = []
    seen_generated_file_ids: set[str] = set()

    try:
        if model_id:
            ensure_user_access_to_model(user_id, model_id, db)
            db_model = db.query(Models).filter(Models.id == model_id, Models.is_active.is_(True)).first()
            if not db_model or not _model_can_be_used_for_chat(db_model):
                raise HTTPException(status_code=404, detail="Chat-capable model not found")
            mode = "model"
            resolved_model_id = db_model.id
            resolved_agent_id = None
            system_instruction_sections, target_reference_attachments = _build_target_instruction_and_attachment_context(
                db,
                user_id=user_id,
                db_model=db_model,
            )
        else:
            if not _agents_enabled_for_user(db, user_id):
                raise HTTPException(status_code=403, detail="Agents are disabled for your group")
            resolved_selection = resolve_selected_model_for_user(db, user_id=user_id, model_id=agent_id)
            if (
                resolved_selection.model_kind != "agent"
                or resolved_selection.agent is None
                or not _model_can_be_used_for_chat(resolved_selection.base_model)
            ):
                raise HTTPException(status_code=404, detail="Agent not found")
            db_model = resolved_selection.base_model
            mode = "agent"
            resolved_model_id = db_model.id
            resolved_agent_id = resolved_selection.agent.id
            system_instruction_sections, target_reference_attachments = _build_target_instruction_and_attachment_context(
                db,
                user_id=user_id,
                db_model=db_model,
                resolved_selection=resolved_selection,
            )

        subagent_tools = _configured_subagent_tools(db_model)

        try:
            from app.chats.utils import _admit_rate_limited_chat_action, _assert_generation_provider_allowed

            _assert_generation_provider_allowed(
                db,
                provider=normalize_provider_value(getattr(db_model, "provider", None)),
                db_model=db_model,
                byok=None,
                feature="subagent generation",
            )
            rate_limit_admission = _admit_rate_limited_chat_action(
                db,
                user_id=user_id,
                group_id=group_id,
                model=db_model,
                action_type=RATE_LIMIT_ADMISSION_ACTION_MESSAGE,
                chat_id=chat_id,
            )
        except HTTPException:
            raise
        except Exception:
            logger.warning("Subagent provider/rate-limit admission check failed", exc_info=True)
            raise

        started_at = _now_iso()
        run = {
            "id": run_id,
            "status": "running",
            "mode": mode,
            "model_id": resolved_model_id,
            "agent_id": resolved_agent_id,
            "prompt": prompt,
            "result": None,
            "error": None,
            "started_at": started_at,
            "completed_at": None,
            "artifacts": [],
            "meta": {
                "model_name": getattr(db_model, "name", None),
                "provider": normalize_provider_value(getattr(db_model, "provider", None)),
                "model_enabled_tools": [_tool_entry_name(entry) or str(entry) for entry in subagent_tools],
                "reference_asset_count": sum(len(values) for values in target_reference_attachments.values()),
            },
            "events": [],
        }
        _append_run_event(
            run,
            "start",
            role="system",
            content=prompt,
            meta={
                "mode": mode,
                "model_id": resolved_model_id,
                "agent_id": resolved_agent_id,
                "nested_generation_id": nested_generation_id,
            },
        )
        yield _subagent_stream_line(
            run_id,
            "start",
            {
                "status": "running",
                "mode": mode,
                "model_id": resolved_model_id,
                "agent_id": resolved_agent_id,
                "model_name": getattr(db_model, "name", None),
                "provider": normalize_provider_value(getattr(db_model, "provider", None)),
                "started_at": started_at,
            },
        )

        nested_history = _append_task_to_history(
            chat_history,
            prompt=prompt,
            context=context,
            attachments=target_reference_attachments,
        )
        settings_override = {
            "enabled_tools": subagent_tools,
            "_runtime_enabled_tools": subagent_tools,
        }
        nested_model = _clone_model_with_subagent_tools(db_model, subagent_tools)
        cancel_registry.set_active(f"subagent:{run_id}", nested_generation_id)

        accumulated_content: list[str] = []
        nested_stream = _dispatch_nested_provider(
            db=db,
            provider=getattr(db_model, "provider", None),
            chat_id=chat_id,
            chat_history=nested_history,
            db_model=nested_model,
            user_id=user_id,
            project_id=project_id,
            generation_id=nested_generation_id,
            settings_override=settings_override,
            system_instruction_sections=system_instruction_sections,
            user_role=user_role,
        )
        for raw_line in nested_stream:
            if generation_id and cancel_registry.is_cancelled(generation_id):
                cancel_registry.cancel(nested_generation_id)
            if cancel_registry.is_cancelled(nested_generation_id):
                partial_result = "".join(accumulated_content)
                _finish_run(
                    run,
                    status="cancelled",
                    result=partial_result,
                    artifacts=generated_artifacts,
                )
                _append_run_event(
                    run,
                    "cancelled",
                    content="Subagent run cancelled.",
                    meta={"status": "cancelled"},
                )
                yield _subagent_stream_line(run_id, "cancelled", {"status": "cancelled"})
                return _structured_error(
                    "subagent_cancelled",
                    "Subagent run was cancelled.",
                    run=run,
                    attachments=generated_attachments,
                    artifacts=generated_artifacts,
                )

            event_type, content, meta = _event_from_nested_line(raw_line)
            if event_type == "message_delta" and content:
                accumulated_content.append(content)

            # Preserve the normal top-level artifact stream alongside the
            # subagent activity event.  This makes the file appear immediately
            # in the parent transcript while the attachment lists below make it
            # durable in the saved assistant message.
            stream_type = str(meta.get("t") or meta.get("type") or "") if isinstance(meta, dict) else ""
            forward_artifact_event = stream_type in SUBAGENT_PARENT_ARTIFACT_EVENT_TYPES
            artifact_file_id = ""
            artifact_fallback_name = None
            if stream_type == "f":
                artifact_file_id = str(meta.get("d") or meta.get("file_id") or "").strip()
                artifact_fallback_name = meta.get("n") or meta.get("file_name")
                forward_artifact_event = False
            elif (
                stream_type == "slide_presentation_evt"
                and str(meta.get("event") or "").strip().lower() == "complete"
            ):
                # The presentation pipeline publishes its durable PPTX only in
                # the completion event; unlike the other artifact tools, it
                # does not follow that event with a generic ``t: f`` line.
                presentation_data = meta.get("data")
                if isinstance(presentation_data, dict):
                    artifact_file_id = str(presentation_data.get("file_id") or "").strip()
                    artifact_fallback_name = (
                        presentation_data.get("file_name")
                        or presentation_data.get("title")
                    )

            if artifact_file_id and artifact_file_id not in seen_generated_file_ids:
                attachment_field, artifact = _record_subagent_artifact(
                    db,
                    file_id=artifact_file_id,
                    fallback_name=artifact_fallback_name,
                    run_id=run_id,
                    user_id=user_id,
                )
                seen_generated_file_ids.add(artifact_file_id)
                generated_attachments[attachment_field].append(artifact_file_id)
                generated_artifacts.append(artifact)
                if stream_type == "f":
                    forward_artifact_event = True
            _append_run_event(
                run,
                event_type,
                role="assistant" if event_type in {"message_delta", "reasoning_delta"} else None,
                name=meta.get("name") if isinstance(meta, dict) else None,
                content=content,
                meta=meta if isinstance(meta, dict) else {},
            )
            yield _subagent_stream_line(
                run_id,
                event_type,
                {
                    "content": content,
                    "raw": meta,
                },
            )
            if forward_artifact_event:
                normalized_line = str(raw_line or "")
                yield normalized_line if normalized_line.endswith("\n") else normalized_line + "\n"

        result_text = "".join(accumulated_content).strip()
        _finish_run(
            run,
            status="completed",
            result=result_text,
            artifacts=generated_artifacts,
        )
        _append_run_event(
            run,
            "complete",
            role="assistant",
            meta={"status": "completed"},
        )
        yield _subagent_stream_line(
            run_id,
            "complete",
            {
                "status": "completed",
                "result": result_text,
                "completed_at": run["completed_at"],
            },
        )
        payload = {
            "status": "completed",
            "run_id": run_id,
            "model_id": resolved_model_id,
            "agent_id": resolved_agent_id,
            "result": result_text,
            "artifacts": generated_artifacts,
        }
        return {
            "content": _json_content(payload),
            "result": payload,
            "documents": generated_attachments["documents"],
            "images": generated_attachments["images"],
            "videos": generated_attachments["videos"],
            "audios": generated_attachments["audios"],
            "youtube": [],
            "webpages": [],
            "tool_meta": {"subagent": run},
        }
    except HTTPException as exc:
        message = str(exc.detail)
        if run is not None:
            _finish_run(
                run,
                status="failed",
                error=message,
                artifacts=generated_artifacts,
            )
            _append_run_event(
                run,
                "error",
                content=message,
                meta={"status": "error"},
            )
            yield _subagent_stream_line(run_id, "error", {"status": "error", "message": message})
        return _structured_error(
            "subagent_failed",
            message,
            run=run,
            attachments=generated_attachments,
            artifacts=generated_artifacts,
        )
    except Exception as exc:
        logger.warning("Subagent run failed", exc_info=True)
        message = str(exc) or "Subagent run failed."
        if run is not None:
            _finish_run(
                run,
                status="failed",
                error=message,
                artifacts=generated_artifacts,
            )
            _append_run_event(
                run,
                "error",
                content=message,
                meta={"status": "error"},
            )
            yield _subagent_stream_line(run_id, "error", {"status": "error", "message": message})
        return _structured_error(
            "subagent_failed",
            message,
            run=run,
            attachments=generated_attachments,
            artifacts=generated_artifacts,
        )
    finally:
        if run is not None:
            cancel_registry.clear(nested_generation_id)
        if rate_limit_admission is not None:
            try:
                final_status = (
                    RATE_LIMIT_ADMISSION_COMPLETED
                    if run is not None and run.get("status") == "completed"
                    else RATE_LIMIT_ADMISSION_FAILED
                )
                finalize_rate_limit_admission(
                    db,
                    getattr(rate_limit_admission, "admission_id", None),
                    final_status=final_status,
                )
            except Exception:
                logger.debug("Failed to finalize subagent rate-limit admission", exc_info=True)


def _without_worker_sequence(line: str) -> str | None:
    """Remove the private Research stream cursor before parent-stream publish."""

    try:
        payload = json.loads(str(line or ""))
    except (TypeError, json.JSONDecodeError):
        normalized = str(line or "")
        return normalized if normalized.endswith("\n") else normalized + "\n"
    if not isinstance(payload, dict):
        return None
    if payload.get("type") == "ping":
        return None
    payload.pop("seq", None)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def _cancel_external_subagent_job(job_id: str, user_id: str) -> None:
    from app.workers.models import request_worker_job_cancellation

    session = SessionLocal()
    try:
        request_worker_job_cancellation(
            session,
            job_id=job_id,
            user_id=user_id,
            commit=True,
        )
    except Exception:
        session.rollback()
        logger.warning(
            "Failed to cancel external subagent job job_id=%s",
            job_id,
            exc_info=True,
        )
    finally:
        session.close()


def _execute_subagent_tool_external(
    *,
    tool_arguments: dict[str, Any] | None,
    user_id: str,
    project_id: str | None,
    model_settings: dict[str, Any] | None,
    chat_id: str | None,
    chat_history: list | None,
    generation_id: str | None,
) -> Generator[str, None, dict[str, Any]]:
    from app.workers.models import WorkerJobFailed, wait_for_worker_job
    from app.workers.research import enqueue_subagent_job

    run_id = str(uuid.uuid4())
    stream_id = f"research-subagent:{run_id}"
    stream_hub.start(
        stream_id,
        f"subagent:{run_id}",
        metadata={"state": "queued", "user_id": str(user_id)},
    )
    queue_session = SessionLocal()
    try:
        job = enqueue_subagent_job(
            queue_session,
            run_id=run_id,
            user_id=user_id,
            tool_arguments=(
                tool_arguments if isinstance(tool_arguments, dict) else {}
            ),
            project_id=project_id,
            model_settings=model_settings,
            chat_id=chat_id,
            chat_history=chat_history,
            parent_generation_id=generation_id,
        )
        job_id = str(job.id)
    except Exception:
        logger.exception("Failed to enqueue external subagent run")
        stream_hub.mark_done(stream_id, status="failed")
        return _structured_error(
            "subagent_queue_unavailable",
            "Subagent run failed.",
        )
    finally:
        queue_session.close()

    queued_at = time.monotonic()
    queue_start_deadline = queued_at + _bounded_subagent_wait_seconds(
        "RESEARCH_SUBAGENT_QUEUE_START_TIMEOUT_SECONDS",
        default=60,
        minimum=5,
        maximum=600,
    )
    completion_deadline = queued_at + _bounded_subagent_wait_seconds(
        "RESEARCH_SUBAGENT_COMPLETION_TIMEOUT_SECONDS",
        default=21600,
        minimum=60,
        maximum=86400,
    )
    queue_started = False
    # The shared stream's normal in-memory heartbeat is intentionally relaxed
    # for browser connections. Use a short bounded heartbeat here so the queue
    # admission and completion deadlines are enforced even after Redis fails
    # over to that backend.
    subscription = stream_hub.subscribe(
        stream_id,
        from_seq=0,
        heartbeat_seconds=0.5,
    )
    try:
        for worker_line in subscription:
            current = time.monotonic()
            if current >= completion_deadline or (
                not queue_started and current >= queue_start_deadline
            ):
                _cancel_external_subagent_job(job_id, user_id)
                stream_hub.mark_done(stream_id, status="failed")
                return _structured_error(
                    "subagent_queue_unavailable" if not queue_started else "subagent_failed",
                    "Subagent run failed.",
                )
            if generation_id and cancel_registry.is_cancelled(generation_id):
                _cancel_external_subagent_job(job_id, user_id)
                stream_hub.mark_done(stream_id, status="cancelled")
                return _structured_error(
                    "subagent_cancelled",
                    "Subagent run was cancelled.",
                )
            parent_line = _without_worker_sequence(worker_line)
            if parent_line is not None:
                queue_started = True
                yield parent_line
        remaining = completion_deadline - time.monotonic()
        if remaining <= 0:
            _cancel_external_subagent_job(job_id, user_id)
            stream_hub.mark_done(stream_id, status="failed")
            return _structured_error("subagent_failed", "Subagent run failed.")
        result = wait_for_worker_job(
            job_id,
            timeout_seconds=min(60.0, remaining),
            poll_seconds=0.1,
        )
        if isinstance(result, dict) and result:
            return result
        return _structured_error("subagent_failed", "Subagent run failed.")
    except WorkerJobFailed as exc:
        return _structured_error(
            "subagent_cancelled" if exc.status == "cancelled" else "subagent_failed",
            (
                "Subagent run was cancelled."
                if exc.status == "cancelled"
                else "Subagent run failed."
            ),
        )
    except TimeoutError:
        _cancel_external_subagent_job(job_id, user_id)
        stream_hub.mark_done(stream_id, status="failed")
        return _structured_error("subagent_failed", "Subagent run failed.")
    finally:
        close_subscription = getattr(subscription, "close", None)
        if callable(close_subscription):
            close_subscription()


def execute_subagent_tool(
    db,
    *,
    tool_arguments: dict[str, Any] | None,
    user_id: str,
    group_id: str | None,
    project_id: str | None,
    model_settings: dict[str, Any] | None,
    chat_id: str | None,
    chat_history: list | None,
    generation_id: str | None,
    user_role: str | None,
) -> Generator[str, None, dict[str, Any]]:
    """Run long subagents in Research Worker, retaining an inline fallback."""

    from app.workers.research import external_research_enabled

    args = tool_arguments if isinstance(tool_arguments, dict) else {}
    if str(args.get("action") or "").strip() == "run" and external_research_enabled():
        return (
            yield from _execute_subagent_tool_external(
                tool_arguments=args,
                user_id=user_id,
                project_id=project_id,
                model_settings=model_settings,
                chat_id=chat_id,
                chat_history=chat_history,
                generation_id=generation_id,
            )
        )
    return (
        yield from _execute_subagent_tool_inline(
            db,
            tool_arguments=args,
            user_id=user_id,
            group_id=group_id,
            project_id=project_id,
            model_settings=model_settings,
            chat_id=chat_id,
            chat_history=chat_history,
            generation_id=generation_id,
            user_role=user_role,
        )
    )
