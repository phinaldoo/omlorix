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

from app.users import init as user_init
from app.utils import utils as app_utils


def test_modal_notice_is_visible_until_the_current_revision_is_dismissed(monkeypatch):
    def fake_setting(page, key, db):
        settings = {
            ("about", "privacy_policy_revision"): 4,
            ("about", "privacy_policy_notice_mode"): "modal",
            ("about", "privacy_policy_notice_message_html"): "",
            ("about", "privacy_policy_notice_updated_at"): "2026-05-01T00:00:00+00:00",
        }
        return settings.get((page, key))

    monkeypatch.setattr(app_utils, "get_value_by_page_and_key", fake_setting)
    monkeypatch.setattr(
        user_init,
        "get_user_setting_value",
        lambda user_id, page, key, db: None,
    )

    policy = app_utils.get_privacy_policy_notice_policy(object(), "user-1")

    assert policy["notice_mode"] == "modal"
    assert policy["stored_notice_mode"] == "modal"
    assert policy["should_show_notice"] is True
    assert policy["privacy_policy_last_interacted_revision"] is None
    assert "accepted_current_revision" not in policy
    assert "require_current_revision_for_access" not in policy


def test_legacy_required_opt_in_is_normalized_to_modal(monkeypatch):
    def fake_setting(page, key, db):
        settings = {
            ("about", "privacy_policy_revision"): 5,
            ("about", "privacy_policy_notice_mode"): "required_opt_in",
            ("about", "privacy_policy_notice_message_html"): "",
            ("about", "privacy_policy_notice_updated_at"): "2026-05-01T00:00:00+00:00",
        }
        return settings.get((page, key))

    monkeypatch.setattr(app_utils, "get_value_by_page_and_key", fake_setting)
    monkeypatch.setattr(
        user_init,
        "get_user_setting_value",
        lambda user_id, page, key, db: 5 if key == "privacy_policy_last_interacted_revision" else None,
    )

    policy = app_utils.get_privacy_policy_notice_policy(object(), "user-1")

    assert policy["notice_mode"] == "modal"
    assert policy["stored_notice_mode"] == "modal"
    assert policy["should_show_notice"] is False
    assert policy["privacy_policy_last_interacted_revision"] == 5
