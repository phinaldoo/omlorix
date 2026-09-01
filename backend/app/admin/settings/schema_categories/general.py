"""Schemas for general application settings."""

from typing import Literal

from app.settings.public_urls import normalize_public_urls
from app.utils.schemas import FieldSchema, Option, Section, Sections
from pydantic import BaseModel, Field, field_validator


class GeneralSettings(BaseModel):
    application_name: str | None = None
    offline_mode: bool = False
    external_requests_mode: Literal[
        "allow_all", "private_only", "allowlist_only", "deny_all"
    ] = "allow_all"
    external_requests_allowlist: list[str] = Field(default_factory=list)
    public_url: list[str] = Field(default_factory=list)
    internet_connectivity_check_enabled: bool = True

    @field_validator("external_requests_allowlist", mode="before")
    @classmethod
    def _normalize_external_requests_allowlist(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            entry = item.strip()
            if entry and entry not in normalized:
                normalized.append(entry)
        return normalized

    @field_validator("public_url", mode="before")
    @classmethod
    def _normalize_public_urls(cls, value):
        """Validate URL entries and transparently upgrade the legacy scalar value."""
        return normalize_public_urls(value, allow_empty=True)


# -------------------
# General Settings Schema
# -------------------
general_schema = Sections(
    sections=[
        Section(
            title="General Application Settings",
            description="General application settings",
            i18n_title="schema_general_sec0_title",
            i18n_description="schema_general_sec0_desc",
            fields=[
                FieldSchema(
                    key="application_name",
                    label="Application Name",
                    description="The name of the application",
                    type="string",
                    placeholder="E.g. Omlorix",
                    i18n_label="schema_general_application_name",
                    i18n_description="schema_general_application_name_desc",
                )
            ],
        ),
        Section(
            title="Connection Settings",
            description="Connection settings",
            i18n_title="schema_general_sec1_title",
            i18n_description="schema_general_sec1_desc",
            fields=[
                FieldSchema(
                    key="offline_mode",
                    label="Offline Mode",
                    description="Force Omlorix into private-network-only mode. Public internet destinations are blocked and internet-facing features stop working.",
                    type="boolean",
                    i18n_label="schema_general_offline_mode",
                    i18n_description="schema_general_offline_mode_desc",
                ),
                FieldSchema(
                    key="external_requests_mode",
                    label="External Requests Policy",
                    description="Choose how Omlorix handles outbound requests when offline mode is disabled.",
                    type="select",
                    i18n_label="schema_general_external_requests_mode",
                    i18n_description="schema_general_external_requests_mode_desc",
                    dependency="offline_mode",
                    dependency_value=False,
                    options=[
                        Option(
                            value="allow_all",
                            label="Allow all outbound requests",
                            i18n_label="schema_general_external_requests_mode_allow_all",
                        ),
                        Option(
                            value="private_only",
                            label="Allow only local and private network targets",
                            i18n_label="schema_general_external_requests_mode_private_only",
                        ),
                        Option(
                            value="allowlist_only",
                            label="Allow only configured allowlist targets",
                            i18n_label="schema_general_external_requests_mode_allowlist_only",
                        ),
                        Option(
                            value="deny_all",
                            label="Block all outbound requests",
                            i18n_label="schema_general_external_requests_mode_deny_all",
                        ),
                    ],
                ),
                FieldSchema(
                    key="external_requests_allowlist",
                    label="External Request Allowlist",
                    description="Optional hostnames, wildcard domains, URLs, or CIDR ranges that remain reachable when the policy is set to allowlist only.",
                    type="string_list",
                    i18n_label="schema_general_external_requests_allowlist",
                    i18n_description="schema_general_external_requests_allowlist_desc",
                    i18n_placeholder="schema_general_external_requests_allowlist_placeholder",
                    dependency="external_requests_mode",
                    dependency_value="allowlist_only",
                    dependency2="offline_mode",
                    dependency2_value=False,
                    placeholder="Examples: api.internal.local, *.corp.example, 10.0.0.0/8",
                ),
                FieldSchema(
                    key="internet_connectivity_check_enabled",
                    label="Internet Connectivity Check Enabled",
                    description="Enable internet connectivity check. For offline mode, this is automatically disabled.",
                    type="boolean",
                    i18n_label="schema_general_internet_connectivity_check",
                    i18n_description="schema_general_internet_connectivity_check_desc",
                    dependency="offline_mode",
                    dependency_value=False,
                ),
                FieldSchema(
                    key="public_url",
                    label="Public URLs",
                    description="Public origins for the application. The first URL is primary for generated links; every URL is accepted for sensitive authentication requests.",
                    type="string_list",
                    metadata={"ordered": True, "primary_first": True},
                    placeholder="E.g. https://chat.example.com",
                    i18n_label="schema_general_public_url",
                    i18n_description="schema_general_public_url_desc",
                    i18n_placeholder="schema_general_public_url_placeholder",
                ),
            ],
        ),
    ]
)
