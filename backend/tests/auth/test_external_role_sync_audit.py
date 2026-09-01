import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import utils as auth_utils
from app.auth.ldap import LDAPAuthenticatedUser, LDAPGroup


def _request():
    return SimpleNamespace(headers={"User-Agent": "pytest-browser"})


def test_sso_role_sync_audits_allowed_pending_role_without_claim_values(monkeypatch):
    db = MagicMock()
    audit_db = MagicMock()
    audit = MagicMock()
    monkeypatch.setattr(auth_utils, "AuditSessionLocal", lambda: audit_db)
    monkeypatch.setattr(auth_utils, "create_audit_log", audit)
    monkeypatch.setattr(
        auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.10"
    )
    monkeypatch.setattr(
        auth_utils,
        "_resolve_group_id_from_setting",
        lambda db, value, default: "group-1",
    )

    user = SimpleNamespace(id="user-1", role="user", group_id="group-1")
    provider = SimpleNamespace(
        provider_type="oidc",
        provider_name="OIDC",
        sync_profile_on_login=lambda: False,
        sync_email_on_login=lambda: False,
        sync_app_group_on_login=lambda: False,
        sync_role_on_login=lambda: True,
        get_default_role=lambda: "user",
        get_default_group=lambda: "group-1",
    )

    auth_utils._sync_existing_user_from_sso(
        db,
        user,
        {
            "omlorix_role": "pending",
            "sub": "upstream-secret-subject",
            "email": "user@example.com",
        },
        provider,
        request=_request(),
    )

    audit.assert_called_once()
    kwargs = audit.call_args.kwargs
    assert kwargs["db_log"] is audit_db
    assert kwargs["user_id"] == "user-1"
    assert kwargs["action"] == "EXTERNAL_ROLE_SYNC_CHANGED"
    assert kwargs["category"] == "auth"
    assert kwargs["details"]["old_role"] == "user"
    assert kwargs["details"]["new_role"] == "pending"
    assert kwargs["details"]["source"] == "sso"
    assert kwargs["details"]["source_context"] == {
        "provider_type": "oidc",
        "provider_name": "OIDC",
        "provider_subject_present": True,
    }
    assert "upstream-secret-subject" not in str(kwargs["details"])
    assert "user@example.com" not in str(kwargs["details"])


def test_ldap_role_sync_audits_allowed_pending_role_without_directory_claim_values(
    monkeypatch,
):
    db = MagicMock()
    audit = MagicMock()
    monkeypatch.setattr(auth_utils, "AuditSessionLocal", lambda: MagicMock())
    monkeypatch.setattr(auth_utils, "create_audit_log", audit)
    monkeypatch.setattr(
        auth_utils, "_client_ip_from_request", lambda request, db: "203.0.113.10"
    )

    user = SimpleNamespace(id="user-2", role="user", group_id="group-1")
    ldap_user = LDAPAuthenticatedUser(
        identifier="alice",
        dn="cn=alice,dc=example,dc=com",
        directory_user_id="directory-secret-id",
        email="alice@example.com",
        first_name="Alice",
        last_name="Example",
        display_name="Alice Example",
        username="alice",
        groups=[LDAPGroup(dn="cn=admins,dc=example,dc=com", name="admins")],
        raw_attributes={"memberOf": ["cn=admins,dc=example,dc=com"]},
    )

    auth_utils._sync_existing_user_from_ldap(
        db,
        user,
        ldap_user,
        {
            "ldap_enable_group_sync": True,
            "ldap_sync_profile_on_login": False,
            "ldap_sync_email_on_login": False,
            "ldap_sync_app_group_on_login": False,
            "ldap_sync_role_on_login": True,
        },
        resolved_group_id="group-1",
        resolved_role="pending",
        request=_request(),
    )

    audit.assert_called_once()
    details = audit.call_args.kwargs["details"]
    assert details["old_role"] == "user"
    assert details["new_role"] == "pending"
    assert details["source"] == "ldap"
    assert details["source_context"] == {
        "directory_user_id_present": True,
        "directory_dn_present": True,
        "group_count": 1,
    }
    assert "directory-secret-id" not in str(details)
    assert "cn=alice" not in str(details)
    assert "alice@example.com" not in str(details)


def test_external_role_resolvers_never_grant_administrative_roles():
    provider = SimpleNamespace(get_default_role=lambda: "admin")

    assert auth_utils._resolve_sso_role(provider, {"omlorix_role": "admin"}) == "user"
    assert auth_utils._resolve_sso_role(provider, {"omlorix_role": "owner"}) == "user"
    assert (
        auth_utils._resolve_ldap_role(
            {
                "ldap_default_role": "admin",
                "ldap_group_to_role": ["admins=owner"],
            },
            ["admins"],
        )
        == "user"
    )
