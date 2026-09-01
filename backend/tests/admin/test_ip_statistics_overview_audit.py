from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException


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

from app.ip_analytics import router as admin_router
from app.ip_analytics.schemas import (
    AdminIPAddressStatisticsDeleteRequest,
    AdminIPAddressStatisticsSettingsUpdate,
    BlockIP,
    EditIPBlock,
)


class _CountQuery:
    def __init__(self, count: int):
        self._count = count

    def count(self) -> int:
        return self._count


class _OverviewDB:
    def query(self, *_args, **_kwargs):
        return _CountQuery(4)


def test_ip_statistics_overview_route_audits_sensitive_reads_with_trusted_client_ip(monkeypatch):
    audit_calls: list[dict] = []
    monkeypatch.setenv("TRUSTED_PROXIES", "172.16.0.0/12")
    monkeypatch.setattr(
        admin_router,
        "get_ip_address_statistics_settings",
        lambda _db: {"enabled": False, "regulatory_confirmed": False},
    )
    monkeypatch.setattr(admin_router, "delete_expired_blocked_ip_addresses", lambda _db: 0)
    monkeypatch.setattr(admin_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))
    monkeypatch.setattr(
        admin_router,
        "build_overview",
        lambda _db, **_kwargs: {
            "enabled": False,
            "regulatory_confirmed": False,
            "summary": {"active_bans": 4},
            "countries": [],
        },
    )

    response = asyncio.run(
        admin_router.get_ip_address_statistics_overview_route(
            request=SimpleNamespace(
                client=SimpleNamespace(host="172.18.0.4"),
                headers={
                    "x-forwarded-for": "203.0.113.10, 172.18.0.2",
                    "user-agent": "pytest",
                },
            ),
            background_tasks=BackgroundTasks(),
            days=30,
            db=_OverviewDB(),
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1"),
        )
    )

    assert response["summary"]["active_bans"] == 4
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "VIEW_IP_ADDRESS_STATISTICS_OVERVIEW"
    assert audit_calls[0]["ip_address"] == "203.0.113.10"
    assert audit_calls[0]["details"] == {
        "filters": {
            "days": 30,
            "ip_address": None,
            "country_code": None,
            "event_type": None,
            "event_source": None,
        },
        "result_counts": {
            "countries": 0,
        },
    }


