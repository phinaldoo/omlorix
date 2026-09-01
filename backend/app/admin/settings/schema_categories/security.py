"""Schemas for application security settings."""

from typing import Any, List, Literal

from app.ip_analytics.schemas import (
    _normalize_country_code_list,
    _normalize_ip_address_list,
    _normalize_trusted_proxy_list,
)
from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field, field_validator


class SecuritySettings(BaseModel):
    enable_block_user_after_wrong_signin: bool = True
    block_user_after_wrong_signin_attempts: int = Field(default=6, ge=1)
    block_user_after_wrong_signin_attempts_time_hours: int = Field(default=1, ge=1)
    access_token_expire_minutes: int = Field(default=15, ge=1)
    refresh_token_expire_minutes: int = Field(default=10080, ge=1)
    enable_ip_restrictions: bool = False
    check_ip_location_provider: Literal["ipinfo", "ipstack", "db-ip-free"] = "ipinfo"
    enable_ip_address_restrictions: bool = False
    ip_address_restriction_mode: Literal["allowlist", "blocklist"] = "blocklist"
    only_allow_specific_ip: bool = False
    allow_specific_ip: List[str] = Field(default_factory=list)
    block_specific_ip: List[str] = Field(default_factory=list)
    enable_ip_country_restrictions: bool = False
    ip_country_restriction_mode: Literal["allowlist", "blocklist"] = "blocklist"
    only_allow_ip_from_specific_countries: bool = False
    allow_country_ip: List[str] = Field(default_factory=list)
    block_country_ip: List[str] = Field(default_factory=list)
    allow_ip_if_no_country_found: bool = False
    trust_proxy_headers: bool = False
    trusted_proxies: List[str] = Field(default_factory=list)
    auth_logs_auto_cleanup_enabled: bool = True
    auth_logs_cleanup_mode: Literal["age", "count"] = "age"
    auth_logs_max_age_days: int = Field(default=90, ge=1)
    auth_logs_max_count: int = Field(default=100000, ge=0)
    auth_logs_cleanup_interval_seconds: int = Field(default=3600, ge=60)
    auth_logs_retention_after_user_delete_mode: Literal[
        "delete_instantly", "delete_after_days", "retain"
    ] = "delete_after_days"
    auth_logs_retention_delete_after_days: int = Field(default=30, ge=0, le=3650)
    audit_logs_retention_after_user_delete_mode: Literal[
        "delete_instantly", "delete_after_days", "retain"
    ] = "delete_after_days"
    audit_logs_retention_delete_after_days: int = Field(default=30, ge=0, le=3650)

    @field_validator("allow_specific_ip", "block_specific_ip", mode="before")
    @classmethod
    def normalize_security_ip_lists(cls, values: Any) -> list[str]:
        """Normalize exact-IP allow/block settings before they reach middleware."""
        return _normalize_ip_address_list(values)

    @field_validator("allow_country_ip", "block_country_ip", mode="before")
    @classmethod
    def normalize_security_country_lists(cls, values: Any) -> list[str]:
        """Normalize country restriction lists to provider-compatible codes."""
        return _normalize_country_code_list(values)

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def normalize_trusted_proxy_lists(cls, values: Any) -> list[str]:
        """Normalize trusted proxy IP/CIDR settings before request IP resolution."""
        return _normalize_trusted_proxy_list(values)


