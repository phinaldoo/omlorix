"""OpenRouter Responses API request and streaming protocol helpers.

OpenRouter exposes an OpenResponses-compatible endpoint, but its model catalog
continues to advertise a few Chat Completions capability names such as
``max_tokens`` and ``response_format``.  This module owns that translation and
keeps raw SSE event bookkeeping out of the already large provider runtime.
"""

from __future__ import annotations

import copy
from collections import OrderedDict
from typing import Any


# These parameters exist at the top level of OpenRouter's Responses request
# schema and can be forwarded without changing their shape.
_DIRECT_RESPONSE_PARAMETERS = {
    "frequency_penalty",
    "parallel_tool_calls",
    "presence_penalty",
    "temperature",
    "top_k",
    "top_p",
}

_REASONING_PARAMETERS = {
    "context",
    "effort",
    "enabled",
    "max_tokens",
    "mode",
    "summary",
}


def _configured_supported_parameters(settings: dict[str, Any]) -> set[str] | None:
    """Return the model-catalog parameter allowlist when one was supplied."""
    raw = settings.get("supported_parameters")
    if not isinstance(raw, (list, tuple, set)):
        return None
    normalized = {
        str(item).strip().lower()
        for item in raw
        if isinstance(item, str) and str(item).strip()
    }
    return normalized or None


def _parameter_allowed(name: str, supported: set[str] | None) -> bool:
    """Treat an absent catalog allowlist as unknown rather than unsupported."""
    return supported is None or name in supported


def _normalize_text_format(value: Any) -> dict[str, Any] | None:
    """Translate Chat Completions ``response_format`` into Responses ``text``.

    Chat Completions nests JSON Schema details below ``json_schema``. Responses
    places ``name``, ``schema``, ``description``, and ``strict`` directly below
    ``text.format``. Plain text and JSON-object configurations already share
    the same shape.
    """
    if not isinstance(value, dict):
        return None

    format_type = str(value.get("type") or "").strip()
    if format_type in {"text", "json_object"}:
        return {"type": format_type}
    if format_type != "json_schema":
        return None

    nested = value.get("json_schema")
    source = nested if isinstance(nested, dict) else value
    name = source.get("name")
    schema = source.get("schema")
    if not isinstance(name, str) or not name.strip() or not isinstance(schema, dict):
        return None

    result: dict[str, Any] = {
        "type": "json_schema",
        "name": name.strip(),
        "schema": copy.deepcopy(schema),
    }
    for key in ("description", "strict"):
        if source.get(key) is not None:
            result[key] = copy.deepcopy(source[key])
    return result


def _normalize_tool_choice(value: Any) -> str | dict[str, Any] | None:
    """Translate a Chat Completions function choice to Responses shape."""
    if isinstance(value, str):
        normalized = value.strip()
        return normalized if normalized in {"auto", "none", "required"} else None
    if not isinstance(value, dict):
        return None

    normalized = copy.deepcopy(value)
    function = normalized.get("function")
    if normalized.get("type") == "function" and isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str) and name.strip():
            return {"type": "function", "name": name.strip()}
        return None
    return normalized


