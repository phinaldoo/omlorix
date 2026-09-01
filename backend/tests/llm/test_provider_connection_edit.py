"""Focused tests for connection checks launched from the provider editor."""

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.llm.schemas import ProviderEnum, TestProviderPayload, normalize_provider_value
from app.llm import utils as llm_utils


@pytest.mark.parametrize(
    "retired_value",
    ("azure_openai_responses", "azure_openai_chat_completions"),
)
def test_retired_azure_provider_values_are_not_aliases(retired_value):
    """Retired Azure protocol values must not validate or map to Microsoft Azure."""
    with pytest.raises(ValueError):
        ProviderEnum(retired_value)

    assert normalize_provider_value(retired_value) == retired_value


def test_connection_payload_requires_new_provider_key_but_allows_saved_provider_reference():
    """Only edit requests may omit a required API key in the browser payload."""
    with pytest.raises(ValidationError, match="Provider api_key is required"):
        TestProviderPayload(provider=ProviderEnum.openai, settings={})

    payload = TestProviderPayload(
        provider=ProviderEnum.openai,
        provider_id="  provider-1  ",
        settings={},
    )

    assert payload.provider_id == "provider-1"
    assert payload.api_key is None


def test_edit_connection_uses_saved_key_and_restores_redacted_headers(monkeypatch):
    """Edit tests combine visible draft values with secrets held by the backend."""
    saved_provider = SimpleNamespace(
        id="provider-1",
        provider="openai",
        api_key="sk-saved-secret",
        settings={"custom_headers": ["X-Tenant: saved-header-secret"]},
    )
    list_models = MagicMock(return_value=[{"id": "gpt-test"}])
    monkeypatch.setattr(llm_utils, "get_llm_provider", lambda db, provider_id: saved_provider)
    monkeypatch.setattr(llm_utils, "assert_llm_config_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_utils, "list_models_openai", list_models)

    result = llm_utils.test_llm_provider(
        MagicMock(),
        TestProviderPayload(
            provider=ProviderEnum.openai,
            provider_id="provider-1",
            base_url="https://draft.example.test/v1",
            settings={"custom_headers": ["X-Tenant: <redacted>"]},
        ),
    )

    assert result["status"] == "success"
    assert result["model_count"] == 1
    list_models.assert_called_once_with(
        ANY,
        byok={
            "api_key": "sk-saved-secret",
            "base_url": "https://draft.example.test/v1",
            "custom_headers": ["X-Tenant: saved-header-secret"],
        },
    )


def test_openrouter_connection_forwards_complete_draft_settings(monkeypatch):
    """OpenRouter tests must use unsaved routing and provider metadata."""
    draft_settings = {
        "eu_routing": True,
        "ranking_url": "https://chat.example.test",
        "ranking_title": "Example Omlorix",
        "disable_background_sync": True,
        "enable_auto_delete_missing_models": False,
        "enable_notify_model_changes": False,
    }
    list_models = MagicMock(return_value=[{"id": "openai/test-model"}])
    monkeypatch.setattr(llm_utils, "assert_llm_config_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_utils, "list_models_openrouter", list_models)

    result = llm_utils.test_llm_provider(
        MagicMock(),
        TestProviderPayload(
            provider=ProviderEnum.openrouter,
            api_key="sk-or-test",
            settings=draft_settings,
        ),
    )

    assert result["status"] == "success"
    assert result["model_count"] == 1
    list_models.assert_called_once_with(
        ANY,
        api_key="sk-or-test",
        provider_settings=draft_settings,
    )


def test_edit_connection_rejects_provider_type_mismatch(monkeypatch):
    """A provider ID cannot be used to borrow secrets from another provider type."""
    saved_provider = SimpleNamespace(
        id="provider-1",
        provider="anthropic",
        api_key="secret",
        settings={},
    )
    monkeypatch.setattr(llm_utils, "get_llm_provider", lambda db, provider_id: saved_provider)

    with pytest.raises(HTTPException) as exc_info:
        llm_utils.test_llm_provider(
            MagicMock(),
            TestProviderPayload(
                provider=ProviderEnum.openai,
                provider_id="provider-1",
                settings={},
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "code": "provider_test_saved_provider_type_mismatch",
    }


def test_edit_connection_returns_stable_code_when_saved_key_is_missing(monkeypatch):
    """Missing saved credentials use a frontend-translatable error contract."""
    saved_provider = SimpleNamespace(
        id="provider-1",
        provider="openai",
        api_key="",
        settings={},
    )
    monkeypatch.setattr(llm_utils, "get_llm_provider", lambda db, provider_id: saved_provider)

    with pytest.raises(HTTPException) as exc_info:
        llm_utils.test_llm_provider(
            MagicMock(),
            TestProviderPayload(
                provider=ProviderEnum.openai,
                provider_id="provider-1",
                settings={},
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "code": "provider_test_api_key_required",
    }
