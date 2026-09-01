from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.settings import utils as admin_utils
from app.admin.settings.schema_categories.audio_generation import (
    AudioGenerationSettings,
    audio_generation_schema,
)
from app.admin.settings.schema_categories.dictation import (
    DictationSettings,
    build_file_transcription_model_field,
    build_live_transcription_model_field,
    dictation_schema,
)
from app.admin.settings.schema_categories.image_generation import (
    ImageGenerationSettings,
    image_generation_schema,
)
from app.admin.settings.schema_categories.models import (
    ModelDefaultsSettings,
    models_schema,
)
from app.admin.settings.schema_categories.music_generation import (
    MusicGenerationSettings,
    music_generation_schema,
)
from app.admin.settings.schema_categories.read_aloud import (
    ReadAloudSettings,
    read_aloud_schema,
)
from app.admin.settings.schema_categories.realtime import (
    RealtimeSettings,
    realtime_schema,
)
from app.admin.settings.schema_categories.video_generation import (
    VideoGenerationSettings,
    video_generation_schema,
)
from app.llm.google_aistudio.realtime import GOOGLE_AISTUDIO_TTS_VOICES
from app.llm.openai.realtime import OPENAI_REALTIME_VOICES
from app.llm.schemas import ProviderEnum
from app.llm.xai import realtime as xai_realtime
from app.llm.xai.transcription import get_live_transcription_settings_schema as get_xai_live_transcription_settings_schema
from app.settings.defaults import DEFAULT_SETTINGS
from app.settings.models import Settings


class _ProviderQuery:
    """Provide the small SQLAlchemy query surface used by the schema helper."""

    def __init__(self, provider_type: str):
        self.provider = SimpleNamespace(provider=provider_type)

    def filter(self, *_args):
        return self

    def first(self):
        return self.provider


class _ProviderDB:
    """Return a deterministic provider without requiring a database fixture."""

    def __init__(self, provider_type: str):
        self.provider_type = provider_type

    def query(self, *_args):
        return _ProviderQuery(self.provider_type)


def _field_map(schema):
    """Flatten schema fields by key for concise capability assertions."""
    return {
        field.key: field
        for section in schema.sections
        for field in section.fields
    }


def test_model_feature_settings_contracts_are_disjoint():
    """Each base schema stays inside its independent persistence contract."""
    contracts = {
        "models": (ModelDefaultsSettings, models_schema),
        "dictation": (DictationSettings, dictation_schema),
        "read_aloud": (ReadAloudSettings, read_aloud_schema),
        "realtime": (RealtimeSettings, realtime_schema),
        "image_generation": (ImageGenerationSettings, image_generation_schema),
        "audio_generation": (AudioGenerationSettings, audio_generation_schema),
        "video_generation": (VideoGenerationSettings, video_generation_schema),
        "music_generation": (MusicGenerationSettings, music_generation_schema),
    }
    seen_fields: set[str] = set()

    independently_named_pages = {"models", "dictation", "read_aloud", "realtime"}
    for page_name, (model, schema) in contracts.items():
        model_fields = set(model.model_fields)
        schema_fields = set(_field_map(schema))
        assert schema_fields <= model_fields
        if page_name in independently_named_pages:
            assert seen_fields.isdisjoint(model_fields)
            seen_fields.update(model_fields)

    assert set(_field_map(dictation_schema)) == {
        "transcription_enabled",
        "transcription_provider_id",
        "live_transcription_enabled",
        "live_transcription_provider_id",
    }
    assert set(_field_map(read_aloud_schema)) == {"read_aloud_provider_id"}
    assert set(_field_map(realtime_schema)) == {
        "realtime_enabled",
        "realtime_provider_id",
    }
    for schema in (
        image_generation_schema,
        audio_generation_schema,
        video_generation_schema,
        music_generation_schema,
    ):
        assert set(_field_map(schema)) == {"provider_id"}


