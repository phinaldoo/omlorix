"""Security regression coverage for reload-safe BYOK credential tokens."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.chats import router as chats_router
from app.llm import router as llm_router
from app.llm.byok_credentials import (
    BYOK_CREDENTIAL_TOKEN_TTL_DAYS,
    ByokCredentialTokenError,
    issue_byok_credential_token,
    resolve_byok_credential_token,
)
from app.llm.schemas import (
    ByokCredentialTokenRequest,
    ListProviderModelsByokRequest,
    ProviderEnum,
)
from app.utils import encryption


@pytest.fixture(autouse=True)
def isolated_encryption_key(monkeypatch):
    """Give each test an independent server-only Fernet key."""

    monkeypatch.setattr(encryption, "_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(encryption, "_CIPHER_SUITE", None)


def _issue(*, now: datetime | None = None) -> tuple[str, datetime]:
    return issue_byok_credential_token(
        user_id="user-1",
        provider="openai",
        provider_id="local-provider-1",
        api_key="sk-provider-secret",
        now=now,
    )


def test_sealed_token_survives_reload_without_containing_plaintext_key():
    """The persisted value can be reused but does not disclose the provider key."""

    token, expires_at = _issue()

    assert "sk-provider-secret" not in token
    assert expires_at > datetime.now(timezone.utc)
    assert resolve_byok_credential_token(
        token,
        user_id="user-1",
        provider="openai",
        provider_id="local-provider-1",
    ) == "sk-provider-secret"


def test_sealed_token_expires_after_thirty_days():
    """Keep the documented BYOK credential lifetime aligned with issuance."""

    issued_at = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    _, expires_at = _issue(now=issued_at)

    assert BYOK_CREDENTIAL_TOKEN_TTL_DAYS == 30
    assert expires_at == issued_at + timedelta(days=30)


@pytest.mark.parametrize(
    ("user_id", "provider", "provider_id"),
    [
        ("user-2", "openai", "local-provider-1"),
        ("user-1", "anthropic", "local-provider-1"),
        ("user-1", "openai", "local-provider-2"),
    ],
)
def test_sealed_token_rejects_cross_user_and_cross_provider_reuse(user_id, provider, provider_id):
    token, _ = _issue()

    with pytest.raises(ByokCredentialTokenError, match="unavailable"):
        resolve_byok_credential_token(
            token,
            user_id=user_id,
            provider=provider,
            provider_id=provider_id,
        )


def test_sealed_token_accepts_non_ascii_bindings():
    """Unicode user and provider-instance bindings compare as UTF-8 bytes."""

    token, _ = issue_byok_credential_token(
        user_id="utilisateur-é",
        provider="fournisseur-модель",
        provider_id="fournisseur-東京",
        api_key="sk-provider-secret",
    )

    assert resolve_byok_credential_token(
        token,
        user_id="utilisateur-é",
        provider="fournisseur-модель",
        provider_id="fournisseur-東京",
    ) == "sk-provider-secret"


def test_sealed_token_rejects_tampering_and_expiration():
    issued_at = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    token, expires_at = _issue(now=issued_at)
    tampered = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"

    with pytest.raises(ByokCredentialTokenError, match="unavailable"):
        resolve_byok_credential_token(
            tampered,
            user_id="user-1",
            provider="openai",
            provider_id="local-provider-1",
        )

    with pytest.raises(ByokCredentialTokenError, match="unavailable"):
        resolve_byok_credential_token(
            token,
            user_id="user-1",
            provider="openai",
            provider_id="local-provider-1",
            now=expires_at + timedelta(seconds=1),
        )


def test_chat_boundary_resolves_token_and_rejects_raw_key(monkeypatch):
    """Chat generation receives raw keys only after policy and token checks."""

    token, _ = _issue()
    monkeypatch.setattr(chats_router, "get_user_group_setting_value", lambda *_args: True)
    byok = {
        "provider": "openai",
        "provider_id": "local-provider-1",
        "credential_token": token,
        "model_name": "gpt-test",
    }

    chats_router._ensure_byok_allowed_for_user("user-1", MagicMock(), byok)

    assert "credential_token" not in byok
    assert byok["api_key"] == "sk-provider-secret"

    with pytest.raises(HTTPException) as exc_info:
        chats_router._ensure_byok_allowed_for_user(
            "user-1",
            MagicMock(),
            {
                "provider": "openai",
                "provider_id": "local-provider-1",
                "api_key": "sk-browser-plaintext",
            },
        )
    assert exc_info.value.detail == {"code": "byok_credential_unavailable"}


def test_credential_issuance_route_returns_token_and_audits_metadata_only(monkeypatch):
    """The authenticated exchange must never copy a key or token into audit data."""

    audit_call = {}
    monkeypatch.setattr(llm_router, "_ensure_byok_allowed", lambda *_args: None)

    def capture_audit(_db_log, _request, user_id, action, details, category):
        audit_call.update(
            user_id=user_id,
            action=action,
            details=details,
            category=category,
        )

    monkeypatch.setattr(llm_router, "_audit_llm_event", capture_audit)
    response = llm_router.issue_byok_credential_token_route(
        payload=ByokCredentialTokenRequest(
            provider=ProviderEnum.openai,
            provider_id="local-provider-1",
            api_key="sk-provider-secret",
        ),
        request=SimpleNamespace(headers={}),
        db=MagicMock(),
        db_log=MagicMock(),
        user=SimpleNamespace(id="user-1"),
    )

    assert resolve_byok_credential_token(
        response.credential_token,
        user_id="user-1",
        provider="openai",
        provider_id="local-provider-1",
    ) == "sk-provider-secret"
    assert audit_call["action"] == "BYOK_CREDENTIAL_TOKEN_ISSUED"
    assert audit_call["category"] == "llm_byok"
    assert audit_call["details"]["provider"] == "openai"
    assert audit_call["details"]["expires_at"] == response.expires_at.isoformat()
    assert "provider_id" not in audit_call["details"]
    assert audit_call["details"]["byok_provider_instance_hash"].startswith("byok_provider_hash_")
    assert "local-provider-1" not in repr(audit_call)
    assert "sk-provider-secret" not in repr(audit_call)
    assert response.credential_token not in repr(audit_call)


def test_model_discovery_resolves_token_before_provider_validation(monkeypatch):
    """Model discovery passes the decrypted key only to the provider adapter."""

    token, _ = _issue()
    payload = ListProviderModelsByokRequest(
        provider=ProviderEnum.openai,
        provider_id="local-provider-1",
        credential_token=token,
        config={},
    )
    observed = {}

    monkeypatch.setattr(llm_router, "_ensure_byok_allowed", lambda *_args: None)
    monkeypatch.setattr(
        llm_router,
        "_ensure_byok_target_allowed",
        lambda _db, _provider, config: config.model_dump(exclude_none=True),
    )

    def fake_list_models(_db, *, byok, **_kwargs):
        observed.update(byok)
        return [{"id": "gpt-test"}]

    monkeypatch.setattr(llm_router, "list_models_openai", fake_list_models)
    monkeypatch.setattr(llm_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(llm_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")

    result = llm_router.list_provider_models_byok_route(
        payload=payload,
        request=SimpleNamespace(headers={}),
        db=MagicMock(),
        db_log=MagicMock(),
        user=SimpleNamespace(id="user-1"),
    )

    assert result == [{"id": "gpt-test"}]
    assert observed["api_key"] == "sk-provider-secret"
    assert "credential_token" not in observed


def test_lmstudio_byok_discovery_accepts_anonymous_native_root(monkeypatch):
    """LM Studio BYOK must work without a key and forward its native server root."""

    payload = ListProviderModelsByokRequest(
        provider=ProviderEnum.lmstudio,
        provider_id="local-lmstudio-provider",
        config={"base_url": "http://host.docker.internal:1234"},
    )
    observed = {}

    monkeypatch.setattr(llm_router, "_ensure_byok_allowed", lambda *_args: None)
    monkeypatch.setattr(
        llm_router,
        "_ensure_byok_target_allowed",
        lambda _db, _provider, config: config.model_dump(exclude_none=True),
    )

    def fake_list_models(_db, *, byok_base_url, byok_api_key):
        observed.update(base_url=byok_base_url, api_key=byok_api_key)
        return [{"id": "local-model", "type": "llm"}]

    monkeypatch.setattr(llm_router, "list_models_lmstudio", fake_list_models)
    monkeypatch.setattr(llm_router, "create_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(llm_router, "get_audit_request_ip", lambda *_args: "127.0.0.1")

    result = llm_router.list_provider_models_byok_route(
        payload=payload,
        request=SimpleNamespace(headers={}),
        db=MagicMock(),
        db_log=MagicMock(),
        user=SimpleNamespace(id="user-1"),
    )

    assert result == [{"id": "local-model", "type": "llm"}]
    assert observed == {
        "base_url": "http://host.docker.internal:1234",
        "api_key": "",
    }


@pytest.mark.parametrize(
    ("status_code", "detail"),
    [
        (401, "Incorrect API key provided"),
        (403, "Forbidden"),
        (400, "Invalid API key"),
        (400, {"code": "invalid_api_key"}),
    ],
)
def test_byok_discovery_normalizes_provider_authentication_errors(
    status_code,
    detail,
):
    normalized = llm_router._byok_discovery_http_error(
        HTTPException(status_code=status_code, detail=detail)
    )

    assert normalized.status_code == 401
    assert normalized.detail == {"code": "byok_provider_authentication_failed"}


def test_byok_discovery_normalizes_other_provider_errors_without_leaking_copy():
    normalized = llm_router._byok_discovery_http_error(
        HTTPException(
            status_code=424,
            detail="Failed to list OpenAI models: upstream-specific English error",
        )
    )

    assert normalized.status_code == 424
    assert normalized.detail == {"code": "byok_model_discovery_failed"}
    assert "upstream-specific" not in str(normalized.detail)
