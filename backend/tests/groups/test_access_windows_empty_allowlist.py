import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.groups import access_windows


def test_enabled_allowlist_without_rules_blocks_access(monkeypatch):
    monkeypatch.setattr(
        access_windows,
        "get_group_access_settings",
        lambda group_id, db: {
            "enabled": True,
            "timezone": "UTC",
            "mode": "allowlist",
            "rules": [],
            "show_next_available": True,
            "blocked_message": "Not available",
        },
    )

    result = access_windows.is_group_accessible_now("group-id", object())

    assert result == {
        "accessible": False,
        "reason": "no_rules_defined",
        "next_allowed_at": None,
        "blocked_message": "Not available",
    }


def test_admins_are_allowed_even_when_allowlist_has_no_rules(monkeypatch):
    """Admins should always be able to sign in and fix restrictive windows."""
    monkeypatch.setattr(
        access_windows,
        "get_group_access_settings",
        lambda group_id, db: {
            "enabled": True,
            "timezone": "UTC",
            "mode": "allowlist",
            "rules": [],
            "show_next_available": True,
            "blocked_message": "Not available",
        },
    )

    result = access_windows.is_group_accessible_now("group-id", object(), is_admin=True)

    assert result == {
        "accessible": True,
        "reason": None,
        "next_allowed_at": None,
        "blocked_message": None,
    }
