import json
from pathlib import Path

import pytest

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
