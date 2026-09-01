from pathlib import Path

from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.init import _sync_with_defaults


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_legacy_user_webhook_settings_are_removed_during_settings_sync():
    """Retired webhook destinations must not survive settings normalization."""

    changed, synchronized = _sync_with_defaults(
        {"notifications": {"webhook_url": "https://example.com/hook"}}
    )

    assert changed is True
    assert "notifications" not in DEFAULT_USER_SETTINGS
    assert "notifications" not in synchronized


def test_user_webhook_api_ui_and_delivery_code_are_absent():
    """Keep the removed feature from being exposed again by a partial rollback."""

    users_router = (REPO_ROOT / "backend/app/users/router.py").read_text(encoding="utf-8")
    user_notifications = (
        REPO_ROOT / "backend/app/userNotifications/models.py"
    ).read_text(encoding="utf-8")
    settings_html = (REPO_ROOT / "frontend/index.html").read_text(encoding="utf-8")

    assert '"/settings/notifications"' not in users_router
    assert "User notification webhook delivery" not in user_notifications
    assert 'data-us-page="notifications"' not in settings_html
    assert "userNotificationsWebhookUrl" not in settings_html
