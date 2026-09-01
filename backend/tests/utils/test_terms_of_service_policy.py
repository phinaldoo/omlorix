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

from app.utils import utils as app_utils


def test_terms_policy_exposes_signup_and_access_enforcement_flags(monkeypatch):
    values = {
        ("about", "terms_of_service_revision"): 4,
        ("about", "terms_of_service_updated_at"): "2026-05-25T09:00:00+00:00",
        ("login_general", "show_terms_of_service_link"): True,
        ("login_general", "enforce_terms_of_service_signup_acceptance"): True,
        ("login_general", "enforce_terms_of_service_access_acceptance"): True,
    }

    monkeypatch.setattr(app_utils, "get_terms_of_service", lambda db: "# Custom Terms")
    monkeypatch.setattr(
        app_utils,
        "get_value_by_page_and_key",
        lambda page, key, db: values.get((page, key), ""),
    )
    monkeypatch.setattr(app_utils, "is_default_terms_of_service", lambda content: False)

    def user_setting_value(_user_id, page, key, _db):
        user_values = {
            ("states", "terms_of_service_accepted_revision"): 3,
            ("states", "terms_of_service_accepted_at"): "",
        }
        return user_values.get((page, key))

    monkeypatch.setattr("app.users.init.get_user_setting_value", user_setting_value)

    policy = app_utils.get_terms_of_service_policy(object(), "user-1")

    assert policy["signup_available"] is True
    assert policy["access_available"] is True
    assert policy["require_current_revision_for_signup"] is True
    assert policy["require_current_revision_for_access"] is True
    assert policy["accepted_current_revision"] is False


def test_terms_policy_signup_availability_no_longer_depends_on_terms_text_or_login_link(monkeypatch):
    values = {
        ("about", "terms_of_service_revision"): 2,
        ("about", "terms_of_service_updated_at"): "2026-05-25T09:00:00+00:00",
        ("login_general", "show_terms_of_service_link"): False,
        ("login_general", "enforce_terms_of_service_signup_acceptance"): True,
        ("login_general", "enforce_terms_of_service_access_acceptance"): False,
    }

    monkeypatch.setattr(app_utils, "get_terms_of_service", lambda db: "")
    monkeypatch.setattr(
        app_utils,
        "get_value_by_page_and_key",
        lambda page, key, db: values.get((page, key), ""),
    )
    monkeypatch.setattr(app_utils, "is_default_terms_of_service", lambda content: True)

    policy = app_utils.get_terms_of_service_policy(object())

    assert policy["signup_available"] is True
    assert policy["signup_block_reason"] is None


def test_terms_policy_keeps_signup_available_when_acceptance_is_optional(monkeypatch):
    """Disabling consent enforcement must not disable account registration."""
    values = {
        ("about", "terms_of_service_revision"): 1,
        ("about", "terms_of_service_updated_at"): "",
        ("login_general", "show_terms_of_service_link"): False,
        ("login_general", "enforce_terms_of_service_signup_acceptance"): False,
        ("login_general", "enforce_terms_of_service_access_acceptance"): False,
    }

    monkeypatch.setattr(app_utils, "get_terms_of_service", lambda db: "")
    monkeypatch.setattr(
        app_utils,
        "get_value_by_page_and_key",
        lambda page, key, db: values.get((page, key), ""),
    )
    monkeypatch.setattr(app_utils, "is_default_terms_of_service", lambda content: True)

    policy = app_utils.get_terms_of_service_policy(object())

    assert policy["signup_available"] is True
    assert policy["require_current_revision_for_signup"] is False
