"""Backward-compatible ACP runtime API.

Focused modules own prompt construction, session controls, turn execution,
metadata normalization, and model discovery. This facade preserves imports.
"""

# ruff: noqa: F401, E402

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
import contextlib
import logging
from pathlib import Path
import os
from typing import Any
from urllib.parse import quote

from acp import (
    PROTOCOL_VERSION,
    audio_block,
    embedded_blob_resource,
    embedded_text_resource,
    image_block,
    resource_block,
    spawn_agent_process,
    text_block,
)
from acp.schema import (
    AllowedOutcome,
    BooleanConfigOptionCapabilities,
    ClientCapabilities,
    ClientSessionCapabilities,
    DeniedOutcome,
    Implementation,
    RequestPermissionResponse,
    SessionConfigOptionsCapabilities,
)

from app.llm.acp.permissions import acp_permission_registry


EventCallback = Callable[[dict[str, Any]], None]
CancelledCallback = Callable[[], bool]
logger = logging.getLogger(__name__)


def _text_fingerprint(value: str) -> dict[str, Any]:
    """Describe sensitive text without writing its contents to application logs."""
    encoded = str(value or "").encode("utf-8")
    return {
        "characters": len(str(value or "")),
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _log_acp_step(
    generation_id: str,
    step: str,
    event: str,
    **details: Any,
) -> None:
    """Write one machine-searchable ACP lifecycle record.

    ACP traffic can contain prompts, credentials, source code, and tool output.
    Callers therefore pass only identifiers, counts, types, timings, and hashes.
    """
    payload = {
        "component": "acp",
        "generation_id": str(generation_id or ""),
        "step": step,
        "event": event,
        **details,
    }
    logger.info("[ACP] %s", json.dumps(payload, sort_keys=True, default=str))


def _block_summary(blocks: list[Any]) -> list[dict[str, Any]]:
    """Return safe per-block diagnostics for an outbound ACP prompt."""
    summary: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        payload = _protocol_payload(block)
        block_type = str(payload.get("type") or type(block).__name__)
        item: dict[str, Any] = {"index": index, "type": block_type}
        text = payload.get("text")
        if isinstance(text, str):
            item.update(_text_fingerprint(text))
        data = payload.get("data")
        if isinstance(data, str):
            item["encoded_characters"] = len(data)
        resource = payload.get("resource")
        if isinstance(resource, dict):
            item["resource_type"] = str(resource.get("type") or "")
            item["mime_type"] = str(resource.get("mimeType") or "")
            resource_text = resource.get("text")
            if isinstance(resource_text, str):
                item["resource_text"] = _text_fingerprint(resource_text)
            resource_blob = resource.get("blob")
            if isinstance(resource_blob, str):
                item["resource_encoded_characters"] = len(resource_blob)
        summary.append(item)
    return summary


def omlorix_client_capabilities(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import prompts as _implementation

    _implementation._sync_compat_dependencies("omlorix_client_capabilities", globals())
    return _implementation._impl_omlorix_client_capabilities(*args, **kwargs)


def _validate_protocol_version(response: Any) -> None:
    """Reject initialize responses that select an incompatible ACP version."""
    selected = getattr(response, "protocol_version", None)
    try:
        compatible = int(selected) == int(PROTOCOL_VERSION)
    except (TypeError, ValueError):
        compatible = False
    if not compatible:
        raise RuntimeError(
            f"ACP protocol version mismatch: agent selected {selected}, "
            f"Omlorix supports {PROTOCOL_VERSION}"
        )


def _attachment_uri(file_id: str) -> str:
    """Return an opaque non-network URI for an embedded Omlorix attachment."""
    return f"omlorix://files/{quote(str(file_id or ''), safe='')}"


def _attachment_metadata_text(
    attachment: dict[str, Any],
    *,
    representation: str,
) -> str:
    """Describe an attachment without exposing storage paths or owner details."""
    payload = {
        "name": str(attachment.get("name") or "attachment"),
        "mime_type": str(attachment.get("mime_type") or "application/octet-stream"),
        "category": str(attachment.get("category") or "unknown"),
        "size": attachment.get("size"),
        "representation": representation,
    }
    omission_reason = str(attachment.get("omission_reason") or "").strip()
    if omission_reason:
        payload["note"] = omission_reason
    return "[Attached file]\n" + json.dumps(
        {key: value for key, value in payload.items() if value not in (None, "")},
        ensure_ascii=False,
    )


def attachment_has_usable_acp_representation(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import prompts as _implementation

    _implementation._sync_compat_dependencies(
        "attachment_has_usable_acp_representation", globals()
    )
    return _implementation._impl_attachment_has_usable_acp_representation(
        *args, **kwargs
    )


def build_acp_prompt_blocks(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import prompts as _implementation

    _implementation._sync_compat_dependencies("build_acp_prompt_blocks", globals())
    return _implementation._impl_build_acp_prompt_blocks(*args, **kwargs)


def _protocol_payload(value: Any) -> dict[str, Any]:
    """Convert an ACP SDK response into a plain alias-keyed mapping."""
    if hasattr(value, "model_dump"):
        payload = value.model_dump(by_alias=True, exclude_none=True)
    elif isinstance(value, dict):
        payload = value
    else:
        return {}
    return payload if isinstance(payload, dict) else {}


def _usage_int(payload: dict[str, Any], *keys: str) -> int | None:
    """Read one non-negative integer, distinguishing absence from a valid zero."""
    for key in keys:
        if key not in payload:
            continue
        try:
            value = payload.get(key)
            if value is None:
                continue
            return max(int(value), 0)
        except (TypeError, ValueError):
            continue
    return None


def normalize_acp_completion_metadata(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import metadata as _implementation

    _implementation._sync_compat_dependencies(
        "normalize_acp_completion_metadata", globals()
    )
    return _implementation._impl_normalize_acp_completion_metadata(*args, **kwargs)


def resolve_workspace_path(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import session_controls as _implementation

    _implementation._sync_compat_dependencies("resolve_workspace_path", globals())
    return _implementation._impl_resolve_workspace_path(*args, **kwargs)


def resolve_additional_directories(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import session_controls as _implementation

    _implementation._sync_compat_dependencies(
        "resolve_additional_directories", globals()
    )
    return _implementation._impl_resolve_additional_directories(*args, **kwargs)


def build_process_environment(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import session_controls as _implementation

    _implementation._sync_compat_dependencies("build_process_environment", globals())
    return _implementation._impl_build_process_environment(*args, **kwargs)


def _field(value: Any, name: str, alias: str | None = None, default: Any = None) -> Any:
    """Read one ACP field from SDK models or alias-keyed dictionaries."""
    if isinstance(value, dict):
        if name in value:
            return value[name]
        if alias and alias in value:
            return value[alias]
        return default
    return getattr(value, name, default)


def _flatten_select_options(options: Any) -> list[dict[str, str | None]]:
    """Return browser-safe values from flat or grouped ACP select options."""
    flattened: list[dict[str, str | None]] = []
    for option in options if isinstance(options, list) else []:
        nested = _field(option, "options")
        if isinstance(nested, list):
            flattened.extend(_flatten_select_options(nested))
            continue
        value = str(_field(option, "value", default="") or "").strip()
        if not value:
            continue
        flattened.append(
            {
                "id": value,
                "name": str(_field(option, "name", default="") or value),
                "description": (
                    str(_field(option, "description", default="") or "").strip() or None
                ),
            }
        )
    return flattened


def _select_control(
    config_options: Any,
    *,
    categories: set[str],
    id_hints: set[str],
) -> dict[str, Any] | None:
    """Find one semantic ACP select control without requiring category metadata."""
    candidates = config_options if isinstance(config_options, list) else []
    for require_category in (True, False):
        for option in candidates:
            option_type = str(_field(option, "type", default="") or "").lower()
            category = str(_field(option, "category", default="") or "").lower()
            option_id = str(_field(option, "id", default="") or "").strip()
            if option_type != "select":
                continue
            category_match = category in categories
            hint_match = option_id.lower() in id_hints
            if (require_category and not category_match) or (
                not require_category and not hint_match
            ):
                continue
            values = _flatten_select_options(_field(option, "options"))
            if not values:
                continue
            current_value = str(
                _field(option, "current_value", "currentValue", "") or ""
            ).strip()
            return {
                "config_id": option_id,
                "name": str(_field(option, "name", default="") or option_id),
                "description": (
                    str(_field(option, "description", default="") or "").strip() or None
                ),
                "current_value": current_value or values[0]["id"],
                "options": values,
                "transport": "config_option",
            }
    return None


def _legacy_mode_control(response: Any) -> dict[str, Any] | None:
    """Normalize the deprecated protocol-level mode selector when still used."""
    modes = _field(response, "modes")
    available_modes = _field(modes, "available_modes", "availableModes", [])
    values = []
    for mode in available_modes if isinstance(available_modes, list) else []:
        value = str(_field(mode, "id", default="") or "").strip()
        if not value:
            continue
        values.append(
            {
                "id": value,
                "name": str(_field(mode, "name", default="") or value),
                "description": (
                    str(_field(mode, "description", default="") or "").strip() or None
                ),
            }
        )
    if not values:
        return None
    current_value = str(
        _field(modes, "current_mode_id", "currentModeId", "") or ""
    ).strip()
    return {
        "config_id": None,
        "name": "Security level",
        "description": None,
        "current_value": current_value or values[0]["id"],
        "options": values,
        "transport": "session_mode",
    }


def _split_model_variant(value: str) -> tuple[str, str] | None:
    """Split Codex-style ``model[effort]`` values used by older ACP agents."""
    normalized = str(value or "").strip()
    if not normalized.endswith("]") or "[" not in normalized:
        return None
    model_id, effort = normalized.rsplit("[", 1)
    model_id = model_id.strip()
    effort = effort[:-1].strip()
    return (model_id, effort) if model_id and effort else None


def _model_name_without_effort(name: str, effort: str) -> str:
    """Remove a trailing parenthesized reasoning label from a model name."""
    normalized = str(name or "").strip()
    suffix = f"({effort})"
    if normalized.lower().endswith(suffix.lower()):
        return normalized[: -len(suffix)].rstrip()
    return normalized


def _common_model_description(descriptions: list[str]) -> str | None:
    """Derive the stable model description shared by all effort variants."""
    values = [value.strip() for value in descriptions if value and value.strip()]
    if not values:
        return None
    prefix = os.path.commonprefix(values).rstrip()
    sentence_end = prefix.rfind(".")
    if sentence_end >= 0:
        return prefix[: sentence_end + 1]
    return values[0]


def _separate_combined_model_control(
    model_control: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Expose combined Codex model/effort variants as two independent controls."""
    parsed_options: list[tuple[dict[str, Any], str, str]] = []
    for option in model_control.get("options") or []:
        parsed = _split_model_variant(str(option.get("id") or ""))
        if not parsed:
            return None
        parsed_options.append((option, parsed[0], parsed[1]))
    if len({effort for _option, _model, effort in parsed_options}) < 2:
        return None

    models_by_id: dict[str, list[dict[str, Any]]] = {}
    efforts: dict[str, dict[str, Any]] = {}
    efforts_by_model: dict[str, list[str]] = {}
    variants: dict[str, str] = {}
    for option, model_id, effort in parsed_options:
        models_by_id.setdefault(model_id, []).append(option)
        supported_efforts = efforts_by_model.setdefault(model_id, [])
        if effort not in supported_efforts:
            supported_efforts.append(effort)
        efforts.setdefault(
            effort,
            {
                "id": effort,
                "name": effort,
                "description": None,
            },
        )
        variants[f"{model_id}\0{effort}"] = str(option["id"])

    models = []
    for model_id, variants_for_model in models_by_id.items():
        first = variants_for_model[0]
        first_effort = _split_model_variant(str(first["id"]))[1]
        models.append(
            {
                "id": model_id,
                "name": _model_name_without_effort(str(first["name"]), first_effort)
                or model_id,
                "description": _common_model_description(
                    [
                        str(variant.get("description") or "")
                        for variant in variants_for_model
                    ]
                ),
            }
        )

    current_variant = _split_model_variant(
        str(model_control.get("current_value") or "")
    )
    current_model = current_variant[0] if current_variant else models[0]["id"]
    current_effort = current_variant[1] if current_variant else next(iter(efforts))
    separated_model = {
        **model_control,
        "current_value": current_model,
        "options": models,
        "combined_variants": variants,
        "reasoning_efforts_by_model": efforts_by_model,
    }
    reasoning = {
        "config_id": model_control["config_id"],
        "name": "Reasoning effort",
        "description": None,
        "current_value": current_effort,
        "options": list(efforts.values()),
        "transport": "combined_model",
        "combined_variants": variants,
        "reasoning_efforts_by_model": efforts_by_model,
    }
    return separated_model, reasoning


def extract_acp_session_controls(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import session_controls as _implementation

    _implementation._sync_compat_dependencies("extract_acp_session_controls", globals())
    return _implementation._impl_extract_acp_session_controls(*args, **kwargs)


def serialize_acp_session_controls(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import session_controls as _implementation

    _implementation._sync_compat_dependencies(
        "serialize_acp_session_controls", globals()
    )
    return _implementation._impl_serialize_acp_session_controls(*args, **kwargs)


def extract_model_config_option(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import session_controls as _implementation

    _implementation._sync_compat_dependencies("extract_model_config_option", globals())
    return _implementation._impl_extract_model_config_option(*args, **kwargs)


class OmlorixAcpClient:
    """ACP client callbacks that translate agent activity into Omlorix stream events."""

    def __init__(
        self,
        *,
        emit: EventCallback,
        user_id: str,
        generation_id: str,
        permission_mode: str,
        permission_timeout_seconds: int,
    ) -> None:
        self.emit = emit
        self.user_id = str(user_id)
        self.generation_id = str(generation_id)
        self.permission_mode = str(permission_mode or "ask")
        self.permission_timeout_seconds = int(permission_timeout_seconds)
        self.suppress_session_updates = False
        self.prompt_started_at: float | None = None
        self.first_output_at: float | None = None

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        """Forward a normalized ACP session update to the provider stream adapter."""
        if self.suppress_session_updates:
            _log_acp_step(
                self.generation_id,
                "receive",
                "session_update_suppressed_during_resume",
                session_id=session_id,
            )
            return
        payload = (
            update.model_dump(by_alias=True, exclude_none=True)
            if hasattr(update, "model_dump")
            else update
        )
        if isinstance(payload, dict):
            update_type = str(
                payload.get("sessionUpdate") or payload.get("session_update") or ""
            )
            if (
                self.prompt_started_at is not None
                and self.first_output_at is None
                and update_type in {"agent_message_chunk", "agent_thought_chunk"}
            ):
                self.first_output_at = asyncio.get_running_loop().time()
            content = payload.get("content")
            content_type = (
                str(content.get("type") or "") if isinstance(content, dict) else ""
            )
            content_text = (
                content.get("text")
                if isinstance(content, dict) and isinstance(content.get("text"), str)
                else ""
            )
            _log_acp_step(
                self.generation_id,
                "receive",
                "session_update",
                session_id=session_id,
                update_type=update_type,
                content_type=content_type,
                content_text=(
                    _text_fingerprint(content_text) if content_text else None
                ),
                tool_call_id=str(
                    payload.get("toolCallId") or payload.get("tool_call_id") or ""
                ),
                status=str(payload.get("status") or ""),
            )
        self.emit(
            {"kind": "session_update", "session_id": session_id, "update": payload}
        )

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: list[Any],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        """Apply the configured policy or wait for an authenticated UI decision."""
        option_payloads = [
            option.model_dump(by_alias=True, exclude_none=True)
            if hasattr(option, "model_dump")
            else dict(option)
            for option in options
        ]
        allow_once_option = next(
            (
                option
                for option in option_payloads
                if str(option.get("kind", "")) == "allow_once"
            ),
            None,
        )
        tool_payload = (
            tool_call.model_dump(by_alias=True, exclude_none=True)
            if hasattr(tool_call, "model_dump")
            else dict(tool_call)
        )
        _log_acp_step(
            self.generation_id,
            "receive",
            "permission_request",
            session_id=session_id,
            tool_call_id=str(
                tool_payload.get("toolCallId") or tool_payload.get("tool_call_id") or ""
            ),
            tool_title=str(tool_payload.get("title") or ""),
            option_ids=[
                str(option.get("optionId") or "") for option in option_payloads
            ],
            permission_mode=self.permission_mode,
        )

        if self.permission_mode == "allow" and allow_once_option:
            _log_acp_step(
                self.generation_id,
                "send",
                "permission_auto_allowed",
                session_id=session_id,
                option_id=str(allow_once_option["optionId"]),
            )
            return RequestPermissionResponse(
                outcome=AllowedOutcome(
                    outcome="selected", option_id=allow_once_option["optionId"]
                )
            )
        if self.permission_mode == "deny" or (
            self.permission_mode == "allow" and allow_once_option is None
        ):
            _log_acp_step(
                self.generation_id,
                "send",
                "permission_auto_denied",
                session_id=session_id,
            )
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

        ticket = acp_permission_registry.create(
            user_id=self.user_id,
            generation_id=self.generation_id,
            options=option_payloads,
            timeout_seconds=self.permission_timeout_seconds,
        )
        self.emit(
            {
                "kind": "permission",
                "permission_id": ticket.permission_id,
                "session_id": session_id,
                "tool_call": tool_payload,
                "options": option_payloads,
            }
        )
        try:
            selected_option_id = await acp_permission_registry.wait(
                ticket,
                timeout_seconds=self.permission_timeout_seconds,
            )
            if selected_option_id:
                _log_acp_step(
                    self.generation_id,
                    "send",
                    "permission_user_selected",
                    session_id=session_id,
                    permission_id=ticket.permission_id,
                    option_id=selected_option_id,
                )
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(
                        outcome="selected", option_id=selected_option_id
                    )
                )
            _log_acp_step(
                self.generation_id,
                "send",
                "permission_cancelled_or_timed_out",
                session_id=session_id,
                permission_id=ticket.permission_id,
            )
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        finally:
            acp_permission_registry.discard(ticket.permission_id)

    async def write_text_file(self, *args: Any, **kwargs: Any) -> None:
        """Filesystem methods are intentionally not advertised by the web client."""
        return None

    async def read_text_file(self, *args: Any, **kwargs: Any) -> Any:
        """Filesystem methods are intentionally not advertised by the web client."""
        raise RuntimeError("Omlorix does not expose its server filesystem through ACP")

    async def create_terminal(self, *args: Any, **kwargs: Any) -> None:
        """Terminal methods are intentionally not advertised by the web client."""
        return None

    async def terminal_output(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def release_terminal(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def wait_for_terminal_exit(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def kill_terminal(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def create_elicitation(self, *args: Any, **kwargs: Any) -> Any:
        """Reject unstable elicitation requests until Omlorix has a typed form UI."""
        from acp.schema import CancelElicitationResponse

        return CancelElicitationResponse(action="cancel")

    async def complete_elicitation(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    def on_connect(self, conn: Any) -> None:
        """The SDK invokes this hook after the JSON-RPC connection is ready."""
        _log_acp_step(
            self.generation_id,
            "transport",
            "json_rpc_connected",
            connection_type=type(conn).__name__,
        )
        return None


async def run_acp_turn(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import turn as _implementation

    _implementation._sync_compat_dependencies("run_acp_turn", globals())
    return await _implementation._impl_run_acp_turn(*args, **kwargs)


async def _run_connected_acp_turn(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import turn as _implementation

    _implementation._sync_compat_dependencies("_run_connected_acp_turn", globals())
    return await _implementation._impl__run_connected_acp_turn(*args, **kwargs)


async def test_acp_connection(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("test_acp_connection", globals())
    return await _implementation._impl_test_acp_connection(*args, **kwargs)


async def discover_acp_models(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("discover_acp_models", globals())
    return await _implementation._impl_discover_acp_models(*args, **kwargs)
