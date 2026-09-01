"""Schemas for user-management defaults."""

from typing import Literal

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field


class UsersSettings(BaseModel):
    user_deletion_mode: Literal["delete_instantly", "delete_after_days", "retain"] = (
        "delete_after_days"
    )
    user_deletion_retention_days: int = Field(default=30, ge=0)
    temporary_account_deletion_mode: Literal[
        "delete_instantly", "delete_after_days", "retain"
    ] = "delete_after_days"
    temporary_account_retention_days: int = Field(default=30, ge=0)


users_schema = Sections(
    sections=[
        Section(
            title="User Data Retention",
            description="Control what happens to user data when an account is deleted.",
            i18n_title="schema_security_sec3_title",
            i18n_description="schema_security_sec3_desc",
            fields=[
                FieldSchema(
                    key="user_deletion_mode",
                    label="User deletion mode",
                    description="Choose what happens when a user account is deleted.",
                    type="select",
                    options=[
                        {
                            "value": "delete_instantly",
                            "label": "Delete instantly",
                            "i18n_label": "schema_option_delete_instantly",
                        },
                        {
                            "value": "delete_after_days",
                            "label": "Delete after N days (allows restore)",
                            "i18n_label": "schema_option_delete_after_days_restore",
                        },
                        {
                            "value": "retain",
                            "label": "Keep forever (soft-delete only)",
                            "i18n_label": "schema_option_keep_forever_soft_delete",
                        },
                    ],
                    i18n_label="schema_security_user_deletion_mode",
                    i18n_description="schema_security_user_deletion_mode_desc",
                ),
                FieldSchema(
                    key="user_deletion_retention_days",
                    label="Retention window (days)",
                    description="How long to retain a deleted user's data before permanent removal.",
                    type="number",
                    attributes={"min": 0},
                    dependency="user_deletion_mode",
                    dependency_value="delete_after_days",
                    i18n_label="schema_security_user_deletion_retention_days",
                    i18n_description="schema_security_user_deletion_retention_days_desc",
                ),
                FieldSchema(
                    key="temporary_account_deletion_mode",
                    label="Temporary account deletion mode",
                    description="Choose what happens to temporary-account data after the account expires or is revoked.",
                    type="select",
                    options=[
                        {
                            "value": "delete_instantly",
                            "label": "Delete instantly",
                            "i18n_label": "schema_option_delete_instantly",
                        },
                        {
                            "value": "delete_after_days",
                            "label": "Delete after N days",
                            "i18n_label": "schema_option_delete_after_days",
                        },
                        {
                            "value": "retain",
                            "label": "Keep forever",
                            "i18n_label": "schema_option_keep_forever",
                        },
                    ],
                    i18n_label="schema_users_temporary_account_deletion_mode",
                    i18n_description="schema_users_temporary_account_deletion_mode_desc",
                ),
                FieldSchema(
                    key="temporary_account_retention_days",
                    label="Temporary account retention window (days)",
                    description="How long expired or revoked temporary-account data remains before permanent removal.",
                    type="number",
                    attributes={"min": 0},
                    dependency="temporary_account_deletion_mode",
                    dependency_value="delete_after_days",
                    i18n_label="schema_users_temporary_account_retention_days",
                    i18n_description="schema_users_temporary_account_retention_days_desc",
                ),
            ],
        ),
    ],
)
