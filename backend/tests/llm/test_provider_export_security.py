import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "numpy" not in sys.modules:
    fake_numpy = ModuleType("numpy")
    fake_numpy.linspace = lambda start, stop, num, dtype=int: []
    for _name in (
        "short",
        "ushort",
        "intc",
        "uintc",
        "int_",
        "uint",
        "longlong",
        "ulonglong",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "intp",
        "uintp",
        "integer",
    ):
        setattr(fake_numpy, _name, int)
    for _name in (
        "half",
        "float16",
        "single",
        "double",
        "longdouble",
        "float32",
        "float64",
        "floating",
    ):
        setattr(fake_numpy, _name, float)
    fake_numpy.bool_ = bool
    sys.modules["numpy"] = fake_numpy

if "numpy.typing" not in sys.modules:
    sys.modules["numpy.typing"] = ModuleType("numpy.typing")

if "pandas" not in sys.modules:
    fake_pandas = ModuleType("pandas")
    fake_pandas.DataFrame = type("DataFrame", (), {})
    fake_pandas.to_datetime = lambda value, *args, **kwargs: value
    fake_pandas.isna = lambda value: False
    sys.modules["pandas"] = fake_pandas

if "elevenlabs" not in sys.modules:
    fake_elevenlabs = ModuleType("elevenlabs")
    fake_elevenlabs.SpeechToTextConvertRequestModelId = "scribe_v1"
    sys.modules["elevenlabs"] = fake_elevenlabs

if "elevenlabs.client" not in sys.modules:
    fake_elevenlabs_client = ModuleType("elevenlabs.client")
    fake_elevenlabs_client.ElevenLabs = lambda *args, **kwargs: SimpleNamespace()
    sys.modules["elevenlabs.client"] = fake_elevenlabs_client

if "markitdown" not in sys.modules:
    fake_markitdown = ModuleType("markitdown")
    fake_markitdown.MarkItDown = lambda *args, **kwargs: SimpleNamespace()
    sys.modules["markitdown"] = fake_markitdown

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(stream_writer=lambda stream: stream)
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(stream_reader=lambda stream: stream)
    sys.modules["zstandard"] = fake_zstandard

from app.llm.models import (  # noqa: E402
    current_llm_provider_export_version,
    export_llm_providers,
    get_llm_provider,
    import_llm_providers,
    update_llm_provider,
)
from app.llm.schemas import (  # noqa: E402
    CreateProviderRequest,
    ListProviderModelsByokRequest,
    PROVIDER_SETTINGS_SCHEMAS,
    ProviderEnum,
    TestProviderPayload,
    provider_api_key_is_optional,
)


