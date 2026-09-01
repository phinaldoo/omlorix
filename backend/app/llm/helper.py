from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any

from app.files.models import AccessDeniedError, list_project_files

def _normalize_tool_name(name: str | None) -> str | None:
    """Normalize a tool name by stripping whitespace."""
    if not isinstance(name, str):
        return None
    normalized = name.strip()
    if not normalized:
        return None
    return normalized


def stringify_tool_call_arguments(arguments: Any) -> str:
    """Return a stable string representation for persisted tool-call arguments.

    Tool providers expose arguments in slightly different shapes. Persisting one
    normalized string keeps the chat schema provider-neutral while preserving raw
    JSON argument strings exactly when a provider already supplied one.
    """
    if isinstance(arguments, str):
        stripped = arguments.strip()
        return stripped or "{}"
    if arguments is None:
        return "{}"
    try:
        return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(arguments)


def parse_legacy_tool_call_content(content: Any) -> tuple[str | None, str | None]:
    """Parse the former ``name(arguments)`` tool-call storage representation.

    This compatibility parser intentionally uses the first opening and final
    closing parenthesis so JSON strings containing parentheses remain readable.
    New messages should use :func:`build_tool_call_block` instead.
    """
    if not isinstance(content, str):
        return None, None
    stripped = content.strip()
    if not stripped:
        return None, None
    open_paren = stripped.find("(")
    close_paren = stripped.rfind(")")
    if open_paren <= 0 or close_paren <= open_paren:
        return None, None
    name = _normalize_tool_name(stripped[:open_paren])
    if not name:
        return None, None
    arguments = stripped[open_paren + 1 : close_paren].strip()
    return name, arguments or "{}"


def extract_tool_call_block(block: Any) -> dict[str, str | None]:
    """Read canonical or legacy tool-call data from a persisted content block."""
    if not isinstance(block, dict):
        return {
            "tool_name": None,
            "arguments": None,
            "tool_call_id": None,
            "tool_namespace": None,
        }

    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    legacy_name, legacy_arguments = parse_legacy_tool_call_content(
        block.get("content") or block.get("text")
    )

    tool_name = (
        _normalize_tool_name(meta.get("tool_name"))
        or _normalize_tool_name(meta.get("name"))
        or _normalize_tool_name(block.get("tool_name"))
        or _normalize_tool_name(block.get("name"))
        or legacy_name
    )

    # ``tool_args`` and ``args`` are accepted for imported and older Omlorix
    # messages. New blocks always use the explicit ``arguments`` key.
    arguments_value = None
    for key in ("arguments", "tool_args", "args"):
        if key in meta and meta.get(key) is not None:
            arguments_value = meta.get(key)
            break
    arguments = (
        stringify_tool_call_arguments(arguments_value)
        if arguments_value is not None
        else legacy_arguments
    )

    raw_call_id = (
        meta.get("tool_call_id")
        or meta.get("call_id")
        or meta.get("tool_use_id")
        or block.get("tool_call_id")
    )
    tool_call_id = str(raw_call_id).strip() if raw_call_id is not None else None
    if not tool_call_id:
        tool_call_id = None

    raw_namespace = meta.get("tool_namespace") or meta.get("namespace") or block.get("namespace")
    tool_namespace = str(raw_namespace).strip() if raw_namespace is not None else None
    if not tool_namespace:
        tool_namespace = None

    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "tool_call_id": tool_call_id,
        "tool_namespace": tool_namespace,
    }


def format_tool_call_block_label(block: Any) -> str:
    """Build the user/model-facing ``name(arguments)`` label for a tool block."""
    extracted = extract_tool_call_block(block)
    tool_name = extracted["tool_name"]
    arguments = extracted["arguments"]
    if tool_name:
        return f"{tool_name}({arguments or '{}'})"

    # Preserve malformed or non-standard legacy rows rather than hiding them.
    if isinstance(block, dict):
        raw_content = block.get("content") or block.get("text")
        if isinstance(raw_content, str):
            return raw_content.strip()
    return ""


