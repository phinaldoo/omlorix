"""Schemas for assistant read-aloud settings."""

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel


class ReadAloudSettings(BaseModel):
    """Validate assistant-message text-to-speech defaults."""

    read_aloud_provider_id: str | None = "browser_native"
    read_aloud_model: str | None = None
    read_aloud_voice: str | None = None
    read_aloud_response_format: str | None = None


def build_read_aloud_model_field(provider_id: str) -> FieldSchema:
    """Build the model step after a non-browser provider is selected."""

    return FieldSchema(
        key="read_aloud_model",
        label="Read aloud model",
        description="Select the TTS model used when a custom provider handles read aloud.",
        type="select",
        options=[],
        placeholder="Select a read aloud model",
        i18n_label="schema_backend_read_aloud_model",
        i18n_description="schema_backend_select_the_tts_model_used_when_a_custom_provider_handles_read_aloud",
        i18n_placeholder="schema_backend_select_a_read_aloud_model",
        dependency="read_aloud_provider_id",
        dependency_value=provider_id,
    )


read_aloud_schema = Sections(
    sections=[
        Section(
            title="Read aloud",
            description="Choose how assistant messages are spoken aloud in chat.",
            fields=[
                FieldSchema(
                    key="read_aloud_provider_id",
                    label="Read aloud provider",
                    description="Use the browser's built-in speech synthesis or route read aloud through a configured TTS provider.",
                    type="select",
                    options=[],
                    placeholder="Choose a read aloud provider",
                ),
            ],
        ),
    ]
)
