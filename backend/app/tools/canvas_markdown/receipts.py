"""Feature-owned compact history representation of tool results."""

from typing import Any
from app.tools.results import _copy_result_fields, _content_metadata


def _compact_canvas_result(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    compact = _copy_result_fields(
        payload,
        (
            "status",
            "saved",
            "file_id",
            "file_name",
            "stored_file_name",
            "content_type",
            "created",
            "viewed",
            "canvas_revision",
            "page_count",
            "pdf_file_id",
            "pdf_file_name",
            "render_revision",
            "render_status",
            "pending_asset_approval_count",
            "selection",
            "content_length",
            "content_sha256",
            "source_file_id",
            "source_file_name",
            "source_revision",
            "code",
            "error",
            "message",
        ),
    )
    content = payload.get("content")
    compact.update(_content_metadata(content))
    return compact or {"status": "completed"}


compact_result = _compact_canvas_result
