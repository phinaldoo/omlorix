from app.connections import policy


def test_managed_workspace_bootstrap_policy_is_independent_from_personal_mcp(monkeypatch):
    values = {
        ("tools_mcp", "enabled_connections"): ["gmail"],
        ("tools_mcp", "allow_file_storage_connections"): False,
        ("tools_mcp", "enable_mcp"): False,
    }
    monkeypatch.setattr(
        "app.groups.init.get_user_group_setting_value",
        lambda _user_id, section, key, _db: values.get((section, key)),
    )

    assert policy.group_has_enabled_workspace_connections("user-1", object()) is True


def test_storage_only_workspace_bootstrap_requires_storage_opt_in(monkeypatch):
    values = {
        ("tools_mcp", "enabled_connections"): ["google_drive"],
        ("tools_mcp", "allow_file_storage_connections"): False,
    }
    monkeypatch.setattr(
        "app.groups.init.get_user_group_setting_value",
        lambda _user_id, section, key, _db: values.get((section, key)),
    )

    assert policy.group_has_enabled_workspace_connections("user-1", object()) is False

    values[("tools_mcp", "allow_file_storage_connections")] = True

    assert policy.group_has_enabled_workspace_connections("user-1", object()) is True


def test_empty_workspace_provider_policy_disables_managed_bootstrap(monkeypatch):
    monkeypatch.setattr(
        "app.groups.init.get_user_group_setting_value",
        lambda _user_id, _section, _key, _db: False,
    )

    assert policy.group_has_enabled_workspace_connections("user-1", object()) is False
