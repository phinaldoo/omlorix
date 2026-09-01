import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import utils as auth_utils
from app.email import models as email_models


def test_password_reset_email_link_uses_fragment_not_query(monkeypatch):
    user = SimpleNamespace(
        id="user-id",
        email="user@example.com",
        deleted_at=None,
        auth_management_mode="local",
        role="user",
        is_active=True,
        account_type="regular",
        temporary_expires_at=None,
        lock={},
    )
    queued_messages = []
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    monkeypatch.setattr(auth_utils, "_find_user_for_password_reset_email", lambda db, email: user)
    monkeypatch.setattr(
        auth_utils,
        "_lock_password_reset_user_for_identifier",
        lambda _db, _user_id, _identifier: user,
    )
    monkeypatch.setattr(auth_utils, "invalidate_user_password_reset_tokens", lambda db, user_id, commit=False: None)
    monkeypatch.setattr(auth_utils, "create_password_reset_token", lambda *args, **kwargs: SimpleNamespace(id="reset-id"))
    monkeypatch.setattr(auth_utils, "get_public_url", lambda db: "https://chat.example")
    monkeypatch.setattr(
        auth_utils,
        "get_user_setting_value",
        lambda user_id, page, key, db, **_kwargs: "en",
    )
    monkeypatch.setattr(auth_utils, "resolve_email_language", lambda user_language, accept_language: "en")
    monkeypatch.setattr(
        auth_utils,
        "validate_user_login_eligibility",
        lambda user, db, **_kwargs: None,
    )
    monkeypatch.setattr(auth_utils, "check_user_locked", lambda db, user_id: False)
    monkeypatch.setattr(auth_utils, "create_authentication_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(email_models, "enqueue_email", lambda *args, **kwargs: queued_messages.append(kwargs))

    auth_utils._process_password_reset_request(
        db, object(), "user@example.com", "127.0.0.1", "Test Agent", "en"
    )

    assert len(queued_messages) == 1
    parsed_link = urlsplit(queued_messages[0]["payload"]["reset_link"])
    assert parsed_link.path == "/login"
    assert parsed_link.query == ""
    assert parsed_link.fragment.startswith("token=")
    assert queued_messages[0]["template_type"] == "password_reset"
