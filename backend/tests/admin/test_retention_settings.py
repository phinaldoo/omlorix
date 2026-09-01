from types import SimpleNamespace

from app.admin.settings import router as admin_router


def test_backfilled_retention_setting_records_previous_policy_when_payload_omits_key(
    monkeypatch,
):
    audit_entries = []
    audit_policy_calls = iter(
        [
            {"mode": "delete_after_days", "retention_days": 30, "delete_immediately": False},
            {"mode": "retain", "retention_days": None, "delete_immediately": False},
        ]
    )
    monkeypatch.setattr(
        admin_router,
        "update_admin_settings_values_for_page",
        lambda **kwargs: ["audit_logs_retention_after_user_delete_mode"],
    )
    monkeypatch.setattr(
        admin_router,
        "get_auth_log_user_deletion_retention_policy",
        lambda _db: {"mode": "retain", "retention_days": None, "delete_immediately": False},
    )
    monkeypatch.setattr(
        admin_router,
        "get_audit_log_user_deletion_retention_policy",
        lambda _db: next(audit_policy_calls),
    )
    monkeypatch.setattr(
        admin_router,
        "create_audit_log",
        lambda **kwargs: audit_entries.append(kwargs),
    )
    monkeypatch.setattr(admin_router, "get_audit_request_ip", lambda *args: "203.0.113.8")

    result = admin_router.update_admin_settings_values(
        page="security",
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        payload={},
        db=object(),
        db_log=object(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    assert result.status == "success"
    security_event = next(
        entry
        for entry in audit_entries
        if entry["action"] == "USER_DELETION_RETENTION_POLICY_UPDATED"
    )
    assert security_event["category"] == "security"
    assert security_event["details"]["previous"]["audit_logs_and_admin_notifications"]["mode"] == "delete_after_days"
    assert security_event["details"]["effective"]["audit_logs_and_admin_notifications"]["mode"] == "retain"