security_schema = Sections(
    sections=[
        Section(
            title="Authentication Token Lifetimes",
            description="Configure how long access and refresh tokens remain valid.",
            i18n_title="schema_security_token_lifetimes_title",
            i18n_description="schema_security_token_lifetimes_desc",
            fields=[
                FieldSchema(
                    key="access_token_expire_minutes",
                    label="Access token expiry (minutes)",
                    description="How long access tokens remain valid.",
                    type="number",
                    attributes={"min": 1},
                    i18n_label="schema_security_access_token_expire_minutes",
                    i18n_description="schema_security_access_token_expire_minutes_desc",
                ),
                FieldSchema(
                    key="refresh_token_expire_minutes",
                    label="Refresh token expiry (minutes)",
                    description="How long refresh tokens remain valid.",
                    type="number",
                    attributes={"min": 1},
                    i18n_label="schema_security_refresh_token_expire_minutes",
                    i18n_description="schema_security_refresh_token_expire_minutes_desc",
                ),
            ],
        ),
        Section(
            title="Sign-in Protection & Sessions",
            description="Limit abusive authentication attempts and configure token lifetimes.",
            i18n_title="schema_security_sec0_title",
            i18n_description="schema_security_sec0_desc",
            fields=[
                FieldSchema(
                    key="enable_block_user_after_wrong_signin",
                    label="Block after failed sign-in attempts",
                    description="Automatically block accounts that exceed the allowed failed sign-in attempts.",
                    type="boolean",
                    i18n_label="schema_security_enable_block_user_after_wrong_signin",
                    i18n_description="schema_security_enable_block_user_after_wrong_signin_desc",
                ),
                FieldSchema(
                    key="block_user_after_wrong_signin_attempts",
                    label="Failed sign-in attempt limit",
                    description="Number of failed sign-in attempts allowed before blocking the user.",
                    type="number",
                    attributes={"min": 1},
                    dependency="enable_block_user_after_wrong_signin",
                    dependency_value=True,
                    i18n_label="schema_security_block_user_after_wrong_signin_attempts",
                    i18n_description="schema_security_block_user_after_wrong_signin_attempts_desc",
                ),
                FieldSchema(
                    key="block_user_after_wrong_signin_attempts_time_hours",
                    label="Block duration (hours)",
                    description="How long to block the user after reaching the failed sign-in attempt limit.",
                    type="number",
                    attributes={"min": 1},
                    dependency="enable_block_user_after_wrong_signin",
                    dependency_value=True,
                    i18n_label="schema_security_block_user_after_wrong_signin_attempts_time_hours",
                    i18n_description="schema_security_block_user_after_wrong_signin_attempts_time_hours_desc",
                ),
            ],
        ),
        Section(
            title="Network & Location Restrictions",
            description="Restrict access by IP address, country, or trusted network paths.",
            i18n_title="schema_security_sec1_title",
            i18n_description="schema_security_sec1_desc",
            fields=[
                FieldSchema(
                    key="enable_ip_restrictions",
                    label="Enable IP restrictions",
                    description="Restrict access based on IP addresses or countries.",
                    type="boolean",
                    i18n_label="schema_security_enable_ip_restrictions",
                    i18n_description="schema_security_enable_ip_restrictions_desc",
                ),
                FieldSchema(
                    key="enable_ip_address_restrictions",
                    label="Enable exact IP rules",
                    description="Apply exact visitor IP allowlist or blocklist rules.",
                    type="boolean",
                    dependency="enable_ip_restrictions",
                    dependency_value=True,
                    i18n_label="security_ip_policy_exact_enable",
                    i18n_description="security_ip_policy_exact_enable_desc",
                ),
                FieldSchema(
                    key="ip_address_restriction_mode",
                    label="Exact IP mode",
                    description="Choose whether the listed IP addresses are allowed or blocked.",
                    type="select",
                    options=[
                        {
                            "value": "allowlist",
                            "label": "Allowlist",
                            "i18n_label": "security_ip_policy_mode_allowlist",
                        },
                        {
                            "value": "blocklist",
                            "label": "Blocklist",
                            "i18n_label": "security_ip_policy_mode_blocklist",
                        },
                    ],
                    dependency="enable_ip_address_restrictions",
                    dependency_value=True,
                    dependency2="enable_ip_restrictions",
                    dependency2_value=True,
                    i18n_label="security_ip_policy_exact_mode_label",
                    i18n_description="security_ip_policy_exact_mode_desc",
                ),
                FieldSchema(
                    key="allow_specific_ip",
                    label="Allowed IP addresses",
                    description="One IP address per line that is permitted to access the system.",
                    type="string_list",
                    placeholder="E.g. 203.0.113.10",
                    dependency="ip_address_restriction_mode",
                    dependency_value="allowlist",
                    dependency2="enable_ip_restrictions",
                    dependency2_value=True,
                    dependency3="enable_ip_address_restrictions",
                    dependency3_value=True,
                    i18n_label="schema_security_allow_specific_ip",
                    i18n_description="schema_security_allow_specific_ip_desc",
                ),
                FieldSchema(
                    key="block_specific_ip",
                    label="Blocked IP addresses",
                    description="One IP address per line that is denied access.",
                    type="string_list",
                    placeholder="E.g. 198.51.100.25",
                    dependency="ip_address_restriction_mode",
                    dependency_value="blocklist",
                    dependency2="enable_ip_restrictions",
                    dependency2_value=True,
                    dependency3="enable_ip_address_restrictions",
                    dependency3_value=True,
                    i18n_label="schema_security_block_specific_ip",
                    i18n_description="schema_security_block_specific_ip_desc",
                ),
                FieldSchema(
                    key="enable_ip_country_restrictions",
                    label="Enable country rules",
                    description="Apply allowlist or blocklist rules based on resolved country codes.",
                    type="boolean",
                    dependency="enable_ip_restrictions",
                    dependency_value=True,
                    i18n_label="security_ip_policy_country_enable",
                    i18n_description="security_ip_policy_country_enable_desc",
                ),
                FieldSchema(
                    key="ip_country_restriction_mode",
                    label="Country mode",
                    description="Choose whether the listed country codes are allowed or blocked.",
                    type="select",
                    options=[
                        {
                            "value": "allowlist",
                            "label": "Allowlist",
                            "i18n_label": "security_ip_policy_mode_allowlist",
                        },
                        {
                            "value": "blocklist",
                            "label": "Blocklist",
                            "i18n_label": "security_ip_policy_mode_blocklist",
                        },
                    ],
                    dependency="enable_ip_country_restrictions",
                    dependency_value=True,
                    dependency2="enable_ip_restrictions",
                    dependency2_value=True,
                    i18n_label="security_ip_policy_country_mode_label",
                    i18n_description="security_ip_policy_country_mode_desc",
                ),
                FieldSchema(
                    key="allow_country_ip",
                    label="Allowed country codes",
                    description="ISO country codes that are permitted.",
                    type="string_list",
                    placeholder="E.g. DE",
                    dependency="ip_country_restriction_mode",
                    dependency_value="allowlist",
                    dependency2="enable_ip_restrictions",
                    dependency2_value=True,
                    dependency3="enable_ip_country_restrictions",
                    dependency3_value=True,
                    i18n_label="schema_security_allow_country_ip",
                    i18n_description="schema_security_allow_country_ip_desc",
                ),
                FieldSchema(
                    key="block_country_ip",
                    label="Blocked country codes",
                    description="ISO country codes that are blocked.",
                    type="string_list",
                    placeholder="E.g. CN",
                    dependency="ip_country_restriction_mode",
                    dependency_value="blocklist",
                    dependency2="enable_ip_restrictions",
                    dependency2_value=True,
                    dependency3="enable_ip_country_restrictions",
                    dependency3_value=True,
                    i18n_label="schema_security_block_country_ip",
                    i18n_description="schema_security_block_country_ip_desc",
                ),
                FieldSchema(
                    key="allow_ip_if_no_country_found",
                    label="Allow IPs without country match",
                    description="Allow access if the geolocation provider cannot resolve the country.",
                    type="boolean",
                    dependency="ip_country_restriction_mode",
                    dependency_value="allowlist",
                    dependency2="enable_ip_restrictions",
                    dependency2_value=True,
                    dependency3="enable_ip_country_restrictions",
                    dependency3_value=True,
                    i18n_label="schema_security_allow_ip_if_no_country_found",
                    i18n_description="schema_security_allow_ip_if_no_country_found_desc",
                ),
                FieldSchema(
                    key="check_ip_location_provider",
                    label="IP location provider",
                    description="Service used to resolve IP geolocation.",
                    type="select",
                    options=[
                        {"value": "ipinfo", "label": "IP Info"},
                        {"value": "ipstack", "label": "IPStack"},
                        {"value": "db-ip-free", "label": "DB-IP (Free)"},
                    ],
                    dependency="enable_ip_country_restrictions",
                    dependency_value=True,
                    dependency2="enable_ip_restrictions",
                    dependency2_value=True,
                    i18n_label="schema_security_check_ip_location_provider",
                    i18n_description="schema_security_check_ip_location_provider_desc",
                ),
                FieldSchema(
                    key="ipinfo",
                    label="IP Info API KEY",
                    description="API key for IP Info geolocation service.",
                    type="string",
                    input_type="password",
                    redact_value=True,
                    masked_placeholder=True,
                    dependency="check_ip_location_provider",
                    dependency_value=["ipinfo"],
                    dependency2="enable_ip_restrictions",
                    dependency2_value=True,
                    dependency3="enable_ip_country_restrictions",
                    dependency3_value=True,
                    i18n_label="schema_security_ipinfo",
                    i18n_description="schema_security_ipinfo_desc",
                ),
                FieldSchema(
                    key="ipstack",
                    label="IPStack API KEY",
                    type="string",
                    input_type="password",
                    redact_value=True,
                    masked_placeholder=True,
                    description="API key for IPStack geolocation service.",
                    dependency="check_ip_location_provider",
                    dependency_value=["ipstack"],
                    dependency2="enable_ip_restrictions",
                    dependency2_value=True,
                    dependency3="enable_ip_country_restrictions",
                    dependency3_value=True,
                    i18n_label="schema_security_ipstack",
                    i18n_description="schema_security_ipstack_desc",
                ),
                FieldSchema(
                    key="trust_proxy_headers",
                    label="Trust proxy headers",
                    description="Use forwarded client IP headers from configured trusted proxies.",
                    type="boolean",
                    i18n_label="schema_security_trust_proxy_headers",
                    i18n_description="schema_security_trust_proxy_headers_desc",
                ),
                FieldSchema(
                    key="trusted_proxies",
                    label="Trusted proxies",
                    description="Proxy IP addresses or CIDR ranges allowed to supply forwarded client IP headers.",
                    type="string_list",
                    placeholder="E.g. 10.0.0.0/8",
                    dependency="trust_proxy_headers",
                    dependency_value=True,
                    i18n_label="schema_security_trusted_proxies",
                    i18n_description="schema_security_trusted_proxies_desc",
                ),
            ],
        ),
        Section(
            title="Authentication Log Retention",
            description="Decide how authentication logs are stored and cleaned up.",
            i18n_title="schema_security_sec2_title",
            i18n_description="schema_security_sec2_desc",
            fields=[
                FieldSchema(
                    key="auth_logs_auto_cleanup_enabled",
                    label="Enable auth log auto-cleanup",
                    description="Automatically clean up authentication logs.",
                    type="boolean",
                    i18n_label="schema_security_auth_logs_auto_cleanup_enabled",
                    i18n_description="schema_security_auth_logs_auto_cleanup_enabled_desc",
                ),
                FieldSchema(
                    key="auth_logs_cleanup_mode",
                    label="Auth log cleanup mode",
                    description="Choose how authentication logs are cleaned up.",
                    type="select",
                    options=[
                        {
                            "value": "age",
                            "label": "By age",
                            "i18n_label": "schema_option_cleanup_mode_age",
                        },
                        {
                            "value": "count",
                            "label": "By count",
                            "i18n_label": "schema_option_cleanup_mode_count",
                        },
                    ],
                    dependency="auth_logs_auto_cleanup_enabled",
                    dependency_value=True,
                    i18n_label="schema_security_auth_logs_cleanup_mode",
                    i18n_description="schema_security_auth_logs_cleanup_mode_desc",
                ),
                FieldSchema(
                    key="auth_logs_max_age_days",
                    label="Auth log max age (days)",
                    description="Maximum age of authentication logs before cleanup.",
                    type="number",
                    attributes={"min": 1},
                    dependency="auth_logs_cleanup_mode",
                    dependency_value="age",
                    dependency2="auth_logs_auto_cleanup_enabled",
                    dependency2_value=True,
                    i18n_label="schema_security_auth_logs_max_age_days",
                    i18n_description="schema_security_auth_logs_max_age_days_desc",
                ),
                FieldSchema(
                    key="auth_logs_max_count",
                    label="Auth log max count",
                    description="Maximum number of authentication logs to retain.",
                    type="number",
                    attributes={"min": 0},
                    dependency="auth_logs_cleanup_mode",
                    dependency_value="count",
                    dependency2="auth_logs_auto_cleanup_enabled",
                    dependency2_value=True,
                    i18n_label="schema_security_auth_logs_max_count",
                    i18n_description="schema_security_auth_logs_max_count_desc",
                ),
                FieldSchema(
                    key="auth_logs_cleanup_interval_seconds",
                    label="Cleanup interval (seconds)",
                    description="Frequency of the authentication log cleanup job.",
                    type="number",
                    attributes={"min": 60},
                    dependency="auth_logs_auto_cleanup_enabled",
                    dependency_value=True,
                    i18n_label="schema_security_auth_logs_cleanup_interval_seconds",
                    i18n_description="schema_security_auth_logs_cleanup_interval_seconds_desc",
                ),
                FieldSchema(
                    key="auth_logs_retention_after_user_delete_mode",
                    label="Per-user deletion retention",
                    description="Choose what happens to authentication logs when a user account is deleted.",
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
                    i18n_label="schema_security_auth_logs_retention_after_user_delete_mode",
                    i18n_description="schema_security_auth_logs_retention_after_user_delete_mode_desc",
                ),
                FieldSchema(
                    key="auth_logs_retention_delete_after_days",
                    label="Retention window after deletion (days)",
                    description="How long to retain a deleted user's auth logs before removal.",
                    type="number",
                    attributes={"min": 0, "max": 3650},
                    dependency="auth_logs_retention_after_user_delete_mode",
                    dependency_value="delete_after_days",
                    i18n_label="schema_security_auth_logs_retention_delete_after_days",
                    i18n_description="schema_security_auth_logs_retention_delete_after_days_desc",
                ),
            ],
        ),
        Section(
            title="Audit Log and Admin Notification Retention",
            description="Control what happens to audit logs and user-scoped admin notifications after a user account is deleted.",
            i18n_title="schema_security_post_deletion_audit_title",
            i18n_description="schema_security_post_deletion_audit_desc",
            fields=[
                FieldSchema(
                    key="audit_logs_retention_after_user_delete_mode",
                    label="Post-deletion retention",
                    description="Apply one coupled retention policy to the deleted user's audit logs and user-scoped admin notifications.",
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
                    i18n_label="schema_security_audit_logs_retention_after_user_delete_mode",
                    i18n_description="schema_security_audit_logs_retention_after_user_delete_mode_desc",
                ),
                FieldSchema(
                    key="audit_logs_retention_delete_after_days",
                    label="Retention window after deletion (days)",
                    description="How long to retain the deleted user's audit logs and user-scoped admin notifications before removal.",
                    type="number",
                    attributes={"min": 0, "max": 3650},
                    dependency="audit_logs_retention_after_user_delete_mode",
                    dependency_value="delete_after_days",
                    i18n_label="schema_security_audit_logs_retention_delete_after_days",
                    i18n_description="schema_security_audit_logs_retention_delete_after_days_desc",
                ),
            ],
        ),
    ],
)