def apply_openrouter_responses_settings(
    payload: dict[str, Any],
    settings: dict[str, Any] | None,
) -> None:
    """Apply saved OpenRouter model settings using Responses wire names.

    Unsupported Chat Completions-only fields are intentionally omitted. Sending
    arbitrary catalog capabilities to ``/responses`` makes the whole request
    fail with ``invalid_prompt`` on strict routes.
    """
    if not isinstance(settings, dict):
        return

    supported = _configured_supported_parameters(settings)
    for key in _DIRECT_RESPONSE_PARAMETERS:
        value = settings.get(key)
        if value is None or not _parameter_allowed(key, supported):
            continue
        payload[key] = copy.deepcopy(value)

    if _parameter_allowed("tool_choice", supported):
        tool_choice = _normalize_tool_choice(settings.get("tool_choice"))
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    # OpenRouter's model catalog calls this capability ``max_tokens`` while the
    # Responses request schema calls the actual field ``max_output_tokens``.
    max_tokens = settings.get("max_output_tokens")
    if max_tokens is None:
        max_tokens = settings.get("max_tokens")
    if max_tokens is not None and _parameter_allowed("max_tokens", supported):
        try:
            normalized_max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            normalized_max_tokens = 0
        if normalized_max_tokens > 0:
            payload["max_output_tokens"] = normalized_max_tokens

    text_config = copy.deepcopy(payload.get("text")) if isinstance(payload.get("text"), dict) else {}
    if _parameter_allowed("response_format", supported):
        normalized_format = _normalize_text_format(settings.get("response_format"))
        if normalized_format:
            text_config["format"] = normalized_format

    verbosity = settings.get("verbosity")
    if verbosity not in (None, "") and _parameter_allowed("verbosity", supported):
        text_config["verbosity"] = str(verbosity).strip()
    if text_config:
        payload["text"] = text_config

    reasoning: dict[str, Any] = {}
    configured_reasoning = settings.get("reasoning")
    if isinstance(configured_reasoning, dict):
        reasoning.update(
            {
                key: copy.deepcopy(value)
                for key, value in configured_reasoning.items()
                if key in _REASONING_PARAMETERS and value is not None
            }
        )

    if "reasoning_enabled" in settings:
        reasoning["enabled"] = bool(settings.get("reasoning_enabled"))
    if settings.get("reasoning_effort") not in (None, ""):
        reasoning["effort"] = settings["reasoning_effort"]
    if settings.get("reasoning_max_tokens") is not None:
        reasoning["max_tokens"] = settings["reasoning_max_tokens"]

    if reasoning and _parameter_allowed("reasoning", supported):
        payload["reasoning"] = reasoning


def extract_openrouter_response_usage(event: Any) -> dict[str, Any]:
    """Return usage from a raw response object or an SSE event envelope."""
    if not isinstance(event, dict):
        return {}
    usage = event.get("usage")
    if isinstance(usage, dict):
        return usage
    response = event.get("response")
    if isinstance(response, dict) and isinstance(response.get("usage"), dict):
        return response["usage"]
    return {}


def extract_openrouter_response_error(event: Any) -> dict[str, Any] | None:
    """Normalize pre-stream and terminal Responses failures.

    OpenRouter places terminal errors inside the event's ``response`` object and
    preserves its more precise provider category in ``error_type``. Pre-stream
    failures use the same error object at the top level.
    """
    if not isinstance(event, dict):
        return None

    response = event.get("response") if isinstance(event.get("response"), dict) else None
    source = response or event
    status = str(source.get("status") or "").strip().lower()
    event_type = str(event.get("type") or "").strip().lower()
    error = source.get("error")
    if not error and status != "failed" and event_type != "response.failed":
        return None

    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message") or code
    else:
        code = None
        message = error
    error_type = source.get("error_type") or event.get("error_type") or code or "server_error"
    return {
        "code": str(code or error_type),
        "error_type": str(error_type),
        "message": str(message or "OpenRouter response failed"),
        "status": status or "failed",
    }


def openrouter_response_error_http_status(
    error: dict[str, Any] | None,
    *,
    default: int = 502,
) -> int:
    """Map a Responses error category to a useful HTTP-compatible status."""
    if not isinstance(error, dict):
        return default

    code = str(error.get("code") or "").strip()
    if code.isdigit():
        status_code = int(code)
        if 400 <= status_code <= 599:
            return status_code

    error_type = str(error.get("error_type") or code).strip().lower()
    if "rate_limit" in error_type:
        return 429
    if error_type in {"invalid_prompt", "invalid_request_error"}:
        return 400
    if error_type in {"authentication_error", "invalid_api_key"}:
        return 401
    if error_type in {"permission_error", "permission_denied"}:
        return 403
    return default


def extract_openrouter_incomplete_reason(event: Any) -> str | None:
    """Return the terminal incomplete reason, if the event represents one."""
    if not isinstance(event, dict):
        return None
    response = event.get("response") if isinstance(event.get("response"), dict) else event
    status = str(response.get("status") or "").strip().lower()
    if status != "incomplete" and event.get("type") != "response.incomplete":
        return None
    details = response.get("incomplete_details")
    if isinstance(details, dict) and details.get("reason"):
        return str(details["reason"])
    return "incomplete"