def test_transcription_sections_explain_live_priority_and_file_routing():
    """Admins should understand which transcription path serves each feature."""
    schema = dictation_schema.model_copy(deep=True)
    file_section = next(
        section
        for section in schema.sections
        if any(field.key == "transcription_enabled" for field in section.fields)
    )
    live_section = next(
        section
        for section in schema.sections
        if any(field.key == "live_transcription_enabled" for field in section.fields)
    )
    fields = _field_map(schema)

    assert file_section.title == "File & meeting transcription"
    assert "meeting uploads" in file_section.description
    assert "fallback" in file_section.description
    assert fields["transcription_provider_id"].label == "File transcription provider"
    assert (
        build_file_transcription_model_field("provider").label
        == "File transcription model"
    )

    assert live_section.title == "Live chat dictation"
    assert "Used first for chatbox" in live_section.description
    assert "meetings still use File & meeting transcription" in live_section.description
    assert fields["live_transcription_provider_id"].label == "Live chat provider"
    assert (
        build_live_transcription_model_field("provider").label
        == "Live chat model"
    )


def test_xai_live_dictation_schema_uses_native_stt_controls():
    """xAI must not inherit OpenAI's unsupported transcript-delay enum."""
    schema = dictation_schema.model_copy(deep=True)

    admin_utils._configure_live_transcription_fields_for_selection(
        schema,
        _ProviderDB(ProviderEnum.xai.value),
        "xai-provider",
        "grok-transcribe",
    )

    fields = _field_map(schema)
    xai_field_keys = set(_field_map(get_xai_live_transcription_settings_schema()))
    assert "live_transcription_delay" not in fields
    assert xai_field_keys <= fields.keys()
    assert fields["live_transcription_xai_smart_turn"].attributes.step == 0.01
    assert fields["live_transcription_xai_vad_threshold"].attributes.step == 0.01


def test_openai_live_dictation_schema_hides_xai_native_controls():
    """OpenAI live dictation should retain only its own delay setting."""
    schema = dictation_schema.model_copy(deep=True)

    admin_utils._configure_live_transcription_fields_for_selection(
        schema,
        _ProviderDB(ProviderEnum.openai.value),
        "openai-provider",
        "gpt-4o-transcribe",
    )

    fields = _field_map(schema)
    assert "live_transcription_delay" in fields
    xai_field_keys = set(_field_map(get_xai_live_transcription_settings_schema()))
    assert not (xai_field_keys & fields.keys())


def test_openai_realtime_schema_exposes_only_consumed_controls():
    """OpenAI should show shared controls while hiding Gemini-only settings."""
    schema = realtime_schema.model_copy(deep=True)

    admin_utils._configure_realtime_fields_for_selection(
        schema,
        _ProviderDB(ProviderEnum.openai.value),
        "openai-provider",
        "gpt-realtime",
    )

    fields = _field_map(schema)
    assert fields["realtime_voice"].type == "select"
    assert [option.value for option in fields["realtime_voice"].options] == [
        *OPENAI_REALTIME_VOICES
    ]
    assert "realtime_max_output_tokens" in fields
    assert "realtime_input_transcription_enabled" in fields
    assert "realtime_temperature" not in fields
    assert "realtime_enable_session_resumption" not in fields


def test_xai_realtime_schema_exposes_native_voices_and_shared_controls():
    """xAI should use its own voices without exposing Gemini-only settings."""
    schema = realtime_schema.model_copy(deep=True)

    admin_utils._configure_realtime_fields_for_selection(
        schema,
        _ProviderDB(ProviderEnum.xai.value),
        "xai-provider",
        "grok-voice-latest",
    )

    fields = _field_map(schema)
    assert fields["realtime_voice"].type == "select"
    assert [option.value for option in fields["realtime_voice"].options] == [
        *xai_realtime.XAI_REALTIME_VOICES
    ]
    assert "realtime_max_output_tokens" not in fields
    assert "realtime_input_transcription_enabled" in fields
    assert "realtime_language_code" in fields
    assert "realtime_prefix_padding_ms" in fields
    assert "realtime_silence_duration_ms" in fields
    assert (
        fields["realtime_language_code"].i18n_description
        == "schema_xai_realtime_language_desc"
    )
    assert "realtime_temperature" not in fields
    assert "realtime_enable_session_resumption" not in fields


