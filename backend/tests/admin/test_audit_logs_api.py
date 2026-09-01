"""Security and contract tests for the general administrator audit browser."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.admin import router as admin_root_router
from app.admin.audit_logs import router
from app.admin.audit_logs.models import AuditLogFilters, list_audit_logs
from app.admin.audit_logs.schemas import AuditLogExportRequest
from app.admin.audit_logs.utils import (
    AUDIT_LOG_EXPORT_MAX_ROWS,
    decode_audit_cursor,
    encode_audit_cursor,
    serialize_audit_log_item,
)
from app.database import AuditBase
from app.dependencies import get_db, get_db_log, verified_admin
from app.logging.models import Logs
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _row(
    row_id: str,
    timestamp: datetime,
    *,
    action: str = "UPDATE_SETTING",
    category: str = "admin",
):
    return SimpleNamespace(
        id=row_id,
        user_id="admin-1",
        action=action,
        reason="Routine investigation",
        timestamp=timestamp,
        details={
            "provider": "openai",
            "chat_id": "chat-1",
            "api_key": "must-not-escape",
            "payload": "must-not-escape",
            "filters": {
                "category": "admin",
                "authorization": "must-not-escape",
            },
        },
        ip_address="ip_123456789abc",
        user_agent="device_123456789abc",
        category=category,
    )


def test_audit_routes_are_registered_and_require_an_administrator():
    audit_routes = [
        route
        for route in admin_root_router.admin_router.routes
        if getattr(route, "path", "").startswith("/api/v1/admin/audit-logs")
    ]

    assert {
        (route.path, method) for route in audit_routes for method in route.methods
    } == {
        ("/api/v1/admin/audit-logs", "GET"),
        ("/api/v1/admin/audit-logs/{row_id}", "GET"),
        ("/api/v1/admin/audit-logs/export", "POST"),
    }
    for route_item in audit_routes:
        dependencies = {
            dependency.call for dependency in route_item.dependant.dependencies
        }
        assert verified_admin in dependencies


def test_original_audit_log_http_probe_now_returns_a_sanitized_page(monkeypatch):
    occurred_at = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    audit_row = _row("a" * 32, occurred_at)
    monkeypatch.setattr(
        router, "list_audit_logs", lambda *_args, **_kwargs: ([audit_row], False)
    )
    monkeypatch.setattr(
        router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "delete_after_days", "retention_days": 30},
    )
    monkeypatch.setattr(router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *_args: None)

    app = FastAPI()
    app.include_router(router.admin_router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_db_log] = lambda: object()
    app.dependency_overrides[verified_admin] = lambda: SimpleNamespace(id="admin-1")

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/admin/audit-logs",
            params={
                "from": (occurred_at - timedelta(days=1)).isoformat(),
                "to": (occurred_at + timedelta(minutes=1)).isoformat(),
            },
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    body = response.json()
    assert body["items"][0]["id"] == audit_row.id
    assert "details" not in body["items"][0]
    assert "must-not-escape" not in response.text


def test_cursor_pagination_is_stable_for_equal_timestamps():
    engine = create_engine("sqlite:///:memory:")
    AuditBase.metadata.create_all(engine, tables=[Logs.__table__])
    timestamp = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    with Session(engine) as db:
        for row_id in ("a" * 32, "b" * 32, "c" * 32):
            db.add(
                Logs(
                    id=row_id,
                    user_id="admin-1",
                    action="UPDATE_SETTING",
                    timestamp=timestamp,
                    category="admin",
                    share_refs_scrubbed=True,
                )
            )
        db.commit()
        filters = AuditLogFilters(
            from_timestamp=timestamp - timedelta(days=1),
            to_timestamp=timestamp + timedelta(days=1),
        )

        first_page, has_next = list_audit_logs(
            db, filters=filters, limit=2, cursor=None
        )
        cursor = decode_audit_cursor(
            encode_audit_cursor(first_page[-1].timestamp, first_page[-1].id)
        )
        second_page, second_has_next = list_audit_logs(
            db, filters=filters, limit=2, cursor=cursor
        )

    assert [row.id for row in first_page] == ["c" * 32, "b" * 32]
    assert has_next is True
    assert [row.id for row in second_page] == ["a" * 32]
    assert second_has_next is False


def test_legacy_raw_network_metadata_is_not_exposed():
    audit_row = _row(
        "a" * 32,
        datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
    )
    audit_row.ip_address = "198.51.100.42"
    audit_row.user_agent = "Browser/1.0 (private workstation details)"

    item = serialize_audit_log_item(audit_row, include_details=True)

    assert item.ip_fingerprint is None
    assert item.device_fingerprint is None


def test_list_and_detail_are_bounded_sanitized_and_audited(monkeypatch):
    occurred_at = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    audit_row = _row("a" * 32, occurred_at)
    audit_calls: list[dict] = []
    monkeypatch.setattr(
        router, "list_audit_logs", lambda *_args, **_kwargs: ([audit_row], False)
    )
    monkeypatch.setattr(router, "get_audit_log", lambda *_args, **_kwargs: audit_row)
    monkeypatch.setattr(
        router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "delete_after_days", "retention_days": 30},
    )
    monkeypatch.setattr(
        router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs)
    )
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *_args: "198.51.100.1")
    request = SimpleNamespace(headers={"user-agent": "pytest"})
    response = Response()

    page = router.list_audit_logs_route(
        request=request,
        response=response,
        limit=50,
        cursor=None,
        snapshot_at=occurred_at + timedelta(minutes=1),
        from_timestamp=occurred_at - timedelta(days=1),
        to_timestamp=occurred_at + timedelta(days=1),
        category=None,
        action=None,
        actor_user_id=None,
        reference="chat-1",
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )
    detail_response = Response()
    detail = router.get_audit_log_route(
        request=request,
        response=detail_response,
        row_id=audit_row.id,
        occurred_at=occurred_at,
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert page["items"][0].has_details is True
    assert not hasattr(page["items"][0], "details")
    assert detail.details == {
        "provider": "openai",
        "chat_id": "chat-1",
        "filters": {"category": "admin"},
    }
    assert "must-not-escape" not in repr(detail)
    assert response.headers["cache-control"] == "no-store, private"
    assert detail_response.headers["cache-control"] == "no-store, private"
    assert [call["action"] for call in audit_calls] == [
        "LIST_AUDIT_LOGS",
        "VIEW_AUDIT_LOG_DETAIL",
    ]
    assert audit_calls[0]["details"]["reference_filter_supplied"] is True
    assert "chat-1" not in repr(audit_calls[0])


async def _read_stream(response) -> bytes:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    return b"".join(chunks)


def test_export_is_bounded_streamed_sanitized_and_audited(monkeypatch):
    occurred_at = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    audit_row = _row("a" * 32, occurred_at)
    audit_calls: list[dict] = []
    stream_state = {"closed": 0}

    class FakeStreamDb:
        def close(self):
            stream_state["closed"] += 1

    monkeypatch.setattr(router, "AuditSessionLocal", FakeStreamDb)
    monkeypatch.setattr(router, "count_audit_logs_capped", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        router, "iter_audit_logs", lambda *_args, **_kwargs: iter([audit_row])
    )
    monkeypatch.setattr(
        router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "delete_after_days", "retention_days": 30},
    )
    monkeypatch.setattr(
        router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs)
    )
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *_args: "198.51.100.1")
    payload = AuditLogExportRequest.model_validate(
        {
            "from": (occurred_at - timedelta(days=1)).isoformat(),
            "to": (occurred_at + timedelta(days=1)).isoformat(),
            "reason": "Incident review",
        }
    )

    response = router.export_audit_logs_route(
        payload=payload,
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )
    exported = json.loads(asyncio.run(_read_stream(response)))

    assert exported["export_type"] == "audit_logs"
    assert exported["total_count"] == 1
    assert exported["events"][0]["details"] == {
        "provider": "openai",
        "chat_id": "chat-1",
        "filters": {"category": "admin"},
    }
    assert "must-not-escape" not in repr(exported)
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert audit_calls[0]["action"] == "EXPORT_AUDIT_LOGS"
    assert audit_calls[0]["reason"] == "Incident review"
    assert stream_state["closed"] == 1


def test_export_rejects_results_over_the_row_cap_and_audits_the_attempt(monkeypatch):
    occurred_at = datetime.now(timezone.utc)
    audit_calls: list[dict] = []
    monkeypatch.setattr(
        router,
        "count_audit_logs_capped",
        lambda *_args, **_kwargs: AUDIT_LOG_EXPORT_MAX_ROWS + 1,
    )
    monkeypatch.setattr(
        router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: {"mode": "retain", "retention_days": None},
    )
    monkeypatch.setattr(
        router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs)
    )
    payload = AuditLogExportRequest.model_validate(
        {
            "from": (occurred_at - timedelta(days=1)).isoformat(),
            "to": occurred_at.isoformat(),
            "reason": "Incident review",
        }
    )

    try:
        router.export_audit_logs_route(
            payload=payload,
            request=SimpleNamespace(headers={}),
            db=object(),
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1"),
        )
    except HTTPException as exc:
        assert exc.status_code == 413
        assert exc.detail == {
            "code": "audit_log_export_too_large",
            "max_rows": AUDIT_LOG_EXPORT_MAX_ROWS,
        }
    else:
        raise AssertionError("oversized export was not rejected")

    assert audit_calls[0]["action"] == "EXPORT_AUDIT_LOGS_REJECTED"
    assert audit_calls[0]["reason"] == "Incident review"
    assert audit_calls[0]["details"]["result"] == "rejected_row_limit"
