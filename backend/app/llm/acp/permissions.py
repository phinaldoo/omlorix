from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import threading
import time
import uuid
from typing import Any, Callable

from app.redis_client import get_redis_client


@dataclass(slots=True)
class PendingAcpPermission:
    """A single ACP permission request waiting for an Omlorix user decision."""

    permission_id: str
    user_id: str
    generation_id: str
    options: list[dict[str, Any]]
    expires_at: datetime
    event: threading.Event = field(default_factory=threading.Event)
    selected_option_id: str | None = None
    shared: bool = False


class AcpPermissionRegistry:
    """Bridge ACP workers and HTTP decisions across one or more API replicas.

    Local events keep the single-process path inexpensive. When Redis is
    available, a short-lived hash makes the ticket visible to every API
    replica so a load-balanced permission POST can resolve the owning worker.
    """

    _KEY_PREFIX = "omlorix:acp-permission:"
    _RESOLUTION_POLL_SECONDS = 0.1

    def __init__(
        self,
        *,
        redis_client_factory: Callable[[], Any | None] = get_redis_client,
    ) -> None:
        self._lock = threading.RLock()
        self._pending: dict[str, PendingAcpPermission] = {}
        self._redis_client_factory = redis_client_factory

    @classmethod
    def _redis_key(cls, permission_id: str) -> str:
        """Return the isolated Redis key for one permission ticket."""
        return f"{cls._KEY_PREFIX}{permission_id}"

    @staticmethod
    def _allowed_option_ids(options: list[dict[str, Any]]) -> set[str]:
        """Extract the exact option identifiers advertised by the ACP agent."""
        return {
            str(option.get("optionId") or option.get("option_id") or "").strip()
            for option in options
            if isinstance(option, dict)
        } - {""}

    def _store_shared(self, ticket: PendingAcpPermission) -> bool:
        """Persist a ticket with a bounded lifetime when Redis is available."""
        client = self._redis_client_factory()
        if client is None:
            return False
        ttl_seconds = max(
            1,
            int((ticket.expires_at - datetime.now(timezone.utc)).total_seconds()) + 30,
        )
        try:
            pipeline = client.pipeline()
            pipeline.hset(
                self._redis_key(ticket.permission_id),
                mapping={
                    "user_id": ticket.user_id,
                    "generation_id": ticket.generation_id,
                    "options": json.dumps(ticket.options, separators=(",", ":")),
                    "status": "pending",
                    "selected_option_id": "",
                    "expires_at": str(ticket.expires_at.timestamp()),
                },
            )
            pipeline.expire(self._redis_key(ticket.permission_id), ttl_seconds)
            pipeline.execute()
            return True
        except Exception:
            # Redis is an availability aid here. A ticket still works safely
            # when the request and ACP worker happen to reach this process.
            return False

    def create(
        self,
        *,
        user_id: str,
        generation_id: str,
        options: list[dict[str, Any]],
        timeout_seconds: int,
    ) -> PendingAcpPermission:
        """Register a permission ticket and remove expired tickets opportunistically."""
        now = datetime.now(timezone.utc)
        ticket = PendingAcpPermission(
            permission_id=str(uuid.uuid4()),
            user_id=str(user_id),
            generation_id=str(generation_id),
            options=options,
            expires_at=now + timedelta(seconds=max(1, int(timeout_seconds))),
        )
        with self._lock:
            self._prune_locked(now)
            self._pending[ticket.permission_id] = ticket
        ticket.shared = self._store_shared(ticket)
        return ticket

    def resolve(self, permission_id: str, *, user_id: str, option_id: str | None) -> bool:
        """Resolve an owned ticket exactly once, including from another replica."""
        now = datetime.now(timezone.utc)
        normalized_permission_id = str(permission_id)
        normalized_user_id = str(user_id)
        normalized_option_id = str(option_id).strip() if option_id is not None else None
        with self._lock:
            self._prune_locked(now)
            ticket = self._pending.get(normalized_permission_id)
            if ticket is not None and (
                ticket.user_id != normalized_user_id or ticket.event.is_set()
            ):
                return False
            if (
                ticket is not None
                and normalized_option_id
                and normalized_option_id not in self._allowed_option_ids(ticket.options)
            ):
                return False

        client = self._redis_client_factory()
        if client is not None and (ticket is None or ticket.shared):
            resolved = self._resolve_shared(
                client,
                normalized_permission_id,
                user_id=normalized_user_id,
                option_id=normalized_option_id,
            )
            if not resolved:
                return False
        elif ticket is None:
            # A process without the local ticket cannot safely validate an
            # owner or option when the shared store is unavailable.
            return False
        elif ticket is not None:
            # Keep the no-Redis fallback exactly-once by performing the final
            # event check and update while holding the same local lock.
            with self._lock:
                if ticket.event.is_set():
                    return False
                ticket.selected_option_id = normalized_option_id
                ticket.event.set()
            return True

        if ticket is not None:
            with self._lock:
                ticket.selected_option_id = normalized_option_id
                ticket.event.set()
        return True

    def _resolve_shared(
        self,
        client: Any,
        permission_id: str,
        *,
        user_id: str,
        option_id: str | None,
    ) -> bool:
        """Atomically validate and resolve a Redis-backed permission ticket."""
        key = self._redis_key(permission_id)
        try:
            from redis.exceptions import WatchError
        except Exception:  # pragma: no cover - redis is installed in supported deployments
            WatchError = RuntimeError

        for _attempt in range(3):
            pipeline = client.pipeline()
            try:
                pipeline.watch(key)
                record = pipeline.hgetall(key)
                if (
                    not record
                    or record.get("status") != "pending"
                    or record.get("user_id") != user_id
                ):
                    return False
                try:
                    if float(record.get("expires_at") or 0) <= time.time():
                        return False
                except (TypeError, ValueError):
                    return False
                try:
                    options = json.loads(record.get("options") or "[]")
                except (TypeError, ValueError):
                    return False
                if option_id and option_id not in self._allowed_option_ids(options):
                    return False

                pipeline.multi()
                pipeline.hset(
                    key,
                    mapping={
                        "status": "resolved",
                        "selected_option_id": option_id or "",
                    },
                )
                pipeline.execute()
                return True
            except WatchError:
                continue
            except Exception:
                return False
            finally:
                pipeline.reset()
        return False

    def _read_shared_resolution(
        self,
        permission_id: str,
    ) -> tuple[bool, str | None]:
        """Read a completed shared decision without exposing ticket contents."""
        client = self._redis_client_factory()
        if client is None:
            return False, None
        try:
            record = client.hgetall(self._redis_key(permission_id))
        except Exception:
            return False, None
        if not record or record.get("status") != "resolved":
            return False, None
        return True, str(record.get("selected_option_id") or "").strip() or None

    async def wait(
        self,
        ticket: PendingAcpPermission,
        *,
        timeout_seconds: int,
    ) -> str | None:
        """Wait asynchronously for a local or cross-replica decision.

        Short polling sleeps avoid occupying a worker thread for the entire
        user-facing timeout and respond immediately to task cancellation.
        """
        deadline = time.monotonic() + max(0, float(timeout_seconds))
        while time.monotonic() < deadline:
            if ticket.event.is_set():
                return ticket.selected_option_id
            if ticket.shared:
                resolved, option_id = await asyncio.to_thread(
                    self._read_shared_resolution,
                    ticket.permission_id,
                )
                if resolved:
                    with self._lock:
                        ticket.selected_option_id = option_id
                        ticket.event.set()
                    return option_id
            await asyncio.sleep(
                min(
                    self._RESOLUTION_POLL_SECONDS,
                    max(0, deadline - time.monotonic()),
                )
            )
        return None

    def discard(self, permission_id: str) -> None:
        """Remove a completed or abandoned ticket locally and from Redis."""
        with self._lock:
            ticket = self._pending.pop(str(permission_id), None)
        if ticket is not None and ticket.shared:
            client = self._redis_client_factory()
            if client is not None:
                try:
                    client.delete(self._redis_key(permission_id))
                except Exception:
                    pass

    def _prune_locked(self, now: datetime) -> None:
        """Discard expired tickets and wake their waiting ACP callbacks."""
        expired = [key for key, ticket in self._pending.items() if ticket.expires_at <= now]
        for key in expired:
            ticket = self._pending.pop(key)
            ticket.event.set()


acp_permission_registry = AcpPermissionRegistry()
