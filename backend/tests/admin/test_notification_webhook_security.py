from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.settings import utils as admin_utils
from app.network import policy
from app.settings.defaults import DEFAULT_SETTINGS


class _DummyDB:
    def commit(self) -> None:
        return None

    def refresh(self, _record) -> None:
        return None


def _install_notification_settings_mocks(monkeypatch) -> SimpleNamespace:
    settings_record = SimpleNamespace(
        data=DEFAULT_SETTINGS["notifications"].copy(),
        page_name="notifications",
        updated_at=None,
    )

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, page: settings_record if page == "notifications" else None,
    )
    monkeypatch.setattr(
        admin_utils,
        "ensure_sensitive_settings_page_encrypted",
        lambda _page_name, data, **_kwargs: (False, data),
    )
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(admin_utils, "invalidate_settings_cache", lambda: None)
    monkeypatch.setattr(
        policy,
        "_resolve_host_ips",
        lambda hostname: ("93.184.216.34",) if hostname == "hooks.example" else (),
    )

    return settings_record


def test_update_admin_notification_settings_stores_only_public_https_webhooks(monkeypatch):
    settings_record = _install_notification_settings_mocks(monkeypatch)

    changed_keys = admin_utils.update_admin_settings_values_for_page(
        page="notifications",
        payload={"webhook_url": " https://hooks.example/webhook "},
        db=_DummyDB(),
    )

    assert changed_keys == ["webhook_url"]
    assert settings_record.data["webhook_url"] == "https://hooks.example/webhook"


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example/webhook",
        "https://localhost/webhook",
        "https://169.254.169.254/latest/meta-data/",
        "https://unresolved.example/webhook",
    ],
)
def test_update_admin_notification_settings_rejects_unsafe_webhooks(monkeypatch, url):
    settings_record = _install_notification_settings_mocks(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        admin_utils.update_admin_settings_values_for_page(
            page="notifications",
            payload={"webhook_url": url},
            db=_DummyDB(),
        )

    assert exc_info.value.status_code == 400
    assert settings_record.data["webhook_url"] == ""
