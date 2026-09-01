import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import models as auth_models
from app.auth import utils as auth_utils


CHROME_WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.6099.224 Safari/537.36"
)

CHROME_ANDROID_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36"
)

SAFARI_IPAD_DESKTOP_CLASS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)


class FakeDb:
    def __init__(self):
        self.added = None

    def add(self, obj):
        self.added = obj

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def test_session_device_info_is_coarse_parser_compatible_user_agent():
    minimized = auth_models.minimize_session_device_info(CHROME_WINDOWS_UA)

    assert minimized == "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/"
    assert "Win64" not in minimized
    assert "AppleWebKit/537.36" not in minimized
    assert "6099.224" not in minimized


def test_android_mobile_minimization_preserves_mobile_signal():
    minimized = auth_models.minimize_session_device_info(CHROME_ANDROID_MOBILE_UA)

    assert minimized == "Mozilla/5.0 (Android 14; Mobile) Chrome/120 Safari/"
    assert "Pixel 8" not in minimized


def test_ipad_desktop_class_safari_is_coarsened_as_ipad():
    minimized = auth_models.minimize_session_device_info(SAFARI_IPAD_DESKTOP_CLASS_UA)

    assert minimized == "Mozilla/5.0 (iPad; CPU OS) Version/17 Safari/"
    assert "Macintosh" not in minimized
    assert "Mobile/15E148" not in minimized


def test_session_ip_address_is_prefix_truncated():
    assert auth_models.minimize_session_ip_address("203.0.113.45") == "203.0.113.0/24"
    assert auth_models.minimize_session_ip_address("2001:db8:abcd:12:3456::1") == "2001:db8:abcd:12::/64"
    assert auth_models.minimize_session_ip_address("203.0.113.0/24") == "203.0.113.0/24"


def test_create_authentication_stores_minimized_session_details(monkeypatch):
    fake_db = FakeDb()
    monkeypatch.setattr(auth_models, "cache_session", lambda user_id, access_token, refresh_token: None)

    auth = auth_models.create_authentication(
        fake_db,
        "user-1",
        CHROME_WINDOWS_UA,
        "203.0.113.45",
        "access-token",
        "refresh-token",
    )

    assert auth is fake_db.added
    assert auth.device_info == "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/"
    assert auth.ip_address == "203.0.113.0/24"
    assert auth.access_token_hash == hashlib.sha256(b"access-token").hexdigest()
    assert auth.refresh_token_hash == hashlib.sha256(b"refresh-token").hexdigest()


def test_list_current_logins_minimizes_legacy_raw_session_details(monkeypatch):
    session = SimpleNamespace(
        id="auth-1",
        device_info=CHROME_WINDOWS_UA,
        ip_address="203.0.113.45",
        access_token_hash=hashlib.sha256(b"access-token").hexdigest(),
        last_active_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(auth_utils, "list_authentication_login_metadata", lambda db, user_id: [session])

    logins = auth_utils.list_current_logins("user-1", object(), token="access-token")

    assert logins == [
        {
            "id": "auth-1",
            "device_info": "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/",
            "ip_address": "203.0.113.0/24",
            "last_active_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "current": True,
        }
    ]
