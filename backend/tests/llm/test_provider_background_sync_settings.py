import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.encoders import jsonable_encoder


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "numpy" not in sys.modules:
    fake_numpy = ModuleType("numpy")
    fake_numpy.linspace = lambda start, stop, num, dtype=int: []
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

    class _FakeMarkItDown:
        def __init__(self, *args, **kwargs):
            pass

    fake_markitdown.MarkItDown = _FakeMarkItDown
    sys.modules["markitdown"] = fake_markitdown

from app.llm.schemas import (
    LLMProviderDetail,
    PROVIDER_SETTINGS_SCHEMAS,
    get_default_provider_icon,
    provider_supports_custom_icon,
    resolve_provider_icon,
)
from app.llm.byok_schema import (
    BYOK_BASE_URL_SUGGESTIONS_KEY,
    BYOK_SCHEMA_METADATA_KEY,
    sanitize_byok_provider_schema,
)
from app.llm.provider_url_suggestions import attach_provider_url_suggestions
from app.llm.worker import _sync_provider


BACKGROUND_SYNC_FIELD_KEY = "settings.disable_background_sync"
DEPENDENT_FIELD_KEYS = {
    "settings.enable_auto_delete_missing_models",
    "settings.enable_notify_model_changes",
}
EXPECTED_BYOK_PROVIDER_FIELDS = {
    "openai": {
        "settings.organization",
        "settings.project",
        "settings.custom_headers",
    },
    "openai_responses": {
        "icon",
        "settings.custom_headers",
    },
    "openai_chat_completions": {
        "icon",
        "settings.custom_headers",
    },
    "microsoft_azure": {
        "settings.azure_endpoint",
        "settings.api_version",
        "settings.custom_headers",
    },
    "anthropic": set(),
    "anthropic_base": {"icon"},
    "google_aistudio": {
        "settings.api_version",
    },
    "openrouter": {"settings.eu_routing"},
    "ollama": set(),
    "lmstudio": set(),
}
BYOK_PROVIDERS_WITH_URL_SUGGESTIONS = {
    "openai_responses",
    "openai_chat_completions",
    "anthropic_base",
    "ollama",
    "lmstudio",
}
EXPECTED_BYOK_PROVIDER_ICONS = {
    "openai_responses": "openai",
    "openai_chat_completions": "openai",
    "anthropic_base": "anthropic",
}


def _get_field(schema, field_key: str):
    for section in getattr(schema, "sections", []) or []:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == field_key:
                return field
    return None


def _sanitize_provider_schema(provider, schema):
    enriched = attach_provider_url_suggestions(schema.model_copy(deep=True), provider.value)
    return sanitize_byok_provider_schema(jsonable_encoder(enriched), provider.value)


