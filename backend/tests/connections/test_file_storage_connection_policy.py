from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.connections import service
from app.connections.models import PROVIDER_GOOGLE_DRIVE, PROVIDER_GMAIL, PROVIDER_SLACK
from app.connections.policy import ensure_group_allows_connection_provider, group_allows_connection_provider


def _patch_group_settings(
    monkeypatch,
    *,
    enabled_connections: list[str],
    allow_file_storage: bool,
    personal_mcp_enabled: bool = True,
) -> None:
    def _setting(_user_id, section, key, _db):
        if (section, key) == ("tools_mcp", "enable_mcp"):
            return personal_mcp_enabled
        if (section, key) == ("tools_mcp", "enabled_connections"):
            return enabled_connections
        if (section, key) == ("tools_mcp", "allow_file_storage_connections"):
            return allow_file_storage
        return None

    monkeypatch.setattr("app.groups.init.get_user_group_setting_value", _setting)


def test_workspace_connection_policy_is_independent_of_personal_mcp_toggle(monkeypatch):
    """The personal-server setting must not disable allowed connections."""
    _patch_group_settings(
        monkeypatch,
        enabled_connections=[PROVIDER_GOOGLE_DRIVE],
        allow_file_storage=True,
        personal_mcp_enabled=False,
    )

    ensure_group_allows_connection_provider(
        "user-1",
        object(),
        provider=PROVIDER_GOOGLE_DRIVE,
    )


def test_storage_provider_policy_requires_enabled_provider_and_file_storage_opt_in(monkeypatch):
    _patch_group_settings(
        monkeypatch,
        enabled_connections=[PROVIDER_GOOGLE_DRIVE],
        allow_file_storage=False,
    )

    assert group_allows_connection_provider("user-1", object(), provider=PROVIDER_GOOGLE_DRIVE) is False

    _patch_group_settings(
        monkeypatch,
        enabled_connections=[PROVIDER_GOOGLE_DRIVE],
        allow_file_storage=True,
    )

    assert group_allows_connection_provider("user-1", object(), provider=PROVIDER_GOOGLE_DRIVE) is True


def test_connection_management_uses_the_stored_provider(monkeypatch):
    gmail_connection = SimpleNamespace(provider=PROVIDER_GMAIL)
    slack_connection = SimpleNamespace(provider=PROVIDER_SLACK)
    checked_providers: list[str] = []

    def allows_provider(_user_id, _db, *, provider):
        checked_providers.append(provider)
        return provider == PROVIDER_SLACK

    monkeypatch.setattr(service, "_group_allows_provider", allows_provider)

    assert service._group_allows_connection_management(
        "user-1",
        object(),
        connection=gmail_connection,
    ) is False
    assert service._group_allows_connection_management(
        "user-1",
        object(),
        connection=slack_connection,
    ) is True
    assert checked_providers == [PROVIDER_GMAIL, PROVIDER_SLACK]
