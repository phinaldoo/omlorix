from __future__ import annotations

import json
import os
from collections.abc import Sized
from typing import Any


_TRUE_VALUES = {"1", "true", "yes", "on"}


def redacted_debug_logging_enabled(flag_name: str = "OMLORIX_LOG_REDACTED_DEBUG") -> bool:
    """Return whether redacted, content-free debug logging is explicitly enabled."""
    return os.getenv(flag_name, "").strip().lower() in _TRUE_VALUES


def safe_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, Sized) and not isinstance(value, str | bytes | bytearray | dict):
        return len(value)
    return 1


def stream_line_metadata(line: str | bytes | None) -> dict[str, Any]:
    """Summarize a chat stream line without exposing event payload content."""
    if line is None:
        return {"line_length": 0, "parseable": False}

    text = line.decode("utf-8", "ignore") if isinstance(line, bytes) else str(line)
    metadata: dict[str, Any] = {
        "line_length": len(text),
        "parseable": False,
    }
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return metadata

    metadata["parseable"] = True
    if not isinstance(event, dict):
        metadata["event_kind"] = type(event).__name__
        return metadata

    event_type = event.get("t") or event.get("type")
    if event_type is not None:
        metadata["event_type"] = str(event_type)
    if "seq" in event:
        metadata["seq"] = event.get("seq")
    if "generation_id" in event:
        metadata["generation_id"] = event.get("generation_id")

    payload = event.get("d")
    if payload is not None:
        metadata["payload_kind"] = type(payload).__name__
        if isinstance(payload, str):
            metadata["payload_length"] = len(payload)
        elif isinstance(payload, Sized):
            metadata["payload_count"] = len(payload)
    return metadata


def object_event_metadata(event: Any) -> dict[str, Any]:
    """Summarize provider stream objects without serializing their content."""
    event_type = getattr(event, "type", None)
    metadata: dict[str, Any] = {
        "event_type": str(event_type or type(event).__name__),
    }

    response = getattr(event, "response", None)
    if response is not None:
        response_id = getattr(response, "id", None)
        model = getattr(response, "model", None)
        if response_id:
            metadata["response_id"] = response_id
        if model:
            metadata["model"] = model

    item = getattr(event, "item", None)
    if item is not None:
        item_type = getattr(item, "type", None)
        item_id = getattr(item, "id", None)
        call_id = getattr(item, "call_id", None)
        status = getattr(item, "status", None)
        if item_type:
            metadata["item_type"] = item_type
        if item_id:
            metadata["item_id"] = item_id
        if call_id:
            metadata["call_id"] = call_id
        if status:
            metadata["status"] = status

    delta = getattr(event, "delta", None)
    if isinstance(delta, str):
        metadata["delta_length"] = len(delta)
    return metadata


def exception_metadata(exc: BaseException | None) -> dict[str, Any]:
    """Summarize exceptions without logging their message text."""
    if exc is None:
        return {"exc_type": None}

    metadata: dict[str, Any] = {
        "exc_type": type(exc).__name__,
    }
    detail = getattr(exc, "detail", None)
    if detail is not None:
        metadata["detail_type"] = type(detail).__name__
        if isinstance(detail, str):
            metadata["detail_length"] = len(detail)
        elif isinstance(detail, Sized):
            metadata["detail_count"] = len(detail)
    return metadata
