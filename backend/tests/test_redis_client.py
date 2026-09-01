from __future__ import annotations

import asyncio

import pytest

from app import redis_client


@pytest.mark.asyncio
async def test_async_redis_first_connection_race_closes_the_loser(monkeypatch):
    """Concurrent probes share one pool and deterministically close the other."""

    probes_started = 0
    both_probes_started = asyncio.Event()
    clients = []

    class FakeAsyncRedis:
        def __init__(self):
            self.close_calls = 0

        async def ping(self):
            nonlocal probes_started
            probes_started += 1
            if probes_started == 2:
                both_probes_started.set()
            await both_probes_started.wait()

        async def aclose(self):
            self.close_calls += 1

    def create_client():
        client = FakeAsyncRedis()
        clients.append(client)
        return client

    monkeypatch.setattr(redis_client, "redis_enabled", lambda: True)
    monkeypatch.setattr(redis_client, "_async_client", None)
    monkeypatch.setattr(redis_client, "_async_client_epoch", 0)
    monkeypatch.setattr(redis_client, "_create_async_redis_client", create_client)

    first, second = await asyncio.gather(
        redis_client.get_async_redis_client(),
        redis_client.get_async_redis_client(),
    )

    assert len(clients) == 2
    assert first is second
    assert first in clients
    assert sorted(client.close_calls for client in clients) == [0, 1]

    await redis_client.close_async_redis_client()

    assert first.close_calls == 1
    assert redis_client._async_client is None


@pytest.mark.asyncio
async def test_async_redis_failed_probe_is_closed(monkeypatch):
    """A failed health check cannot leak its unshared connection pool."""

    class FakeAsyncRedis:
        def __init__(self):
            self.close_calls = 0

        async def ping(self):
            raise ConnectionError("redis unavailable")

        async def aclose(self):
            self.close_calls += 1

    client = FakeAsyncRedis()
    monkeypatch.setattr(redis_client, "redis_enabled", lambda: True)
    monkeypatch.setattr(redis_client, "_async_client", None)
    monkeypatch.setattr(redis_client, "_async_client_epoch", 0)
    monkeypatch.setattr(redis_client, "_last_connect_error_at", 0.0)
    monkeypatch.setattr(redis_client, "_create_async_redis_client", lambda: client)

    assert await redis_client.get_async_redis_client() is None
    assert client.close_calls == 1
    assert redis_client._async_client is None


@pytest.mark.asyncio
async def test_async_redis_failed_probe_returns_concurrent_winner(monkeypatch):
    """One failed probe does not hide a healthy client installed alongside it."""

    failed_probe_started = asyncio.Event()
    release_failed_probe = asyncio.Event()

    class FailingAsyncRedis:
        def __init__(self):
            self.close_calls = 0

        async def ping(self):
            failed_probe_started.set()
            await release_failed_probe.wait()
            raise ConnectionError("one endpoint probe failed")

        async def aclose(self):
            self.close_calls += 1

    class HealthyAsyncRedis:
        def __init__(self):
            self.close_calls = 0

        async def ping(self):
            return None

        async def aclose(self):
            self.close_calls += 1

    failed_client = FailingAsyncRedis()
    healthy_client = HealthyAsyncRedis()
    clients = iter((failed_client, healthy_client))
    monkeypatch.setattr(redis_client, "redis_enabled", lambda: True)
    monkeypatch.setattr(redis_client, "_async_client", None)
    monkeypatch.setattr(redis_client, "_async_client_epoch", 0)
    monkeypatch.setattr(redis_client, "_create_async_redis_client", lambda: next(clients))

    failed_connection = asyncio.create_task(redis_client.get_async_redis_client())
    await asyncio.wait_for(failed_probe_started.wait(), timeout=0.5)
    winner = await redis_client.get_async_redis_client()
    release_failed_probe.set()

    assert winner is healthy_client
    assert await asyncio.wait_for(failed_connection, timeout=0.5) is winner
    assert failed_client.close_calls == 1
    assert healthy_client.close_calls == 0

    await redis_client.close_async_redis_client()
    assert healthy_client.close_calls == 1


@pytest.mark.asyncio
async def test_async_redis_cancelled_probe_is_closed(monkeypatch):
    """Request cancellation cannot strand a partially initialized pool."""

    ping_started = asyncio.Event()

    class FakeAsyncRedis:
        def __init__(self):
            self.close_calls = 0

        async def ping(self):
            ping_started.set()
            await asyncio.Future()

        async def aclose(self):
            self.close_calls += 1

    client = FakeAsyncRedis()
    monkeypatch.setattr(redis_client, "redis_enabled", lambda: True)
    monkeypatch.setattr(redis_client, "_async_client", None)
    monkeypatch.setattr(redis_client, "_async_client_epoch", 0)
    monkeypatch.setattr(redis_client, "_create_async_redis_client", lambda: client)

    connection = asyncio.create_task(redis_client.get_async_redis_client())
    await asyncio.wait_for(ping_started.wait(), timeout=0.5)
    connection.cancel()

    with pytest.raises(asyncio.CancelledError):
        await connection
    assert client.close_calls == 1
    assert redis_client._async_client is None


@pytest.mark.asyncio
async def test_async_redis_shutdown_invalidates_an_in_flight_probe(monkeypatch):
    """Shutdown prevents a slow first probe from installing a new pool afterward."""

    ping_started = asyncio.Event()
    finish_ping = asyncio.Event()

    class FakeAsyncRedis:
        def __init__(self):
            self.close_calls = 0

        async def ping(self):
            ping_started.set()
            await finish_ping.wait()

        async def aclose(self):
            self.close_calls += 1

    client = FakeAsyncRedis()
    monkeypatch.setattr(redis_client, "redis_enabled", lambda: True)
    monkeypatch.setattr(redis_client, "_async_client", None)
    monkeypatch.setattr(redis_client, "_async_client_epoch", 0)
    monkeypatch.setattr(redis_client, "_create_async_redis_client", lambda: client)

    connection = asyncio.create_task(redis_client.get_async_redis_client())
    await asyncio.wait_for(ping_started.wait(), timeout=0.5)
    await redis_client.close_async_redis_client()
    finish_ping.set()

    assert await asyncio.wait_for(connection, timeout=0.5) is None
    assert client.close_calls == 1
    assert redis_client._async_client is None
