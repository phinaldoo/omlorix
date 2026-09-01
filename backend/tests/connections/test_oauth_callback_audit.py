from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException

from app.connections import router


def _request():
    return SimpleNamespace(headers={"user-agent": "pytest"})


def test_connection_oauth_denial_consumes_valid_state_and_audits_fixed_details(
    monkeypatch,
):
    consumed = []
    audit_calls = []
    monkeypatch.setattr(
        router,
        "consume_connection_oauth_audit_subject",
        lambda db, *, state, provider: consumed.append((db, state, provider))
        or {"user_id": "user-1", "provider": "slack"},
    )
    monkeypatch.setattr(
        router,
        "_audit_connection_event",
        lambda *args: audit_calls.append(args),
    )
    monkeypatch.setattr(
        router,
        "build_callback_redirect_url",
        lambda *_args, **_kwargs: "/workspace/connections?connection_status=error",
    )
    db = object()

    response = router.complete_connection_oauth_route(
        provider="slack",
        request=_request(),
        code=None,
        state="private-state",
        iss=None,
        error="private-provider-error",
        db=db,
        db_log=object(),
    )

    assert response.status_code == 302
    assert consumed == [(db, "private-state", "slack")]
    assert audit_calls[0][3:] == (
        "CONNECTION_OAUTH_DENIED",
        {
            "provider": "slack",
            "status": "denied",
            "outcome": "provider_denied",
        },
    )
    assert audit_calls[0][2] == "user-1"
    assert "private-state" not in repr(audit_calls)
    assert "private-provider-error" not in repr(audit_calls)


def test_connection_oauth_invalid_state_does_not_generate_audit_noise(monkeypatch):
    monkeypatch.setattr(
        router,
        "consume_connection_oauth_audit_subject",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        router,
        "_audit_connection_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid state must not be audited")
        ),
    )
    monkeypatch.setattr(
        router,
        "build_callback_redirect_url",
        lambda *_args, **_kwargs: "/workspace/connections?connection_status=error",
    )

    response = router.complete_connection_oauth_route(
        provider="slack",
        request=_request(),
        code=None,
        state="attacker-state",
        iss=None,
        error="access_denied",
        db=object(),
        db_log=object(),
    )

    assert response.status_code == 302


def test_connection_oauth_completion_failure_uses_prevalidated_subject(monkeypatch):
    audit_calls = []
    cleanup_calls = []
    monkeypatch.setattr(
        router,
        "resolve_connection_oauth_audit_subject",
        lambda *_args, **_kwargs: {"user_id": "user-1", "provider": "github"},
    )
    monkeypatch.setattr(
        router,
        "complete_connection_oauth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=400, detail="private provider response")
        ),
    )
    monkeypatch.setattr(
        router,
        "consume_connection_oauth_audit_subject",
        lambda db, *, state, provider: cleanup_calls.append((state, provider)),
    )
    monkeypatch.setattr(
        router,
        "_audit_connection_event",
        lambda *args: audit_calls.append(args),
    )
    monkeypatch.setattr(
        router,
        "build_callback_redirect_url",
        lambda *_args, **_kwargs: "/workspace/connections?connection_status=error",
    )

    response = router.complete_connection_oauth_route(
        provider="github",
        request=_request(),
        code="private-code",
        state="private-state",
        iss=None,
        error=None,
        db=object(),
        db_log=object(),
    )

    assert response.status_code == 302
    assert cleanup_calls == [("private-state", "github")]
    assert audit_calls[0][3:] == (
        "CONNECTION_OAUTH_FAILED",
        {
            "provider": "github",
            "status": "failed",
            "outcome": "completion_failed",
        },
    )
    assert "private-code" not in repr(audit_calls)
    assert "private-state" not in repr(audit_calls)
    assert "private provider response" not in repr(audit_calls)


def test_connection_oauth_denial_response_survives_audit_delivery_failure(
    monkeypatch,
):
    monkeypatch.setattr(
        router,
        "consume_connection_oauth_audit_subject",
        lambda *_args, **_kwargs: {"user_id": "user-1", "provider": "slack"},
    )
    monkeypatch.setattr(
        router,
        "_audit_connection_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("audit unavailable")
        ),
    )
    monkeypatch.setattr(
        router,
        "build_callback_redirect_url",
        lambda *_args, **_kwargs: "/workspace/connections?connection_status=error",
    )

    response = router.complete_connection_oauth_route(
        provider="slack",
        request=_request(),
        code=None,
        state="private-state",
        iss=None,
        error="access_denied",
        db=object(),
        db_log=object(),
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/workspace/connections?connection_status=error"