def test_xai_realtime_schema_includes_accessible_custom_voices(monkeypatch):
    """The realtime picker should use xAI's account-aware shared voice catalog."""
    schema = realtime_schema.model_copy(deep=True)
    monkeypatch.setattr(
        xai_realtime,
        "get_xai_realtime_voice_options",
        lambda **_kwargs: [
            {"value": "eve", "label": "Eve"},
            {"value": "nlbqfwie", "label": "Studio Narrator"},
        ],
    )

    admin_utils._configure_realtime_fields_for_selection(
        schema,
        _ProviderDB(ProviderEnum.xai.value),
        "xai-provider",
        "grok-voice-latest",
    )

    fields = _field_map(schema)
    assert [option.value for option in fields["realtime_voice"].options] == [
        "eve",
        "nlbqfwie",
    ]


def test_google_31_realtime_schema_hides_unsupported_native_audio_features():
    """Gemini 3.1 Flash Live does not support affective or proactive audio."""
    schema = realtime_schema.model_copy(deep=True)

    admin_utils._configure_realtime_fields_for_selection(
        schema,
        _ProviderDB(ProviderEnum.google_aistudio.value),
        "google-provider",
        "gemini-3.1-flash-live-preview",
    )

    fields = _field_map(schema)
    assert fields["realtime_voice"].type == "select"
    assert [option.value for option in fields["realtime_voice"].options] == [
        *GOOGLE_AISTUDIO_TTS_VOICES
    ]
    assert fields["realtime_voice"].i18n_placeholder == "llm.shared.voice.placeholder"
    assert "realtime_enable_affective_dialog" not in fields
    assert "realtime_enable_proactive_audio" not in fields
    assert "realtime_temperature" in fields
    assert "realtime_enable_session_resumption" in fields


def test_google_realtime_schema_preserves_a_valid_non_default_voice(monkeypatch):
    """Schema hydration must not replace a supported saved Google voice."""
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda *_args: {
            "realtime_enabled": True,
            "realtime_provider_id": "google-provider",
            "realtime_model": "gemini-3.1-flash-live-preview",
            "realtime_voice": "Puck",
        },
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_realtime_provider_options",
        lambda *_args: [{"value": "google-provider", "label": "Google AI Studio"}],
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_realtime_model_options",
        lambda *_args: [
            {
                "value": "gemini-3.1-flash-live-preview",
                "label": "Gemini 3.1 Flash Live",
            }
        ],
    )
    monkeypatch.setattr(admin_utils, "list_available_tool_options", lambda *_args: [])

    response = admin_utils.get_admin_settings_schema_response(
        "realtime",
        include_values=True,
        db=_ProviderDB(ProviderEnum.google_aistudio.value),
    )
    fields = {
        field["key"]: field
        for section in response["sections"]
        for field in section["fields"]
    }

    assert response["values"]["realtime_voice"] == "Puck"
    assert fields["realtime_voice"]["value"] == "Puck"


def test_realtime_schema_waits_for_model_before_showing_provider_controls():
    """Provider-dependent settings should remain hidden until model selection."""
    schema = realtime_schema.model_copy(deep=True)

    admin_utils._configure_realtime_fields_for_selection(
        schema,
        _ProviderDB(ProviderEnum.google_aistudio.value),
        "google-provider",
        None,
    )

    fields = _field_map(schema)
    assert "realtime_voice" not in fields
    assert "realtime_temperature" not in fields
    assert "realtime_provider_id" in fields
    assert "realtime_model" not in fields


