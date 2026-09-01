"""Schemas for default group assignment settings."""

from app.settings.defaults import DEFAULT_SETTINGS
from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel


# Group defaults are persisted on the login-general settings page, but this
# category owns its validation model and UI schema. Read the shared canonical
# defaults directly instead of coupling this module to ``login_general``.
_LOGIN_GENERAL_DEFAULTS = DEFAULT_SETTINGS["login_general"]


class GroupsDefaultsSettings(BaseModel):
    default_user_group: str = _LOGIN_GENERAL_DEFAULTS["default_user_group"]


groups_defaults_schema = Sections(
    sections=[
        Section(
            title="Registration defaults",
            description="Choose which group new accounts use by default.",
            i18n_title="groups_default_title",
            i18n_description="groups_default_desc",
            fields=[
                FieldSchema(
                    key="default_user_group",
                    label="Default user group",
                    description="New email, social, and enterprise SSO accounts use this group unless another provisioning rule overrides it.",
                    type="select",
                    i18n_label="groups_default_select_label",
                    i18n_description="groups_default_select_desc",
                ),
            ],
        ),
    ],
)
