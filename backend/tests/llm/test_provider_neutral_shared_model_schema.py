import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.llm.openai.schemas import get_openai_model_schema


class _EmptyQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _EmptyDB:
    def query(self, *_args, **_kwargs):
        return _EmptyQuery()


@pytest.mark.parametrize("provider", ["google_aistudio", "openrouter"])
def test_saved_inactive_model_schema_survives_failed_discovery(monkeypatch, provider):
    from app.llm.google_aistudio import schemas as google_schemas
    from app.llm.openrouter import schemas as openrouter_schemas

    model = SimpleNamespace(
        id="inactive", is_active=False, model_name="vendor/saved-model", name="Saved name",
        description="Saved description", model_icon=provider, status="normal",
        tools=[], access={"everyone": True}, capabilities=[], meta={},
        settings={"input_formats": ["text"], "output_formats": ["text"], "system_instruction": "Saved instruction"},
    )

    def get_saved_model(db, model_id, *, include_inactive=False):
        assert model_id == "inactive"
        assert include_inactive is True
        return model

    def unavailable(*args, **kwargs):
        raise HTTPException(status_code=503, detail="Provider down")

    monkeypatch.setattr("app.llm.models.get_model", get_saved_model)
    if provider == "google_aistudio":
        monkeypatch.setattr(google_schemas, "get_aistudio_model_info", unavailable)
        get_schema = google_schemas.get_aistudio_model_schema
    else:
        monkeypatch.setattr("app.llm.openrouter.utils.get_model_information_endpoint", unavailable)
        get_schema = openrouter_schemas.get_openrouter_model_schema

    schema = get_schema(_EmptyDB(), "provider-1", model_id="inactive")
    fields = {field.key: field for section in schema.sections for field in section.fields or []}
    assert fields["name"].value == "Saved name"
    assert fields["settings.system_instruction"].value == "Saved instruction"
    # New-model discovery errors must still surface to the creation flow.
    with pytest.raises(HTTPException):
        get_schema(_EmptyDB(), "provider-1", model_name="vendor/new-model")


@pytest.mark.parametrize("model_name", ["gpt-5.6", "manual-openai-model"])
def test_openai_model_schema_uses_provider_neutral_shared_descriptions(model_name):
    """Admin and manual BYOK OpenAI schemas must not describe Gemini behavior."""
    schema = get_openai_model_schema(_EmptyDB(), None, model_name)
    sections = {section.title: section for section in schema.sections}

    title_section = sections["Conversation titles & prompts"]
    limits_section = sections["Modalities & platform limits"]

    assert title_section.description == (
        "Configure how this model generates conversation titles and which base "
        "instructions apply."
    )
    assert title_section.i18n_description == (
        "schema_backend_conversation_titles_and_prompts_description"
    )
    assert limits_section.description == (
        "Configure the supported modalities and attachment or token limits for this model."
    )
    assert limits_section.i18n_description == (
        "schema_backend_modalities_and_platform_limits_description"
    )
    assert "gemini" not in title_section.description.lower()
    assert "gemini" not in limits_section.description.lower()


def test_provider_neutral_shared_descriptions_are_translated_in_every_locale():
    i18n_root = Path(__file__).resolve().parents[3] / "frontend" / "i18n"
    required_keys = {
        "schema_backend_conversation_titles_and_prompts_description",
        "schema_backend_modalities_and_platform_limits_description",
    }
    removed_keys = {
        "schema_backend_configure_how_gemini_summarizes_chats_and_which_base_instructions_apply",
        "schema_backend_declare_supported_modalities_and_any_attachment_or_token_limits_enforced_for_gemini",
    }

    for schema_path in i18n_root.glob("*/schema.json"):
        translations = json.loads(schema_path.read_text(encoding="utf-8"))
        assert required_keys <= translations.keys(), schema_path
        assert removed_keys.isdisjoint(translations), schema_path
