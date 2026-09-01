from __future__ import annotations

import asyncio
import heapq
import json
import logging
import os
import queue
import threading
import time
import uuid
from collections import deque
from typing import Any, AsyncGenerator, Callable, Dict, Generator, Iterator, Optional

from app.redis_client import get_async_redis_client, get_redis_client


logger = logging.getLogger(__name__)


_STREAM_KEY_PREFIX = "omlorix:stream"
_CANCEL_KEY_PREFIX = "omlorix:cancel"
_GENERATION_OWNER_KEY_PREFIX = "omlorix:generation_owner"

_STREAM_TTL_SECONDS = max(120, int(os.getenv("STREAM_REDIS_TTL_SECONDS", "3600") or "3600"))
_CANCEL_TTL_SECONDS = max(60, int(os.getenv("STREAM_CANCEL_TTL_SECONDS", "300") or "300"))

# These are hard security boundaries, rather than tuning defaults. A single
# generation may retain at most 20,000 serialized events and 50 MiB of their
# UTF-8 payload. Keeping both limits is important: a line limit alone permits a
# few huge events, while a byte limit alone permits excessive Python/Redis
# bookkeeping for millions of tiny events.
_STREAM_MAX_LINES = 20_000
_STREAM_MAX_BYTES = 50 * 1024 * 1024
_STREAM_SIZE_CHUNK_CHARACTERS = 64 * 1024
# Completed fallback streams stay available just long enough for the initial
# HTTP subscriber or a quick reconnect to attach. Redis-backed streams retain
# their normal, longer TTL; keeping the in-memory grace short bounds worst-case
# process memory when Redis is unavailable.
_IN_MEMORY_COMPLETED_RETENTION_SECONDS = 30.0

# Redis must append and trim atomically. Otherwise concurrent publishers could
# all observe a stream below the limit and temporarily or permanently exceed
# it. A parallel list stores each stream entry's logical UTF-8 size, allowing
# the script to remove the oldest entries until both hard limits hold.
_REDIS_APPEND_AND_TRIM_SCRIPT = """
local events_key = KEYS[1]
local sizes_key = KEYS[2]
local meta_key = KEYS[3]

local line = ARGV[1]
local add_sequence = ARGV[2] == '1'
local max_lines = tonumber(ARGV[3])
local max_bytes = tonumber(ARGV[4])

-- Status validation, sequence allocation, and append all happen inside this
-- script. Readers therefore never observe a false gap caused by two publishers
-- appending reserved sequence numbers in the opposite order.
if redis.call('HGET', meta_key, 'status') ~= 'active' then
    return {-1, '', 0, 0}
end

local sequence = tonumber(redis.call('HGET', meta_key, 'seq') or '0') + 1
if add_sequence then
    local payload = cjson.decode(line)
    payload['seq'] = sequence
    line = cjson.encode(payload)
end
local line_bytes = string.len(line)

-- Do not mutate the stream or consume a sequence number when adding the
-- server-owned sequence field pushes a boundary-sized line over the limit.
if line_bytes > max_bytes then
    return {-2, '', 0, line_bytes}
end

local entry_id = redis.call(
    'XADD',
    events_key,
    'MAXLEN', '=', max_lines,
    '*',
    'seq', sequence,
    'line', line
)
redis.call('HSET', meta_key, 'seq', sequence)
redis.call('RPUSH', sizes_key, line_bytes)

local total_bytes = tonumber(
    redis.call('HGET', meta_key, 'buffer_bytes') or '0'
) + line_bytes
local line_count = redis.call('LLEN', sizes_key)

-- XADD already removed the oldest stream entries for the exact line limit.
-- Mirror those removals in the size list and byte counter.
while line_count > max_lines do
    local removed_size = tonumber(redis.call('LPOP', sizes_key) or '0')
    total_bytes = math.max(0, total_bytes - removed_size)
    line_count = line_count - 1
end

-- The byte limit may require removing additional entries. XDEL is exact, so
-- the stream and its size list remain aligned after every successful script.
while total_bytes > max_bytes and line_count > 0 do
    local oldest = redis.call('XRANGE', events_key, '-', '+', 'COUNT', 1)
    local removed_size = tonumber(redis.call('LPOP', sizes_key) or '0')
    if #oldest > 0 then
        redis.call('XDEL', events_key, oldest[1][1])
    end
    total_bytes = math.max(0, total_bytes - removed_size)
    line_count = line_count - 1
end

redis.call('HSET', meta_key, 'buffer_bytes', total_bytes)
return {sequence, entry_id, line_count, total_bytes}
"""

# Completion uses a separate one-entry signal stream. It wakes subscribers
# blocked in XREAD without consuming a user-visible sequence number or changing
# the retained event/byte accounting above.
_REDIS_MARK_DONE_SCRIPT = """
local meta_key = KEYS[1]
local signal_key = KEYS[2]
local events_key = KEYS[3]
local sizes_key = KEYS[4]
local active_key = KEYS[5]
local generation_chat_key = KEYS[6]

local status = ARGV[1]
local finished_at = ARGV[2]
local generation_id = ARGV[3]
local ttl_seconds = tonumber(ARGV[4])
local has_chat = ARGV[5] == '1'

redis.call('HSET', meta_key, 'status', status, 'finished_at', finished_at)
local signal_id = redis.call(
    'XADD', signal_key, 'MAXLEN', '=', 1, '*', 'terminal', status
)

if has_chat and redis.call('GET', active_key) == generation_id then
    redis.call('DEL', active_key)
end

redis.call('EXPIRE', meta_key, ttl_seconds)
redis.call('EXPIRE', signal_key, ttl_seconds)
redis.call('EXPIRE', events_key, ttl_seconds)
redis.call('EXPIRE', sizes_key, ttl_seconds)
redis.call('EXPIRE', generation_chat_key, ttl_seconds)
return signal_id
"""


class StreamLineLimitExceeded(ValueError):
    """Raised when one serialized stream line cannot fit in the byte budget."""


