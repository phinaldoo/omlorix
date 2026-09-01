from __future__ import annotations

import asyncio
import json
import threading
import time
from itertools import islice

import pytest

from app.chats import streaming


def test_interruptible_provider_stream_wakes_without_another_provider_chunk(monkeypatch):
    """Cancellation closes a stalled stream and wakes its consumer promptly."""

    monkeypatch.setattr(streaming, "get_redis_client", lambda: None)
    generation_id = "generation-interruptible"
    close_called = threading.Event()

    class StalledStream:
        def __init__(self):
            self.first = True

        def __iter__(self):
            return self

        def __next__(self):
            if self.first:
                self.first = False
                return "first"
            close_called.wait(timeout=5)
            raise StopIteration

        def close(self):
            close_called.set()

    stream = StalledStream()
    iterator = streaming.interruptible_provider_stream(stream, generation_id)
    try:
        assert next(iterator) == "first"

        started_at = time.monotonic()
        streaming.cancel_registry.cancel(generation_id)
        wakeup = next(iterator)

        assert isinstance(wakeup, streaming._CancellationWakeup)
        assert close_called.is_set()
        assert time.monotonic() - started_at < 0.5
    finally:
        iterator.close()
        streaming.cancel_registry.clear(generation_id)


def test_interruptible_provider_stream_runs_before_wait_on_consumer_thread(monkeypatch):
    """Provider cleanup hooks cannot move SQLAlchemy work to the producer."""

    monkeypatch.setattr(streaming, "get_redis_client", lambda: None)
    caller_thread = threading.get_ident()
    callback_threads = []

    def before_wait():
        callback_threads.append(threading.get_ident())
        if len(callback_threads) == 1:
            raise RuntimeError("best-effort cleanup failed")

    result = list(
        streaming.interruptible_provider_stream(
            iter(["first"]),
            "generation-before-wait",
            before_wait=before_wait,
        )
    )

    assert result == ["first"]
    assert callback_threads
    assert set(callback_threads) == {caller_thread}


def test_cancel_registry_reserves_client_generation_ids_per_user(monkeypatch):
    """Client IDs can be authorized early but cannot be reused for another job."""

    monkeypatch.setattr(streaming, "get_redis_client", lambda: None)
    registry = streaming.CancelRegistry()

    assert registry.reserve("generation-client", "user-1") is True
    assert registry.is_owned_by("generation-client", "user-1") is True
    assert registry.is_owned_by("generation-client", "user-2") is False
    assert registry.reserve("generation-client", "user-1") is False

    registry.clear("generation-client")
    assert registry.is_owned_by("generation-client", "user-1") is False


def test_cancel_registry_prefers_redis_ownership_over_stale_local_data(monkeypatch):
    """A stale local reservation must not override authoritative Redis data."""

    class FakeRedis:
        def get(self, _key):
            return "user-2"

    registry = streaming.CancelRegistry()
    registry._fallback.reserve("generation-client", "user-1")
    monkeypatch.setattr(streaming, "get_redis_client", lambda: FakeRedis())

    assert registry.is_owned_by("generation-client", "user-1") is False
    assert registry.is_owned_by("generation-client", "user-2") is True


def test_cancel_registry_uses_local_ownership_when_redis_lookup_fails(monkeypatch):
    """Process-local ownership remains the availability fallback for Redis."""

    class FailingRedis:
        def get(self, _key):
            raise RuntimeError("redis unavailable")

    registry = streaming.CancelRegistry()
    registry._fallback.reserve("generation-client", "user-1")
    monkeypatch.setattr(streaming, "get_redis_client", lambda: FailingRedis())

    assert registry.is_owned_by("generation-client", "user-1") is True


def test_provider_close_tries_underlying_response_after_wrapper_failure():
    """A broken wrapper close must not prevent its response from closing."""

    close_calls = []

    class Response:
        def close(self):
            close_calls.append("response")

    class Wrapper:
        response = Response()

        def close(self):
            close_calls.append("wrapper")
            raise RuntimeError("wrapper close failed")

    streaming._close_provider_resource(Wrapper())

    assert close_calls == ["wrapper", "response"]


