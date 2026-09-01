"""Schemas for social-login provider settings."""

import re
from typing import List

from app.auth.github_oauth import DEFAULT_GITHUB_BASE_URL, normalize_github_base_url
from app.auth.microsoft import normalize_microsoft_tenant
from app.utils.schemas import FieldSchema, Option, Section, Sections
from pydantic import BaseModel, Field, field_validator


_SOCIAL_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
_MICROSOFT_TENANT_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _normalize_social_domains(value) -> list[str]:
    """Normalize exact email/hosted-domain policies for social sign-in."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Allowed domains must be a list")
    normalized: list[str] = []
    for raw_domain in value:
        domain = str(raw_domain or "").strip().lower().lstrip("@").rstrip(".")
        if not domain:
            continue
        if not _SOCIAL_DOMAIN_PATTERN.fullmatch(domain):
            raise ValueError(f"Invalid allowed domain: {raw_domain}")
        if domain not in normalized:
            normalized.append(domain)
    return normalized


class LoginSocialSettings(BaseModel):
    # Google OAuth
    enable_google_oauth: bool = False
    enable_google_login: bool = False
    import_google_oauth_profile_picture: bool = False
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_picker_api_key: str | None = None
    google_picker_app_id: str | None = None
    google_button_text: str = ""
    google_allowed_domains: List[str] = Field(default_factory=list)
    google_allow_signup: bool = True
    # GitHub OAuth
    enable_github_oauth: bool = False
    enable_github_login: bool = False
    import_github_oauth_profile_picture: bool = False
    github_base_url: str = DEFAULT_GITHUB_BASE_URL
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_connection_scope_tier: str = "repository_access"
    github_button_text: str = Field(default="")
    github_allowed_domains: List[str] = Field(default_factory=list)
    github_allowed_organizations: List[str] = Field(default_factory=list)
    github_allow_signup: bool = Field(default=True)
    # Slack OAuth
    enable_slack_oauth: bool = False
    enable_slack_login: bool = False
    import_slack_oauth_profile_picture: bool = False
    slack_client_id: str | None = None
    slack_client_secret: str | None = None
    slack_connection_scope_tier: str = "public_read"
    slack_button_text: str = Field(default="")
    slack_allowed_domains: List[str] = Field(default_factory=list)
    slack_allowed_workspace_ids: List[str] = Field(default_factory=list)
    slack_allow_signup: bool = Field(default=True)
    # Microsoft OAuth
    enable_microsoft_oauth: bool = False
    enable_microsoft_login: bool = False
    microsoft_tenant: str = "common"
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None
    import_microsoft_oauth_profile_picture: bool = False
    microsoft_button_text: str = Field(default="")
    microsoft_allowed_domains: List[str] = Field(default_factory=list)
    microsoft_allowed_tenant_ids: List[str] = Field(default_factory=list)
    microsoft_allow_signup: bool = Field(default=True)
    # Apple OAuth
    enable_apple_login: bool = False
    apple_client_id: str | None = None
    apple_team_id: str | None = None
    apple_key_id: str | None = None
    apple_private_key: str | None = None
    apple_button_text: str = Field(default="")
    apple_allowed_domains: List[str] = Field(default_factory=list)
    apple_allow_signup: bool = Field(default=True)

    @field_validator("enable_microsoft_login", mode="before")
    @classmethod
    def validate_enable_microsoft_login(cls, v):
        return v if v is not None else False

    @field_validator("microsoft_tenant", mode="before")
    @classmethod
    def validate_microsoft_tenant(cls, v):
        """Constrain the value interpolated into Microsoft OAuth URLs."""

        return normalize_microsoft_tenant(v)

    @field_validator("microsoft_button_text", mode="before")
    @classmethod
    def validate_microsoft_button_text(cls, v):
        return v if v is not None else ""

    @field_validator(
        "google_allowed_domains",
        "github_allowed_domains",
        "slack_allowed_domains",
        "microsoft_allowed_domains",
        "apple_allowed_domains",
        mode="before",
    )
    @classmethod
    def normalize_allowed_domains(cls, value):
        return _normalize_social_domains(value)

    @field_validator("microsoft_allowed_tenant_ids", mode="before")
    @classmethod
    def normalize_microsoft_tenant_ids(cls, value):
        values = [] if value is None else value
        if not isinstance(values, list):
            raise ValueError("Allowed Microsoft tenant IDs must be a list")
        normalized = list(
            dict.fromkeys(
                str(item or "").strip().lower()
                for item in values
                if str(item or "").strip()
            )
        )
        if any(not _MICROSOFT_TENANT_ID_PATTERN.fullmatch(item) for item in normalized):
            raise ValueError("Microsoft tenant IDs must be UUIDs")
        return normalized

    @field_validator("github_allowed_organizations", mode="before")
    @classmethod
    def normalize_github_organizations(cls, value):
        values = [] if value is None else value
        if not isinstance(values, list):
            raise ValueError("Allowed GitHub organizations must be a list")
        return list(
            dict.fromkeys(
                str(item or "").strip().lower()
                for item in values
                if str(item or "").strip()
            )
        )

    @field_validator("github_base_url", mode="before")
    @classmethod
    def normalize_github_server_origin(cls, value):
        """Accept GitHub.com or one self-hosted GitHub server origin."""

        return normalize_github_base_url(value)

    @field_validator("github_connection_scope_tier", mode="before")
    @classmethod
    def validate_github_connection_scope_tier(cls, v):
        value = str(v or "").strip().lower()
        if value in {"profile_only", "repository_access", "extended_access"}:
            return value
        return "repository_access"

    @field_validator("slack_connection_scope_tier", mode="before")
    @classmethod
    def validate_slack_connection_scope_tier(cls, v):
        value = str(v or "").strip().lower()
        if value in {"public_read", "workspace_read", "workspace_write"}:
            return value
        return "public_read"

    @field_validator("slack_button_text", mode="before")
    @classmethod
    def validate_slack_button_text(cls, v):
        """Keep the Slack button usable when older settings omit its label."""
        return v if v is not None else ""

    @field_validator("slack_allowed_workspace_ids", mode="before")
    @classmethod
    def validate_slack_allowlists(cls, v):
        """Normalize missing Slack allowlists to empty, unrestricted lists."""
        return v if v is not None else []

    @field_validator("slack_allow_signup", mode="before")
    @classmethod
    def validate_slack_allow_signup(cls, v):
        """Preserve the default self-service signup policy for omitted values."""
        return v if v is not None else True

    @field_validator("microsoft_allow_signup", mode="before")
    @classmethod
    def validate_microsoft_allow_signup(cls, v):
        return v if v is not None else True

    @field_validator("enable_github_login", mode="before")
    @classmethod
    def validate_enable_github_login(cls, v):
        return v if v is not None else False

    @field_validator("github_button_text", mode="before")
    @classmethod
    def validate_github_button_text(cls, v):
        return v if v is not None else ""

    @field_validator("github_allow_signup", mode="before")
    @classmethod
    def validate_github_allow_signup(cls, v):
        return v if v is not None else True

    @field_validator("enable_apple_login", mode="before")
    @classmethod
    def validate_enable_apple_login(cls, v):
        return v if v is not None else False

    @field_validator("apple_button_text", mode="before")
    @classmethod
    def validate_apple_button_text(cls, v):
        return v if v is not None else ""

    @field_validator("apple_allow_signup", mode="before")
    @classmethod
    def validate_apple_allow_signup(cls, v):
        return v if v is not None else True


login_social_schema = Sections(
    sections=[
        Section(
            title="Google OAuth",
            description="Configure Google OAuth for login and supported workspace connections.",
            i18n_title="schema_login_social_sec1_title",
            i18n_description="schema_login_social_sec1_desc",
            fields=[
                FieldSchema(
                    key="enable_google_oauth",
                    label="Enable Google OAuth",
                    description="Enable the Google OAuth configuration for login and supported workspace connections.",
                    type="boolean",
                    i18n_label="schema_login_social_enable_google_oauth",
                    i18n_description="schema_login_social_enable_google_oauth_desc",
                ),
                FieldSchema(
                    key="enable_google_login",
                    label="Enable Google Login",
                    description="Allow users to sign in with their Google accounts.",
                    type="boolean",
                    dependency="enable_google_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_enable_google_login",
                    i18n_description="schema_login_social_enable_google_login_desc",
                ),
                FieldSchema(
                    key="import_google_oauth_profile_picture",
                    label="Import Google OAuth Profile Picture",
                    description="Download and store Google profile pictures when users sign in with Google.",
                    type="boolean",
                    dependency="enable_google_login",
                    dependency_value=True,
                    dependency2="enable_google_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_import_google_oauth_profile_picture",
                    i18n_description="schema_login_social_import_google_oauth_profile_picture_desc",
                ),
                FieldSchema(
                    key="google_client_id",
                    label="Google Client ID",
                    description="OAuth 2.0 Client ID from Google Cloud Console.",
                    type="string",
                    placeholder="Enter Google Client ID",
                    dependency="enable_google_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_google_client_id",
                    i18n_description="schema_login_social_google_client_id_desc",
                ),
                FieldSchema(
                    key="google_client_secret",
                    label="Google Client Secret",
                    description="OAuth 2.0 Client Secret from Google Cloud Console.",
                    type="string",
                    input_type="password",
                    redact_value=True,
                    masked_placeholder=True,
                    placeholder="Enter Google Client Secret",
                    dependency="enable_google_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_google_client_secret",
                    i18n_description="schema_login_social_google_client_secret_desc",
                ),
                FieldSchema(
                    key="google_picker_api_key",
                    label="Google Picker API Key",
                    description="Browser API key for Google Picker. Restrict it to your Omlorix HTTP referrers and the Google Picker API.",
                    type="string",
                    dependency="enable_google_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_google_picker_api_key",
                    i18n_description="schema_login_social_google_picker_api_key_desc",
                ),
                FieldSchema(
                    key="google_picker_app_id",
                    label="Google Picker App ID",
                    description="Numeric Google Cloud project number from the same project as the OAuth client and Picker API key.",
                    type="string",
                    dependency="enable_google_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_google_picker_app_id",
                    i18n_description="schema_login_social_google_picker_app_id_desc",
                ),
                FieldSchema(
                    key="google_button_text",
                    label="Google Button Text",
                    description="Text displayed on the Google sign-in button.",
                    type="string",
                    placeholder="Continue with Google",
                    dependency="enable_google_login",
                    dependency_value=True,
                    dependency2="enable_google_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_google_button_text",
                    i18n_description="schema_login_social_google_button_text_desc",
                ),
                FieldSchema(
                    key="google_allowed_domains",
                    label="Allowed Google Domains",
                    description="Restrict Google login to specific email domains. Leave empty to allow all domains.",
                    type="string_list",
                    placeholder="e.g., company.com",
                    dependency="enable_google_login",
                    dependency_value=True,
                    dependency2="enable_google_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_google_allowed_domains",
                    i18n_description="schema_login_social_google_allowed_domains_desc",
                ),
                FieldSchema(
                    key="google_allow_signup",
                    label="Allow Google Signup",
                    description="Allow new users to register via Google login. If disabled, only existing users can link their Google account.",
                    type="boolean",
                    dependency="enable_google_login",
                    dependency_value=True,
                    dependency2="enable_google_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_google_allow_signup",
                    i18n_description="schema_login_social_google_allow_signup_desc",
                ),
            ],
        ),
        Section(
            title="GitHub OAuth",
            description="Configure GitHub OAuth for login and managed workspace connections.",
            i18n_title="schema_login_social_sec_github_title",
            i18n_description="schema_login_social_sec_github_desc",
            fields=[
                FieldSchema(
                    key="enable_github_oauth",
                    label="Enable GitHub OAuth",
                    description="Enable the GitHub OAuth configuration for login and managed workspace connections.",
                    type="boolean",
                    i18n_label="schema_login_social_enable_github_oauth",
                    i18n_description="schema_login_social_enable_github_oauth_desc",
                ),
                FieldSchema(
                    key="github_base_url",
                    label="GitHub Base URL",
                    description="GitHub server origin used for login. Keep https://github.com for GitHub.com, or enter one self-hosted GitHub Enterprise Server. Managed workspace connections are available only with GitHub.com.",
                    type="string",
                    placeholder=DEFAULT_GITHUB_BASE_URL,
                    dependency="enable_github_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_github_base_url",
                    i18n_description="schema_login_social_github_base_url_desc",
                ),
                FieldSchema(
                    key="enable_github_login",
                    label="Enable GitHub Login",
                    description="Allow users to sign in with their GitHub accounts.",
                    type="boolean",
                    dependency="enable_github_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_enable_github_login",
                    i18n_description="schema_login_social_enable_github_login_desc",
                ),
                FieldSchema(
                    key="import_github_oauth_profile_picture",
                    label="Import GitHub OAuth Profile Picture",
                    description="Download and store GitHub profile pictures when users sign in with GitHub.",
                    type="boolean",
                    dependency="enable_github_login",
                    dependency_value=True,
                    dependency2="enable_github_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_import_github_oauth_profile_picture",
                    i18n_description="schema_login_social_import_github_oauth_profile_picture_desc",
                ),
                FieldSchema(
                    key="github_client_id",
                    label="GitHub Client ID",
                    description="OAuth App Client ID from GitHub.",
                    type="string",
                    placeholder="Enter GitHub Client ID",
                    dependency="enable_github_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_github_client_id",
                    i18n_description="schema_login_social_github_client_id_desc",
                ),
                FieldSchema(
                    key="github_client_secret",
                    label="GitHub Client Secret",
                    description="OAuth App Client Secret from GitHub.",
                    type="string",
                    input_type="password",
                    redact_value=True,
                    masked_placeholder=True,
                    placeholder="Enter GitHub Client Secret",
                    dependency="enable_github_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_github_client_secret",
                    i18n_description="schema_login_social_github_client_secret_desc",
                ),
                FieldSchema(
                    key="github_connection_scope_tier",
                    label="GitHub Connection Scope Tier",
                    description="Choose which GitHub OAuth scopes managed workspace connections request. GitHub OAuth apps do not offer a private-repository read-only scope, so repository access still requires the broad repo scope.",
                    type="select",
                    dependency="enable_github_oauth",
                    dependency_value=True,
                    options=[
                        Option(
                            value="profile_only",
                            label="Profile only",
                            i18n_label="schema_login_social_github_connection_scope_tier_profile_only",
                        ),
                        Option(
                            value="repository_access",
                            label="Repository access",
                            i18n_label="schema_login_social_github_connection_scope_tier_repository_access",
                        ),
                        Option(
                            value="extended_access",
                            label="Extended access",
                            i18n_label="schema_login_social_github_connection_scope_tier_extended_access",
                        ),
                    ],
                    i18n_label="schema_login_social_github_connection_scope_tier",
                    i18n_description="schema_login_social_github_connection_scope_tier_desc",
                ),
                FieldSchema(
                    key="github_button_text",
                    label="GitHub Button Text",
                    description="Text displayed on the GitHub sign-in button.",
                    type="string",
                    placeholder="Continue with GitHub",
                    dependency="enable_github_login",
                    dependency_value=True,
                    dependency2="enable_github_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_github_button_text",
                    i18n_description="schema_login_social_github_button_text_desc",
                ),
                FieldSchema(
                    key="github_allowed_domains",
                    label="Allowed GitHub Domains",
                    description="Restrict GitHub login to specific email domains. Leave empty to allow all domains.",
                    type="string_list",
                    placeholder="e.g., company.com",
                    dependency="enable_github_login",
                    dependency_value=True,
                    dependency2="enable_github_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_github_allowed_domains",
                    i18n_description="schema_login_social_github_allowed_domains_desc",
                ),
                FieldSchema(
                    key="github_allowed_organizations",
                    label="Allowed GitHub organizations",
                    description="Require membership in at least one listed GitHub organization. Leave empty to allow all organizations.",
                    type="string_list",
                    placeholder="e.g., my-company",
                    dependency="enable_github_login",
                    dependency_value=True,
                    dependency2="enable_github_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_github_allowed_organizations",
                    i18n_description="schema_login_social_github_allowed_organizations_desc",
                ),
                FieldSchema(
                    key="github_allow_signup",
                    label="Allow GitHub Signup",
                    description="Allow new users to register via GitHub login. If disabled, only existing users can link their GitHub account.",
                    type="boolean",
                    dependency="enable_github_login",
                    dependency_value=True,
                    dependency2="enable_github_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_github_allow_signup",
                    i18n_description="schema_login_social_github_allow_signup_desc",
                ),
            ],
        ),
        Section(
            title="Slack OAuth",
            description="Configure Slack OAuth for sign-in and managed workspace connections.",
            i18n_title="schema_login_social_sec_slack_title",
            i18n_description="schema_login_social_sec_slack_desc",
            fields=[
                FieldSchema(
                    key="enable_slack_oauth",
                    label="Enable Slack OAuth",
                    description="Enable the Slack OAuth configuration for sign-in and managed workspace connections.",
                    type="boolean",
                    i18n_label="schema_login_social_enable_slack_oauth",
                    i18n_description="schema_login_social_enable_slack_oauth_desc",
                ),
                FieldSchema(
                    key="enable_slack_login",
                    label="Enable Slack Login",
                    description="Allow users to sign in with their Slack accounts through OpenID Connect.",
                    type="boolean",
                    dependency="enable_slack_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_enable_slack_login",
                    i18n_description="schema_login_social_enable_slack_login_desc",
                ),
                FieldSchema(
                    key="import_slack_oauth_profile_picture",
                    label="Import Slack OAuth Profile Picture",
                    description="Download and store Slack profile pictures when users sign in with Slack.",
                    type="boolean",
                    dependency="enable_slack_login",
                    dependency_value=True,
                    dependency2="enable_slack_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_import_slack_oauth_profile_picture",
                    i18n_description="schema_login_social_import_slack_oauth_profile_picture_desc",
                ),
                FieldSchema(
                    key="slack_client_id",
                    label="Slack Client ID",
                    description="Client ID from the Slack app used for sign-in and managed workspace connections.",
                    type="string",
                    placeholder="Enter Slack Client ID",
                    dependency="enable_slack_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_slack_client_id",
                    i18n_description="schema_login_social_slack_client_id_desc",
                ),
                FieldSchema(
                    key="slack_client_secret",
                    label="Slack Client Secret",
                    description="Client Secret from the Slack app used for sign-in and managed workspace connections.",
                    type="string",
                    input_type="password",
                    redact_value=True,
                    masked_placeholder=True,
                    placeholder="Enter Slack Client Secret",
                    dependency="enable_slack_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_slack_client_secret",
                    i18n_description="schema_login_social_slack_client_secret_desc",
                ),
                FieldSchema(
                    key="slack_connection_scope_tier",
                    label="Slack Connection Scope Tier",
                    description="Choose how much managed Slack workspace connections can read or write. Users must reconnect Slack after changing this setting.",
                    type="select",
                    dependency="enable_slack_oauth",
                    dependency_value=True,
                    options=[
                        Option(
                            value="public_read",
                            label="Public channels only",
                            i18n_label="schema_login_social_slack_connection_scope_tier_public_read",
                        ),
                        Option(
                            value="workspace_read",
                            label="Workspace read access",
                            i18n_label="schema_login_social_slack_connection_scope_tier_workspace_read",
                        ),
                        Option(
                            value="workspace_write",
                            label="Workspace write access",
                            i18n_label="schema_login_social_slack_connection_scope_tier_workspace_write",
                        ),
                    ],
                    i18n_label="schema_login_social_slack_connection_scope_tier",
                    i18n_description="schema_login_social_slack_connection_scope_tier_desc",
                ),
                FieldSchema(
                    key="slack_button_text",
                    label="Slack Button Text",
                    description="Optional custom text for the Slack sign-in button. Leave empty to use the translated default.",
                    type="string",
                    placeholder="Sign in with Slack",
                    i18n_placeholder="schema_login_social_slack_button_text_placeholder",
                    dependency="enable_slack_login",
                    dependency_value=True,
                    dependency2="enable_slack_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_slack_button_text",
                    i18n_description="schema_login_social_slack_button_text_desc",
                ),
                FieldSchema(
                    key="slack_allowed_domains",
                    label="Allowed Slack Email Domains",
                    description="Restrict Slack login to specific verified email domains. Leave empty to allow all domains.",
                    type="string_list",
                    placeholder="e.g., company.com",
                    dependency="enable_slack_login",
                    dependency_value=True,
                    dependency2="enable_slack_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_slack_allowed_domains",
                    i18n_description="schema_login_social_slack_allowed_domains_desc",
                ),
                FieldSchema(
                    key="slack_allowed_workspace_ids",
                    label="Allowed Slack Workspace IDs",
                    description="Restrict login to specific Slack workspace IDs, such as T01234567. Leave empty to allow all workspaces.",
                    type="string_list",
                    placeholder="e.g., T01234567",
                    i18n_placeholder="schema_login_social_slack_allowed_workspace_ids_placeholder",
                    dependency="enable_slack_login",
                    dependency_value=True,
                    dependency2="enable_slack_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_slack_allowed_workspace_ids",
                    i18n_description="schema_login_social_slack_allowed_workspace_ids_desc",
                ),
                FieldSchema(
                    key="slack_allow_signup",
                    label="Allow Slack Signup",
                    description="Allow new users to register through Slack login.",
                    type="boolean",
                    dependency="enable_slack_login",
                    dependency_value=True,
                    dependency2="enable_slack_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_slack_allow_signup",
                    i18n_description="schema_login_social_slack_allow_signup_desc",
                ),
            ],
        ),
        Section(
            title="Microsoft OAuth",
            description="Configure Microsoft OAuth for social login.",
            i18n_title="schema_login_social_sec2_title",
            i18n_description="schema_login_social_sec2_desc",
            fields=[
                FieldSchema(
                    key="enable_microsoft_oauth",
                    label="Enable Microsoft OAuth",
                    description="Enable Microsoft OAuth for social login.",
                    type="boolean",
                    i18n_label="schema_login_social_enable_microsoft_oauth",
                    i18n_description="schema_login_social_enable_microsoft_oauth_desc",
                ),
                FieldSchema(
                    key="enable_microsoft_login",
                    label="Enable Microsoft Login",
                    description="Allow users to sign in with their Microsoft accounts.",
                    type="boolean",
                    dependency="enable_microsoft_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_enable_microsoft_login",
                    i18n_description="schema_login_social_enable_microsoft_login_desc",
                ),
                FieldSchema(
                    key="import_microsoft_oauth_profile_picture",
                    label="Import Microsoft OAuth Profile Picture",
                    description="Download and store Microsoft profile pictures when users sign in with Microsoft.",
                    type="boolean",
                    dependency="enable_microsoft_login",
                    dependency_value=True,
                    dependency2="enable_microsoft_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_import_microsoft_oauth_profile_picture",
                    i18n_description="schema_login_social_import_microsoft_oauth_profile_picture_desc",
                ),
                FieldSchema(
                    key="microsoft_tenant",
                    label="Microsoft Account Tenant",
                    description="Choose common, organizations, consumers, or enter the tenant GUID/domain supported by the Azure app registration.",
                    type="string",
                    placeholder="common",
                    dependency="enable_microsoft_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_microsoft_tenant",
                    i18n_description="schema_login_social_microsoft_tenant_desc",
                    i18n_placeholder="schema_login_social_microsoft_tenant_placeholder",
                ),
                FieldSchema(
                    key="microsoft_client_id",
                    label="Microsoft Client ID",
                    description="Application (client) ID from Azure App Registration.",
                    type="string",
                    placeholder="Enter Microsoft Client ID",
                    dependency="enable_microsoft_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_microsoft_client_id",
                    i18n_description="schema_login_social_microsoft_client_id_desc",
                ),
                FieldSchema(
                    key="microsoft_client_secret",
                    label="Microsoft Client Secret",
                    description="Client Secret from Azure App Registration.",
                    type="string",
                    input_type="password",
                    redact_value=True,
                    masked_placeholder=True,
                    placeholder="Enter Microsoft Client Secret",
                    dependency="enable_microsoft_oauth",
                    dependency_value=True,
                    i18n_label="schema_login_social_microsoft_client_secret",
                    i18n_description="schema_login_social_microsoft_client_secret_desc",
                ),
                FieldSchema(
                    key="microsoft_button_text",
                    label="Microsoft Button Text",
                    description="Text displayed on the Microsoft sign-in button.",
                    type="string",
                    placeholder="Continue with Microsoft",
                    dependency="enable_microsoft_login",
                    dependency_value=True,
                    dependency2="enable_microsoft_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_microsoft_button_text",
                    i18n_description="schema_login_social_microsoft_button_text_desc",
                ),
                FieldSchema(
                    key="microsoft_allowed_domains",
                    label="Allowed Microsoft Domains",
                    description="Restrict Microsoft login to specific email domains. Leave empty to allow all domains.",
                    type="string_list",
                    placeholder="e.g., company.com",
                    dependency="enable_microsoft_login",
                    dependency_value=True,
                    dependency2="enable_microsoft_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_microsoft_allowed_domains",
                    i18n_description="schema_login_social_microsoft_allowed_domains_desc",
                ),
                FieldSchema(
                    key="microsoft_allowed_tenant_ids",
                    label="Allowed Microsoft tenant IDs",
                    description="Require the verified Microsoft tenant claim to match one of these UUIDs. Leave empty to follow the tenant mode above.",
                    type="string_list",
                    placeholder="00000000-0000-0000-0000-000000000000",
                    dependency="enable_microsoft_login",
                    dependency_value=True,
                    dependency2="enable_microsoft_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_microsoft_allowed_tenant_ids",
                    i18n_description="schema_login_social_microsoft_allowed_tenant_ids_desc",
                ),
                FieldSchema(
                    key="microsoft_allow_signup",
                    label="Allow Microsoft Signup",
                    description="Allow new users to register via Microsoft login. If disabled, only existing users can link their Microsoft account.",
                    type="boolean",
                    dependency="enable_microsoft_login",
                    dependency_value=True,
                    dependency2="enable_microsoft_oauth",
                    dependency2_value=True,
                    i18n_label="schema_login_social_microsoft_allow_signup",
                    i18n_description="schema_login_social_microsoft_allow_signup_desc",
                ),
            ],
        ),
        Section(
            title="Apple Sign In",
            description="Configure Sign in with Apple for user login.",
            i18n_title="schema_login_social_sec3_title",
            i18n_description="schema_login_social_sec3_desc",
            fields=[
                FieldSchema(
                    key="enable_apple_login",
                    label="Enable Apple Login",
                    description="Allow users to sign in with their Apple ID.",
                    type="boolean",
                    i18n_label="schema_login_social_enable_apple_login",
                    i18n_description="schema_login_social_enable_apple_login_desc",
                ),
                FieldSchema(
                    key="apple_client_id",
                    label="Apple Service ID",
                    description="Service ID (Identifier) from Apple Developer Services.",
                    type="string",
                    placeholder="Enter Apple Service ID",
                    dependency="enable_apple_login",
                    dependency_value=True,
                    i18n_label="schema_login_social_apple_client_id",
                    i18n_description="schema_login_social_apple_client_id_desc",
                ),
                FieldSchema(
                    key="apple_team_id",
                    label="Apple Team ID",
                    description="Team ID from Apple Developer Account.",
                    type="string",
                    placeholder="Enter Apple Team ID",
                    dependency="enable_apple_login",
                    dependency_value=True,
                    i18n_label="schema_login_social_apple_team_id",
                    i18n_description="schema_login_social_apple_team_id_desc",
                ),
                FieldSchema(
                    key="apple_key_id",
                    label="Apple Key ID",
                    description="Key ID for the private key registered with Apple.",
                    type="string",
                    placeholder="Enter Apple Key ID",
                    dependency="enable_apple_login",
                    dependency_value=True,
                    i18n_label="schema_login_social_apple_key_id",
                    i18n_description="schema_login_social_apple_key_id_desc",
                ),
                FieldSchema(
                    key="apple_private_key",
                    label="Apple Private Key",
                    description="Private key (.p8 file content) for Sign in with Apple.",
                    type="textarea",
                    input_type="password",
                    redact_value=True,
                    masked_placeholder=True,
                    rows=6,
                    placeholder="-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----",
                    dependency="enable_apple_login",
                    dependency_value=True,
                    i18n_label="schema_login_social_apple_private_key",
                    i18n_description="schema_login_social_apple_private_key_desc",
                ),
                FieldSchema(
                    key="apple_button_text",
                    label="Apple Button Text",
                    description="Text displayed on the Apple sign-in button.",
                    type="string",
                    placeholder="Continue with Apple",
                    dependency="enable_apple_login",
                    dependency_value=True,
                    i18n_label="schema_login_social_apple_button_text",
                    i18n_description="schema_login_social_apple_button_text_desc",
                ),
                FieldSchema(
                    key="apple_allowed_domains",
                    label="Allowed Apple Domains",
                    description="Restrict Apple login to specific email domains. Leave empty to allow all domains.",
                    type="string_list",
                    placeholder="e.g., company.com",
                    dependency="enable_apple_login",
                    dependency_value=True,
                    i18n_label="schema_login_social_apple_allowed_domains",
                    i18n_description="schema_login_social_apple_allowed_domains_desc",
                ),
                FieldSchema(
                    key="apple_allow_signup",
                    label="Allow Apple Signup",
                    description="Allow new users to register via Apple login. If disabled, only existing users can link their Apple account.",
                    type="boolean",
                    dependency="enable_apple_login",
                    dependency_value=True,
                    i18n_label="schema_login_social_apple_allow_signup",
                    i18n_description="schema_login_social_apple_allow_signup_desc",
                ),
            ],
        ),
    ],
)
