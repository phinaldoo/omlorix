from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "webauthn" not in sys.modules:
    def _bytes_to_base64url(value: bytes) -> str:
        import base64

        return base64.urlsafe_b64encode(bytes(value)).rstrip(b"=").decode("ascii")

    def _base64url_to_bytes(value: str) -> bytes:
        import base64

        normalized = str(value)
        return base64.urlsafe_b64decode(normalized + "=" * (-len(normalized) % 4))

    webauthn_stub = ModuleType("webauthn")
    webauthn_stub.generate_authentication_options = lambda *args, **kwargs: None
    webauthn_stub.generate_registration_options = lambda *args, **kwargs: None
    webauthn_stub.options_to_json = lambda value: value
    webauthn_stub.verify_authentication_response = lambda *args, **kwargs: None
    webauthn_stub.verify_registration_response = lambda *args, **kwargs: None

    helpers_stub = ModuleType("webauthn.helpers")
    helpers_stub.base64url_to_bytes = _base64url_to_bytes
    helpers_stub.bytes_to_base64url = _bytes_to_base64url

    structs_stub = ModuleType("webauthn.helpers.structs")

    class _StructPlaceholder:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            for key, item in kwargs.items():
                setattr(self, key, item)
            if args:
                self.id = args[0]

    structs_stub.AuthenticatorSelectionCriteria = _StructPlaceholder
    structs_stub.PublicKeyCredentialDescriptor = _StructPlaceholder
    structs_stub.UserVerificationRequirement = _StructPlaceholder

    sys.modules["webauthn"] = webauthn_stub
    sys.modules["webauthn.helpers"] = helpers_stub
    sys.modules["webauthn.helpers.structs"] = structs_stub

from app.auth import passkeys


class FakeQuery:
    def __init__(self, db):
        self.db = db

    def filter(self, *_args):
        return self

    def with_for_update(self):
        self.db.lock_count += 1
        return self

    def first(self):
        return self.db.entry


class FakeDb:
    def __init__(self, entry):
        self.entry = entry
        self.lock_count = 0
        self.commit_count = 0
        self.refresh_count = 0
        self.rollback_count = 0

    def query(self, *_args):
        return FakeQuery(self)

    def commit(self):
        self.commit_count += 1

    def refresh(self, entry):
        self.refresh_count += 1
        self.entry = entry

    def rollback(self):
        self.rollback_count += 1


def test_consume_challenge_locks_row_before_marking_it_used():
    entry = SimpleNamespace(
        challenge="challenge-b64",
        flow="authentication",
        used_at=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        user_id="user-1",
    )
    db = FakeDb(entry)

    result = passkeys._consume_challenge(
        db,
        challenge_b64="challenge-b64",
        flow="authentication",
        user_id="user-1",
    )

    assert result is entry
    assert entry.used_at is not None
    assert db.lock_count == 1
    assert db.commit_count == 1
    assert db.refresh_count == 1
    assert db.rollback_count == 0


def test_consume_challenge_accepts_postgres_shaped_naive_expiry():
    entry = SimpleNamespace(
        challenge="challenge-b64",
        flow="authentication",
        used_at=None,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(minutes=5),
        user_id="user-1",
    )
    db = FakeDb(entry)

    result = passkeys._consume_challenge(
        db,
        challenge_b64="challenge-b64",
        flow="authentication",
        user_id="user-1",
    )

    assert result is entry
    assert entry.used_at is not None
    assert db.commit_count == 1
    assert db.rollback_count == 0


def test_consume_challenge_rolls_back_locked_row_when_validation_fails():
    entry = SimpleNamespace(
        challenge="challenge-b64",
        flow="authentication",
        used_at=None,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        user_id="user-1",
    )
    db = FakeDb(entry)

    with pytest.raises(HTTPException) as exc:
        passkeys._consume_challenge(
            db,
            challenge_b64="challenge-b64",
            flow="authentication",
            user_id="user-1",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "WebAuthn challenge has expired"
    assert db.lock_count == 1
    assert db.commit_count == 0
    assert db.refresh_count == 0
    assert db.rollback_count == 1