def test_successful_unblock_survives_optional_analytics_failure(monkeypatch):
    """Return success and write the audit after the committed ban removal."""

    audit_calls: list[dict] = []

    class _MutationDB:
        def __init__(self):
            self.rollback_calls = 0

        def rollback(self):
            self.rollback_calls += 1

    db = _MutationDB()
    monkeypatch.setattr(
        admin_router,
        "deblock_ip_address",
        lambda _ip_address, _db: {"status": "success"},
    )
    monkeypatch.setattr(
        admin_router,
        "record_ip_address_security_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("analytics unavailable")
        ),
    )
    monkeypatch.setattr(
        admin_router,
        "get_audit_request_ip",
        lambda _request, _db: "203.0.113.10",
    )
    monkeypatch.setattr(
        admin_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    response = admin_router.upsert_ip_block_route(
        payload=BlockIP(ip_address="198.51.100.20", banned=False),
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=db,
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert response.status_code == 204
    assert db.rollback_calls == 1
    assert audit_calls[0]["action"] == "UNBLOCK_IP_ADDRESS"


def test_failed_unblock_returns_422_without_success_audit(monkeypatch):
    """Do not report success when enforcement rejects the unblock mutation."""
    audit_calls = []
    monkeypatch.setattr(
        admin_router,
        "deblock_ip_address",
        lambda _ip_address, _db: {
            "status": "error",
            "message": "enforcement storage unavailable",
        },
    )
    monkeypatch.setattr(
        admin_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_router.upsert_ip_block_route(
            payload=BlockIP(ip_address="198.51.100.20", banned=False),
            request=SimpleNamespace(headers={"user-agent": "pytest"}),
            db=object(),
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1"),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "enforcement storage unavailable"
    assert audit_calls == []


def test_rejected_ip_statistics_settings_do_not_mutate_orm_state(monkeypatch):
    """Validate a detached effective dictionary before attaching it to the row."""
    original_data = {
        "enabled": False,
        "regulatory_confirmed": False,
        "regulatory_justification": "",
        "policy_reference": "",
    }
    settings_page = SimpleNamespace(data=original_data, updated_at=None)
    monkeypatch.setattr(
        admin_router,
        "get_ip_statistics_settings_page",
        lambda _db: settings_page,
    )
    monkeypatch.setattr(
        admin_router,
        "persist_ip_statistics_settings_page",
        lambda *_args: pytest.fail("Rejected settings must not be persisted."),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_router.update_ip_address_statistics_settings_route(
            payload=AdminIPAddressStatisticsSettingsUpdate(enabled=True),
            request=SimpleNamespace(headers={"user-agent": "pytest"}),
            db=object(),
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1"),
        )

    assert exc_info.value.status_code == 400
    assert settings_page.data is original_data
    assert settings_page.data["enabled"] is False


def test_delete_ip_statistics_normalizes_target_for_delete_and_audit(monkeypatch):
    """Use the canonical storage form even when validation is bypassed internally."""
    delete_calls = []
    audit_calls = []
    monkeypatch.setattr(
        admin_router,
        "delete_statistics",
        lambda db, **kwargs: delete_calls.append((db, kwargs)) or 2,
    )
    monkeypatch.setattr(
        admin_router,
        "get_audit_request_ip",
        lambda _request, _db: "203.0.113.10",
    )
    monkeypatch.setattr(
        admin_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )
    db = object()

    result = admin_router.delete_ip_address_statistics_route(
        payload=AdminIPAddressStatisticsDeleteRequest.model_construct(
            days=30,
            ip_address=" 2001:0db8::1 ",
        ),
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=db,
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert result == {"status": "success", "affected_rows": 2}
    assert delete_calls == [(db, {"days": 30, "ip_address": "2001:db8::1"})]
    assert audit_calls[0]["details"]["scope"]["ip_address"] == "2001:db8::1"


def test_successful_ban_address_edit_survives_optional_analytics_failure(monkeypatch):
    """Keep the committed edit and mandatory audit when lifecycle storage fails."""

    now = admin_router.datetime.now(admin_router.timezone.utc)
    entry = SimpleNamespace(
        ip_address="198.51.100.30",
        expires_at=now,
        reason="old reason",
    )
    audit_calls: list[dict] = []

    class _Query:
        def __init__(self, db):
            self.db = db

        def filter(self, *_args):
            return self

        def first(self):
            self.db.first_calls += 1
            return entry if self.db.first_calls == 1 else None

    class _MutationDB:
        def __init__(self):
            self.first_calls = 0
            self.commit_calls = 0
            self.rollback_calls = 0

        def query(self, *_args):
            return _Query(self)

        def commit(self):
            self.commit_calls += 1

        def refresh(self, _entry):
            return None

        def rollback(self):
            self.rollback_calls += 1

    db = _MutationDB()
    monkeypatch.setattr(
        admin_router,
        "get_audit_request_ip",
        lambda _request, _db: "203.0.113.10",
    )
    monkeypatch.setattr(
        admin_router,
        "record_ip_address_security_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("analytics unavailable")
        ),
    )
    monkeypatch.setattr(
        admin_router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    result = admin_router.edit_ip_block_route(
        ip_address="198.51.100.30",
        payload=EditIPBlock(
            ip_address="198.51.100.31",
            duration_days=14,
            reason="updated reason",
        ),
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=db,
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert result.status == "success"
    assert entry.ip_address == "198.51.100.31"
    assert db.commit_calls == 1
    assert db.rollback_calls == 1
    assert audit_calls[0]["action"] == "EDIT_IP_ADDRESS_BLOCK"


@pytest.mark.parametrize(
    "loopback_address",
    [
        "localhost",
        "127.0.0.2",
        "::1",
        "::ffff:127.0.0.1",
    ],
)
def test_ban_address_edit_rejects_every_loopback_representation(
    monkeypatch,
    loopback_address,
):
    """Editing a safe ban must not bypass the localhost lockout protection."""

    class _NoWriteDB:
        def query(self, *_args, **_kwargs):
            pytest.fail("A rejected loopback edit must not query or mutate the ban table.")

    monkeypatch.setattr(
        admin_router,
        "get_audit_request_ip",
        lambda _request, _db: "203.0.113.10",
    )

    with pytest.raises(HTTPException) as excinfo:
        admin_router.edit_ip_block_route(
            ip_address="198.51.100.30",
            payload=EditIPBlock(
                ip_address=loopback_address,
                duration_days=14,
                reason="unsafe edit",
            ),
            request=SimpleNamespace(headers={"user-agent": "pytest"}),
            db=_NoWriteDB(),
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1"),
        )

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == "Cannot block localhost IP addresses"
