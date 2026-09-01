from pydantic import BaseModel

from app.utils.schemas import FieldSchema, Section, Sections


class ElevenlabsSettings(BaseModel):
    enable_logging: bool = True
    disable_background_sync: bool = False


ELEVENLABS_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this ElevenLabs connection appears across the admin UI.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="Display name for this ElevenLabs provider configuration.",
                    type="string",
                    placeholder="E.g. My ElevenLabs provider",
                    hide_on_byok=True,
                ),
            ],
        ),
        Section(
            title="API credentials & endpoint",
            description="Configure the credentials and endpoint used for speech-to-text and text-to-speech requests.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="ElevenLabs API key used for authenticating requests.",
                    type="string",
                    placeholder="E.g. sk_xxxxxxxxxxxxxxxxx",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.enable_logging",
                    label="Enable logging",
                    description="When enable_logging is set to false zero retention mode will be used for the request. This will mean log and transcript storage features are unavailable for this request. Zero retention mode may only be used by enterprise customers.",
                    type="boolean",
                    default=True,
                ),
                FieldSchema(
                    key="settings.disable_background_sync",
                    label="Disable regular provider requests",
                    description="Skip recurring background requests to this provider, such as periodic model list synchronization.",
                    type="boolean",
                    default=False,
                    hide_on_byok=True,
                ),
            ],
        ),
    ]
)