def _limited_utf8_size(value: str, max_bytes: int) -> int:
    """Return a string's UTF-8 size without allocating one giant byte string.

    A malicious tool result could itself be very large. Encoding the complete
    value merely to measure it would briefly allocate another equally large
    object before the limit can reject it, so measurement is performed in
    bounded chunks and stops as soon as the limit is crossed.
    """

    if len(value) > max_bytes:
        raise StreamLineLimitExceeded(
            f"Stream line exceeds the {max_bytes}-byte retention limit."
        )

    total_bytes = 0
    for offset in range(0, len(value), _STREAM_SIZE_CHUNK_CHARACTERS):
        chunk = value[offset : offset + _STREAM_SIZE_CHUNK_CHARACTERS]
        total_bytes += len(chunk.encode("utf-8"))
        if total_bytes > max_bytes:
            raise StreamLineLimitExceeded(
                f"Stream line exceeds the {max_bytes}-byte retention limit."
            )
    return total_bytes


def _normalize_stream_line(line: str, sequence: int, max_bytes: int) -> tuple[str, int]:
    """Add the sequence number and return the normalized line plus UTF-8 size."""

    # Reject obviously oversized input before JSON parsing/dumping can create
    # additional large temporary objects.
    _limited_utf8_size(line, max_bytes)
    try:
        payload = json.loads(line)
        if "seq" not in payload:
            payload["seq"] = sequence
        normalized = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        normalized = json.dumps(
            {"type": "raw", "seq": sequence, "delta": line},
            separators=(",", ":"),
            ensure_ascii=True,
        )
    return normalized, _limited_utf8_size(normalized, max_bytes)


def _prepare_redis_stream_line(line: str, max_bytes: int) -> tuple[str, bool]:
    """Normalize a Redis line before its sequence is allocated atomically.

    Redis supplies the sequence inside the append script. This helper leaves a
    missing sequence out of the serialized object and tells the script whether
    it must insert one. Existing provider-supplied sequence values retain the
    same compatibility behavior as the in-memory implementation.
    """

    _limited_utf8_size(line, max_bytes)
    add_sequence = False
    try:
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise TypeError("Stream payload must be a JSON object.")
        add_sequence = "seq" not in payload
        normalized = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        normalized = json.dumps(
            {"type": "raw", "delta": line},
            separators=(",", ":"),
            ensure_ascii=True,
        )
        add_sequence = True
    _limited_utf8_size(normalized, max_bytes)
    return normalized, add_sequence


