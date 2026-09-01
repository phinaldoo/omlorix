"""Safe transformations for administrator audit-log responses and exports."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable
from datetime import datetime, timezone
import json
import re
from typing import Any

from app.admin.audit_logs.schemas import AuditLogDetail, AuditLogItem
from app.logging.models import _is_sensitive_detail_key


AUDIT_LOG_EXPORT_BATCH_SIZE = 500
AUDIT_LOG_EXPORT_MAX_ROWS = 50_000
AUDIT_LOG_DETAIL_MAX_CHARS = 4_000
AUDIT_LOG_DETAIL_MAX_DEPTH = 3
AUDIT_LOG_DETAIL_MAX_ITEMS = 50
_IP_FINGERPRINT_PATTERN = re.compile(r"^ip_[0-9a-f]{12}$")
_DEVICE_FINGERPRINT_PATTERN = re.compile(r"^device_[0-9a-f]{12}$")

_PUBLIC_DETAIL_KEYS = frozenset(
    {
        "attempts",
        "batch_size",
        "categories",
        "category",
        "changed_fields",
        "count",
        "created_count",
        "deleted_count",
        "error_count",
        "event_source",
        "fields",
        "filter",
        "filters",
        "flow",
        "imported_count",
        "item_count",
        "method",
        "mode",
        "operation",
        "page",
        "page_size",
        "provider",
        "reference",
        "result",
        "role",
        "scope",
        "sensitivity_category",
        "stage",
        "status",
        "success",
        "type",
        "types",
        "updated_count",
    }
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def encode_audit_cursor(timestamp: datetime, row_id: str) -> str:
    """Encode the stable keyset position without exposing SQL syntax."""

    payload = json.dumps(
        {"timestamp": _utc(timestamp).isoformat(), "id": str(row_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_audit_cursor(cursor: str) -> tuple[datetime, str]:
    """Decode and strictly validate a keyset cursor supplied by a client."""

    if not cursor or len(cursor) > 512:
        raise ValueError("invalid cursor")
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(f"{cursor}{padding}".encode("ascii")).decode(
                "utf-8"
            )
        )
        timestamp = datetime.fromisoformat(str(payload["timestamp"]))
        row_id = str(payload["id"])
    except (
        binascii.Error,
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid cursor") from exc
    if not row_id or len(row_id) > 64:
        raise ValueError("invalid cursor")
    return _utc(timestamp), row_id


def _is_public_detail_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if not normalized or _is_sensitive_detail_key(normalized):
        return False
    return normalized in _PUBLIC_DETAIL_KEYS or normalized.endswith(
        ("_id", "_ids", "_count")
    )


def _project_detail_value(value: Any, *, depth: int) -> Any:
    if depth > AUDIT_LOG_DETAIL_MAX_DEPTH:
        return None
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        for key, item in list(value.items())[:AUDIT_LOG_DETAIL_MAX_ITEMS]:
            if not _is_public_detail_key(str(key)):
                continue
            projected[str(key)] = _project_detail_value(item, depth=depth + 1)
        return projected
    if isinstance(value, (list, tuple, set)):
        return [
            _project_detail_value(item, depth=depth + 1)
            for item in list(value)[:AUDIT_LOG_DETAIL_MAX_ITEMS]
        ]
    return str(value)[:512]


def project_public_audit_details(details: Any) -> dict[str, Any] | None:
    """Return a bounded allowlisted projection of heterogeneous audit details."""

    if not isinstance(details, dict):
        return None
    projected = _project_detail_value(details, depth=0)
    if not isinstance(projected, dict) or not projected:
        return None
    serialized = json.dumps(projected, ensure_ascii=False, default=str)
    if len(serialized) <= AUDIT_LOG_DETAIL_MAX_CHARS:
        return projected
    return {"result": f"{serialized[: AUDIT_LOG_DETAIL_MAX_CHARS - 3]}..."}


def serialize_audit_log_item(row: Any, *, include_details: bool = False):
    """Project one audit row into the explicit administrator-facing contract."""

    public_details = project_public_audit_details(getattr(row, "details", None))
    stored_ip = str(row.ip_address) if row.ip_address is not None else None
    stored_device = str(row.user_agent) if row.user_agent is not None else None
    values = {
        "id": str(row.id),
        "actor_user_id": str(row.user_id),
        "action": str(row.action),
        "reason": str(row.reason)[:255] if row.reason is not None else None,
        "timestamp": row.timestamp,
        "category": str(row.category),
        "ip_fingerprint": (
            stored_ip
            if stored_ip and _IP_FINGERPRINT_PATTERN.fullmatch(stored_ip)
            else None
        ),
        "device_fingerprint": (
            stored_device
            if stored_device and _DEVICE_FINGERPRINT_PATTERN.fullmatch(stored_device)
            else None
        ),
        "has_details": public_details is not None,
    }
    if include_details:
        return AuditLogDetail(**values, details=public_details)
    return AuditLogItem(**values)


def _iso_utc(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def serialize_audit_log_export_row(row: Any) -> dict[str, Any]:
    item = serialize_audit_log_item(row, include_details=True)
    payload = item.model_dump(mode="json")
    payload["timestamp"] = _iso_utc(item.timestamp)
    return payload


def iter_audit_log_export_json(
    rows: Iterable[Any],
    *,
    total_count: int,
    exported_at: datetime,
    from_timestamp: datetime,
    to_timestamp: datetime,
    retention: dict[str, Any],
) -> Iterable[str]:
    """Stream a stable, versioned JSON envelope without buffering all rows."""

    header = {
        "export_type": "audit_logs",
        "export_version": 1,
        "total_count": total_count,
        "exported_at": _iso_utc(exported_at),
        "from": _iso_utc(from_timestamp),
        "to": _iso_utc(to_timestamp),
        "retention": retention,
    }
    header_json = json.dumps(header, separators=(",", ":"), default=str)
    yield f'{header_json[:-1]},"events":['
    first = True
    for row in rows:
        if not first:
            yield ","
        first = False
        yield json.dumps(
            serialize_audit_log_export_row(row),
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
    yield "]}"