@pytest.mark.parametrize(
    ("page", "settings_model", "provider_key", "model_key", "options_helper"),
    [
        (
            "dictation",
            DictationSettings,
            "transcription_provider_id",
            "transcription_model",
            "_get_transcription_provider_options",
        ),
        (
            "dictation",
            DictationSettings,
            "live_transcription_provider_id",
            "live_transcription_model",
            "_get_live_transcription_provider_options",
        ),
        (
            "realtime",
            RealtimeSettings,
            "realtime_provider_id",
            "realtime_model",
            "_get_realtime_provider_options",
        ),
    ],
)
def test_provider_change_clears_omitted_dependent_model(
    monkeypatch,
    page,
    settings_model,
    provider_key,
    model_key,
    options_helper,
):
    """Changing a parent provider must invalidate its omitted model child."""
    existing_data = settings_model().model_dump()
    existing_data.update({provider_key: "old-provider", model_key: "shared-model-id"})
    existing_record = Settings(page_name=page, data=existing_data)
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, _page: existing_record,
    )
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(
        admin_utils,
        options_helper,
        lambda _db: [{"value": "new-provider", "label": "New provider"}],
    )

    admin_utils.update_admin_settings_values_for_page(
        page,
        {provider_key: "new-provider"},
        MagicMock(),
    )

    assert existing_record.data[provider_key] == "new-provider"
    assert existing_record.data[model_key] is None


def test_realtime_provider_change_clears_omitted_voice(monkeypatch):
    """A provider switch must not retain a voice from the old capability set."""
    existing_data = RealtimeSettings().model_dump()
    existing_data.update(
        {
            "realtime_provider_id": "old-provider",
            "realtime_model": "old-model",
            "realtime_voice": "old-provider-voice",
        }
    )
    existing_record = Settings(page_name="realtime", data=existing_data)
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, _page: existing_record,
    )
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(
        admin_utils,
        "_get_realtime_provider_options",
        lambda _db: [{"value": "new-provider", "label": "New provider"}],
    )

    admin_utils.update_admin_settings_values_for_page(
        "realtime",
        {"realtime_provider_id": "new-provider"},
        MagicMock(),
    )

    assert existing_record.data["realtime_provider_id"] == "new-provider"
    assert existing_record.data["realtime_model"] is None
    assert existing_record.data["realtime_voice"] is None


@pytest.mark.parametrize(
    ("page", "provider_options_helper", "model_options_helper"),
    [
        (
            "image_generation",
            "_get_image_generation_provider_options",
            "_get_image_generation_model_options",
        ),
        (
            "audio_generation",
            "_get_audio_generation_provider_options",
            "_get_audio_generation_model_options",
        ),
        (
            "music_generation",
            "_get_music_generation_provider_options",
            "_get_music_generation_model_options",
        ),
        (
            "video_generation",
            "_get_video_generation_provider_options",
            "_get_video_generation_model_options",
        ),
    ],
)
def test_media_generation_schema_adds_model_only_after_provider_selection(
    monkeypatch,
    page,
    provider_options_helper,
    model_options_helper,
):
    """Every media schema follows the same provider-to-model wizard contract."""
    monkeypatch.setattr(
        admin_utils,
        provider_options_helper,
        lambda _db: [{"value": "provider", "label": "Provider"}],
    )
    monkeypatch.setattr(
        admin_utils,
        model_options_helper,
        lambda _db, _provider_id: [{"value": "model", "label": "Model"}],
    )

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda *_args: {"provider_id": None},
    )
    empty_response = admin_utils.get_admin_settings_schema_response(
        page,
        include_values=True,
        db=MagicMock(),
    )
    empty_fields = {
        field["key"]
        for section in empty_response["sections"]
        for field in section["fields"]
    }
    assert empty_fields == {"provider_id"}

    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda *_args: {"provider_id": "provider"},
    )
    selected_response = admin_utils.get_admin_settings_schema_response(
        page,
        include_values=True,
        db=MagicMock(),
    )
    selected_fields = {
        field["key"]: field
        for section in selected_response["sections"]
        for field in section["fields"]
    }
    assert set(selected_fields) == {"provider_id", "model_name"}
    assert selected_fields["model_name"]["dependency"] == "provider_id"
    assert selected_fields["model_name"]["dependency_value"] == "provider"


