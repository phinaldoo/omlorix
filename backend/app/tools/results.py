"""Typed tool boundary shared by chat, workers, and nested generations.

Only model_content is sent into the current round. history_receipt is the
bounded durable representation; ui_payload and files remain explicit channels.
Feature modules define receipt semantics, independent of provider protocols.
"""

from collections.abc import Mapping, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import importlib
import json
from typing import Any

_PERSISTED_TOOL_RESULT_MAX_CHARS = 64_000
_PERSISTED_TOOL_RESULT_PREVIEW_CHARS = 2_000


def _normalize_tool_name(name):
    return str(name or "").strip().removesuffix("()")


def _decode_tool_result(value: Any) -> Any:
    if not isinstance(value, str):
        return deepcopy(value)
    stripped = value.strip()
    if not stripped:
        return value
    try:
        return json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _content_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    return {
        "content_length": len(value),
        "content_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()[:16],
    }


def _copy_result_fields(
    payload: dict[str, Any], fields: tuple[str, ...]
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for field in fields:
        if field not in payload:
            continue
        value = payload[field]
        if value is None or value == "" or value == [] or value == {}:
            continue
        compact[field] = deepcopy(value)
    return compact


def _bound_persisted_tool_result(tool_name: str | None, value: Any) -> Any:
    try:
        serialized = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )
    except (TypeError, ValueError):
        serialized = str(value)
    if len(serialized) <= _PERSISTED_TOOL_RESULT_MAX_CHARS:
        return value
    return {
        "status": "result_compacted",
        "tool_name": _normalize_tool_name(tool_name),
        "original_chars": len(serialized),
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16],
        "preview": serialized[:_PERSISTED_TOOL_RESULT_PREVIEW_CHARS],
    }


_RECEIPT_MODULES = {
    "canvas": "canvas_markdown",
    "latex_pdf": "canvas_markdown",
    "notes": "notes",
    "skills": "skills",
    "todos": "todos",
    "automations": "automations",
}


def history_receipt(tool_name, content):
    name = _normalize_tool_name(tool_name)
    decoded = _decode_tool_result(content)
    module = _RECEIPT_MODULES.get(name)
    if module:
        decoded = importlib.import_module(
            f"app.tools.{module}.receipts"
        ).compact_result(decoded)
    return _bound_persisted_tool_result(name, decoded)


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    kind: str
    id: str
    revision: str | int | None = None


def _references(receipt):
    if not isinstance(receipt, dict):
        return ()
    refs = []
    if receipt.get("file_id"):
        refs.append(
            ArtifactReference(
                "file", str(receipt["file_id"]), receipt.get("canvas_revision")
            )
        )
    for kind in ("note", "skill", "todo", "todo_list", "automation"):
        item = receipt.get(kind)
        if isinstance(item, dict) and item.get("id"):
            refs.append(
                ArtifactReference(
                    kind,
                    str(item["id"]),
                    item.get("updated_at") or item.get("last_updated_at"),
                )
            )
    return tuple(refs)


@dataclass(slots=True)
class ToolResult(Mapping[str, Any]):
    model_content: Any = ""
    history_receipt: Any = None
    artifacts: tuple[ArtifactReference, ...] = ()
    ui_payload: dict | None = None
    files: dict[str, list] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    # Mapping compatibility keeps integrations working while making each
    # channel explicit. It never retains an extra full copy of the raw result.
    def __getitem__(self, key):
        if key == "content":
            return self.model_content
        if key == "result":
            return self.history_receipt
        if key == "widget":
            return self.ui_payload
        if key in ("tool_meta", "meta"):
            return self.metadata
        if key == "file_id":
            for reference in self.artifacts:
                if reference.kind == "file":
                    return reference.id
        return self.files[key]

    def __iter__(self) -> Iterator[str]:
        return iter(("content", "result", "widget", "tool_meta", *self.files))

    def __len__(self):
        return 4 + len(self.files)

    @classmethod
    def from_payload(cls, tool_name, payload):
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("Tool execution must return a result mapping")
        from app.llm.helper import extract_widget_model_context

        widget = payload.get("widget")
        widget_context = extract_widget_model_context(widget)
        receipt_source = (
            widget_context if widget_context is not None else payload.get("result")
        )
        if receipt_source in (None, ""):
            receipt_source = payload.get("content", "")
        receipt = history_receipt(tool_name, receipt_source)
        return cls(
            model_content=payload.get("content") or payload.get("result") or "",
            history_receipt=receipt,
            artifacts=_references(receipt),
            ui_payload=widget,
            files={
                key: payload.get(key) or []
                for key in (
                    "documents",
                    "images",
                    "videos",
                    "audios",
                    "youtube",
                    "webpages",
                )
            },
            metadata=payload.get("tool_meta") or payload.get("meta") or {},
        )
