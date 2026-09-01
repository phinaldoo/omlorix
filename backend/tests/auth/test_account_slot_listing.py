from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import sys
from pathlib import Path

from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import account_slots

def _request_with_cookies(cookie_header: str = "") -> Request:
    headers = []
    if cookie_header:
        headers.append((b"cookie", cookie_header.encode("utf-8")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/auth/accounts",
            "headers": headers,
        }
    )


def test_list_accounts_payload_exposes_display_name_only(monkeypatch):
    accounts = [
        SimpleNamespace(
            slot=1,
            user_id="user-1",
            refresh_token="refresh-1",
            display_name="First User",
            has_custom_profile_picture=False,
            has_profile_picture=True,
            last_active_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            slot=2,
            user_id="user-2",
            refresh_token="refresh-2",
            display_name="Second User",
            has_custom_profile_picture=False,
            has_profile_picture=False,
            last_active_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
    ]
    seen_responses: list[object | None] = []

    def fake_list_browser_accounts(request, db, response=None, *, include_legacy=True):
        seen_responses.append(response)
        assert include_legacy is False
        return accounts

    monkeypatch.setattr(account_slots, "list_browser_accounts", fake_list_browser_accounts)

    request = _request_with_cookies("omlorix_active_slot=9")
    payload = account_slots.list_accounts_payload(request, db=object())

    assert seen_responses == [None]
    assert payload == {
        "accounts": [
            {"slot": 1, "display_name": "First User", "has_profile_picture": True, "active": False},
            {"slot": 2, "display_name": "Second User", "has_profile_picture": False, "active": True},
        ],
        "active_slot": 2,
        "can_add_account": True,
        "max_accounts": 5,
    }


def test_resolve_browser_account_slot_rejects_invalid_refresh_token(monkeypatch):
    def fail_get_user(*args, **kwargs):
        raise AssertionError("invalid refresh token must not resolve a user")

    monkeypatch.setattr(account_slots, "get_authentication_by_token", lambda db, token, token_type: object())
    monkeypatch.setattr(account_slots, "_decode_refresh_slot_token", lambda token, db: None)
    monkeypatch.setattr(account_slots, "get_user", fail_get_user)

    request = _request_with_cookies("omlorix_refresh_slot_2=expired-refresh-token")

    assert account_slots.resolve_browser_account_slot(2, request, db=object()) is None


def test_profile_picture_slot_route_uses_validated_slot_resolver():
    router_path = Path(__file__).resolve().parents[2] / "app" / "users" / "router.py"
    source = router_path.read_text()
    route_source = source[source.index('@users_router.get("/profile-picture/slot/{slot}")') :]
    route_source = route_source[: route_source.index('@users_router.post("/personal-details/update")')]

    assert "resolve_browser_account_slot" in route_source
    assert "get_authentication_by_token" not in route_source