def test_distributed_cancel_monitor_closes_handle_owned_by_another_worker(monkeypatch):
    """A Redis flag reaches the process that owns the upstream socket."""

    cancelled_in_redis = threading.Event()
    handle_closed = threading.Event()

    class FakeRedis:
        def get(self, _key):
            return "1" if cancelled_in_redis.is_set() else None

        def mget(self, keys):
            value = "1" if cancelled_in_redis.is_set() else None
            return [value for _key in keys]

    monkeypatch.setattr(streaming, "get_redis_client", lambda: FakeRedis())
    registry = streaming.CancelRegistry()
    token = registry.register_handle("generation-remote", handle_closed.set)
    try:
        cancelled_in_redis.set()
        assert handle_closed.wait(timeout=0.5)
    finally:
        registry.unregister_handle("generation-remote", token)
        registry.clear("generation-remote")


def test_active_in_memory_generation_replays_more_than_two_thousand_lines():
    """Reconnect replay retains the complete active generation."""

    hub = streaming._InMemoryStreamHub()
    hub.start("generation-1", "chat-1")
    line_count = 2_505

    for index in range(line_count):
        hub.publish_dict("generation-1", {"t": "delta", "index": index})

    replay = hub.subscribe("generation-1", from_seq=0)
    lines = list(islice(replay, line_count))
    replay.close()

    assert len(lines) == line_count
    assert json.loads(lines[0])["index"] == 0
    assert json.loads(lines[-1])["index"] == line_count - 1


def test_in_memory_reconnect_cursor_replays_every_subsequent_line():
    """A high sequence cursor does not depend on a fixed replay window."""

    hub = streaming._InMemoryStreamHub()
    hub.start("generation-1", "chat-1")
    line_count = 2_505
    from_seq = 2_000

    for index in range(line_count):
        hub.publish_dict("generation-1", {"t": "delta", "index": index})

    replay = hub.subscribe("generation-1", from_seq=from_seq)
    lines = list(islice(replay, line_count - from_seq))
    replay.close()

    assert len(lines) == line_count - from_seq
    assert json.loads(lines[0])["seq"] == from_seq + 1
    assert json.loads(lines[-1])["seq"] == line_count


def test_queued_new_chat_streams_do_not_share_an_empty_chat_mapping():
    hub = streaming._InMemoryStreamHub()

    hub.start("generation-1", "")
    hub.start("generation-2", "")

    assert "" not in hub._by_chat
    hub.start("generation-1", "chat-1")
    hub.start("generation-2", "chat-2")
    assert hub.get_status("chat-1")["generation_id"] == "generation-1"
    assert hub.get_status("chat-2")["generation_id"] == "generation-2"


def test_in_memory_generation_retains_only_the_latest_line_limit():
    """A generation never retains more than its configured number of lines."""

    hub = streaming._InMemoryStreamHub(max_lines=3, max_bytes=10_000)
    hub.start("generation-1", "chat-1")

    for index in range(5):
        hub.publish_dict("generation-1", {"t": "delta", "index": index})

    retained = list(hub._gens["generation-1"]["buffer"])

    assert len(retained) == 3
    assert [json.loads(line)["index"] for _, line, _ in retained] == [2, 3, 4]


def test_default_stream_retention_limits_match_security_policy():
    """Production defaults are fixed at 20,000 lines and 50 MiB."""

    assert streaming._STREAM_MAX_LINES == 20_000
    assert streaming._STREAM_MAX_BYTES == 50 * 1024 * 1024


def test_in_memory_generation_retains_only_the_latest_byte_limit():
    """UTF-8 payload bytes, rather than character count, enforce the byte cap."""

    hub = streaming._InMemoryStreamHub(max_lines=100, max_bytes=240)
    hub.start("generation-1", "chat-1")

    for index in range(8):
        hub.publish_dict("generation-1", {"t": "delta", "index": index, "text": "€" * 20})

    generation = hub._gens["generation-1"]
    retained = list(generation["buffer"])

    assert retained
    assert generation["buffer_bytes"] <= 240
    assert sum(line_bytes for _, _, line_bytes in retained) == generation["buffer_bytes"]
    assert json.loads(retained[-1][1])["index"] == 7


def test_in_memory_rejects_one_line_larger_than_the_byte_limit():
    """One oversized line cannot bypass the total retained-byte limit."""

    hub = streaming._InMemoryStreamHub(max_lines=100, max_bytes=64)
    hub.start("generation-1", "chat-1")

    with pytest.raises(streaming.StreamLineLimitExceeded):
        hub.publish_dict("generation-1", {"t": "delta", "text": "x" * 100})

    generation = hub._gens["generation-1"]
    assert generation["seq"] == 0
    assert generation["buffer_bytes"] == 0
    assert list(generation["buffer"]) == []


