from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
import time
from typing import Any

from app.redis_client import get_redis_client


WRITE_FREEZE_KEY = "omlorix:maintenance:write_freeze"

_local_lock = threading.Lock()
_local_freeze_until: float = 0.0
_local_freeze_reason: str | None = None


def _now_ts() -> float:
    """Get current timestamp."""
    return time.time()


def activate_write_freeze(*, reason: str, ttl_seconds: int) -> None:
    """Activate write freeze for maintenance."""
    ttl = max(10, int(ttl_seconds))
    client = get_redis_client()
    payload = {
        "reason": reason,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ttl_seconds": ttl,
    }
    if client is not None:
        client.set(WRITE_FREEZE_KEY, json.dumps(payload), ex=ttl)
        return

    global _local_freeze_until, _local_freeze_reason
    with _local_lock:
        _local_freeze_until = _now_ts() + ttl
        _local_freeze_reason = reason


def deactivate_write_freeze() -> None:
    """Deactivate write freeze."""
    client = get_redis_client()
    if client is not None:
        client.delete(WRITE_FREEZE_KEY)
        return

    global _local_freeze_until, _local_freeze_reason
    with _local_lock:
        _local_freeze_until = 0.0
        _local_freeze_reason = None


def is_write_freeze_active() -> bool:
    """Check if write freeze is active."""
    client = get_redis_client()
    if client is not None:
        return bool(client.get(WRITE_FREEZE_KEY))

    with _local_lock:
        return _local_freeze_until > _now_ts()


def get_write_freeze_details() -> dict[str, Any]:
    """Get write freeze details."""
    client = get_redis_client()
    if client is not None:
        raw = client.get(WRITE_FREEZE_KEY)
        if not raw:
            return {"active": False}
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"reason": "unknown"}
        payload["active"] = True
        return payload

    with _local_lock:
        active = _local_freeze_until > _now_ts()
        if not active:
            return {"active": False}
        return {
            "active": True,
            "reason": _local_freeze_reason,
            "expires_at": datetime.fromtimestamp(_local_freeze_until, tz=timezone.utc).isoformat(),
        }