class OpenRouterFunctionCallAccumulator:
    """Collect parallel function calls from canonical Responses SSE events."""

    def __init__(self) -> None:
        self._calls: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._key_by_output_index: dict[int, str] = {}

    def _resolve_key(
        self,
        *,
        item_id: Any = None,
        output_index: Any = None,
        create: bool,
    ) -> str | None:
        normalized_item_id = str(item_id).strip() if item_id not in (None, "") else ""
        normalized_index = output_index if isinstance(output_index, int) else None

        if normalized_item_id and normalized_item_id in self._calls:
            return normalized_item_id
        if normalized_index is not None and normalized_index in self._key_by_output_index:
            return self._key_by_output_index[normalized_index]
        if not create:
            return None

        key = normalized_item_id or (
            f"output:{normalized_index}" if normalized_index is not None else f"unkeyed:{len(self._calls)}"
        )
        self._calls[key] = {
            "item_id": normalized_item_id or None,
            "call_id": None,
            "name": None,
            "arguments": "",
            "output_index": normalized_index,
            "finalized": False,
            "emitted": False,
        }
        if normalized_index is not None:
            self._key_by_output_index[normalized_index] = key
        return key

    def register_item(
        self,
        item: Any,
        *,
        output_index: Any = None,
        finalized: bool = False,
    ) -> dict[str, Any] | None:
        """Register metadata carried by output-item added/done events."""
        if not isinstance(item, dict) or item.get("type") != "function_call":
            return None
        key = self._resolve_key(
            item_id=item.get("id"),
            output_index=output_index,
            create=True,
        )
        if key is None:
            return None
        state = self._calls[key]
        for state_key, item_key in (("item_id", "id"), ("call_id", "call_id"), ("name", "name")):
            value = item.get(item_key)
            if value not in (None, ""):
                state[state_key] = str(value)
        arguments = item.get("arguments")
        if isinstance(arguments, str) and (arguments or not state["arguments"]):
            state["arguments"] = arguments
        if finalized:
            state["finalized"] = True
        return state

    def register_output_event(self, event: Any, *, finalized: bool = False) -> dict[str, Any] | None:
        """Register the item contained in an output-item SSE event."""
        if not isinstance(event, dict):
            return None
        return self.register_item(
            event.get("item"),
            output_index=event.get("output_index"),
            finalized=finalized,
        )

    def append_delta(self, event: Any) -> dict[str, Any] | None:
        """Append one argument fragment without conflating parallel calls."""
        if not isinstance(event, dict):
            return None
        key = self._resolve_key(
            item_id=event.get("item_id"),
            output_index=event.get("output_index"),
            create=True,
        )
        if key is None:
            return None
        state = self._calls[key]
        delta = event.get("delta")
        if isinstance(delta, str):
            state["arguments"] += delta
        return state

    def finalize_arguments(self, event: Any) -> dict[str, Any] | None:
        """Replace deltas with the authoritative arguments-done payload."""
        if not isinstance(event, dict):
            return None
        key = self._resolve_key(
            item_id=event.get("item_id"),
            output_index=event.get("output_index"),
            create=True,
        )
        if key is None:
            return None
        state = self._calls[key]
        if event.get("item_id") not in (None, ""):
            state["item_id"] = str(event["item_id"])
        if event.get("name") not in (None, ""):
            state["name"] = str(event["name"])
        arguments = event.get("arguments")
        if isinstance(arguments, str):
            state["arguments"] = arguments
        state["finalized"] = True
        return state

    def register_completed_response(self, response: Any) -> None:
        """Use terminal output as a compatibility fallback for missing events."""
        if not isinstance(response, dict):
            return
        output = response.get("output")
        if not isinstance(output, list):
            return
        for output_index, item in enumerate(output):
            self.register_item(item, output_index=output_index, finalized=True)

    def public_state(self, state: dict[str, Any] | None) -> dict[str, Any] | None:
        """Return a copy suitable for UI streaming or provider continuation."""
        if not isinstance(state, dict):
            return None
        return {
            "item_id": state.get("item_id"),
            "call_id": state.get("call_id"),
            "name": state.get("name"),
            "arguments": state.get("arguments") or "",
            "output_index": state.get("output_index"),
            "finalized": bool(state.get("finalized")),
        }

    def finalized_calls(self) -> list[dict[str, Any]]:
        """Return finalized calls in provider order, exactly once."""
        result: list[dict[str, Any]] = []
        for state in self._calls.values():
            if not state.get("finalized") or state.get("emitted"):
                continue
            state["emitted"] = True
            public = self.public_state(state)
            if public:
                result.append(public)
        return result
