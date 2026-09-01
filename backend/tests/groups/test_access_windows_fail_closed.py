import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.groups import access_windows


def test_access_window_lookup_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(
        access_windows,
        "get_group_page_settings",
        lambda group_id, page, db: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    result = access_windows.is_group_accessible_now("group-id", object())

    assert result == {
        "accessible": False,
        "reason": "policy_error",
        "next_allowed_at": None,
        "blocked_message": None,
    }


def test_admin_access_skips_access_window_lookup_failure(monkeypatch):
    """Admins are allowed before loading the access-window policy."""
    called = False

    def fail_if_called(group_id, page, db):
        nonlocal called
        called = True
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(access_windows, "get_group_page_settings", fail_if_called)

    result = access_windows.is_group_accessible_now("group-id", object(), is_admin=True)

    assert called is False
    assert result == {
        "accessible": True,
        "reason": None,
        "next_allowed_at": None,
        "blocked_message": None,
    }


def test_missing_access_window_page_uses_disabled_defaults(monkeypatch):
    monkeypatch.setattr(
        access_windows,
        "get_group_page_settings",
        lambda group_id, page, db: (_ for _ in ()).throw(KeyError("access_windows")),
    )

    result = access_windows.is_group_accessible_now("group-id", object())

    assert result["accessible"] is True
    assert result["reason"] is None


def test_enabled_blocklist_with_malformed_rules_fails_closed(monkeypatch):
    monkeypatch.setattr(
        access_windows,
        "get_group_page_settings",
        lambda group_id, page, db: {
            "enabled": True,
            "timezone": "UTC",
            "mode": "blocklist",
            "rules": [{"label": "Missing hours"}],
            "show_next_available": True,
            "blocked_message": "",
        },
    )

    result = access_windows.is_group_accessible_now("group-id", object())

    assert result == {
        "accessible": False,
        "reason": "policy_error",
        "next_allowed_at": None,
        "blocked_message": None,
    }