def _retention_gap_error_line() -> str:
    """Return the translated stream error emitted when retained history has a gap."""

    return json.dumps(
        {
            "t": "e",
            "d": (
                "The live response became too large to keep in memory. "
                "Please retry the message."
            ),
            "i18n_key": "chat_stream_retention_limit_exceeded",
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _stream_meta_key(generation_id: str) -> str:
    """Return the Redis key for stream metadata of a generation."""
    return f"{_STREAM_KEY_PREFIX}:meta:{generation_id}"


def _stream_events_key(generation_id: str) -> str:
    """Return the Redis key for stream events of a generation."""
    return f"{_STREAM_KEY_PREFIX}:events:{generation_id}"


def _stream_event_sizes_key(generation_id: str) -> str:
    """Return the Redis key tracking retained event sizes for a generation."""
    return f"{_STREAM_KEY_PREFIX}:event_sizes:{generation_id}"


def _stream_signal_key(generation_id: str) -> str:
    """Return the Redis stream used only to wake subscribers at completion."""

    return f"{_STREAM_KEY_PREFIX}:signal:{generation_id}"


def _chat_active_key(chat_id: str) -> str:
    """Return the Redis key tracking the active generation for a chat."""
    return f"{_STREAM_KEY_PREFIX}:chat_active:{chat_id}"


def _generation_chat_key(generation_id: str) -> str:
    """Return the Redis key mapping a generation to its chat."""
    return f"{_STREAM_KEY_PREFIX}:generation_chat:{generation_id}"


def _cancel_key(generation_id: str) -> str:
    """Return the Redis key for a generation's cancellation flag."""
    return f"{_CANCEL_KEY_PREFIX}:{generation_id}"


def _generation_owner_key(generation_id: str) -> str:
    """Return the Redis key that binds a client generation ID to its user."""
    return f"{_GENERATION_OWNER_KEY_PREFIX}:{generation_id}"


class _InMemoryCancelRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._flags: Dict[str, threading.Event] = {}
        self._active_by_chat: Dict[str, str] = {}
        self._owners: Dict[str, str] = {}

    def reserve(self, generation_id: str, user_id: str) -> bool:
        """Reserve a client-created generation ID for one authenticated user."""
        with self._lock:
            owner = self._owners.get(generation_id)
            if owner is not None:
                return False
            self._owners[generation_id] = user_id
            return True

    def is_owned_by(self, generation_id: str, user_id: str) -> bool:
        """Return whether ``user_id`` owns the reserved generation ID."""
        with self._lock:
            return self._owners.get(generation_id) == user_id

    def set_active(self, chat_id: str, generation_id: str):
        """Register a generation as the active one for a chat."""
        with self._lock:
            self._active_by_chat[chat_id] = generation_id
            if generation_id not in self._flags:
                self._flags[generation_id] = threading.Event()

    def get_active(self, chat_id: str) -> Optional[str]:
        """Return the active generation ID for a chat, or None."""
        with self._lock:
            return self._active_by_chat.get(chat_id)

    def clear_active_if_match(self, chat_id: str, generation_id: str):
        """Clear the active generation for a chat only if it matches the given ID."""
        with self._lock:
            if self._active_by_chat.get(chat_id) == generation_id:
                self._active_by_chat.pop(chat_id, None)

    def cancel(self, generation_id: str):
        """Set the cancellation flag for a generation."""
        with self._lock:
            evt = self._flags.get(generation_id)
            if evt is None:
                evt = threading.Event()
                self._flags[generation_id] = evt
            evt.set()

    def is_cancelled(self, generation_id: str) -> bool:
        """Return whether a generation has been cancelled."""
        with self._lock:
            evt = self._flags.get(generation_id)
            return bool(evt and evt.is_set())

    def clear(self, generation_id: str):
        """Clear the cancellation flag for a generation."""
        with self._lock:
            self._flags.pop(generation_id, None)
            self._owners.pop(generation_id, None)


class _ActiveCancellationHandles:
    """Own process-local provider handles that can interrupt blocking reads.

    Cancellation flags remain the durable/distributed source of truth. These
    handles are deliberately process-local because sockets and SDK stream
    objects cannot be shared between workers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: Dict[str, threading.Event] = {}
        self._callbacks: Dict[str, Dict[str, Callable[[], None]]] = {}

    def register(self, generation_id: str, callback: Callable[[], None]) -> str:
        """Register an idempotent close callback and return its opaque token."""
        token = str(uuid.uuid4())
        with self._lock:
            self._events.setdefault(generation_id, threading.Event())
            self._callbacks.setdefault(generation_id, {})[token] = callback
        return token

    def unregister(self, generation_id: str, token: str) -> None:
        """Remove one provider handle without affecting other nested streams."""
        with self._lock:
            callbacks = self._callbacks.get(generation_id)
            if callbacks is None:
                return
            callbacks.pop(token, None)
            if not callbacks:
                self._callbacks.pop(generation_id, None)
                self._events.pop(generation_id, None)

    def cancel(self, generation_id: str) -> None:
        """Wake local waiters and close every active upstream resource."""
        with self._lock:
            event = self._events.setdefault(generation_id, threading.Event())
            event.set()
            callbacks = list(self._callbacks.get(generation_id, {}).values())
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logger.debug(
                    "Failed to close provider stream generation_id=%s",
                    generation_id,
                    exc_info=True,
                )

    def wait(self, generation_id: str, timeout: float) -> bool:
        """Wait for process-local cancellation without polling Redis."""
        with self._lock:
            event = self._events.setdefault(generation_id, threading.Event())
        return event.wait(timeout)

    def clear(self, generation_id: str) -> None:
        """Discard local handles after a generation has finalized."""
        with self._lock:
            self._events.pop(generation_id, None)
            self._callbacks.pop(generation_id, None)

    def generation_ids(self) -> list[str]:
        """Return a stable snapshot of generations with live provider handles."""
        with self._lock:
            return list(self._callbacks)


class _CancellationWakeup:
    """Synthetic iterator value used only to enter provider cancel handling."""


_CANCELLATION_WAKEUP = _CancellationWakeup()


class _InMemoryStreamHub:
    def __init__(
        self,
        max_lines: int = _STREAM_MAX_LINES,
        max_bytes: int = _STREAM_MAX_BYTES,
        completed_retention_seconds: float = _IN_MEMORY_COMPLETED_RETENTION_SECONDS,
    ):
        self._lock = threading.Lock()
        self._gens: Dict[str, Dict] = {}
        self._by_chat: Dict[str, str] = {}
        self._max_lines = max(1, int(max_lines))
        self._max_bytes = max(1, int(max_bytes))
        self._completed_retention_seconds = max(
            0.001,
            float(completed_retention_seconds),
        )
        # One lazily-created reaper services every completed generation in this
        # hub. A heap keeps cleanup bounded without creating one Timer thread per
        # response, which would itself become a resource-exhaustion vector.
        self._cleanup_condition = threading.Condition(self._lock)
        self._cleanup_deadlines: list[tuple[float, str]] = []
        self._cleanup_thread: threading.Thread | None = None

    def start(self, generation_id: str, chat_id: str, metadata: Optional[dict] = None):
        """Initialize a new in-memory stream for a generation."""
        with self._lock:
            if generation_id in self._gens:
                # The API reserves a stream before a new-chat job is queued so
                # the browser can subscribe immediately.  Once the worker has
                # created the chat, upgrade the initially empty mapping.
                existing = self._gens[generation_id]
                old_chat_id = str(existing.get("chat_id") or "")
                if chat_id and not old_chat_id:
                    existing["chat_id"] = chat_id
                    self._by_chat[chat_id] = generation_id
                return
            self._gens[generation_id] = {
                "chat_id": chat_id,
                "seq": 0,
                # The shared replay deque is also the live source for every
                # subscriber. Subscribers keep only a sequence cursor, avoiding
                # the previous per-client copy queues and their memory
                # amplification. Byte trimming is handled explicitly below.
                "buffer": deque(maxlen=self._max_lines),
                "buffer_bytes": 0,
                "subscriber_count": 0,
                # Async HTTP subscribers wait on process-local events instead
                # of occupying AnyIO worker threads. Publishers can run in
                # background threads, so each event is signalled through its
                # owning loop with ``call_soon_threadsafe``.
                "async_subscribers": {},
                "condition": threading.Condition(self._lock),
                "status": "active",
                "created_at": time.time(),
                "cleanup_deadline": None,
                "cleanup_scheduled": False,
                "metadata": metadata or {},
            }
            # New-chat generations are queued before a chat ID exists. Never
            # let those unrelated streams contend for one shared empty key.
            if chat_id:
                self._by_chat[chat_id] = generation_id

    def publish_dict(self, generation_id: str, payload: dict) -> int:
        """Publish a dict payload as JSON to the stream."""
        return self.publish_line(generation_id, json.dumps(payload))

    def publish_line(self, generation_id: str, line: str) -> int:
        """Publish a string line to the stream, assigning a sequence number."""
        with self._lock:
            g = self._gens.get(generation_id)
            if not g or g["status"] != "active":
                return -1

            # Normalize and validate before committing the sequence number so a
            # rejected oversized line cannot create an artificial replay gap.
            seq = g["seq"] + 1
            line, line_bytes = _normalize_stream_line(line, seq, self._max_bytes)
            g["seq"] = seq

            buffer = g["buffer"]
            if len(buffer) == self._max_lines:
                # deque(maxlen=...) would discard this item automatically, but
                # its size must be removed from the byte counter first.
                _, _, removed_bytes = buffer.popleft()
                g["buffer_bytes"] -= removed_bytes

            buffer.append((seq, line, line_bytes))
            g["buffer_bytes"] += line_bytes
            while buffer and g["buffer_bytes"] > self._max_bytes:
                _, _, removed_bytes = buffer.popleft()
                g["buffer_bytes"] -= removed_bytes

            # Wake cursor-based subscribers. No line is copied into a private
            # queue, so a stalled subscriber cannot accumulate pending events.
            g["condition"].notify_all()
            self._notify_async_subscribers_unlocked(g)
            return seq

    def subscribe(
        self,
        generation_id: str,
        from_seq: int = 0,
        *,
        heartbeat_seconds: float | None = None,
    ) -> Generator[str, None, None]:
        """Yield retained/live lines after ``from_seq`` using a shared cursor."""

        cursor = max(0, int(from_seq or 0))
        try:
            heartbeat = float(heartbeat_seconds) if heartbeat_seconds is not None else 60.0
        except (TypeError, ValueError):
            heartbeat = 60.0
        heartbeat = max(0.05, min(heartbeat, 60.0))
        registered = False
        try:
            with self._lock:
                g = self._gens.get(generation_id)
                if not g:
                    return
                g["subscriber_count"] += 1
                registered = True

            while True:
                line = None
                retention_gap = False
                timed_out = False
                with self._lock:
                    g = self._gens.get(generation_id)
                    if not g:
                        return

                    buffer = g["buffer"]
                    if buffer:
                        oldest_seq = buffer[0][0]
                        newest_seq = buffer[-1][0]
                        if cursor < oldest_seq - 1:
                            retention_gap = True
                        elif cursor < newest_seq:
                            # In-memory sequence numbers are contiguous because
                            # they are committed only after a line passes its
                            # size validation.
                            next_index = max(0, cursor - oldest_seq + 1)
                            seq, line, _ = buffer[next_index]
                            cursor = seq

                    if line is None and not retention_gap:
                        if g["status"] != "active":
                            return
                        timed_out = not g["condition"].wait(timeout=heartbeat)

                if retention_gap:
                    logger.warning(
                        "Disconnecting stream subscriber after retention gap "
                        "generation_id=%s cursor=%s",
                        generation_id,
                        cursor,
                    )
                    yield _retention_gap_error_line() + "\n"
                    return
                if line is not None:
                    yield line + "\n"
                elif timed_out:
                    yield json.dumps({"type": "ping"}) + "\n"
        finally:
            if registered:
                with self._lock:
                    g = self._gens.get(generation_id)
                    if g:
                        g["subscriber_count"] = max(
                            0,
                            int(g["subscriber_count"]) - 1,
                        )
                        if g["status"] != "active" and not g["subscriber_count"]:
                            self._schedule_cleanup_unlocked(generation_id)

    async def subscribe_async(
        self,
        generation_id: str,
        from_seq: int = 0,
        *,
        heartbeat_seconds: float | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield retained/live in-memory lines without blocking the event loop."""

        cursor = max(0, int(from_seq or 0))
        try:
            heartbeat = (
                float(heartbeat_seconds)
                if heartbeat_seconds is not None
                else 60.0
            )
        except (TypeError, ValueError):
            heartbeat = 60.0
        heartbeat = max(0.05, min(heartbeat, 60.0))

        loop = asyncio.get_running_loop()
        wakeup = asyncio.Event()
        subscriber_id = uuid.uuid4().hex
        registered = False
        try:
            with self._lock:
                generation = self._gens.get(generation_id)
                if not generation:
                    return
                generation["subscriber_count"] += 1
                generation["async_subscribers"][subscriber_id] = (loop, wakeup)
                registered = True

            while True:
                line = None
                retention_gap = False
                should_wait = False
                with self._lock:
                    generation = self._gens.get(generation_id)
                    if not generation:
                        return

                    buffer = generation["buffer"]
                    if buffer:
                        oldest_seq = buffer[0][0]
                        newest_seq = buffer[-1][0]
                        if cursor < oldest_seq - 1:
                            retention_gap = True
                        elif cursor < newest_seq:
                            next_index = max(0, cursor - oldest_seq + 1)
                            sequence, line, _ = buffer[next_index]
                            cursor = sequence

                    if line is None and not retention_gap:
                        if generation["status"] != "active":
                            return
                        # Clearing while holding the hub lock closes the race
                        # between observing no line and a threaded publisher
                        # scheduling the next wakeup.
                        wakeup.clear()
                        should_wait = True

                if retention_gap:
                    logger.warning(
                        "Disconnecting async stream subscriber after retention gap "
                        "generation_id=%s cursor=%s",
                        generation_id,
                        cursor,
                    )
                    yield _retention_gap_error_line() + "\n"
                    return
                if line is not None:
                    yield line + "\n"
                    continue
                if not should_wait:
                    return

                try:
                    await asyncio.wait_for(wakeup.wait(), timeout=heartbeat)
                except TimeoutError:
                    yield json.dumps({"type": "ping"}) + "\n"
        finally:
            if registered:
                with self._lock:
                    generation = self._gens.get(generation_id)
                    if generation:
                        removed = generation["async_subscribers"].pop(
                            subscriber_id,
                            None,
                        )
                        if removed is not None:
                            generation["subscriber_count"] = max(
                                0,
                                int(generation["subscriber_count"]) - 1,
                            )
                        if (
                            generation["status"] != "active"
                            and not generation["subscriber_count"]
                        ):
                            self._schedule_cleanup_unlocked(generation_id)

    def mark_done(self, generation_id: str, status: str = "done"):
        """Mark a generation stream as done and notify subscribers."""
        with self._lock:
            g = self._gens.get(generation_id)
            if not g:
                return
            g["status"] = status
            g["condition"].notify_all()
            self._notify_async_subscribers_unlocked(g)
            chat_id = g["chat_id"]
            if self._by_chat.get(chat_id) == generation_id:
                self._by_chat.pop(chat_id, None)
            if not g["subscriber_count"]:
                self._schedule_cleanup_unlocked(generation_id)

    @staticmethod
    def _notify_async_subscribers_unlocked(generation: Dict[str, Any]) -> None:
        """Wake async subscribers from a publisher thread (hub lock held)."""

        subscribers = generation.get("async_subscribers") or {}
        stale_subscribers = []
        for subscriber_id, (loop, event) in list(subscribers.items()):
            if loop.is_closed():
                stale_subscribers.append(subscriber_id)
                continue
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                stale_subscribers.append(subscriber_id)
        for subscriber_id in stale_subscribers:
            subscribers.pop(subscriber_id, None)
        if stale_subscribers:
            generation["subscriber_count"] = max(
                0,
                int(generation["subscriber_count"]) - len(stale_subscribers),
            )

    def get_status(self, chat_id: str) -> dict:
        """Return the streaming status for a chat."""
        with self._lock:
            gen = self._by_chat.get(chat_id)
            if not gen:
                return {"active": False}
            g = self._gens.get(gen)
            if not g:
                return {"active": False}
            return {
                "active": g["status"] == "active",
                "generation_id": gen,
                "last_seq": g["seq"],
                "started_at": g["created_at"],
                "status": g["status"],
                "metadata": g.get("metadata") or {},
            }

    def get_chat_for_generation(self, generation_id: str) -> Optional[str]:
        """Return the chat_id associated with a generation."""
        with self._lock:
            g = self._gens.get(generation_id)
            return g["chat_id"] if g else None

    def _cleanup_unlocked(self, generation_id: str):
        """Remove a generation's data from the hub (caller must hold lock)."""
        g = self._gens.pop(generation_id, None)
        if not g:
            return
        chat_id = g.get("chat_id")
        if chat_id and self._by_chat.get(chat_id) == generation_id:
            self._by_chat.pop(chat_id, None)

    def _schedule_cleanup_unlocked(self, generation_id: str) -> None:
        """Schedule bounded cleanup while preserving a short attach window."""

        generation = self._gens.get(generation_id)
        if not generation or generation["status"] == "active":
            return

        deadline = generation.get("cleanup_deadline")
        if deadline is None:
            deadline = time.monotonic() + self._completed_retention_seconds
            generation["cleanup_deadline"] = deadline

        if deadline <= time.monotonic() and not generation["subscriber_count"]:
            self._cleanup_unlocked(generation_id)
            return

        if not generation.get("cleanup_scheduled"):
            heapq.heappush(self._cleanup_deadlines, (deadline, generation_id))
            generation["cleanup_scheduled"] = True

        if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_completed_generations,
                name="omlorix-stream-cleanup",
                daemon=True,
            )
            self._cleanup_thread.start()
        self._cleanup_condition.notify_all()

    def _cleanup_completed_generations(self) -> None:
        """Remove expired completed streams from the shared deadline heap."""

        while True:
            with self._lock:
                if not self._cleanup_deadlines:
                    self._cleanup_thread = None
                    return

                deadline, generation_id = self._cleanup_deadlines[0]
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    self._cleanup_condition.wait(timeout=remaining)
                    continue

                heapq.heappop(self._cleanup_deadlines)
                generation = self._gens.get(generation_id)
                if not generation:
                    continue
                if generation.get("cleanup_deadline") != deadline:
                    continue

                generation["cleanup_scheduled"] = False
                if generation["status"] != "active" and not generation["subscriber_count"]:
                    self._cleanup_unlocked(generation_id)


class CancelRegistry:
    def __init__(self):
        self._fallback = _InMemoryCancelRegistry()
        self._handles = _ActiveCancellationHandles()
        self._monitor_lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None

    def reserve(self, generation_id: str, user_id: str) -> bool:
        """Bind a client-created generation ID to its authenticated owner.

        The reservation is created before a background worker starts. This
        closes the early-stop race where the cancel endpoint previously could
        not authorize a generation until its first stream event existed.
        """
        client = get_redis_client()
        if client is None:
            return self._fallback.reserve(generation_id, user_id)
        try:
            created = client.set(
                _generation_owner_key(generation_id),
                user_id,
                ex=_STREAM_TTL_SECONDS,
                nx=True,
            )
            if created:
                self._fallback.reserve(generation_id, user_id)
                return True
            return False
        except Exception:
            return self._fallback.reserve(generation_id, user_id)

    def is_owned_by(self, generation_id: str, user_id: str) -> bool:
        """Authorize cancellation before the generation-to-chat map exists."""
        client = get_redis_client()
        if client is None:
            return self._fallback.is_owned_by(generation_id, user_id)
        try:
            return client.get(_generation_owner_key(generation_id)) == user_id
        except Exception:
            return self._fallback.is_owned_by(generation_id, user_id)

    def set_active(self, chat_id: str, generation_id: str):
        """Register a generation as active for a chat via Redis (falls back to in-memory)."""
        client = get_redis_client()
        if client is None:
            self._fallback.set_active(chat_id, generation_id)
            return
        try:
            if chat_id:
                client.set(_chat_active_key(chat_id), generation_id, ex=_STREAM_TTL_SECONDS)
                client.set(_generation_chat_key(generation_id), chat_id, ex=_STREAM_TTL_SECONDS)
        except Exception:
            self._fallback.set_active(chat_id, generation_id)

    def get_active(self, chat_id: str) -> Optional[str]:
        """Get the active generation ID for a chat via Redis."""
        client = get_redis_client()
        if client is None:
            return self._fallback.get_active(chat_id)
        try:
            value = client.get(_chat_active_key(chat_id))
            return value if isinstance(value, str) and value else None
        except Exception:
            return self._fallback.get_active(chat_id)

    def clear_active_if_match(self, chat_id: str, generation_id: str):
        """Atomically clear the active generation via Redis if it matches."""
        client = get_redis_client()
        if client is None:
            self._fallback.clear_active_if_match(chat_id, generation_id)
            return
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] "
            "then return redis.call('del', KEYS[1]) "
            "else return 0 end"
        )
        try:
            client.eval(script, 1, _chat_active_key(chat_id), generation_id)
        except Exception:
            self._fallback.clear_active_if_match(chat_id, generation_id)

    def cancel(self, generation_id: str):
        """Set the distributed flag and interrupt local provider reads."""
        # Always set the local flag, even when Redis is healthy. Provider
        # adapters can therefore observe same-process cancellation without a
        # network round trip, while Redis still carries it across workers.
        self._fallback.cancel(generation_id)
        self._handles.cancel(generation_id)
        client = get_redis_client()
        if client is None:
            return
        try:
            client.set(_cancel_key(generation_id), "1", ex=_CANCEL_TTL_SECONDS)
        except Exception:
            pass

    def is_cancelled(self, generation_id: str) -> bool:
        """Check cancellation flag via Redis."""
        if self._fallback.is_cancelled(generation_id):
            return True
        client = get_redis_client()
        if client is None:
            return False
        try:
            value = client.get(_cancel_key(generation_id))
            return value == "1"
        except Exception:
            return False

    def register_handle(self, generation_id: str, close: Callable[[], None]) -> str:
        """Register a close callback for an active upstream provider stream."""
        token = self._handles.register(generation_id, close)
        if self.is_cancelled(generation_id):
            self._handles.cancel(generation_id)
        else:
            self._ensure_distributed_monitor()
        return token

    def unregister_handle(self, generation_id: str, token: str) -> None:
        """Unregister an upstream provider stream after iteration finishes."""
        self._handles.unregister(generation_id, token)

    def wait_local(self, generation_id: str, timeout: float) -> bool:
        """Wait until local or distributed cancellation reaches this worker."""
        return self._handles.wait(generation_id, timeout)

    def _ensure_distributed_monitor(self) -> None:
        """Start one lightweight Redis listener loop for this process.

        A batched poll is used instead of one polling thread per generation.
        It works with the project's existing Redis client and keeps cancellation
        portable across deployments without requiring a dedicated Pub/Sub
        connection lifecycle.
        """
        with self._monitor_lock:
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._monitor_thread = threading.Thread(
                target=self._monitor_distributed_cancellations,
                name="chat-generation-cancel-monitor",
                daemon=True,
            )
            self._monitor_thread.start()

    def _monitor_distributed_cancellations(self) -> None:
        """Propagate Redis cancellation flags to process-local socket handles."""
        while True:
            generation_ids = self._handles.generation_ids()
            if not generation_ids:
                time.sleep(0.1)
                continue
            client = get_redis_client()
            if client is not None:
                try:
                    values = client.mget([_cancel_key(value) for value in generation_ids])
                    for generation_id, value in zip(generation_ids, values):
                        if value == "1":
                            self._fallback.cancel(generation_id)
                            self._handles.cancel(generation_id)
                except Exception:
                    logger.debug("Distributed generation cancellation poll failed", exc_info=True)
            time.sleep(0.05)

    def clear(self, generation_id: str):
        """Clear cancellation, ownership, and active local provider handles."""
        self._fallback.clear(generation_id)
        self._handles.clear(generation_id)
        client = get_redis_client()
        if client is None:
            return
        try:
            client.delete(_cancel_key(generation_id), _generation_owner_key(generation_id))
        except Exception:
            pass


