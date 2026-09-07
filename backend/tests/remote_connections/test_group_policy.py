"""Coverage for dependent SSH and user-managed ACP group permissions."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.dependencies import get_db, verified_user
from app.groups.defaults import DEFAULT_GROUP_SETTINGS
from app.admin.groups.schemas import FIELD_SCHEMA_BY_KEY
from app.remote_connections import policy as policy_module
from app.remote_connections.router import remote_connections_router


def _patch_settings(monkeypatch, *, allow_ssh: bool, allow_acp: bool) -> None:
    """Install a deterministic group-setting lookup for policy tests."""

    values = {
        ("tools_mcp", "allow_ssh_connections"): allow_ssh,
        ("tools_mcp", "allow_custom_acp_connections"): allow_acp,
    }
    monkeypatch.setattr(
        policy_module,
        "get_user_group_setting_value",
        lambda _user_id, section, key, _db: values[(section, key)],
    )


def test_custom_acp_permission_is_always_gated_by_ssh(monkeypatch):
    """An enabled ACP preference is ineffective while parent SSH access is off."""
    _patch_settings(monkeypatch, allow_ssh=False, allow_acp=True)

    assert policy_module.get_remote_connection_permissions(object(), "user-one") == (
        False,
        False,
    )
    with pytest.raises(HTTPException) as exc:
        policy_module.require_custom_acp_connections(object(), "user-one")
    assert exc.value.status_code == 403


def test_remote_connection_defaults_and_schema_express_dependency():
    """SSH requires opt-in while the admin form exposes the ACP dependency."""
    defaults = DEFAULT_GROUP_SETTINGS["tools_mcp"]
    assert defaults["allow_ssh_connections"] is False
    assert defaults["allow_custom_acp_connections"] is True

    acp_field = FIELD_SCHEMA_BY_KEY[
        "settings.tools_mcp.allow_custom_acp_connections"
    ]
    assert acp_field.dependency == "settings.tools_mcp.allow_ssh_connections"
    assert acp_field.dependency_value is True


def test_ssh_route_rejects_users_when_group_access_is_disabled(monkeypatch):
    """REST enforcement must reject direct callers even when the UI is hidden."""
    _patch_settings(monkeypatch, allow_ssh=False, allow_acp=True)
    app = FastAPI()
    app.include_router(remote_connections_router)
    app.dependency_overrides[verified_user] = lambda: SimpleNamespace(id="user-one")
    app.dependency_overrides[get_db] = lambda: object()

    with TestClient(app) as client:
        response = client.get("/api/v1/remote-connections/ssh")

    assert response.status_code == 403
    assert response.json()["detail"] == "SSH connections are disabled for your group."
