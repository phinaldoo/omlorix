from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.settings import utils as admin_utils


def test_admin_notification_settings_schema_returns_webhook_url(monkeypatch):
    """Admin notification settings should expose their editable webhook URL."""

    def get_settings_page_data(_db, page, **_kwargs):
        if page != "notifications":
            return {}
        return {
            "enable_notifications": True,
            "webhook_url": "https://hooks.example/webhook",
        }

    monkeypatch.setattr(admin_utils, "get_settings_page_data", get_settings_page_data)

    response = admin_utils.get_admin_settings_schema_response(
        "notifications",
        include_values=True,
        db=object(),
    )

    assert response["values"]["enable_notifications"] is True
    assert response["values"]["webhook_url"] == "https://hooks.example/webhook"
