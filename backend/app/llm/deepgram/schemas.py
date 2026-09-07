from __future__ import annotations

from pydantic import BaseModel

from app.utils.schemas import FieldSchema, Section, Sections


class DeepgramSettings(BaseModel):
    timeout: int = 120
    disable_background_sync: bool = False


DEEPGRAM_PROVIDER_SCHEMA = Sections(
    sections=[
        Section(
            title="Provider identity",
            description="Name how this Deepgram connection appears across the admin UI.",
            fields=[
                FieldSchema(
                    key="name",
                    label="Provider name",
                    description="Display name for this Deepgram provider configuration.",
                    type="string",
                    placeholder="E.g. My Deepgram provider",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="icon",
                    label="Provider icon",
                    description="Select a preset icon or provide a custom SVG for this provider.",
                    type="string",
                    default="deepgram",
                ),
            ],
        ),
        Section(
            title="API credentials",
            description="Configure the Deepgram API key used for speech-to-text and text-to-speech requests.",
            fields=[
                FieldSchema(
                    key="api_key",
                    label="API key",
                    description="Deepgram API key used for authenticating requests.",
                    type="string",
                    placeholder="E.g. dg_xxxxxxxxxxxxxxxxx",
                    hide_on_byok=True,
                ),
                FieldSchema(
                    key="settings.timeout",
                    label="Request timeout (seconds)",
                    description="Timeout applied to Deepgram API requests.",
                    type="number",
                    attributes={"min": 1},
                    default=120,
                    hide_on_byok=True,
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
        Section(
            title="Privacy",
            description="Deepgram requests sent by Omlorix always opt out of model improvement data usage.",
            fields=[],
        ),
    ]
)
