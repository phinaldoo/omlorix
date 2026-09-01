import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app.auth.social import AppleAuthProvider
from app.auth.social import APPLE_PRIVATE_KEY_ERROR_DETAIL, validate_apple_private_key
from app.auth.utils import _merge_apple_form_post_user
from app.settings.defaults import DEFAULT_SETTINGS


def test_apple_login_defaults_include_all_runtime_keys():
    login_social = DEFAULT_SETTINGS["login_social"]

    assert login_social["enable_apple_login"] is False
    assert login_social["apple_client_id"] == ""
    assert login_social["apple_team_id"] == ""
    assert login_social["apple_key_id"] == ""
    assert login_social["apple_private_key"] == ""
    assert login_social["apple_button_text"] == ""
    assert login_social["apple_allowed_domains"] == []
    assert login_social["apple_allow_signup"] is True


def test_apple_provider_enablement_uses_one_login_toggle():
    provider = AppleAuthProvider.__new__(AppleAuthProvider)
    provider.settings = {
        "enable_apple_login": False,
        "apple_client_id": "com.example.service",
        "apple_team_id": "TEAMID1234",
        "apple_key_id": "KEYID12345",
        "apple_private_key": "private-key",
    }

    assert provider.is_enabled() is False

    provider.settings["enable_apple_login"] = True
    assert provider.is_enabled() is True


def test_apple_private_key_accepts_escaped_newlines():
    provider = AppleAuthProvider.__new__(AppleAuthProvider)
    provider.settings = {
        "apple_private_key": "-----BEGIN PRIVATE KEY-----\\nkey-data\\n-----END PRIVATE KEY-----",
    }

    assert (
        provider.get_private_key()
        == "-----BEGIN PRIVATE KEY-----\nkey-data\n-----END PRIVATE KEY-----"
    )


def test_validate_apple_private_key_accepts_full_p8_pem_with_escaped_newlines():
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")

    assert validate_apple_private_key(pem.replace("\n", "\\n")) == pem.strip()


def test_validate_apple_private_key_rejects_missing_pem_framing():
    with pytest.raises(HTTPException) as exc_info:
        validate_apple_private_key("base64-key-body-only")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == APPLE_PRIVATE_KEY_ERROR_DETAIL


def test_apple_form_post_user_payload_fills_first_login_name():
    user_info = {
        "sub": "apple-sub",
        "email": "person@example.com",
        "name": "",
        "given_name": "",
        "family_name": "",
    }
    raw_payload = json.dumps(
        {
            "name": {
                "firstName": "Ada",
                "lastName": "Lovelace",
            },
            "email": "ignored@example.com",
        }
    )

    merged = _merge_apple_form_post_user(user_info, raw_payload)

    assert merged["name"] == "Ada Lovelace"
    assert merged["given_name"] == "Ada"
    assert merged["family_name"] == "Lovelace"
    assert merged["email"] == "person@example.com"


def test_apple_form_post_user_payload_does_not_fill_missing_email():
    user_info = {
        "sub": "apple-sub",
        "email": "",
        "name": "",
        "given_name": "",
        "family_name": "",
    }
    raw_payload = json.dumps(
        {
            "name": {
                "firstName": "Ada",
                "lastName": "Lovelace",
            },
            "email": "attacker-supplied@example.com",
        }
    )

    merged = _merge_apple_form_post_user(user_info, raw_payload)

    assert merged["name"] == "Ada Lovelace"
    assert merged["given_name"] == "Ada"
    assert merged["family_name"] == "Lovelace"
    assert merged["email"] == ""
