"""Schemas for administrative notification settings."""

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, constr


class NotificationSettings(BaseModel):
    enable_notifications: bool = False
    webhook_url: constr(max_length=2048) | None = None


notification_settings_schema = Sections(
    sections=[
        Section(
            title="Administrative notifications",
            description="Control how external systems (e.g., Discord, Telegram) receive admin alerts.",
            i18n_title="schema_notifications_sec0_title",
            i18n_description="schema_notifications_sec0_desc",
            fields=[
                FieldSchema(
                    key="enable_notifications",
                    label="Enable outgoing notifications",
                    description="Toggle webhook delivery for newly created admin notifications.",
                    type="boolean",
                    i18n_label="schema_notifications_enable_notifications",
                    i18n_description="schema_notifications_enable_notifications_desc",
                ),
                FieldSchema(
                    key="webhook_url",
                    label="Webhook destination URL",
                    description="HTTPS endpoint for posting notification payloads (e.g., Discord/Telegram bot URL).",
                    type="string",
                    placeholder="https://example.com/webhook",
                    dependency="enable_notifications",
                    dependency_value=True,
                    i18n_label="schema_notifications_webhook_url",
                    i18n_description="schema_notifications_webhook_url_desc",
                ),
            ],
        )
    ]
)