def test_slow_in_memory_subscriber_is_disconnected_after_retention_gap():
    """Slow subscribers use no private queue and receive an explicit gap error."""

    hub = streaming._InMemoryStreamHub(max_lines=3, max_bytes=10_000)
    hub.start("generation-1", "chat-1")
    hub.publish_dict("generation-1", {"t": "delta", "index": 0})

    subscriber = hub.subscribe("generation-1")
    assert json.loads(next(subscriber))["index"] == 0

    for index in range(1, 6):
        hub.publish_dict("generation-1", {"t": "delta", "index": index})

    gap = json.loads(next(subscriber))
    assert gap["t"] == "e"
    assert gap["i18n_key"] == "chat_stream_retention_limit_exceeded"
    with pytest.raises(StopIteration):
        next(subscriber)

    generation = hub._gens["generation-1"]
    assert generation["subscriber_count"] == 0
    assert "subs" not in generation


def test_in_memory_subscriber_honors_short_deadline_heartbeat():
    """Worker admission checks are not delayed by the browser heartbeat."""

    hub = streaming._InMemoryStreamHub(max_lines=3, max_bytes=10_000)
    hub.start("generation-1", "chat-1")
    subscriber = hub.subscribe("generation-1", heartbeat_seconds=0.05)
    started = time.monotonic()
    try:
        heartbeat = json.loads(next(subscriber))
    finally:
        subscriber.close()

    assert heartbeat == {"type": "ping"}
    assert time.monotonic() - started < 0.5


def test_in_memory_reconnect_at_retention_boundary_replays_available_lines():
    """A legitimate reconnect at the oldest retained cursor remains complete."""

    hub = streaming._InMemoryStreamHub(max_lines=3, max_bytes=10_000)
    hub.start("generation-1", "chat-1")
    for index in range(5):
        hub.publish_dict("generation-1", {"t": "delta", "index": index})

    replay = hub.subscribe("generation-1", from_seq=2)
    lines = [json.loads(next(replay)) for _ in range(3)]
    replay.close()

    assert [line["seq"] for line in lines] == [3, 4, 5]
    assert [line["index"] for line in lines] == [2, 3, 4]


def test_done_in_memory_generation_allows_late_attach_then_expires():
    """A fast completion remains attachable without leaking indefinitely."""

    hub = streaming._InMemoryStreamHub(
        max_lines=3,
        max_bytes=10_000,
        completed_retention_seconds=0.02,
    )
    hub.start("generation-1", "chat-1")
    hub.publish_dict("generation-1", {"t": "delta"})

    hub.mark_done("generation-1")

    # The producer may finish before the HTTP response generator subscribes.
    # Keep the completed buffer available throughout that handoff window.
    assert "generation-1" in hub._gens
    replay = list(hub.subscribe("generation-1", from_seq=0))
    assert len(replay) == 1
    assert json.loads(replay[0])["t"] == "delta"

    deadline = time.monotonic() + 1
    while "generation-1" in hub._gens and time.monotonic() < deadline:
        time.sleep(0.01)

    assert "generation-1" not in hub._gens
    assert "chat-1" not in hub._by_chat


def test_redis_publish_uses_atomic_line_and_byte_retention_limits(monkeypatch):
    """Redis publishing passes both hard limits to one atomic append/trim script."""

    class FakeRedis:
        def __init__(self):
            self.eval_calls = []
            self.expired_keys = []

        def eval(self, script, key_count, *args):
            self.eval_calls.append((script, key_count, args))
            return [1, "1-0", 1, len(str(args[-4]).encode("utf-8"))]

        def expire(self, key, _seconds):
            self.expired_keys.append(key)
            return True

    client = FakeRedis()
    monkeypatch.setattr(streaming, "get_redis_client", lambda: client)

    hub = streaming.StreamHub(max_lines=20_000, max_bytes=50 * 1024 * 1024)
    sequence = hub.publish_dict("generation-1", {"t": "delta"})

    assert sequence == 1
    assert len(client.eval_calls) == 1
    script, key_count, args = client.eval_calls[0]
    assert key_count == 3
    assert "'MAXLEN', '=', max_lines" in script
    assert "redis.call('HGET', meta_key, 'seq')" in script
    assert "redis.call('HSET', meta_key, 'seq', sequence)" in script
    assert args[-2:] == (20_000, 50 * 1024 * 1024)
    assert streaming._stream_event_sizes_key("generation-1") in client.expired_keys