def test_realtime_tools_are_an_accessible_multi_select_with_registry_options(monkeypatch):
    """The admin schema should expose built-in and custom tools by stable id."""
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page_data",
        lambda *_args: {
            "realtime_enabled": True,
            "realtime_provider_id": "openai-provider",
            "realtime_model": "gpt-realtime",
        },
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_realtime_provider_options",
        lambda *_args: [{"value": "openai-provider", "label": "OpenAI"}],
    )
    monkeypatch.setattr(
        admin_utils,
        "_get_realtime_model_options",
        lambda *_args: [{"value": "gpt-realtime", "label": "gpt-realtime"}],
    )
    monkeypatch.setattr(
        admin_utils,
        "list_available_tool_options",
        lambda *_args: [
            {
                "name": "weather",
                "label": "Weather",
                "i18n_label": "rate_limit_tool_label_weather",
            },
            {"name": "company_lookup", "label": "Company lookup"},
        ],
    )

    response = admin_utils.get_admin_settings_schema_response(
        "realtime",
        include_values=True,
        db=_ProviderDB(ProviderEnum.openai.value),
    )
    fields = {
        field["key"]: field
        for section in response["sections"]
        for field in section["fields"]
    }
    realtime_tools = fields["realtime_tools"]

    assert realtime_tools["type"] == "select"
    assert realtime_tools["multiple"] is True
    assert [option["value"] for option in realtime_tools["options"]] == [
        "weather",
        "company_lookup",
    ]
    assert realtime_tools["options"][0]["i18n_label"] == "rate_limit_tool_label_weather"
    assert response["values"]["realtime_tools"] == []


def test_realtime_tool_selection_is_validated_and_persisted(monkeypatch):
    """Saving the multi-select should store known ids in canonical order."""
    db = MagicMock()
    record = Settings(page_name="realtime", data=RealtimeSettings().model_dump())
    monkeypatch.setattr(admin_utils, "get_settings_page", lambda *_args: record)
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(
        admin_utils,
        "list_available_tool_options",
        lambda *_args: [
            {"name": "weather", "label": "Weather"},
            {"name": "company_lookup", "label": "Company lookup"},
        ],
    )

    changed = admin_utils.update_admin_settings_values_for_page(
        "realtime",
        {"realtime_tools": ["company_lookup", "weather", "company_lookup"]},
        db,
    )

    assert "realtime_tools" in changed
    assert record.data["realtime_tools"] == ["weather", "company_lookup"]


@pytest.mark.parametrize("legacy_value", [None, "missing"])
def test_partial_realtime_update_repairs_legacy_tool_storage(monkeypatch, legacy_value):
    """Partial model saves must work with rows created before realtime tools."""
    db = MagicMock()
    existing_data = RealtimeSettings().model_dump()
    if legacy_value == "missing":
        existing_data.pop("realtime_tools")
    else:
        existing_data["realtime_tools"] = legacy_value
    record = Settings(page_name="realtime", data=existing_data)

    monkeypatch.setattr(admin_utils, "get_settings_page", lambda *_args: record)
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)

    changed = admin_utils.update_admin_settings_values_for_page(
        "realtime",
        {"realtime_enabled": True},
        db,
    )

    assert "realtime_enabled" in changed
    assert record.data["realtime_tools"] == []


def test_realtime_tools_have_a_canonical_persisted_default():
    """Startup synchronization must backfill the realtime tool list."""
    assert DEFAULT_SETTINGS["realtime"]["realtime_tools"] == []


def test_invalid_model_setting_returns_a_client_error(monkeypatch):
    """Pydantic validation failures must not escape as HTTP 500 responses."""
    record = Settings(page_name="realtime", data=RealtimeSettings().model_dump())
    monkeypatch.setattr(admin_utils, "get_settings_page", lambda *_args: record)

    with pytest.raises(HTTPException) as exc:
        admin_utils.update_admin_settings_values_for_page(
            "realtime",
            {"realtime_temperature": "not-a-number"},
            MagicMock(),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "admin_settings_validation_failed"


def test_realtime_tool_selection_rejects_unknown_tools(monkeypatch):
    """Clients cannot smuggle arbitrary backend tool names into settings."""
    monkeypatch.setattr(
        admin_utils,
        "list_available_tool_options",
        lambda *_args: [{"name": "weather", "label": "Weather"}],
    )

    with pytest.raises(HTTPException) as exc:
        admin_utils.update_admin_settings_values_for_page(
            "realtime",
            {"realtime_tools": ["unknown_tool"]},
            MagicMock(),
        )

    assert exc.value.status_code == 400
    assert "unknown_tool" in str(exc.value.detail)