class StreamHub:
    """Redis-backed stream hub with in-memory fallback."""

    def __init__(
        self,
        max_lines: int = _STREAM_MAX_LINES,
        max_bytes: int = _STREAM_MAX_BYTES,
        completed_retention_seconds: float = _IN_MEMORY_COMPLETED_RETENTION_SECONDS,
    ):
        self._max_lines = max(1, int(max_lines))
        self._max_bytes = max(1, int(max_bytes))
        self._fallback = _InMemoryStreamHub(
            max_lines=self._max_lines,
            max_bytes=self._max_bytes,
            completed_retention_seconds=completed_retention_seconds,
        )

    def start(self, generation_id: str, chat_id: str, metadata: Optional[dict] = None):
        """Start a new stream via Redis (falls back to in-memory)."""
        client = get_redis_client()
        if client is None:
            self._fallback.start(generation_id, chat_id, metadata=metadata)
            return

        meta_key = _stream_meta_key(generation_id)
        events_key = _stream_events_key(generation_id)
        sizes_key = _stream_event_sizes_key(generation_id)
        now = time.time()
        try:
            if client.exists(meta_key):
                existing_chat_id = str(client.hget(meta_key, "chat_id") or "")
                if chat_id and not existing_chat_id:
                    # A generation ID is single-owner reserved, so this
                    # compare-and-upgrade cannot steal another user's stream.
                    client.hset(meta_key, mapping={"chat_id": chat_id})
                    client.set(_chat_active_key(chat_id), generation_id, ex=_STREAM_TTL_SECONDS)
                    client.set(_generation_chat_key(generation_id), chat_id, ex=_STREAM_TTL_SECONDS)
                return
            client.hset(
                meta_key,
                mapping={
                    "chat_id": chat_id,
                    "status": "active",
                    "seq": 0,
                    "buffer_bytes": 0,
                    "created_at": f"{now:.6f}",
                    "metadata": json.dumps(metadata or {}, separators=(",", ":"), ensure_ascii=True),
                },
            )
            client.set(_chat_active_key(chat_id), generation_id, ex=_STREAM_TTL_SECONDS)
            client.set(_generation_chat_key(generation_id), chat_id, ex=_STREAM_TTL_SECONDS)
            client.expire(meta_key, _STREAM_TTL_SECONDS)
            client.expire(events_key, _STREAM_TTL_SECONDS)
            client.expire(sizes_key, _STREAM_TTL_SECONDS)
        except Exception:
            self._fallback.start(generation_id, chat_id, metadata=metadata)

    def publish_dict(self, generation_id: str, payload: dict) -> int:
        """Publish a dict payload to the Redis-backed stream."""
        return self.publish_line(generation_id, json.dumps(payload))

    def publish_line(self, generation_id: str, line: str) -> int:
        """Publish a line to the Redis stream."""
        raw_line = line
        client = get_redis_client()
        if client is None:
            return self._fallback.publish_line(generation_id, raw_line)

        meta_key = _stream_meta_key(generation_id)
        events_key = _stream_events_key(generation_id)
        sizes_key = _stream_event_sizes_key(generation_id)
        try:
            # The script allocates the sequence and appends the normalized line
            # as one indivisible operation. Python only prepares the JSON shape.
            line, add_sequence = _prepare_redis_stream_line(
                raw_line,
                self._max_bytes,
            )
            result = client.eval(
                _REDIS_APPEND_AND_TRIM_SCRIPT,
                3,
                events_key,
                sizes_key,
                meta_key,
                line,
                "1" if add_sequence else "0",
                self._max_lines,
                self._max_bytes,
            )
            seq = int(result[0])
            if seq == -1:
                return -1
            if seq == -2:
                raise StreamLineLimitExceeded(
                    f"Stream line exceeds the {self._max_bytes}-byte retention limit."
                )

            # The event is durably appended at this point. A later TTL refresh
            # failure must not republish it into the fallback with a different
            # sequence number; the existing key TTLs still provide bounded
            # cleanup, and a future successful publish can refresh them again.
            try:
                client.expire(meta_key, _STREAM_TTL_SECONDS)
                client.expire(events_key, _STREAM_TTL_SECONDS)
                client.expire(sizes_key, _STREAM_TTL_SECONDS)
                client.expire(_generation_chat_key(generation_id), _STREAM_TTL_SECONDS)
            except Exception:
                logger.warning(
                    "Failed to refresh Redis stream TTLs generation_id=%s",
                    generation_id,
                    exc_info=True,
                )
            return seq
        except StreamLineLimitExceeded:
            raise
        except Exception:
            return self._fallback.publish_line(generation_id, raw_line)

    def subscribe(
        self,
        generation_id: str,
        from_seq: int = 0,
        *,
        heartbeat_seconds: float | None = None,
    ) -> Generator[str, None, None]:
        """Subscribe to a Redis-backed stream, yielding lines."""
        client = get_redis_client()
        if client is None:
            yield from self._fallback.subscribe(
                generation_id,
                from_seq=from_seq,
                heartbeat_seconds=heartbeat_seconds,
            )
            return

        meta_key = _stream_meta_key(generation_id)
        events_key = _stream_events_key(generation_id)

        try:
            if not client.exists(meta_key) and not client.exists(events_key):
                return
        except Exception:
            yield from self._fallback.subscribe(
                generation_id,
                from_seq=from_seq,
                heartbeat_seconds=heartbeat_seconds,
            )
            return

        try:
            heartbeat = float(heartbeat_seconds) if heartbeat_seconds is not None else 5.0
        except (TypeError, ValueError):
            heartbeat = 5.0
        heartbeat_ms = max(50, min(int(heartbeat * 1000), 5000))

        # XREAD returns retained entries immediately and then blocks for new
        # ones. Reading in bounded batches keeps replay memory stable; the
        # append script above keeps Redis retention within both hard limits.
        last_id = "0-0"
        cursor = max(0, int(from_seq or 0))
        while True:
            try:
                status = client.hget(meta_key, "status")
            except Exception:
                status = "done"

            try:
                streams = client.xread(
                    {events_key: last_id},
                    count=100,
                    block=heartbeat_ms,
                )
            except Exception:
                streams = []

            emitted = False
            for _, messages in streams:
                for message_id, fields in messages:
                    emitted = True
                    last_id = message_id
                    line = fields.get("line")
                    if not isinstance(line, str):
                        continue
                    try:
                        seq = int(fields.get("seq") or "0")
                    except Exception:
                        seq = 0
                    if seq <= cursor:
                        continue
                    if seq > cursor + 1:
                        logger.warning(
                            "Disconnecting Redis stream subscriber after "
                            "retention gap generation_id=%s cursor=%s next_seq=%s",
                            generation_id,
                            cursor,
                            seq,
                        )
                        yield _retention_gap_error_line() + "\n"
                        return
                    cursor = seq
                    if seq > from_seq:
                        yield line + "\n"

            if emitted:
                continue

            if status != "active":
                return

            yield json.dumps({"type": "ping"}) + "\n"

    async def subscribe_async(
        self,
        generation_id: str,
        from_seq: int = 0,
        *,
        heartbeat_seconds: float | None = None,
    ) -> AsyncGenerator[str, None]:
        """Subscribe with native async Redis I/O for HTTP streaming clients."""

        try:
            client = await get_async_redis_client()
        except Exception:
            logger.debug("Async Redis stream client unavailable", exc_info=True)
            client = None
        if client is None:
            async for line in self._fallback.subscribe_async(
                generation_id,
                from_seq=from_seq,
                heartbeat_seconds=heartbeat_seconds,
            ):
                yield line
            return

        meta_key = _stream_meta_key(generation_id)
        events_key = _stream_events_key(generation_id)
        signal_key = _stream_signal_key(generation_id)

        try:
            meta_exists = await client.exists(meta_key)
            events_exist = await client.exists(events_key)
            signal_exists = await client.exists(signal_key)
        except Exception:
            async for line in self._fallback.subscribe_async(
                generation_id,
                from_seq=from_seq,
                heartbeat_seconds=heartbeat_seconds,
            ):
                yield line
            return

        if not meta_exists and not events_exist and not signal_exists:
            # The synchronous publisher may have switched to process-local
            # state during a transient Redis outage. Prefer that available
            # stream over returning an empty response.
            async for line in self._fallback.subscribe_async(
                generation_id,
                from_seq=from_seq,
                heartbeat_seconds=heartbeat_seconds,
            ):
                yield line
            return

        try:
            heartbeat = (
                float(heartbeat_seconds)
                if heartbeat_seconds is not None
                else 5.0
            )
        except (TypeError, ValueError):
            heartbeat = 5.0
        heartbeat_ms = max(50, min(int(heartbeat * 1000), 5000))

        # Unlike the synchronous compatibility API above, this XREAD yields
        # control to the ASGI event loop while Redis waits for the next batch.
        # Cancellation of the response also cancels the pending Redis command.
        last_id = "0-0"
        last_signal_id = "0-0"
        cursor = max(0, int(from_seq or 0))
        initial_status_known = True
        try:
            initial_status = await client.hget(meta_key, "status")
        except Exception:
            initial_status = None
            initial_status_known = False
        # Completed streams created before the terminal-signal rollout still
        # replay without waiting for a heartbeat before the final status check.
        # A successful missing-value lookup also means the stream metadata has
        # expired or been removed, so drain any retained events and close. Only
        # an actual Redis lookup failure leaves the terminal state unknown.
        terminal_seen = initial_status_known and initial_status != "active"
        while True:
            read_failed = False
            try:
                streams = await client.xread(
                    {
                        events_key: last_id,
                        signal_key: last_signal_id,
                    },
                    count=100,
                    # Once completion is known, drain retained events without
                    # another blocking heartbeat before closing the response.
                    block=None if terminal_seen else heartbeat_ms,
                )
            except Exception:
                streams = []
                read_failed = True

            received_message = False
            for stream_name, messages in streams:
                is_signal_stream = stream_name == signal_key
                for message_id, fields in messages:
                    received_message = True
                    if is_signal_stream:
                        last_signal_id = message_id
                        if fields.get("terminal") is not None:
                            terminal_seen = True
                        continue
                    last_id = message_id
                    line = fields.get("line")
                    if not isinstance(line, str):
                        continue
                    try:
                        sequence = int(fields.get("seq") or "0")
                    except Exception:
                        sequence = 0
                    if sequence <= cursor:
                        continue
                    if sequence > cursor + 1:
                        logger.warning(
                            "Disconnecting async Redis stream subscriber after "
                            "retention gap generation_id=%s cursor=%s next_seq=%s",
                            generation_id,
                            cursor,
                            sequence,
                        )
                        yield _retention_gap_error_line() + "\n"
                        return
                    cursor = sequence
                    if sequence > from_seq:
                        yield line + "\n"

            if received_message:
                continue

            if terminal_seen:
                return

            try:
                status = await client.hget(meta_key, "status")
            except Exception:
                # Keep the stream recoverable during a transient Redis error.
                # This is distinct from a successful lookup of a missing key,
                # which is terminal and must not emit heartbeats forever.
                pass
            else:
                if status != "active":
                    return

            if read_failed:
                # A failed XREAD may return immediately. Retrying at the normal
                # heartbeat cadence prevents an outage from creating a hot
                # loop or flooding the client with ping lines.
                await asyncio.sleep(heartbeat_ms / 1000)
            yield json.dumps({"type": "ping"}) + "\n"

    def mark_done(self, generation_id: str, status: str = "done"):
        """Mark a stream as done in Redis."""
        client = get_redis_client()
        if client is None:
            self._fallback.mark_done(generation_id, status=status)
            return

        meta_key = _stream_meta_key(generation_id)
        events_key = _stream_events_key(generation_id)
        sizes_key = _stream_event_sizes_key(generation_id)
        signal_key = _stream_signal_key(generation_id)
        generation_chat_key = _generation_chat_key(generation_id)
        try:
            chat_id = client.get(generation_chat_key) or client.hget(meta_key, "chat_id")
            normalized_chat_id = chat_id if isinstance(chat_id, str) else ""
            client.eval(
                _REDIS_MARK_DONE_SCRIPT,
                6,
                meta_key,
                signal_key,
                events_key,
                sizes_key,
                _chat_active_key(normalized_chat_id),
                generation_chat_key,
                status,
                f"{time.time():.6f}",
                generation_id,
                _STREAM_TTL_SECONDS,
                "1" if normalized_chat_id else "0",
            )
        except Exception:
            self._fallback.mark_done(generation_id, status=status)

    def get_status(self, chat_id: str) -> dict:
        """Get streaming status from Redis."""
        client = get_redis_client()
        if client is None:
            return self._fallback.get_status(chat_id)

        try:
            generation_id = client.get(_chat_active_key(chat_id))
            if not generation_id:
                return {"active": False}

            meta_key = _stream_meta_key(generation_id)
            meta = client.hgetall(meta_key) or {}
            if not meta:
                return {"active": False}

            metadata = {}
            try:
                raw_metadata = meta.get("metadata")
                if isinstance(raw_metadata, str) and raw_metadata:
                    parsed = json.loads(raw_metadata)
                    if isinstance(parsed, dict):
                        metadata = parsed
            except Exception:
                metadata = {}

            try:
                started_at = float(meta.get("created_at") or "0")
            except Exception:
                started_at = 0.0
            try:
                last_seq = int(meta.get("seq") or "0")
            except Exception:
                last_seq = 0

            status = str(meta.get("status") or "done")
            return {
                "active": status == "active",
                "generation_id": generation_id,
                "last_seq": last_seq,
                "started_at": started_at,
                "status": status,
                "metadata": metadata,
            }
        except Exception:
            return self._fallback.get_status(chat_id)

    def get_chat_for_generation(self, generation_id: str) -> Optional[str]:
        """Get the chat_id for a generation from Redis."""
        client = get_redis_client()
        if client is None:
            return self._fallback.get_chat_for_generation(generation_id)
        try:
            chat_id = client.get(_generation_chat_key(generation_id))
            if isinstance(chat_id, str) and chat_id:
                return chat_id
            chat_id = client.hget(_stream_meta_key(generation_id), "chat_id")
            return chat_id if isinstance(chat_id, str) and chat_id else None
        except Exception:
            return self._fallback.get_chat_for_generation(generation_id)