def test_redis_publish_fallback_assigns_its_own_sequence(monkeypatch):
    """A failed Redis append passes raw input to the in-memory fallback."""

    class FailingRedis:
        def eval(self, *_args):
            raise RuntimeError("Redis append failed")

    monkeypatch.setattr(streaming, "get_redis_client", lambda: FailingRedis())
    hub = streaming.StreamHub()
    hub._fallback.start("generation-1", "chat-1")

    sequence = hub.publish_dict("generation-1", {"t": "delta"})
    retained = hub._fallback._gens["generation-1"]["buffer"]
    payload = json.loads(retained[0][1])

    assert sequence == 1
    assert payload["seq"] == 1


def test_redis_ttl_failure_does_not_republish_a_successful_append(monkeypatch):
    """Post-append housekeeping cannot replace Redis's committed sequence."""

    class RedisWithFailingExpiry:
        def eval(self, *_args):
            return [8, "8-0", 8, 512]

        def expire(self, *_args):
            raise RuntimeError("TTL refresh failed")

    monkeypatch.setattr(
        streaming,
        "get_redis_client",
        lambda: RedisWithFailingExpiry(),
    )
    hub = streaming.StreamHub()

    assert hub.publish_dict("generation-1", {"t": "delta"}) == 8
    assert "generation-1" not in hub._fallback._gens


def test_redis_rejects_one_line_larger_than_the_byte_limit(monkeypatch):
    """Redis is never called for a line that cannot fit in its byte budget."""

    class FakeRedis:
        def __init__(self):
            self.eval_calls = 0

        def eval(self, *_args):
            self.eval_calls += 1

    client = FakeRedis()
    monkeypatch.setattr(streaming, "get_redis_client", lambda: client)
    hub = streaming.StreamHub(max_lines=100, max_bytes=64)

    with pytest.raises(streaming.StreamLineLimitExceeded):
        hub.publish_dict("generation-1", {"t": "delta", "text": "x" * 100})

    assert client.eval_calls == 0


def test_redis_subscriber_receives_error_after_retention_gap(monkeypatch):
    """Redis subscribers do not silently accept a trimmed replay history."""

    class FakeRedis:
        def exists(self, _key):
            return True

        def hget(self, _key, _field):
            return "active"

        def xread(self, _streams, *, count, block):
            del count, block
            return [
                (
                    "events",
                    [
                        (
                            "3-0",
                            {
                                "seq": "3",
                                "line": json.dumps({"t": "delta", "seq": 3}),
                            },
                        )
                    ],
                )
            ]

    monkeypatch.setattr(streaming, "get_redis_client", lambda: FakeRedis())
    replay = streaming.StreamHub().subscribe("generation-1", from_seq=0)

    gap = json.loads(next(replay))

    assert gap["t"] == "e"
    assert gap["i18n_key"] == "chat_stream_retention_limit_exceeded"
    with pytest.raises(StopIteration):
        next(replay)


def test_redis_reconnect_streams_complete_history_in_batches(monkeypatch):
    """Redis replay drains all retained entries instead of a 2000-line window."""

    line_count = 2_505

    class FakeRedis:
        def exists(self, _key):
            return True

        def hget(self, _key, _field):
            return "active"

        def xread(self, streams, *, count, block):
            del block
            last_id = next(iter(streams.values()))
            last_sequence = int(str(last_id).split("-", 1)[0])
            upper = min(line_count, last_sequence + count)
            if upper <= last_sequence:
                return []
            messages = [
                (
                    f"{sequence}-0",
                    {
                        "seq": str(sequence),
                        "line": json.dumps({"t": "delta", "seq": sequence}),
                    },
                )
                for sequence in range(last_sequence + 1, upper + 1)
            ]
            return [("events", messages)]

    monkeypatch.setattr(streaming, "get_redis_client", lambda: FakeRedis())
    hub = streaming.StreamHub()

    replay = hub.subscribe("generation-1", from_seq=0)
    lines = list(islice(replay, line_count))
    replay.close()

    assert len(lines) == line_count
    assert json.loads(lines[0])["seq"] == 1
    assert json.loads(lines[-1])["seq"] == line_count