class ProviderBackgroundSyncSettingsTests:
    def test_provider_icon_policy_keeps_native_brands_fixed(self):
        """Native records cannot override their brand; custom protocols can."""
        native_provider_icons = {
            "openai": "openai",
            "microsoft_azure": "microsoft",
            "anthropic": "anthropic",
            "google_aistudio": "google_aistudio",
            "openrouter": "openrouter",
            "ollama": "ollama",
            "lmstudio": "lmstudio",
            "elevenlabs": "elevenlabs",
            "xai": "xai",
        }
        for provider, expected_icon in native_provider_icons.items():
            assert not provider_supports_custom_icon(provider)
            assert get_default_provider_icon(provider) == expected_icon
            assert resolve_provider_icon(provider, "<svg />") == expected_icon

        for provider in ("openai_responses", "openai_chat_completions", "anthropic_base"):
            assert provider_supports_custom_icon(provider)
            assert resolve_provider_icon(provider, "<svg />") == "<svg />"

        native_detail = LLMProviderDetail.model_validate(
            {
                "id": "provider-1",
                "provider": "openai",
                "name": "OpenAI",
                "icon": "<svg />",
                "settings": {},
                "status": {},
            }
        )
        assert native_detail.icon == "openai"

    def test_every_provider_schema_exposes_background_sync_toggle(self):
        for provider, schema in PROVIDER_SETTINGS_SCHEMAS.items():
            field = _get_field(schema, BACKGROUND_SYNC_FIELD_KEY)

            assert field is not None, f"{provider.value} is missing {BACKGROUND_SYNC_FIELD_KEY}"
            assert field.type == "boolean"
            assert field.default is False

    def test_auto_delete_and_notifications_depend_on_background_sync_toggle(self):
        for provider, schema in PROVIDER_SETTINGS_SCHEMAS.items():
            for field_key in DEPENDENT_FIELD_KEYS:
                field = _get_field(schema, field_key)
                if field is None:
                    continue

                assert field.dependency == BACKGROUND_SYNC_FIELD_KEY, (
                    f"{provider.value} field {field_key} should depend on {BACKGROUND_SYNC_FIELD_KEY}"
                )
                assert field.dependency_value is False

    def test_provider_sync_notifications_are_admin_only(self):
        """Provider model-change alerts must never leak into user BYOK forms."""
        for provider, schema in PROVIDER_SETTINGS_SCHEMAS.items():
            field = _get_field(schema, "settings.enable_notify_model_changes")
            if field is None:
                continue

            assert field.hide_on_byok is True, (
                f"{provider.value} field settings.enable_notify_model_changes should be admin-only"
            )

    def test_openai_compatible_background_sync_controls_are_admin_only(self):
        """Hide background synchronization for both custom OpenAI API modes."""
        provider_values = {"openai_responses", "openai_chat_completions"}
        matching_schemas = {
            provider.value: schema
            for provider, schema in PROVIDER_SETTINGS_SCHEMAS.items()
            if provider.value in provider_values
        }

        assert set(matching_schemas) == provider_values
        for provider, schema in matching_schemas.items():
            field = _get_field(schema, BACKGROUND_SYNC_FIELD_KEY)
            assert field is not None, f"{provider} is missing {BACKGROUND_SYNC_FIELD_KEY}"
            assert field.hide_on_byok is True, (
                f"{provider} field {BACKGROUND_SYNC_FIELD_KEY} should be admin-only"
            )

    def test_byok_provider_schemas_expose_only_user_owned_fields(self):
        """Keep the complete user BYOK schema contract explicit and reviewable."""
        actual_provider_values = {
            provider.value
            for provider in PROVIDER_SETTINGS_SCHEMAS
            if provider.value in EXPECTED_BYOK_PROVIDER_FIELDS
        }
        assert actual_provider_values == set(EXPECTED_BYOK_PROVIDER_FIELDS)

        for provider, schema in PROVIDER_SETTINGS_SCHEMAS.items():
            if provider.value not in EXPECTED_BYOK_PROVIDER_FIELDS:
                continue
            payload = _sanitize_provider_schema(provider, schema)
            fields = [
                field
                for section in payload["sections"]
                for field in section["fields"]
            ]
            keys = {field["key"] for field in fields}

            assert keys == EXPECTED_BYOK_PROVIDER_FIELDS[provider.value]
            assert all(not field.get("hide_on_byok") for field in fields)
            assert all(section["fields"] for section in payload["sections"])

    def test_only_custom_provider_schemas_expose_an_icon_picker(self):
        """Native providers use fixed icons; compatible endpoints are editable."""
        for provider, schema in PROVIDER_SETTINGS_SCHEMAS.items():
            expected_icon = EXPECTED_BYOK_PROVIDER_ICONS.get(provider.value)
            payload = _sanitize_provider_schema(provider, schema)
            icon_fields = [
                field
                for section in payload["sections"]
                for field in section["fields"]
                if field["key"] == "icon"
            ]

            if expected_icon is None:
                assert not icon_fields, f"{provider.value} should use its fixed native icon"
                continue
            assert len(icon_fields) == 1, f"{provider.value} should expose one icon picker"
            assert icon_fields[0]["default"] == expected_icon

    def test_byok_base_url_suggestions_survive_duplicate_field_removal(self):
        """Move endpoint presets to BYOK metadata without rendering another URL."""
        for provider, schema in PROVIDER_SETTINGS_SCHEMAS.items():
            if provider.value not in EXPECTED_BYOK_PROVIDER_FIELDS:
                continue
            payload = _sanitize_provider_schema(provider, schema)
            keys = {
                field["key"]
                for section in payload["sections"]
                for field in section["fields"]
            }
            suggestions = payload.get(BYOK_SCHEMA_METADATA_KEY, {}).get(
                BYOK_BASE_URL_SUGGESTIONS_KEY,
                [],
            )

            assert "settings.base_url" not in keys
            if provider.value in BYOK_PROVIDERS_WITH_URL_SUGGESTIONS:
                assert suggestions, f"{provider.value} should retain endpoint suggestions"
            else:
                assert not suggestions

    def test_auto_delete_missing_models_defaults_to_false_in_provider_schemas(self):
        for provider, schema in PROVIDER_SETTINGS_SCHEMAS.items():
            field = _get_field(schema, "settings.enable_auto_delete_missing_models")
            if field is None:
                continue

            assert field.default is False, (
                f"{provider.value} field settings.enable_auto_delete_missing_models should default to false"
            )

    def test_worker_skips_regular_provider_requests_when_background_sync_is_disabled(self):
        provider = SimpleNamespace(
            id="provider-1",
            provider="openai",
            name="OpenAI Test Provider",
            settings={
                "disable_background_sync": True,
                "enable_auto_delete_missing_models": True,
                "enable_notify_model_changes": True,
            },
            status={
                "available": "down",
                "model_list": ["gpt-4.1"],
                "last_synced_at": "2026-01-01T00:00:00+00:00",
            },
        )
        db = MagicMock()

        with patch("app.llm.worker.assert_llm_provider_allowed") as mock_assert_allowed, patch(
            "app.llm.worker.list_provider_status_models"
        ) as mock_list_provider_models, patch(
            "app.llm.worker.delete_model_by_name"
        ) as mock_delete_model:
            _sync_provider(db, provider)

        mock_assert_allowed.assert_not_called()
        mock_list_provider_models.assert_not_called()
        mock_delete_model.assert_not_called()
        db.add.assert_called_once_with(provider)
        db.query.assert_not_called()
        db.commit.assert_called_once()

        assert provider.status["available"] == "unknown"
        assert provider.status["model_list"] == ["gpt-4.1"]

    def test_ollama_worker_uses_stable_status_list_without_false_notifications(self):
        provider = SimpleNamespace(
            id="provider-1",
            provider="ollama",
            name="Ollama Test Provider",
            settings={
                "enable_auto_delete_missing_models": False,
                "enable_notify_model_changes": True,
            },
            status={
                "available": "up",
                "model_list": ["llama3:latest"],
                "last_synced_at": "2026-01-01T00:00:00+00:00",
            },
        )
        db = MagicMock()

        with patch("app.llm.worker.assert_llm_provider_allowed"), patch(
            "app.llm.worker.list_provider_status_models",
            return_value=[{"id": "llama3:latest"}],
        ), patch("app.llm.worker._notify") as mock_notify:
            _sync_provider(db, provider)

        mock_notify.assert_not_called()
