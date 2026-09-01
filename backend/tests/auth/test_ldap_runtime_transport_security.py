from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import ldap as ldap_auth
from app.auth.ldap_transport import LDAP_TRANSPORT_SECURITY_RUNTIME_ERROR_DETAIL
from app.settings.defaults import DEFAULT_SETTINGS


def test_ldap_provider_rejects_legacy_plaintext_bind_settings(monkeypatch):
    ldap_settings = DEFAULT_SETTINGS["login_ldap"].copy()
    ldap_settings.update(
        {
            "enable_ldap": True,
            "ldap_server_uris": ["ldap://ldap.example.com"],
            "ldap_user_base_dn": "dc=example,dc=com",
        }
    )

    monkeypatch.setattr(
        ldap_auth, "get_settings_page_data", lambda _db, _page: ldap_settings
    )

    provider = ldap_auth.LDAPAuthProvider(db=object())

    with pytest.raises(HTTPException) as exc_info:
        provider._build_server()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == LDAP_TRANSPORT_SECURITY_RUNTIME_ERROR_DETAIL
