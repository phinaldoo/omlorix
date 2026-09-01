import sys
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


def _bytes_to_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

webauthn_module = ModuleType("webauthn")
webauthn_module.generate_authentication_options = lambda **kwargs: kwargs
webauthn_module.generate_registration_options = lambda **kwargs: kwargs
webauthn_module.options_to_json = lambda value: "{}"
webauthn_module.verify_authentication_response = lambda **kwargs: None
webauthn_module.verify_registration_response = lambda **kwargs: None

webauthn_helpers_module = ModuleType("webauthn.helpers")
webauthn_helpers_module.base64url_to_bytes = lambda value: base64.urlsafe_b64decode(
    value + "=" * (-len(value) % 4)
)
webauthn_helpers_module.bytes_to_base64url = _bytes_to_base64url  # type: ignore[name-defined]

webauthn_structs_module = ModuleType("webauthn.helpers.structs")


class _AuthenticatorSelectionCriteria:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _PublicKeyCredentialDescriptor:
    def __init__(self, id):
        self.id = id


class _UserVerificationRequirement:
    REQUIRED = "required"


webauthn_structs_module.AuthenticatorSelectionCriteria = _AuthenticatorSelectionCriteria
webauthn_structs_module.PublicKeyCredentialDescriptor = _PublicKeyCredentialDescriptor
webauthn_structs_module.UserVerificationRequirement = _UserVerificationRequirement

sys.modules.setdefault("webauthn", webauthn_module)
sys.modules.setdefault("webauthn.helpers", webauthn_helpers_module)
sys.modules.setdefault("webauthn.helpers.structs", webauthn_structs_module)

from app.auth import passkeys
from app.auth.models import PasskeyCredential
from app.database import Base
from app.users.models import User


def _passkey_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[User.__table__, PasskeyCredential.__table__])
    return sessionmaker(bind=engine)()


def test_webauthn_config_selects_matching_secondary_public_origin(monkeypatch):
    """Each configured host receives WebAuthn options for its own RP ID."""
    monkeypatch.setattr(
        passkeys,
        "_resolve_webauthn_config",
        lambda db: ("primary.example", "Omlorix", "https://primary.example"),
    )
    monkeypatch.setattr(
        passkeys,
        "get_public_urls",
        lambda db: ["https://primary.example", "https://secondary.example"],
    )

    assert passkeys._resolve_webauthn_config_for_origin(object(), None) == (
        "primary.example",
        "Omlorix",
        "https://primary.example",
    )
    assert passkeys._resolve_webauthn_config_for_origin(object(), "https://secondary.example/path") == (
        "secondary.example",
        "Omlorix",
        "https://secondary.example",
    )


def test_webauthn_config_preserves_secondary_ipv6_origin(monkeypatch):
    """WebAuthn expected origins keep the URL brackets required for IPv6 hosts."""
    monkeypatch.setattr(
        passkeys,
        "_resolve_webauthn_config",
        lambda db: ("primary.example", "Omlorix", "https://primary.example"),
    )
    monkeypatch.setattr(
        passkeys,
        "get_public_urls",
        lambda db: ["https://primary.example", "https://[2001:db8::2]:8443"],
    )

    assert passkeys._resolve_webauthn_config_for_origin(
        object(),
        "https://[2001:db8::2]:8443/path",
    ) == (
        "2001:db8::2",
        "Omlorix",
        "https://[2001:db8::2]:8443",
    )


def _patch_registration_verification(monkeypatch, *, credential_id: bytes, public_key: bytes, sign_count: int = 0):
    monkeypatch.setattr(passkeys, "get_passkey_policy", lambda db: {"enable_passkeys": True})
    monkeypatch.setattr(passkeys, "_resolve_webauthn_config", lambda db: ("chat.example.com", "Omlorix", "https://chat.example.com"))
    monkeypatch.setattr(passkeys, "_consume_challenge", lambda db, challenge_b64, flow, user_id: None)
    monkeypatch.setattr(passkeys, "_normalize_webauthn_credential_payload", lambda credential, registration: credential)
    monkeypatch.setattr(passkeys, "_extract_client_origin_from_credential", lambda credential: "https://chat.example.com")
    monkeypatch.setattr(passkeys, "_derive_passkey_device_name", lambda user_agent: "Test Passkey")
    monkeypatch.setattr(
        passkeys,
        "verify_registration_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=credential_id,
            credential_public_key=public_key,
            sign_count=sign_count,
        ),
    )


