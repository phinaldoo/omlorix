"""Compliance watermark helpers shared by the chat export paths.

Watermarks are resolved at export time from the user's effective group
settings.  The helpers in this module only modify an in-memory export copy;
the persisted chat message is never changed.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from fastapi import HTTPException

from app.groups.init import get_user_group_setting_value


_WATERMARKED_MESSAGE_ROLES = {"assistant", "tool"}


def get_compliance_watermark(user_id: str, db) -> str:
    """Return the effective, normalized watermark for a user.

    An empty string means that watermarking is disabled, the configured text
    is empty, or the referenced user no longer exists.  The last case matters
    for historical/admin exports that can contain orphaned chat rows; those
    exports should remain readable instead of failing while resolving policy.
    """

    try:
        enabled = get_user_group_setting_value(
            user_id,
            "compliance",
            "enable_watermark",
            db,
        )
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        # The chat row can outlive a deleted user in historical/admin data.
        return ""
    if enabled is not True:
        return ""

    watermark = get_user_group_setting_value(
        user_id,
        "compliance",
        "watermark",
        db,
    )
    return watermark.strip() if isinstance(watermark, str) else ""


class ComplianceWatermarkResolver:
    """Resolve and cache watermarks while exporting many chats.

    Self exports usually contain many chats owned by one user, while admin
    exports can contain many chats per user.  Caching avoids repeating the
    group-policy lookup for every chat without caching policy across requests.
    """

    def __init__(self, db) -> None:
        """Create a request-scoped resolver backed by *db*."""

        self._db = db
        self._cache: dict[str, str] = {}

    def for_user(self, user_id: str) -> str:
        """Return the watermark for *user_id*, resolving it at most once."""

        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return ""
        if normalized_user_id not in self._cache:
            self._cache[normalized_user_id] = get_compliance_watermark(
                normalized_user_id,
                self._db,
            )
        return self._cache[normalized_user_id]


def append_compliance_watermark(raw_text: Any, watermark: str) -> str:
    """Append a normalized watermark to plain exported text.

    This mirrors the browser copy behavior: trailing whitespace is removed
    before adding a blank line and the marker, and an empty base becomes just
    the marker.  Disabled/empty watermark configuration leaves the original
    text untouched.
    """

    base = raw_text if isinstance(raw_text, str) else ""
    marker = watermark.strip() if isinstance(watermark, str) else ""
    if not marker:
        return base

    trimmed_base = base.rstrip()
    if not trimmed_base:
        return marker
    return f"{trimmed_base}\n\n{marker}"


def _append_watermark_to_serialized_content(raw_content: Any, watermark: str) -> Any:
    """Append a watermark while preserving JSON-encoded message blocks."""

    if not isinstance(raw_content, str):
        if isinstance(raw_content, (dict, list)):
            parsed = deepcopy(raw_content)
        else:
            return append_compliance_watermark(raw_content, watermark)
    else:
        try:
            parsed = json.loads(raw_content)
        except (TypeError, ValueError):
            return append_compliance_watermark(raw_content, watermark)

    watermark_block = {"type": "content", "content": watermark}
    if isinstance(parsed, list):
        parsed.append(watermark_block)
    elif isinstance(parsed, dict):
        parsed = [parsed, watermark_block]
    else:
        return append_compliance_watermark(str(parsed), watermark)
    return json.dumps(parsed, ensure_ascii=False)


def apply_compliance_watermark_to_chat_export(
    payload: dict[str, Any],
    watermark: str,
) -> dict[str, Any]:
    """Mark assistant/tool message content in an in-memory chat export.

    User prompts remain unchanged.  JSON block lists receive a new visible
    content block so the exported JSON remains valid and can still be imported
    without corrupting the original persisted message.
    """

    marker = watermark.strip() if isinstance(watermark, str) else ""
    if not marker or not isinstance(payload, dict):
        return payload

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role not in _WATERMARKED_MESSAGE_ROLES:
            continue
        message["content"] = _append_watermark_to_serialized_content(
            message.get("content"),
            marker,
        )

    return payload
