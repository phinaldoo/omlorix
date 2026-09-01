from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import dependencies


class _FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _request(path: str = "/api/v1/admin/users") -> Request:
    return Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"user-agent", b"test-agent")],
            "client": ("203.0.113.10", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _credentials():
    return SimpleNamespace(scheme="Bearer", credentials="valid-user-token")


def test_valid_non_admin_access_denial_is_audited(monkeypatch):
    denied_user = SimpleNamespace(id="user-1", role="user")
    audit_calls = []
    db = _FakeDb()

    def reject_admin(*_args, **_kwargs):
        raise HTTPException(
            status_code=401,
            detail="You do not have permission to perform this action",
        )

    monkeypatch.setattr(dependencies, "check_admin_by_token", reject_admin)
    monkeypatch.setattr(dependencies, "check_user_by_token", lambda *_args, **_kwargs: denied_user)
    monkeypatch.setattr(dependencies, "get_audit_request_ip", lambda *_args, **_kwargs: "203.0.113.10")
    monkeypatch.setattr(
        dependencies,
        "stage_audit_log_event",
        lambda staged_db, **kwargs: audit_calls.append(
            {"db": staged_db, **kwargs}
        ),
    )

    with pytest.raises(HTTPException) as exc:
        dependencies.verified_admin(
            _request(),
            _credentials(),
            db=db,
        )

    assert exc.value.status_code == 401
    assert audit_calls == [
        {
            "db": db,
            "user_id": "user-1",
            "action": "ADMIN_ACCESS_DENIED",
            "details": {
                "method": "DELETE",
                "route": "/api/v1/admin/users",
                "actor_role": "user",
            },
            "ip_address": "203.0.113.10",
            "user_agent": "test-agent",
            "category": "auth_security",
        }
    ]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_invalid_credential_denial_is_not_attributed(monkeypatch):
    audit_calls = []
    db = _FakeDb()

    def reject_token(*_args, **_kwargs):
        raise HTTPException(status_code=401, detail="Invalid or malformed access token")

    monkeypatch.setattr(dependencies, "check_admin_by_token", reject_token)
    monkeypatch.setattr(
        dependencies,
        "check_user_by_token",
        lambda *_args, **_kwargs: pytest.fail("invalid credentials must not be attributed"),
    )
    monkeypatch.setattr(
        dependencies,
        "stage_audit_log_event",
        lambda *_args, **kwargs: audit_calls.append(kwargs),
    )

    with pytest.raises(HTTPException) as exc:
        dependencies.verified_admin(
            _request(),
            _credentials(),
            db=db,
        )

    assert exc.value.detail == "Invalid or malformed access token"
    assert audit_calls == []
    assert db.commits == 0
    assert db.rollbacks == 0