@pytest.mark.asyncio
async def test_async_redis_reconnect_streams_complete_history_in_batches(monkeypatch):
    """The ASGI subscriber uses async Redis and preserves cursor replay."""

    line_count = 205

    class FakeAsyncRedis:
        def __init__(self):
            self.hget_calls = 0
            self.xread_calls = 0
            self.terminal_sent = False

        async def exists(self, _key):
            return True

        async def hget(self, _key, _field):
            self.hget_calls += 1
            return "active"

        async def xread(self, streams, *, count, block):
            del block
            self.xread_calls += 1
            last_id = next(iter(streams.values()))
            last_sequence = int(str(last_id).split("-", 1)[0])
            upper = min(line_count, last_sequence + count)
            if upper <= last_sequence:
                if not self.terminal_sent:
                    self.terminal_sent = True
                    return [
                        (
                            streaming._stream_signal_key("generation-1"),
                            [("1-0", {"terminal": "done"})],
                        )
                    ]
                return []
            messages = [
                (
                    f"{sequence}-0",
                    {
                        "seq": str(sequence),
                        "line": json.dumps({"t": "delta", "seq": sequence}),
                    },
                )
                for sequence in range(last_sequence + 1, upper + 1)
            ]
            return [("events", messages)]

    client = FakeAsyncRedis()

    async def get_client():
        return client

    monkeypatch.setattr(streaming, "get_async_redis_client", get_client)
    monkeypatch.setattr(
        streaming,
        "get_redis_client",
        lambda: pytest.fail("Async subscribers must not open a sync Redis client"),
    )

    lines = [
        line
        async for line in streaming.StreamHub().subscribe_async(
            "generation-1",
            from_seq=200,
        )
    ]

    assert [json.loads(line)["seq"] for line in lines] == [201, 202, 203, 204, 205]
    assert client.hget_calls == 1
    assert client.xread_calls == 5


def test_redis_mark_done_atomically_appends_terminal_wakeup(monkeypatch):
    """Completion updates metadata and wakes async readers in one Redis script."""

    class FakeRedis:
        def __init__(self):
            self.eval_calls = []

        def get(self, key):
            assert key == streaming._generation_chat_key("generation-1")
            return "chat-1"

        def eval(self, script, key_count, *args):
            self.eval_calls.append((script, key_count, args))
            return "1-0"

    client = FakeRedis()
    monkeypatch.setattr(streaming, "get_redis_client", lambda: client)

    streaming.StreamHub().mark_done("generation-1", status="failed")

    assert len(client.eval_calls) == 1
    script, key_count, args = client.eval_calls[0]
    assert key_count == 6
    assert args[:6] == (
        streaming._stream_meta_key("generation-1"),
        streaming._stream_signal_key("generation-1"),
        streaming._stream_events_key("generation-1"),
        streaming._stream_event_sizes_key("generation-1"),
        streaming._chat_active_key("chat-1"),
        streaming._generation_chat_key("generation-1"),
    )
    assert args[6] == "failed"
    assert args[8:] == (
        "generation-1",
        streaming._STREAM_TTL_SECONDS,
        "1",
    )
    assert "HSET" in script
    assert "XADD" in script
    assert "terminal" in script


@pytest.mark.asyncio
async def test_async_redis_terminal_signal_wakes_without_status_poll(monkeypatch):
    """A terminal stream entry promptly wakes XREAD without another HGET."""

    xread_started = asyncio.Event()
    release_terminal = asyncio.Event()

    class FakeAsyncRedis:
        def __init__(self):
            self.hget_calls = 0
            self.xread_calls = []

        async def exists(self, _key):
            return True

        async def hget(self, _key, _field):
            self.hget_calls += 1
            if self.hget_calls > 1:
                pytest.fail("Terminal wakeup must not require another status poll")
            return "active"

        async def xread(self, streams, *, count, block):
            self.xread_calls.append((dict(streams), count, block))
            if len(self.xread_calls) == 1:
                xread_started.set()
                await release_terminal.wait()
                return [
                    (
                        streaming._stream_signal_key("generation-1"),
                        [("1-0", {"terminal": "done"})],
                    )
                ]
            assert block is None
            return []

    client = FakeAsyncRedis()

    async def get_client():
        return client

    monkeypatch.setattr(streaming, "get_async_redis_client", get_client)
    replay = streaming.StreamHub().subscribe_async("generation-1")
    completed = asyncio.create_task(anext(replay))
    await asyncio.wait_for(xread_started.wait(), timeout=0.5)

    release_terminal.set()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(completed, timeout=0.5)

    expected_streams = {
        streaming._stream_events_key("generation-1"): "0-0",
        streaming._stream_signal_key("generation-1"): "0-0",
    }
    assert client.xread_calls[0][0] == expected_streams
    assert len(client.xread_calls) == 2
    assert client.hget_calls == 1


