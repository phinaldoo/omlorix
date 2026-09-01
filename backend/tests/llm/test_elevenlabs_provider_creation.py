"""Regression coverage for ElevenLabs provider creation status."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.llm import router as llm_router
from app.llm.schemas import CreateProviderRequest, ProviderEnum


def test_create_elevenlabs_provider_refreshes_status_immediately(monkeypatch):
    """Creation should return the model-list-backed status without waiting for the worker."""
    created_provider = SimpleNamespace(
        id="elevenlabs-provider-1",
        provider=ProviderEnum.elevenlabs.value,
        name="ElevenLabs",
        status={"available": "unknown", "model_list": []},
    )
    refreshed_provider = SimpleNamespace(
        id=created_provider.id,
        provider=created_provider.provider,
        name=created_provider.name,
        status={"available": "up", "model_list": ["eleven_multilingual_v2"]},
    )
    create_provider = MagicMock(return_value=created_provider)
    refresh_status = MagicMock(return_value=refreshed_provider)
    db = MagicMock()

    monkeypatch.setattr(llm_router, "create_llm_provider", create_provider)
    monkeypatch.setattr(llm_router, "refresh_provider_status_snapshot", refresh_status)
    monkeypatch.setattr(llm_router, "create_audit_log", MagicMock())
    monkeypatch.setattr(llm_router, "get_audit_request_ip", lambda _request, _db: "127.0.0.1")
    monkeypatch.setattr(llm_router, "serialize_llm_provider_detail", lambda provider: provider)

    result = llm_router.create_provider_route(
        payload=CreateProviderRequest(
            provider=ProviderEnum.elevenlabs,
            name="ElevenLabs",
            api_key="test-api-key",
            settings={},
        ),
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=db,
        db_log=MagicMock(),
        admin_user=SimpleNamespace(id="admin-1"),
    )

    refresh_status.assert_called_once_with(db, created_provider.id)
    assert result is refreshed_provider
    assert result.status["available"] == "up"