def test_finish_registration_reactivates_same_user_inactive_credential(monkeypatch):
    db = _passkey_session()
    try:
        credential_id = b"credential-1"
        public_key = b"new-public-key"
        credential_id_b64 = _bytes_to_base64url(credential_id)
        challenge_b64 = _bytes_to_base64url(b"challenge-1")
        previous_created_at = datetime.now(timezone.utc) - timedelta(days=2)
        row = PasskeyCredential(
            id="passkey-1",
            user_id="user-1",
            credential_id=credential_id_b64,
            public_key=_bytes_to_base64url(b"old-public-key"),
            sign_count="1",
            transports="usb",
            name="Old Passkey",
            created_at=previous_created_at,
            last_used_at=datetime.now(timezone.utc) - timedelta(days=1),
            is_active=False,
        )
        db.add(row)
        db.commit()

        _patch_registration_verification(
            monkeypatch,
            credential_id=credential_id,
            public_key=public_key,
            sign_count=7,
        )
        monkeypatch.setattr(
            "app.users.models.get_user",
            lambda _db, user_id: SimpleNamespace(
                id=user_id,
                email="user@example.com",
            ),
        )
        monkeypatch.setattr(
            "app.email.service.enqueue_security_event",
            lambda *_args, **_kwargs: None,
        )

        result = passkeys.finish_registration(
            db,
            user_id="user-1",
            credential={},
            expected_challenge=challenge_b64,
            user_agent="Mozilla/5.0",
        )

        db.refresh(row)
        assert result == {"status": "success", "credential_id": credential_id_b64}
        assert db.query(PasskeyCredential).count() == 1
        assert row.is_active is True
        assert row.public_key == _bytes_to_base64url(public_key)
        assert row.sign_count == "7"
        assert row.transports is None
        assert row.name == "Test Passkey"
        assert row.last_used_at is None
        assert row.created_at.replace(tzinfo=timezone.utc) >= previous_created_at
    finally:
        db.close()


def test_finish_registration_rejects_inactive_credential_owned_by_another_user(monkeypatch):
    db = _passkey_session()
    try:
        credential_id = b"credential-2"
        credential_id_b64 = _bytes_to_base64url(credential_id)
        challenge_b64 = _bytes_to_base64url(b"challenge-2")
        row = PasskeyCredential(
            id="passkey-2",
            user_id="user-2",
            credential_id=credential_id_b64,
            public_key=_bytes_to_base64url(b"public-key"),
            sign_count="3",
            transports=None,
            name="Other User Passkey",
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            is_active=False,
        )
        db.add(row)
        db.commit()

        _patch_registration_verification(
            monkeypatch,
            credential_id=credential_id,
            public_key=b"replacement-public-key",
            sign_count=4,
        )

        with pytest.raises(HTTPException) as exc_info:
            passkeys.finish_registration(
                db,
                user_id="user-1",
                credential={},
                expected_challenge=challenge_b64,
                user_agent="Mozilla/5.0",
            )

        db.refresh(row)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "Passkey already exists"
        assert row.is_active is False
    finally:
        db.close()


def test_active_only_unique_index_allows_inactive_duplicates_but_blocks_two_active_rows():
    db = _passkey_session()
    try:
        now = datetime.now(timezone.utc)
        shared_credential_id = "shared-credential"
        db.add_all(
            [
                PasskeyCredential(
                    id="inactive-1",
                    user_id="user-1",
                    credential_id=shared_credential_id,
                    public_key="public-key-1",
                    sign_count="0",
                    transports=None,
                    name="Inactive One",
                    created_at=now,
                    last_used_at=None,
                    is_active=False,
                ),
                PasskeyCredential(
                    id="inactive-2",
                    user_id="user-2",
                    credential_id=shared_credential_id,
                    public_key="public-key-2",
                    sign_count="0",
                    transports=None,
                    name="Inactive Two",
                    created_at=now,
                    last_used_at=None,
                    is_active=False,
                ),
            ]
        )
        db.commit()

        db.add(
            PasskeyCredential(
                id="active-1",
                user_id="user-1",
                credential_id=shared_credential_id,
                public_key="public-key-3",
                sign_count="0",
                transports=None,
                name="Active One",
                created_at=now,
                last_used_at=None,
                is_active=True,
            )
        )
        db.commit()

        db.add(
            PasskeyCredential(
                id="active-2",
                user_id="user-2",
                credential_id=shared_credential_id,
                public_key="public-key-4",
                sign_count="0",
                transports=None,
                name="Active Two",
                created_at=now,
                last_used_at=None,
                is_active=True,
            )
        )

        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_localhost_origin_fallback_rejects_different_loopback_scheme_or_port():
    assert not passkeys._can_use_localhost_origin_fallback(
        "https://localhost:8443",
        "http://localhost:8443",
        "localhost",
    )
    assert not passkeys._can_use_localhost_origin_fallback(
        "http://localhost:3000",
        "http://localhost:9999",
        "localhost",
    )


def test_localhost_origin_fallback_allows_only_equivalent_loopback_origins():
    assert passkeys._can_use_localhost_origin_fallback(
        "http://localhost:80",
        "http://localhost",
        "localhost",
    )
    assert passkeys._can_use_localhost_origin_fallback(
        "https://localhost:443",
        "https://LOCALHOST",
        "localhost",
    )