stream_hub = StreamHub()
cancel_registry = CancelRegistry()


def _close_provider_resource(resource: Any) -> None:
    """Best-effort close an SDK stream or its underlying HTTP response."""
    candidates = [
        resource,
        getattr(resource, "response", None),
        getattr(resource, "_response", None),
        getattr(resource, "_client", None),
        getattr(resource, "_api_client", None),
    ]
    for candidate in candidates:
        close = getattr(candidate, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Provider resource close failed", exc_info=True)
                continue
            return


def interruptible_provider_stream(
    iterable: Any,
    generation_id: str | None,
    *,
    close_resource: Any | None = None,
    close_resource_on_finish: bool = True,
    poll_seconds: float = 0.05,
    before_wait: Callable[[], None] | None = None,
) -> Iterator[Any]:
    """Iterate a blocking provider stream while remaining promptly cancellable.

    Most synchronous SDK iterators block inside ``next()`` until the provider
    emits another event. A daemon producer owns that blocking call while the
    generation worker consumes a bounded queue. Cancellation can therefore
    wake the worker immediately, close the upstream socket, and enter each
    provider's existing partial-response persistence path without waiting for
    another model token.

    The synthetic wake-up value is safe because every integrated provider
    checks ``cancel_registry`` before interpreting its stream item.

    ``before_wait`` runs on the consumer thread immediately before each queue
    wait. Providers use it for best-effort cleanup such as releasing database
    transactions before waiting on upstream network I/O.
    """
    if not generation_id:
        yield from iterable
        return

    resource = close_resource if close_resource is not None else iterable
    item_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue(maxsize=8)
    producer_stopped = threading.Event()

    def _offer(kind: str, value: Any = None) -> None:
        while not producer_stopped.is_set():
            try:
                item_queue.put((kind, value), timeout=poll_seconds)
                return
            except queue.Full:
                continue

    def _produce() -> None:
        try:
            for item in iterable:
                if producer_stopped.is_set():
                    return
                _offer("item", item)
        except Exception as exc:
            _offer("error", exc)
        finally:
            _offer("done")

    handle_token = cancel_registry.register_handle(
        generation_id,
        lambda: _close_provider_resource(resource),
    )
    producer: threading.Thread | None = None
    try:
        if cancel_registry.is_cancelled(generation_id):
            yield _CANCELLATION_WAKEUP
            return

        producer = threading.Thread(
            target=_produce,
            name=f"provider-stream-{generation_id[:12]}",
            daemon=True,
        )
        producer.start()

        while True:
            if cancel_registry.wait_local(generation_id, 0):
                yield _CANCELLATION_WAKEUP
                return
            if before_wait is not None:
                try:
                    before_wait()
                except Exception:
                    logger.debug(
                        "Provider stream before-wait callback failed",
                        exc_info=True,
                    )
            try:
                kind, value = item_queue.get(timeout=poll_seconds)
            except queue.Empty:
                continue
            if cancel_registry.wait_local(generation_id, 0):
                yield _CANCELLATION_WAKEUP
                return
            if kind == "item":
                yield value
                continue
            if kind == "error":
                if cancel_registry.is_cancelled(generation_id):
                    yield _CANCELLATION_WAKEUP
                    return
                raise value
            return
    finally:
        producer_stopped.set()
        # A separately supplied SDK client is useful for interrupting a lazy
        # generator but may be reused for a following tool-call continuation.
        # Close it on cancellation, while normal iteration closes only the
        # exhausted stream unless the caller explicitly opts in.
        if close_resource_on_finish or cancel_registry.is_cancelled(generation_id):
            _close_provider_resource(resource)
        if resource is not iterable:
            _close_provider_resource(iterable)
        cancel_registry.unregister_handle(generation_id, handle_token)
        if producer is not None and producer.is_alive():
            producer.join(timeout=0.1)