def _provider(**overrides):
    data = {
        "id": "provider-1",
        "provider": "openai",
        "name": "OpenAI",
        "icon": "openai",
        "api_key": "sk-live-secret",
        "settings": {},
        "status": {"available": "up"},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _provider_import_payload(provider_entry):
    return {
        "export_type": "llm_provider",
        "export_version": current_llm_provider_export_version,
        "data": {
            "providers": [provider_entry],
        },
    }


class _ProviderUpdateQuery:
    def __init__(self, first_results):
        self.first_results = first_results

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.first_results.pop(0) if self.first_results else None


class _ProviderUpdateDb:
    def __init__(self, *first_results):
        self.first_results = list(first_results)
        self.added = []
        self.committed = False
        self.refreshed = []

    def query(self, *args, **kwargs):
        return _ProviderUpdateQuery(self.first_results)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.committed = True

    def refresh(self, value):
        self.refreshed.append(value)


class ProviderExportSecurityTests:
    def test_provider_export_omits_live_api_keys(self):
        db = MagicMock()
        db.query.return_value.all.return_value = [_provider()]

        result = export_llm_providers(db)

        exported_provider = result["data"]["providers"][0]
        assert result["export_version"] == current_llm_provider_export_version
        assert "api_key" not in exported_provider
        assert exported_provider["credentials"] == {
            "api_key_exported": False,
            "api_key_required": True,
            "api_key_configured": True,
        }

    def test_provider_export_marks_optional_api_key_without_exporting_value(self):
        db = MagicMock()
        db.query.return_value.all.return_value = [
            _provider(provider="ollama", name="Local Ollama", api_key="", settings={"base_url": "http://localhost:11434"})
        ]

        result = export_llm_providers(db)

        exported_provider = result["data"]["providers"][0]
        assert "api_key" not in exported_provider
        assert exported_provider["credentials"]["api_key_exported"] is False
        assert exported_provider["credentials"]["api_key_required"] is False
        assert exported_provider["credentials"]["api_key_configured"] is False

    def test_openai_compatible_provider_api_keys_are_required(self):
        for provider in (ProviderEnum.openai_responses, ProviderEnum.openai_chat_completions):
            assert provider_api_key_is_optional(provider) is False

            api_key_field = next(
                field
                for section in PROVIDER_SETTINGS_SCHEMAS[provider].sections
                for field in section.fields
                if field.key == "api_key"
            )
            assert api_key_field.required is True

            with pytest.raises(ValidationError, match="Provider api_key is required"):
                CreateProviderRequest(
                    provider=provider,
                    name="Custom OpenAI",
                    settings={},
                )

            with pytest.raises(ValidationError, match="Provider api_key is required"):
                TestProviderPayload(provider=provider, settings={})

            with pytest.raises(ValidationError, match="sealed BYOK credential"):
                ListProviderModelsByokRequest(
                    provider=provider,
                    provider_id="local-provider-1",
                    config={"base_url": "https://api.openai.com/v1"},
                )

            with pytest.raises(ValidationError, match="Raw BYOK API keys"):
                ListProviderModelsByokRequest(
                    provider=provider,
                    provider_id="local-provider-1",
                    credential_token="sealed-token",
                    config={"api_key": "sk-raw-key"},
                )

        db = MagicMock()
        db.query.return_value.all.return_value = [
            _provider(provider="openai_responses", name="OpenAI Responses", api_key="sk-responses-secret"),
            _provider(provider="openai_chat_completions", name="OpenAI Chat Completions", api_key="sk-chat-secret"),
        ]

        result = export_llm_providers(db)

        assert [provider["credentials"]["api_key_required"] for provider in result["data"]["providers"]] == [True, True]

    def test_native_anthropic_api_key_is_required_by_the_provider_schema(self):
        """Keep browser validation aligned with the create request contract."""
        api_key_field = next(
            field
            for section in PROVIDER_SETTINGS_SCHEMAS[ProviderEnum.anthropic].sections
            for field in section.fields
            if field.key == "api_key"
        )

        assert api_key_field.required is True
        with pytest.raises(ValidationError, match="Provider api_key is required"):
            CreateProviderRequest(
                provider=ProviderEnum.anthropic,
                name="Anthropic",
                settings={},
            )

    def test_byok_model_listing_rejects_stored_provider_ids_for_user_requests(self):
        for provider, config in (
            (ProviderEnum.anthropic, {"anthropic_provider_id": "anthropic-provider-1"}),
            (ProviderEnum.anthropic_base, {"anthropic_provider_id": "anthropic-provider-1", "base_url": "https://api.anthropic.com"}),
            (ProviderEnum.openrouter, {"openrouter_provider_id": "openrouter-provider-1"}),
        ):
            with pytest.raises(ValidationError, match="stored provider ID"):
                ListProviderModelsByokRequest(
                    provider=provider,
                    provider_id="local-provider-1",
                    credential_token="sealed-token",
                    config=config,
                )

    def test_byok_model_listing_requires_api_key_for_anthropic_and_openrouter(self):
        for provider, config in (
            (ProviderEnum.anthropic, {}),
            (ProviderEnum.anthropic_base, {"base_url": "https://api.anthropic.com"}),
            (ProviderEnum.openrouter, {}),
        ):
            with pytest.raises(ValidationError, match="sealed BYOK credential"):
                ListProviderModelsByokRequest(
                    provider=provider,
                    provider_id="local-provider-1",
                    config=config,
                )

        for provider, config in (
            (ProviderEnum.anthropic, {}),
            (ProviderEnum.anthropic_base, {"base_url": "https://api.anthropic.com"}),
            (ProviderEnum.openrouter, {}),
        ):
            payload = ListProviderModelsByokRequest(
                provider=provider,
                provider_id="local-provider-1",
                credential_token="sealed-token",
                config=config,
            )
            assert payload.credential_token == "sealed-token"
            assert "api_key" not in payload.config

    def test_import_required_key_provider_fails_without_api_key(self):
        db = MagicMock()
        payload = _provider_import_payload(
            {
                "provider": "openai",
                "name": "OpenAI Import",
                "settings": {},
                "status": {},
            }
        )

        result = import_llm_providers(db, payload)

        assert result["created"] == []
        assert result["errors"] == [
            {
                "index": 0,
                "name": "OpenAI Import",
                "error": "Provider api_key is required.",
            }
        ]

    def test_import_succeeds_when_request_supplies_fresh_api_key(self):
        db = MagicMock()
        payload = _provider_import_payload(
            {
                "provider": "openai",
                "name": "OpenAI Import",
                "api_key": "sk-new-secret",
                "settings": {},
                "status": {},
            }
        )
        created_provider = SimpleNamespace(id="provider-created", name="OpenAI Import", provider="openai")

        with patch("app.llm.models.create_llm_provider", return_value=created_provider) as mock_create:
            result = import_llm_providers(db, payload)

        mock_create.assert_called_once()
        assert mock_create.call_args.args[3] == "sk-new-secret"
        assert result == {
            "created": [
                {
                    "id": "provider-created",
                    "name": "OpenAI Import",
                    "provider": "openai",
                }
            ],
            "errors": [],
        }

    def test_provider_export_route_writes_metadata_only_audit_log(self):
        from app.llm import router as llm_router_module

        db = MagicMock()
        db.query.return_value.all.return_value = [
            _provider(id="provider-1", provider="openai", name="OpenAI", api_key="sk-live-secret"),
            _provider(id="provider-2", provider="ollama", name="Local Ollama", api_key="", settings={"base_url": "http://localhost:11434"}),
        ]
        db_log = MagicMock()
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})
        admin_user = SimpleNamespace(id="admin-1")

        with patch.object(llm_router_module, "create_audit_log") as mock_audit:
            result = llm_router_module.export_llm_providers_route(request, db, db_log, admin_user)

        mock_audit.assert_called_once()
        audit_kwargs = mock_audit.call_args.kwargs
        assert audit_kwargs["db_log"] is db_log
        assert audit_kwargs["user_id"] == "admin-1"
        assert audit_kwargs["action"] == "EXPORT_LLM_PROVIDERS"
        assert audit_kwargs["category"] == "llm_provider"
        assert audit_kwargs["details"] == {
            "export_version": current_llm_provider_export_version,
            "provider_count": 2,
            "provider_types": ["ollama", "openai"],
            "required_api_key_count": 1,
            "configured_api_key_count": 1,
            "api_keys_exported": False,
        }
        assert "sk-live-secret" not in str(audit_kwargs)
        assert "api_key" not in result["data"]["providers"][0]

    def test_update_preserves_api_key_when_payload_leaves_secret_empty(self):
        provider = _provider(api_key="sk-live-secret", settings={"base_url": "https://api.example.com"})
        db = _ProviderUpdateDb(provider)

        result = update_llm_provider(db, provider.id, api_key="", settings={"base_url": "https://api.updated.example.com"})

        assert result is provider
        assert provider.api_key == "sk-live-secret"
        assert provider.settings == {"base_url": "https://api.updated.example.com"}
        assert db.committed is True

    def test_update_preserves_api_key_when_payload_sends_masked_preview(self):
        provider = _provider(api_key="sk-live-secret")
        db = _ProviderUpdateDb(provider)

        update_llm_provider(db, provider.id, api_key="sk-live...")

        assert provider.api_key == "sk-live-secret"

    def test_update_replaces_api_key_when_payload_sends_new_secret(self):
        provider = _provider(api_key="sk-live-secret")
        db = _ProviderUpdateDb(provider)

        update_llm_provider(db, provider.id, api_key="  sk-new-secret  ")

        assert provider.api_key == "sk-new-secret"

    def test_masked_provider_lookup_does_not_mutate_stored_api_key(self):
        provider = _provider(api_key="sk-live-secret")
        provider.created_at = None
        db = _ProviderUpdateDb(provider)

        masked = get_llm_provider(db, provider.id, mask_api_key=True)

        assert masked is not provider
        assert masked.api_key == "sk-live..."
        assert provider.api_key == "sk-live-secret"
