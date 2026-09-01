from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from _otel_test_stubs import install_otel_stubs

install_otel_stubs()

from app.admin.settings import utils as admin_utils  # noqa: E402
from app.admin.settings.schema_categories.read_aloud import (  # noqa: E402
    ReadAloudSettings,
    build_read_aloud_model_field,
)
from app.chats.read_aloud_constants import (  # noqa: E402
    READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID,
)
from app.settings.models import Settings  # noqa: E402


def test_read_aloud_model_field_has_complete_i18n_metadata():
    """The dynamic model step must remain translated beside its sibling fields."""
    field = build_read_aloud_model_field("provider")

    assert field.i18n_label == "schema_backend_read_aloud_model"
    assert (
        field.i18n_description
        == "schema_backend_select_the_tts_model_used_when_a_custom_provider_handles_read_aloud"
    )
    assert field.i18n_placeholder == "schema_backend_select_a_read_aloud_model"


def test_update_admin_settings_read_aloud_switch_to_browser_native(monkeypatch):
    """Test that switching read_aloud_provider_id to browser_native clears model settings without raising error."""
    mock_db = MagicMock()

    # Create mock existing settings populated with defaults and custom TTS fields
    existing_data = ReadAloudSettings().model_dump()
    existing_data.update({
        "read_aloud_provider_id": "provider-openai-1",
        "read_aloud_model": "tts-1",
        "read_aloud_voice": "alloy",
    })
    mock_existing_record = Settings(page_name="read_aloud", data=existing_data)

    monkeypatch.setattr(admin_utils, "get_settings_page", lambda _db, _page: mock_existing_record)
    monkeypatch.setattr(admin_utils, "flag_modified", lambda instance, key: None)

    payload = {"read_aloud_provider_id": READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID}

    # Execute normalization/update for the dedicated read-aloud page.
    updated_keys = admin_utils.update_admin_settings_values_for_page("read_aloud", payload, mock_db)

    assert "read_aloud_provider_id" in updated_keys
    assert mock_existing_record.data["read_aloud_provider_id"] == READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID
    assert mock_existing_record.data["read_aloud_model"] is None
    assert mock_existing_record.data["read_aloud_voice"] is None
    assert mock_existing_record.data["read_aloud_response_format"] is None


def test_update_admin_settings_read_aloud_switch_between_custom_providers(
    monkeypatch,
):
    """A custom-provider change clears every omitted capability child."""
    mock_db = MagicMock()
    existing_data = ReadAloudSettings().model_dump()
    existing_data.update(
        {
            "read_aloud_provider_id": "old-provider",
            "read_aloud_model": "shared-model-id",
            "read_aloud_voice": "old-voice",
            "read_aloud_response_format": "mp3",
        }
    )
    existing_record = Settings(page_name="read_aloud", data=existing_data)
    monkeypatch.setattr(
        admin_utils,
        "get_settings_page",
        lambda _db, _page: existing_record,
    )
    monkeypatch.setattr(admin_utils, "flag_modified", lambda *_args: None)
    monkeypatch.setattr(
        admin_utils,
        "_get_read_aloud_provider_options",
        lambda _db: [
            {
                "value": READ_ALOUD_BROWSER_NATIVE_PROVIDER_ID,
                "label": "Browser",
            },
            {"value": "new-provider", "label": "New provider"},
        ],
    )
    monkeypatch.setattr(
        admin_utils,
        "get_llm_provider",
        lambda _db, _provider_id: SimpleNamespace(provider="openai"),
    )

    admin_utils.update_admin_settings_values_for_page(
        "read_aloud",
        {"read_aloud_provider_id": "new-provider"},
        mock_db,
    )

    assert existing_record.data["read_aloud_provider_id"] == "new-provider"
    assert existing_record.data["read_aloud_model"] is None
    assert existing_record.data["read_aloud_voice"] is None
    assert existing_record.data["read_aloud_response_format"] is None
