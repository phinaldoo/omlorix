from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


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

from app.userNotifications import router as user_notifications_router


def _notification(notification_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=notification_id,
        message=f"Notification {notification_id}",
        category="security",
        type="info",
        everyone=False,
        details={"scope": "all"},
        timestamp=None,
        user_id_list=lambda: ["user-1"],
        group_id_list=lambda: [],
    )


def test_admin_list_all_notifications_route_audits_sensitive_reads(monkeypatch):
    audit_calls: list[dict] = []
    monkeypatch.setenv("TRUSTED_PROXIES", "172.16.0.0/12")
    monkeypatch.setattr(
        user_notifications_router,
        "get_all_user_notifications",
        lambda **_kwargs: ([_notification("notification-1"), _notification("notification-2")], 9),
    )
    monkeypatch.setattr(user_notifications_router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))

    response = user_notifications_router.list_all_notifications_route(
        request=SimpleNamespace(
            client=SimpleNamespace(host="172.18.0.4"),
            headers={
                "x-forwarded-for": "203.0.113.10, 172.18.0.2",
                "user-agent": "pytest",
            },
        ),
        page=2,
        page_size=2,
        db=object(),
        db_log=object(),
        admin=SimpleNamespace(id="admin-1"),
    )

    assert response.total == 9
    assert len(response.notifications) == 2
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "LIST_ALL_USER_NOTIFICATIONS"
    assert audit_calls[0]["ip_address"] == "203.0.113.10"
    assert audit_calls[0]["details"] == {
        "page": 2,
        "page_size": 2,
        "result_count": 2,
        "total": 9,
    }
