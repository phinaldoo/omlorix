"""Regression tests for OAuth popup data embedded in inline JavaScript."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.files import google_drive, router


SCRIPT_BREAKOUT = "</script><script>globalThis.oauthPwned=true</script>"


def _response_text(response) -> str:
    return response.body.decode("utf-8")


def test_google_drive_callback_uses_same_script_safe_serialization(monkeypatch):
    """Serialize untrusted Google callback text safely before inline insertion."""
    monkeypatch.setattr(router, "get_public_url", lambda _db: "https://chat.example")
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *_args: None)

    html = _response_text(
        router.complete_google_drive_oauth_route(
            request=SimpleNamespace(headers={}),
            code=None,
            state=None,
            error=SCRIPT_BREAKOUT,
            db=object(),
        )
    )

    assert SCRIPT_BREAKOUT not in html
    assert r"\u003c/script\u003e\u003cscript\u003e" in html


@pytest.mark.parametrize("connection_change", ["created", "updated"])
def test_google_drive_callback_audits_connection_without_oauth_material(
    monkeypatch,
    connection_change,
):
    """A successful provider callback records only the resulting connection."""

    audit_calls = []
    db = object()
    monkeypatch.setattr(router, "get_public_url", lambda _db: "https://chat.example")
    monkeypatch.setattr(
        router,
        "resolve_connection_oauth_audit_subject",
        lambda *_args, **_kwargs: {
            "user_id": "user-1",
            "provider": "google_drive",
        },
    )
    def complete_oauth(*_args, before_connection_commit, **_kwargs):
        before_connection_commit(
            SimpleNamespace(id="connection-1", user_id="user-1"),
            connection_change,
        )
        return {
            "connection_id": "connection-1",
            "connected": True,
            "return_path": "/chat",
            "user_id": "user-1",
            "connection_change": connection_change,
        }

    monkeypatch.setattr(router, "complete_google_drive_oauth", complete_oauth)
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *_args: "audit-ip")
    monkeypatch.setattr(
        router,
        "stage_audit_log_event",
        lambda staged_db, **kwargs: audit_calls.append(
            {"db": staged_db, **kwargs}
        ),
    )

    response = router.complete_google_drive_oauth_route(
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        code="oauth-code",
        state="oauth-state",
        error=None,
        db=db,
    )

    assert "connected" in _response_text(response)
    assert audit_calls[0]["action"] == "CONNECTION_OAUTH_COMPLETED"
    assert audit_calls[0]["db"] is db
    assert audit_calls[0]["user_id"] == "user-1"
    assert audit_calls[0]["details"] == {
        "connection_id": "connection-1",
        "provider": "google_drive",
        "status": "connected",
        "connection_change": connection_change,
    }
    serialized_details = repr(audit_calls[0]["details"])
    assert "oauth-code" not in serialized_details
    assert "oauth-state" not in serialized_details


def test_google_drive_callback_reports_failure_when_audit_intent_cannot_be_staged(
    monkeypatch,
):
    monkeypatch.setattr(router, "get_public_url", lambda _db: "https://chat.example")
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *_args: None)
    monkeypatch.setattr(
        router,
        "resolve_connection_oauth_audit_subject",
        lambda *_args, **_kwargs: {
            "user_id": "user-1",
            "provider": "google_drive",
        },
    )
    monkeypatch.setattr(
        router,
        "consume_connection_oauth_audit_subject",
        lambda *_args, **_kwargs: None,
    )

    def complete_oauth(*_args, before_connection_commit, **_kwargs):
        before_connection_commit(
            SimpleNamespace(id="connection-1", user_id="user-1"),
            "created",
        )
        pytest.fail("connection completion must stop when audit staging fails")

    monkeypatch.setattr(
        router,
        "complete_google_drive_oauth",
        complete_oauth,
    )
    monkeypatch.setattr(
        router,
        "stage_audit_log_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit unavailable")
        ),
    )

    response = router.complete_google_drive_oauth_route(
        request=SimpleNamespace(headers={}),
        code="oauth-code",
        state="oauth-state",
        error=None,
        db=object(),
    )

    response_text = _response_text(response)
    assert '"status":"error"' in response_text
    assert "audit unavailable" not in response_text


def test_google_drive_oauth_denial_consumes_valid_state_and_audits_fixed_details(
    monkeypatch,
):
    consumed = []
    audit_calls = []
    monkeypatch.setattr(router, "get_public_url", lambda _db: "https://chat.example")
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *_args: "audit-ip")
    monkeypatch.setattr(
        router,
        "consume_connection_oauth_audit_subject",
        lambda db, *, state, provider: consumed.append((db, state, provider))
        or {"user_id": "user-1", "provider": "google_drive"},
    )
    monkeypatch.setattr(
        router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )
    db = object()

    response = router.complete_google_drive_oauth_route(
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        code=None,
        state="private-state",
        error="private-provider-error",
        db=db,
        db_log=object(),
    )

    assert consumed == [(db, "private-state", "google_drive")]
    assert audit_calls[0]["action"] == "CONNECTION_OAUTH_DENIED"
    assert audit_calls[0]["user_id"] == "user-1"
    assert audit_calls[0]["category"] == "connections"
    assert audit_calls[0]["details"] == {
        "provider": "google_drive",
        "status": "denied",
        "outcome": "provider_denied",
    }
    assert "private-state" not in repr(audit_calls)
    assert "private-provider-error" not in repr(audit_calls)
    assert '"status":"error"' in _response_text(response)


def test_google_drive_invalid_oauth_state_does_not_generate_audit_noise(monkeypatch):
    monkeypatch.setattr(router, "get_public_url", lambda _db: "https://chat.example")
    monkeypatch.setattr(
        router,
        "consume_connection_oauth_audit_subject",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        router,
        "create_audit_log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid state must not be audited")
        ),
    )

    response = router.complete_google_drive_oauth_route(
        request=SimpleNamespace(headers={}),
        code=None,
        state="attacker-state",
        error="access_denied",
        db=object(),
        db_log=object(),
    )

    assert '"status":"error"' in _response_text(response)


def test_google_drive_completion_failure_audits_without_callback_material(monkeypatch):
    audit_calls = []
    monkeypatch.setattr(router, "get_public_url", lambda _db: "https://chat.example")
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *_args: None)
    monkeypatch.setattr(
        router,
        "resolve_connection_oauth_audit_subject",
        lambda *_args, **_kwargs: {
            "user_id": "user-1",
            "provider": "google_drive",
        },
    )
    monkeypatch.setattr(
        router,
        "consume_connection_oauth_audit_subject",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        router,
        "complete_google_drive_oauth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=400, detail="private provider response")
        ),
    )
    monkeypatch.setattr(
        router,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    response = router.complete_google_drive_oauth_route(
        request=SimpleNamespace(headers={}),
        code="private-code",
        state="private-state",
        error=None,
        db=object(),
        db_log=object(),
    )

    assert audit_calls[0]["action"] == "CONNECTION_OAUTH_FAILED"
    assert audit_calls[0]["details"] == {
        "provider": "google_drive",
        "status": "failed",
        "outcome": "completion_failed",
    }
    assert "private-code" not in repr(audit_calls)
    assert "private-state" not in repr(audit_calls)
    assert "private provider response" not in repr(audit_calls)
    assert '"status":"error"' in _response_text(response)


def test_google_drive_denial_popup_survives_audit_delivery_failure(monkeypatch):
    monkeypatch.setattr(router, "get_public_url", lambda _db: "https://chat.example")
    monkeypatch.setattr(
        router,
        "consume_connection_oauth_audit_subject",
        lambda *_args, **_kwargs: {
            "user_id": "user-1",
            "provider": "google_drive",
        },
    )
    monkeypatch.setattr(
        router,
        "create_audit_log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit unavailable")
        ),
    )

    response = router.complete_google_drive_oauth_route(
        request=SimpleNamespace(headers={}),
        code=None,
        state="private-state",
        error="access_denied",
        db=object(),
        db_log=object(),
    )

    response_text = _response_text(response)
    assert '"status":"error"' in response_text
    assert "access_denied" in response_text
    assert "audit unavailable" not in response_text


@pytest.mark.parametrize(
    ("existing", "expected_change"),
    [(None, "created"), (SimpleNamespace(id="connection-1"), "updated")],
)
def test_google_drive_oauth_completion_returns_safe_actor_and_change(
    monkeypatch,
    existing,
    expected_change,
):
    calls = []
    monkeypatch.setattr(
        google_drive,
        "_assert_google_drive_url_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        google_drive,
        "complete_google_oauth",
        lambda *_args, **_kwargs: {
            "user_id": "user-1",
            "return_path": "/chat",
            "secrets": {"access_token": "private-token"},
            "status": {"state": "connected"},
        },
    )
    monkeypatch.setattr(
        google_drive,
        "ensure_group_allows_connection_provider",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        google_drive,
        "get_user_connection_by_provider",
        lambda *_args, **_kwargs: existing,
    )
    def persist_connection(change, *_args, **kwargs):
        record = SimpleNamespace(id="connection-1", user_id="user-1")
        calls.append((change, kwargs))
        if kwargs.get("before_commit") is not None:
            kwargs["before_commit"](record)
        return record

    monkeypatch.setattr(
        google_drive,
        "create_user_connection",
        lambda *_args, **kwargs: persist_connection("created", *_args, **kwargs),
    )
    monkeypatch.setattr(
        google_drive,
        "update_user_connection",
        lambda *_args, **kwargs: persist_connection("updated", *_args, **kwargs),
    )

    staged = []

    result = google_drive.complete_google_drive_oauth(
        object(),
        state="oauth-state",
        code="oauth-code",
        before_connection_commit=lambda record, change: staged.append(
            (record.id, record.user_id, change)
        ),
    )

    assert calls[0][0] == expected_change
    assert staged == [("connection-1", "user-1", expected_change)]
    assert result == {
        "return_path": "/chat",
        "connected": True,
        "connection_id": "connection-1",
        "user_id": "user-1",
        "connection_change": expected_change,
    }
    assert "secrets" not in result
    assert "oauth-code" not in repr(result)
    assert "oauth-state" not in repr(result)