@pytest.mark.asyncio
async def test_async_redis_subscriber_stops_when_metadata_disappears(monkeypatch):
    """A successfully missing status is terminal, not an endless heartbeat."""

    class FakeAsyncRedis:
        def __init__(self):
            self.hget_calls = 0

        async def exists(self, _key):
            return True

        async def hget(self, _key, _field):
            self.hget_calls += 1
            return "active" if self.hget_calls == 1 else None

        async def xread(self, _streams, *, count, block):
            del count, block
            return []

    client = FakeAsyncRedis()

    async def get_client():
        return client

    monkeypatch.setattr(streaming, "get_async_redis_client", get_client)
    replay = streaming.StreamHub().subscribe_async("generation-1")

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(replay), timeout=0.5)

    assert client.hget_calls == 2


@pytest.mark.asyncio
async def test_async_redis_subscriber_receives_error_after_retention_gap(monkeypatch):
    """The async Redis path retains the explicit replay-gap failure."""

    class FakeAsyncRedis:
        async def exists(self, _key):
            return True

        async def hget(self, _key, _field):
            return "active"

        async def xread(self, _streams, *, count, block):
            del count, block
            return [
                (
                    "events",
                    [
                        (
                            "3-0",
                            {
                                "seq": "3",
                                "line": json.dumps({"t": "delta", "seq": 3}),
                            },
                        )
                    ],
                )
            ]

    async def get_client():
        return FakeAsyncRedis()

    monkeypatch.setattr(streaming, "get_async_redis_client", get_client)
    replay = streaming.StreamHub().subscribe_async("generation-1", from_seq=0)

    gap = json.loads(await anext(replay))

    assert gap["t"] == "e"
    assert gap["i18n_key"] == "chat_stream_retention_limit_exceeded"
    with pytest.raises(StopAsyncIteration):
        await anext(replay)


@pytest.mark.asyncio
async def test_async_in_memory_subscriber_heartbeat_wakeup_and_cleanup(monkeypatch):
    """The local fallback stays async, live, and disconnect-safe."""

    async def no_redis():
        return None

    monkeypatch.setattr(streaming, "get_async_redis_client", no_redis)
    hub = streaming.StreamHub()
    hub._fallback.start("generation-1", "chat-1")
    replay = hub.subscribe_async(
        "generation-1",
        heartbeat_seconds=0.05,
    )

    assert json.loads(await asyncio.wait_for(anext(replay), timeout=0.5)) == {
        "type": "ping"
    }

    pending_line = asyncio.create_task(anext(replay))
    await asyncio.sleep(0)
    await asyncio.to_thread(
        hub._fallback.publish_dict,
        "generation-1",
        {"t": "delta", "text": "ready"},
    )
    assert json.loads(await asyncio.wait_for(pending_line, timeout=0.5))["text"] == "ready"

    pending_done = asyncio.create_task(anext(replay))
    await asyncio.sleep(0)
    await asyncio.to_thread(hub._fallback.mark_done, "generation-1")
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending_done, timeout=0.5)

    generation = hub._fallback._gens["generation-1"]
    assert generation["subscriber_count"] == 0
    assert generation["async_subscribers"] == {}


@pytest.mark.asyncio
async def test_cancelling_async_redis_subscriber_cancels_pending_xread(monkeypatch):
    """An HTTP disconnect does not leave a Redis XREAD task running."""

    xread_started = asyncio.Event()
    xread_cancelled = asyncio.Event()

    class FakeAsyncRedis:
        async def exists(self, _key):
            return True

        async def hget(self, _key, _field):
            return "active"

        async def xread(self, _streams, *, count, block):
            del count, block
            xread_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                xread_cancelled.set()
                raise

    async def get_client():
        return FakeAsyncRedis()

    monkeypatch.setattr(streaming, "get_async_redis_client", get_client)
    replay = streaming.StreamHub().subscribe_async("generation-1")
    pending_line = asyncio.create_task(anext(replay))
    await asyncio.wait_for(xread_started.wait(), timeout=0.5)

    pending_line.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending_line

    assert xread_cancelled.is_set()
