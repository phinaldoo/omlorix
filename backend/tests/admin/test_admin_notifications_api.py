from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from app.admin.notifications import router as admin_router
from app.admin.notifications.utils import ADMIN_NOTIFICATIONS_EXPORT_BATCH_SIZE


def _notification(*, notification_id: str, details):
    return SimpleNamespace(
        id=notification_id,
        category="system",
        type="warning",
        message="Provider health degraded",
        timestamp=datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc),
        user_id="admin-1",
        details=details,
    )


def test_admin_list_notifications_includes_bounded_details(monkeypatch):
    audit_calls: list[dict] = []
    items = [
        _notification(notification_id="notif-1", details={"provider_id": "openai", "status": "degraded"}),
        _notification(notification_id="notif-2", details={"payload": "x" * 5000}),
    ]

    monkeypatch.setattr(
        admin_router,
        "list_admin_notifications_paginated",
        lambda *_args, **_kwargs: (items, len(items), {"system"}, {"warning"}),
    )
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    response = admin_router.admin_list_notifications(
        request=SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.10"),
            headers={"user-agent": "pytest"},
        ),
        page=1,
        page_size=20,
        category=None,
        categories=None,
        types=None,
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert response["total"] == 2
    assert response["items"][0].details == {"provider_id": "openai", "status": "degraded"}
    assert isinstance(response["items"][1].details, str)
    assert len(response["items"][1].details) == 4000
    assert response["items"][1].details.endswith("...")
    assert audit_calls[0]["details"] == {
        "page": 1,
        "page_size": 20,
        "category": None,
        "categories": None,
        "types": None,
    }


async def _read_streaming_response(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    return b"".join(chunks)


def test_admin_export_notifications_streams_all_rows_without_unbounded_all(monkeypatch):
    audit_calls: list[dict] = []
    notifications = [
        _notification(notification_id=f"notif-{index}", details={"index": index})
        for index in range(10005)
    ]
    query_calls = {"yield_per": 0, "closed": 0}

    class FakeOrderedQuery:
        def execution_options(self, **kwargs):
            assert kwargs == {"stream_results": True}
            return self

        def yield_per(self, batch_size):
            query_calls["yield_per"] += 1
            assert batch_size == ADMIN_NOTIFICATIONS_EXPORT_BATCH_SIZE
            return self

        def all(self):
            raise AssertionError("export must not load every notification with all()")

        def __iter__(self):
            return iter(notifications)

    class FakeQuery:
        def count(self):
            return len(notifications)

        def order_by(self, *_args, **_kwargs):
            return FakeOrderedQuery()

    class FakeDb:
        def query(self, model):
            return FakeQuery()

        def close(self):
            query_calls["closed"] += 1

    monkeypatch.setattr(admin_router, "SessionLocal", FakeDb)
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    response = admin_router.admin_export_notifications(
        request=SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.10"),
            headers={"user-agent": "pytest"},
        ),
        db=FakeDb(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    payload = json.loads(asyncio.run(_read_streaming_response(response)))

    assert payload["export_type"] == "admin_notifications"
    assert payload["total_count"] == 10005
    assert len(payload["notifications"]) == 10005
    assert payload["notifications"][0]["id"] == "notif-0"
    assert payload["notifications"][-1]["id"] == "notif-10004"
    assert audit_calls[0]["details"] == {"count": 10005}
    assert query_calls["yield_per"] == 1
    assert query_calls["closed"] == 1
