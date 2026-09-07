from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.llm import router as llm_router
from app.llm import schemas as llm_schemas
from app.llm.acp import utils as acp_utils
from app.llm.models import export_llm_models, export_llm_providers


def test_acp_is_not_registered_as_an_admin_provider_or_model_type():
    """ACP remains a runtime enum only for private SSH-backed user records."""
    assert llm_schemas.ProviderEnum.acp not in llm_schemas.PROVIDER_SETTINGS_MODELS
    assert llm_schemas.ProviderEnum.acp not in llm_schemas.PROVIDER_SETTINGS_SCHEMAS
    assert llm_schemas.ProviderEnum.acp not in llm_schemas.PROVIDER_MODEL_SETTINGS_MODELS
    assert llm_schemas.ProviderEnum.acp not in llm_schemas.MODEL_CAPABLE_PROVIDERS
    assert llm_schemas.ProviderEnum.acp.value in llm_schemas.ADMIN_HIDDEN_PROVIDER_VALUES


def test_admin_creation_payloads_reject_acp():
    """Direct API clients cannot bypass the removed admin UI."""
    with pytest.raises(ValidationError, match="Unsupported provider 'ProviderEnum.acp'"):
        llm_schemas.CreateProviderRequest(
            provider=llm_schemas.ProviderEnum.acp,
            name="Local ACP",
            settings={},
        )

    with pytest.raises(ValidationError, match="Unsupported provider 'acp'"):
        llm_schemas.TestProviderPayload(
            provider=llm_schemas.ProviderEnum.acp,
            settings={},
        )

    with pytest.raises(ValidationError, match="Unsupported provider 'ProviderEnum.acp'"):
        llm_schemas.CreateProviderModelRequest(
            provider=llm_schemas.ProviderEnum.acp,
            provider_id="legacy-acp-provider",
            model=llm_schemas.CreateModel(
                name="Legacy ACP",
                description="No longer supported",
                model_icon="terminal",
                model="default",
                tools=[],
                status=llm_schemas.ModelStatusEnum.normal,
            ),
            settings={},
            access=llm_schemas.ModelAccess(everyone=True),
        )


def test_admin_available_provider_catalog_omits_acp():
    available = llm_router.list_llm_providers_available_route()
    assert "acp" not in {entry["id"] for entry in available}


def test_admin_provider_routes_reject_acp_before_database_access():
    """Admin read endpoints cannot expose ACP through crafted query values."""
    with pytest.raises(HTTPException) as list_error:
        llm_router.list_llm_providers_route(
            provider=llm_schemas.ProviderEnum.acp,
            db=SimpleNamespace(),
        )
    assert list_error.value.status_code == 404

    with pytest.raises(HTTPException) as schema_error:
        llm_router.get_provider_schema_route(
            provider=llm_schemas.ProviderEnum.acp,
            db=SimpleNamespace(),
        )
    assert schema_error.value.status_code == 404


def test_admin_provider_group_routes_reject_legacy_acp_groups(monkeypatch):
    """A legacy group cannot expose private ACP provider rows through admin APIs."""
    group = SimpleNamespace(
        id="legacy-acp-group",
        members=[{"provider_id": "legacy-acp-provider", "weight": 1}],
    )
    monkeypatch.setattr(llm_router, "get_provider_group", lambda _db, _id: group)
    monkeypatch.setattr(
        llm_router,
        "get_llm_provider",
        lambda _db, _id: SimpleNamespace(provider="acp"),
    )

    with pytest.raises(HTTPException) as exc_info:
        llm_router._get_admin_managed_provider_group(SimpleNamespace(), group.id)

    assert exc_info.value.status_code == 404


def test_admin_exports_omit_acp_implementation_rows():
    class Query:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    provider_rows = [
        SimpleNamespace(
            id="provider-openai",
            provider="openai",
            name="OpenAI",
            icon="openai",
            api_key="secret",
            settings={},
            status={},
        ),
        SimpleNamespace(
            id="provider-acp",
            provider="acp",
            name="Private routing row",
            icon="terminal",
            api_key="user-managed",
            settings={"user_managed": True},
            status={},
        ),
    ]
    model_rows = [
        SimpleNamespace(
            id="model-openai",
            name="OpenAI model",
            description="Shared",
            model_icon="openai",
            provider="openai",
            provider_id="provider-openai",
            model_name="gpt-test",
            settings={},
            capabilities=["completion"],
            tools=[],
            access={"everyone": True},
            status="normal",
            is_active=True,
            created_at=None,
        ),
        SimpleNamespace(
            id="model-acp",
            name="Private ACP",
            description="Personal",
            model_icon="terminal",
            provider="acp",
            provider_id="provider-acp",
            model_name="acp-profile:one",
            settings={"acp_profile_id": "one"},
            capabilities=["completion", "tools", "thinking"],
            tools=[],
            access={"everyone": False},
            status="normal",
            is_active=True,
            created_at=None,
        ),
    ]

    provider_db = SimpleNamespace(query=lambda _model: Query(provider_rows))
    model_db = SimpleNamespace(query=lambda _model: Query(model_rows))

    assert [row["provider"] for row in export_llm_providers(provider_db)["data"]["providers"]] == ["openai"]
    assert [row["provider"] for row in export_llm_models(model_db)["data"]["models"]] == ["openai"]


def test_acp_chat_rejects_legacy_local_provider_configuration(monkeypatch, tmp_path):
    """Legacy admin ACP rows cannot launch a subprocess in the backend."""
    provider = SimpleNamespace(
        id="legacy-provider",
        settings={
            "command": "opencode",
            "workspace_root": str(tmp_path),
        },
    )
    model = SimpleNamespace(
        id="legacy-model",
        provider_id=provider.id,
        settings={"cwd": "."},
    )
    monkeypatch.setattr(acp_utils, "get_llm_provider", lambda _db, _id: provider)

    with pytest.raises(HTTPException) as exc_info:
        list(
            acp_utils.acp_chat(
                chat_id="chat-one",
                chat_history=[{"role": "user", "content": "Hello"}],
                db=SimpleNamespace(),
                db_model=model,
                user_id="user-one",
                generation_id="generation-one",
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Your ACP profile is unavailable"