def build_tool_call_block(
    tool_name: str | None,
    arguments: Any,
    *,
    tool_call_id: str | None = None,
    tool_namespace: str | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical persisted representation of a generic tool call.

    The display label is deliberately not stored in ``content``. Consumers use
    :func:`format_tool_call_block_label` to derive it, avoiding duplication for
    calls with large arguments such as code execution and document generation.
    """
    meta = deepcopy(extra_meta) if isinstance(extra_meta, dict) else {}
    normalized_name = _normalize_tool_name(tool_name)
    if normalized_name:
        meta["tool_name"] = normalized_name
    meta["arguments"] = stringify_tool_call_arguments(arguments)

    normalized_call_id = str(tool_call_id or "").strip()
    if normalized_call_id:
        meta["tool_call_id"] = normalized_call_id

    normalized_namespace = str(tool_namespace or "").strip()
    if normalized_namespace:
        meta["tool_namespace"] = normalized_namespace

    return {"type": "tool_call", "meta": meta}


def _coerce_tool_entries(raw: Any) -> list:
    """Return a list of tool entries (str or dict) from arbitrary input."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        iterable = raw
    else:
        iterable = [raw]

    tools: list = []
    for item in iterable:
        if isinstance(item, (str, dict)):
            tools.append(deepcopy(item))
    return tools


def _extract_tool_name(tool_entry: Any) -> str | None:
    """Extract tool name from a tool entry (string or dict)."""
    if isinstance(tool_entry, str):
        return _normalize_tool_name(tool_entry)
    if isinstance(tool_entry, dict):
        name = tool_entry.get("name")
        if isinstance(name, str):
            return _normalize_tool_name(name)
    return None


def normalize_unsupported_file_ids(raw_ids: Any) -> list[str]:
    """Normalize unsupported file IDs to a list of unique strings."""
    if not isinstance(raw_ids, (list, tuple, set)):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_id in raw_ids:
        sid = str(raw_id or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        normalized.append(sid)
    return normalized


def build_stream_tool_event_meta(
    db,
    *,
    user_id: str | None,
    tool_name: str | None,
    model_settings: dict[str, Any] | None,
    arguments: dict[str, Any] | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any] | None:
    normalized_tool_name = _normalize_tool_name(tool_name)
    if not db or not user_id or not normalized_tool_name:
        return None
    try:
        from app.mcp.utils import build_mcp_tool_stream_meta

        return build_mcp_tool_stream_meta(
            db,
            user_id=user_id,
            public_name=normalized_tool_name,
            model_settings=model_settings,
            arguments=arguments,
            tool_call_id=tool_call_id,
        )
    except Exception:
        return None


def merge_unsupported_file_ids(target: set[str], raw_ids: Any) -> None:
    """Merge unsupported file IDs into a target set."""
    for sid in normalize_unsupported_file_ids(raw_ids):
        target.add(sid)


def safe_list_project_files(
    db,
    user_id: str,
    project_id: str,
    *,
    logger,
    log_prefix: str,
    failure_message: str,
    include_project_id: bool = False,
):
    """Safely list project files with error handling."""
    try:
        return list_project_files(db, user_id, project_id)
    except AccessDeniedError as exc:
        if include_project_id:
            logger.warning("%s Project file access denied for project %s: %s", log_prefix, project_id, exc)
        else:
            logger.warning("%s Project file access denied: %s", log_prefix, exc)
    except Exception as exc:
        if include_project_id:
            logger.warning("%s %s for project %s: %s", log_prefix, failure_message, project_id, exc)
        else:
            logger.warning("%s %s: %s", log_prefix, failure_message, exc)
    return []


def _normalize_supported_mime_types(raw_mime_types: Any) -> set[str]:
    """Normalize supported MIME types to a lowercase set."""
    if not isinstance(raw_mime_types, (list, tuple, set)):
        return set()
    normalized: set[str] = set()
    for raw_value in raw_mime_types:
        value = str(raw_value or "").strip().lower()
        if value:
            normalized.add(value)
    return normalized


def _infer_text_content_included(
    *,
    model_context_representation: str | None,
    text_content_included: bool | None,
) -> bool | None:
    """Infer whether extracted text content was included in the prompt."""
    if text_content_included is not None:
        return bool(text_content_included)
    if model_context_representation == "text_extract":
        return True
    if model_context_representation in {"native_file", "rendered_images", "metadata_only"}:
        return False
    return None


def _infer_native_context_included(
    *,
    model_context_representation: str | None,
    native_context_included: bool | None,
) -> bool | None:
    """Infer whether the original file was natively injected into model context."""
    if native_context_included is not None:
        return bool(native_context_included)
    if model_context_representation == "native_file":
        return True
    if model_context_representation in {"rendered_images", "text_extract", "metadata_only"}:
        return False
    return None


def _suggest_png_output_name(filename: str | None) -> str:
    """Return a PNG filename suggestion derived from the original name."""
    raw_name = str(filename or "").strip()
    if not raw_name:
        return "converted.png"
    if "." in raw_name:
        stem = raw_name.rsplit(".", 1)[0].strip() or "converted"
    else:
        stem = raw_name
    return f"{stem}.png"


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _resolve_user_visible_file_name(meta: dict[str, Any]) -> str | None:
    """Return a filename that came from user-visible metadata, not storage."""
    return (
        _normalize_optional_string(meta.get("original_filename"))
        or _normalize_optional_string(meta.get("original_name"))
        or _normalize_optional_string(meta.get("name"))
    )


def build_file_metadata_payload(
    file_id: Any,
    file_info: dict[str, Any] | None,
    *,
    native_context_included: bool | None = None,
    model_context_representation: str | None = None,
    text_content_included: bool | None = None,
    provider_supported_image_mime_types: Any = None,
) -> dict[str, Any]:
    """Build file metadata payload for tool calls."""
    info = file_info if isinstance(file_info, dict) else {}
    meta = info.get("meta") if isinstance(info.get("meta"), dict) else {}

    user_visible_file_name = _resolve_user_visible_file_name(meta)

    mime_type = info.get("file_type") or meta.get("mime_type")
    mime_type = _normalize_optional_string(mime_type)

    file_category = info.get("file_category")
    file_category = _normalize_optional_string(file_category)

    file_size = info.get("file_size")
    if file_size is None:
        file_size = meta.get("file_size")
    try:
        file_size = int(file_size) if file_size is not None else None
    except (TypeError, ValueError):
        file_size = None

    normalized_representation = str(model_context_representation or "").strip() or None
    normalized_native_context_included = _infer_native_context_included(
        model_context_representation=normalized_representation,
        native_context_included=native_context_included,
    )
    normalized_text_content_included = _infer_text_content_included(
        model_context_representation=normalized_representation,
        text_content_included=text_content_included,
    )

    payload = {
        # File IDs are opaque, user-scoped handles rather than storage paths or
        # credentials. File-consuming tools need this exact value to reference
        # an attachment the model can already see. Every tool resolver still
        # performs its own ownership check before reading the file.
        "file_id": _normalize_optional_string(file_id),
        "file_name": user_visible_file_name,
        "file_mime_type": mime_type,
        "file_category": file_category,
        "file_size": file_size,
    }
    if normalized_native_context_included is not None:
        payload["native_context_included"] = normalized_native_context_included
    if normalized_representation:
        payload["model_context_representation"] = normalized_representation
    if normalized_text_content_included is not None:
        payload["text_content_included"] = normalized_text_content_included

    supported_image_mime_types = _normalize_supported_mime_types(provider_supported_image_mime_types)
    normalized_mime_type = str(mime_type or "").strip().lower()
    if (
        file_category == "image"
        and normalized_mime_type
        and normalized_native_context_included is False
        and supported_image_mime_types
        and normalized_mime_type not in supported_image_mime_types
    ):
        payload["suggested_code_execution_preprocessing"] = {
            "tool": "code_execution",
            "action": "convert_image_to_png",
            "output_filename": _suggest_png_output_name(user_visible_file_name),
            "output_mime_type": "image/png",
            "reason": f"This model input path does not natively support {normalized_mime_type}.",
        }

    return {key: value for key, value in payload.items() if value is not None}


def build_file_metadata_text(
    file_id: Any,
    file_info: dict[str, Any] | None,
    *,
    native_context_included: bool | None = None,
    model_context_representation: str | None = None,
    text_content_included: bool | None = None,
    provider_supported_image_mime_types: Any = None,
) -> str:
    """Build file metadata text for tool calls."""
    payload = build_file_metadata_payload(
        file_id,
        file_info,
        native_context_included=native_context_included,
        model_context_representation=model_context_representation,
        text_content_included=text_content_included,
        provider_supported_image_mime_types=provider_supported_image_mime_types,
    )
    return f"Metadata of the file: {json.dumps(payload, ensure_ascii=False)}"


def _resolve_tool_overrides(base_tools: list, override_tools: list, override_supplied: bool) -> list:
    """Return the final tool payload honoring overrides when supplied."""
    if not override_supplied:
        return base_tools

    if not override_tools:
        return []

    if not base_tools:
        return []

    base_lookup: dict[str, Any] = {}
    for entry in base_tools:
        name = _extract_tool_name(entry)
        if name and name not in base_lookup:
            base_lookup[name] = entry

    resolved: list = []
    seen: set[str] = set()
    for entry in override_tools:
        name = _extract_tool_name(entry)
        if not name or name in seen:
            continue
        if name in base_lookup:
            resolved.append(deepcopy(base_lookup[name]))
            seen.add(name)

    return resolved


def merge_settings(db_model_settings, override_settings, schema_keys, db_tools: list | None = None):
    """Merge DB model settings with overrides and filter tools based on overrides."""
    if db_model_settings is None:
        db_model_settings = {}
    override = override_settings if isinstance(override_settings, dict) else {}
    if isinstance(override.get("settings"), dict):
        flattened_override = dict(override)
        nested = flattened_override.pop("settings")
        flattened_override.update(nested)
        override = flattened_override
    db_settings = db_model_settings if isinstance(db_model_settings, dict) else {}

    if isinstance(schema_keys, dict):
        key_iterable = schema_keys.keys()
    elif hasattr(schema_keys, "__iter__") and not isinstance(schema_keys, str):
        key_iterable = schema_keys
    elif schema_keys is None:
        key_iterable = []
    else:
        key_iterable = [schema_keys]

    merged: dict[str, Any] = {}
    for key in key_iterable:
        if key in override and override.get(key) is not None:
            merged[key] = override.get(key)
        elif key in db_settings and db_settings.get(key) is not None:
            merged[key] = db_settings.get(key)
        else:
            merged[key] = None

    # Model-level MCP allowlists must never be expanded by user overrides.
    db_allowed_mcp_servers = db_settings.get("allowed_mcp_servers")
    if isinstance(db_allowed_mcp_servers, (list, tuple, set)):
        merged["allowed_mcp_servers"] = [str(item).strip() for item in db_allowed_mcp_servers if str(item).strip()]
    elif db_allowed_mcp_servers is None:
        merged.pop("allowed_mcp_servers", None)
    if "allow_custom_user_mcp_servers" in db_settings:
        merged["allow_custom_user_mcp_servers"] = bool(db_settings.get("allow_custom_user_mcp_servers"))

    # Carry only administrator-configured per-tool settings through to tool
    # execution. These settings are outside provider model schemas and can
    # influence privileged media generation tools that use server credentials,
    # so request-level custom settings must not override them.
    db_tool_settings = db_settings.get("tool_settings")
    if isinstance(db_tool_settings, dict):
        merged["tool_settings"] = deepcopy(db_tool_settings)

    # Carry conversation-scoped MCP server selections through to runtime even
    # though they are not persisted in provider-specific model schemas.
    override_mcp_servers = override.get("enabled_mcp_servers")
    db_mcp_servers = db_settings.get("enabled_mcp_servers")
    if isinstance(override_mcp_servers, (list, tuple, set)):
        merged["enabled_mcp_servers"] = [str(item).strip() for item in override_mcp_servers if str(item).strip()]
    elif isinstance(db_mcp_servers, (list, tuple, set)):
        merged["enabled_mcp_servers"] = [str(item).strip() for item in db_mcp_servers if str(item).strip()]

    # This key is injected only after the authenticated chat request has
    # resolved every target. It is not a provider generation parameter; it
    # travels with the merged settings solely so every provider's common tool
    # dispatcher can enforce the same per-generation Subagent allowlist.
    from app.tools.subagents.schemas import SUBAGENT_RUNTIME_TARGETS_SETTING

    if SUBAGENT_RUNTIME_TARGETS_SETTING in override:
        raw_subagent_targets = override.get(SUBAGENT_RUNTIME_TARGETS_SETTING)
        merged[SUBAGENT_RUNTIME_TARGETS_SETTING] = deepcopy(raw_subagent_targets) if isinstance(raw_subagent_targets, list) else []

    base_tools = _coerce_tool_entries(db_tools)
    override_supplied = False
    override_tools_payload = None
    if isinstance(override, dict):
        if "enabled_tools" in override:
            override_supplied = True
            override_tools_payload = override.get("enabled_tools")
        elif "tools" in override:
            override_supplied = True
            override_tools_payload = override.get("tools")
    override_tools = _coerce_tool_entries(override_tools_payload) if override_supplied else []
    resolved_tools = _resolve_tool_overrides(base_tools, override_tools, override_supplied)

    return merged, resolved_tools


def format_meta_timestamp(dt: datetime | None = None) -> str:
    """Return a UTC timestamp string suitable for metadata blobs."""
    moment = dt or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def coerce_allow_custom_flag(value) -> bool:
    """Normalize allow_custom_generation_parameter flags from model settings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


FILE_BLOCK_TOOL_NAMES = {
    "image_generation",
    "video_generation",
    "audio_generation",
    "music_generation",
    "code_execution",
    "canvas",
    "slide_presentation",
    # A subagent can invoke any of the artifact-producing tools above. Its
    # collected file IDs must be stored in the same durable file-block shape as
    # files produced directly by the parent model.
    "subagent",
}

def should_persist_files_in_file_block(tool_name: str | None) -> bool:
    """Check if files should be persisted for a given tool."""
    return bool(tool_name and str(tool_name).strip() in FILE_BLOCK_TOOL_NAMES)


def _normalize_widget_model_context_value(value: Any) -> Any:
    """Normalize widget model-context values into JSON-friendly Python objects."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return deepcopy(value)
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value
        try:
            return json.loads(stripped)
        except Exception:
            return value
    try:
        json.dumps(value, ensure_ascii=False)
        return deepcopy(value)
    except Exception:
        return str(value)


def extract_widget_model_context(widget_data: dict | None) -> Any:
    """Extract the structured model-context payload for a widget, if present."""
    widget_payload = widget_data if isinstance(widget_data, dict) else {}
    widget_type = str(widget_payload.get("type") or "").strip()

    if "model_context" in widget_payload:
        return _normalize_widget_model_context_value(widget_payload.get("model_context"))

    if widget_type == "mcp_app" and isinstance(widget_payload.get("app"), dict):
        tool_result = widget_payload["app"].get("tool_result")
        if isinstance(tool_result, dict):
            if tool_result.get("structuredContent") is not None:
                return _normalize_widget_model_context_value(tool_result.get("structuredContent"))
            if tool_result.get("content") is not None:
                return _normalize_widget_model_context_value(tool_result.get("content"))

    return None


def sanitize_tool_result_content_for_persistence(
    tool_name: str | None,
    content: Any,
    widget_data: dict | None = None,
) -> Any:
    """Choose the payload that should represent this tool result in persisted history."""
    widget_model_context = extract_widget_model_context(widget_data)
    if widget_model_context is not None:
        return widget_model_context
    return content


def stringify_tool_result_content_for_persistence(
    tool_name: str | None,
    content: Any,
    widget_data: dict | None = None,
) -> str:
    """Serialize a persisted tool result payload into the stored block text format."""
    sanitized = sanitize_tool_result_content_for_persistence(tool_name, content, widget_data)
    if sanitized is None:
        return ""
    if isinstance(sanitized, str):
        return sanitized
    try:
        return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(sanitized)


def stringify_tool_result_content_for_model(
    content: Any,
    fallback: Any = None,
) -> str:
    """Serialize the immediate tool output that the calling model must consume.

    Persisted widget context is deliberately compact, while the active model
    turn may need richer output to continue its answer. Keeping these two
    representations separate prevents a long-running tool from returning only
    display metadata to the assistant that invoked it.
    """

    value = content if content not in (None, "") else fallback
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def build_widget_block_meta(
    widget_data: dict | None,
    *,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    tool_namespace: str | None = None,
) -> dict[str, Any]:
    """Build widget block metadata used by frontend widget renderers."""
    widget_payload = widget_data if isinstance(widget_data, dict) else {}
    meta: dict[str, Any] = {
        "widget_type": widget_payload.get("type", "unknown"),
    }
    render_mode = str(widget_payload.get("render_mode") or "").strip().lower()
    if render_mode:
        meta["render_mode"] = render_mode
    if isinstance(widget_payload.get("allow_scripts"), bool):
        meta["allow_scripts"] = bool(widget_payload.get("allow_scripts"))
    if meta["widget_type"] == "visualization" and isinstance(widget_payload.get("visualization"), dict):
        # The visualization metadata contains only bounded presentation and
        # capability fields produced by the server-side validator. Persist it
        # with the widget so restored, shared, and exported chats retain the
        # same normal/wide mode and permission boundary.
        meta["visualization"] = deepcopy(widget_payload["visualization"])
    widget_model_context = extract_widget_model_context(widget_payload)
    if widget_model_context is not None:
        meta["tool_result"] = widget_model_context
    normalized_tool_name = _normalize_tool_name(tool_name)
    if normalized_tool_name:
        meta["tool_name"] = normalized_tool_name
    if isinstance(tool_call_id, str) and tool_call_id.strip():
        meta["tool_call_id"] = tool_call_id.strip()
    if isinstance(tool_namespace, str) and tool_namespace.strip():
        meta["tool_namespace"] = tool_namespace.strip()
    if meta["widget_type"] == "mcp_app" and isinstance(widget_payload.get("app"), dict):
        meta["mcp_app"] = deepcopy(widget_payload["app"])
    return meta


def build_tool_file_block(
    *,
    tool_name: str | None,
    tool_label: str | None,
    documents: list | None = None,
    images: list | None = None,
    videos: list | None = None,
    audios: list | None = None,
) -> dict | None:
    """Build a tool file block with documents, images, videos, and audios."""
    docs = list(dict.fromkeys(documents or []))
    imgs = list(dict.fromkeys(images or []))
    vids = list(dict.fromkeys(videos or []))
    aus = list(dict.fromkeys(audios or []))
    if not any((docs, imgs, vids, aus)):
        return None

    block: dict[str, Any] = {
        "type": "file",
        "documents": docs or None,
        "images": imgs or None,
        "videos": vids or None,
        "audios": aus or None,
    }

    meta: dict[str, Any] = {}
    if tool_name:
        meta["tool_name"] = tool_name
    if tool_label:
        meta["tool_label"] = tool_label
    if meta:
        block["meta"] = meta

    return block
