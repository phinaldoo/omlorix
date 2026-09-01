"""Schemas and normalization helpers for enterprise SSO settings."""

import re
from typing import Any, Dict, List, Literal
from urllib.parse import urlsplit

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, ConfigDict, Field, field_validator


_SSO_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
_SSO_SCOPE_PATTERN = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]+$")


def _normalize_http_url(value: Any) -> str | None:
    """Validate an administrator-configured federation endpoint URL."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Federation endpoint URLs must be strings")
    normalized = value.strip()
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            "Federation endpoint URLs must use http:// or https:// and include a host"
        )
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError(
            "Federation endpoint URLs must not include credentials or fragments"
        )
    return normalized


def _normalize_sso_string_list(values: Any, *, label: str) -> list[str]:
    """Normalize a bounded list of non-empty Enterprise SSO strings."""

    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a list")

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{label} entries must be strings")
        item = value.strip()
        if not item:
            continue
        if len(item) > 255:
            raise ValueError(f"{label} entries must not exceed 255 characters")
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _normalize_sso_domains(values: Any) -> list[str]:
    """Canonicalize exact email domains used by SSO policies and routing."""

    domains = _normalize_sso_string_list(values, label="SSO domains")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_domain in domains:
        domain = raw_domain.lower().lstrip("@").rstrip(".")
        if not _SSO_DOMAIN_PATTERN.fullmatch(domain):
            raise ValueError(f"Invalid SSO domain: {raw_domain}")
        if domain in seen:
            continue
        seen.add(domain)
        normalized.append(domain)
    return normalized


def _normalize_oidc_scopes(values: Any) -> list[str]:
    """Normalize OIDC scopes and retain the mandatory OpenID scope."""

    scopes = _normalize_sso_string_list(values, label="OIDC scopes")
    if "openid" not in scopes:
        raise ValueError("OIDC scopes must include openid")
    for scope in scopes:
        if not _SSO_SCOPE_PATTERN.fullmatch(scope):
            raise ValueError(f"Invalid OIDC scope: {scope}")
    return scopes


def _normalize_attribute_mapping(value: Any) -> dict[str, str]:
    """Validate the claim-to-profile mapping used by SAML and OIDC."""

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Attribute mapping must be an object")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise ValueError("Attribute mapping keys and values must be strings")
        key = raw_key.strip()
        mapped_value = raw_value.strip()
        if not key or not mapped_value:
            raise ValueError(
                "Attribute mapping keys and values must be non-empty strings"
            )
        if len(key) > 128 or len(mapped_value) > 255:
            raise ValueError("Attribute mapping entries are too long")
        normalized[key] = mapped_value
    return normalized


class EnterpriseIdentityPolicy(BaseModel):
    """Validated account-linking, sync, and group policy for a default provider."""

    model_config = ConfigDict(extra="forbid")

    link_existing_users_by_email: bool = False
    sync_profile_on_login: bool = False
    sync_email_on_login: bool = False
    sync_app_group_on_login: bool = False
    sync_role_on_login: bool = False
    enable_group_sync: bool = False
    group_claim: str = Field(default="groups", min_length=1, max_length=255)
    groups_separator: str = Field(default=",", min_length=1, max_length=16)
    required_groups: List[str] = Field(default_factory=list)
    group_to_app_group: List[str] = Field(default_factory=list)
    group_to_role: List[str] = Field(default_factory=list)

    @field_validator(
        "required_groups", "group_to_app_group", "group_to_role", mode="before"
    )
    @classmethod
    def normalize_group_lists(cls, value: Any) -> list[str]:
        return _normalize_sso_string_list(
            value, label="Enterprise identity group policy"
        )


class SAMLAdvancedSettings(BaseModel):
    """Validated SAML trust and request-signing settings."""

    model_config = ConfigDict(extra="forbid")

    idp_entity_id: str | None = Field(default=None, max_length=2048)
    additional_x509_certs: List[str] = Field(default_factory=list)
    nameid_format: str = Field(
        default="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent", max_length=2048
    )
    sign_authn_requests: bool = False
    sp_x509_cert: str | None = None
    sp_private_key: str | None = None

    @field_validator("additional_x509_certs", mode="before")
    @classmethod
    def normalize_certificates(cls, value: Any) -> list[str]:
        return _normalize_sso_string_list(value, label="SAML signing certificates")


class OIDCAdvancedSettings(BaseModel):
    """Validated OIDC authorization and token-client behavior."""

    model_config = ConfigDict(extra="forbid")

    token_endpoint_auth_method: Literal["client_secret_basic", "client_secret_post"] = (
        "client_secret_basic"
    )
    prompt: str | None = Field(default=None, max_length=255)


class LoginEnterpriseSSOSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enable_scim: bool = False
    scim_bearer_token: str | None = None
    scim_previous_bearer_token: str | None = None
    scim_default_role: Literal["user", "pending"] = "user"
    scim_default_group: str = "default"
    scim_link_existing_users_by_email: bool = True
    scim_sync_group_memberships: bool = True
    enable_saml: bool = False
    saml_entity_id: str | None = None
    saml_sso_url: str | None = None
    saml_x509_cert: str | None = None
    saml_advanced_settings: SAMLAdvancedSettings = Field(
        default_factory=SAMLAdvancedSettings
    )
    saml_button_text: str = ""
    saml_allowed_domains: List[str] = Field(default_factory=list)
    saml_enable_jit_provisioning: bool = True
    saml_default_role: Literal["user", "pending"] = "user"
    saml_default_group: str = "default"
    saml_attribute_mapping: Dict[str, str] = Field(default_factory=dict)
    saml_identity_policy: EnterpriseIdentityPolicy = Field(
        default_factory=EnterpriseIdentityPolicy
    )
    enable_oidc: bool = False
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_discovery_url: str | None = None
    oidc_issuer: str | None = None
    oidc_jwks_uri: str | None = None
    oidc_authorization_endpoint: str | None = None
    oidc_token_endpoint: str | None = None
    oidc_userinfo_endpoint: str | None = None
    oidc_scopes: List[str] = Field(
        default_factory=lambda: ["openid", "email", "profile"]
    )
    oidc_advanced_settings: OIDCAdvancedSettings = Field(
        default_factory=OIDCAdvancedSettings
    )
    oidc_button_text: str = ""
    oidc_allowed_domains: List[str] = Field(default_factory=list)
    oidc_enable_jit_provisioning: bool = True
    oidc_default_role: Literal["user", "pending"] = "user"
    oidc_default_group: str = "default"
    oidc_attribute_mapping: Dict[str, str] = Field(default_factory=dict)
    oidc_identity_policy: EnterpriseIdentityPolicy = Field(
        default_factory=EnterpriseIdentityPolicy
    )
    @field_validator(
        "saml_allowed_domains",
        "oidc_allowed_domains",
        mode="before",
    )
    @classmethod
    def normalize_allowed_domains(cls, value: Any) -> list[str]:
        return _normalize_sso_domains(value)

    @field_validator("oidc_scopes", mode="before")
    @classmethod
    def normalize_oidc_scopes(cls, value: Any) -> list[str]:
        return _normalize_oidc_scopes(value)

    @field_validator("saml_attribute_mapping", "oidc_attribute_mapping", mode="before")
    @classmethod
    def normalize_attribute_mappings(cls, value: Any) -> dict[str, str]:
        return _normalize_attribute_mapping(value)

    @field_validator(
        "saml_sso_url",
        "oidc_discovery_url",
        "oidc_issuer",
        "oidc_jwks_uri",
        "oidc_authorization_endpoint",
        "oidc_token_endpoint",
        "oidc_userinfo_endpoint",
        mode="before",
    )
    @classmethod
    def normalize_endpoint_urls(cls, value: Any) -> str | None:
        return _normalize_http_url(value)

login_enterprise_sso_schema = Sections(
    sections=[
        Section(
            title="SCIM 2.0",
            description="Expose a SCIM 2.0 endpoint so your identity provider can provision users and groups in Omlorix.",
            i18n_title="schema_login_enterprise_sso_scim_title",
            i18n_description="schema_login_enterprise_sso_scim_desc",
            fields=[
                FieldSchema(
                    key="enable_scim",
                    label="Enable SCIM 2.0",
                    description="Allow external identity providers to provision and update Omlorix users and groups over the SCIM 2.0 API.",
                    type="boolean",
                ),
                FieldSchema(
                    key="scim_bearer_token",
                    label="SCIM Bearer Token",
                    description="Static bearer token required for SCIM API access. Configure the same value in your identity provider.",
                    type="string",
                    input_type="password",
                    masked_placeholder=True,
                    redact_value=True,
                    placeholder="Enter SCIM bearer token",
                    dependency="enable_scim",
                    dependency_value=True,
                ),
                FieldSchema(
                    key="scim_previous_bearer_token",
                    label="Previous SCIM bearer token",
                    description="Optional previous token accepted during a credential rotation. Clear it after the identity provider uses the new token.",
                    type="string",
                    input_type="password",
                    masked_placeholder=True,
                    redact_value=True,
                    placeholder="Enter previous SCIM bearer token",
                    dependency="enable_scim",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_scim_previous_bearer_token",
                    i18n_description="schema_login_enterprise_sso_scim_previous_bearer_token_desc",
                    i18n_placeholder="schema_login_enterprise_sso_scim_previous_bearer_token_placeholder",
                ),
                FieldSchema(
                    key="scim_link_existing_users_by_email",
                    label="Link existing users by email",
                    description="If a SCIM user already exists locally with the same email, link and update that user instead of rejecting provisioning.",
                    type="boolean",
                    dependency="enable_scim",
                    dependency_value=True,
                ),
                FieldSchema(
                    key="scim_sync_group_memberships",
                    label="Sync SCIM group memberships",
                    description="Track SCIM-managed memberships and map the first SCIM group to the user's active Omlorix group.",
                    type="boolean",
                    dependency="enable_scim",
                    dependency_value=True,
                ),
                FieldSchema(
                    key="scim_default_role",
                    label="Default User Role",
                    description="Fallback role used when the SCIM payload does not provide a supported role mapping.",
                    type="select",
                    options=[
                        {
                            "value": "user",
                            "label": "User",
                            "i18n_label": "schema_option_role_user",
                        },
                        {
                            "value": "pending",
                            "label": "Pending",
                            "i18n_label": "schema_option_role_pending",
                        },
                    ],
                    dependency="enable_scim",
                    dependency_value=True,
                ),
                FieldSchema(
                    key="scim_default_group",
                    label="Default User Group",
                    description="Fallback Omlorix group applied when a SCIM user has no mapped group memberships.",
                    type="select",
                    dependency="enable_scim",
                    dependency_value=True,
                ),
            ],
        ),
        Section(
            title="SAML 2.0",
            description="Configure SAML 2.0 for enterprise SSO (Okta, Azure AD, OneLogin, etc.)",
            i18n_title="schema_login_enterprise_sso_sec1_title",
            i18n_description="schema_login_enterprise_sso_sec1_desc",
            fields=[
                FieldSchema(
                    key="enable_saml",
                    label="Enable SAML SSO",
                    description="Enable SAML 2.0 authentication.",
                    type="boolean",
                    i18n_label="schema_login_enterprise_sso_enable_saml",
                    i18n_description="schema_login_enterprise_sso_enable_saml_desc",
                ),
                FieldSchema(
                    key="saml_entity_id",
                    label="Entity ID (SP Entity ID)",
                    description="Your application's unique identifier for SAML. Example: https://yourapp.com/saml/metadata",
                    type="string",
                    placeholder="https://yourapp.com/saml/metadata",
                    dependency="enable_saml",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_saml_entity_id",
                    i18n_description="schema_login_enterprise_sso_saml_entity_id_desc",
                ),
                FieldSchema(
                    key="saml_sso_url",
                    label="Identity Provider SSO URL",
                    description="The SAML SSO URL provided by your identity provider.",
                    type="string",
                    placeholder="https://idp.example.com/sso/saml",
                    dependency="enable_saml",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_saml_sso_url",
                    i18n_description="schema_login_enterprise_sso_saml_sso_url_desc",
                ),
                FieldSchema(
                    key="saml_x509_cert",
                    label="Identity Provider X.509 Certificate",
                    description="The X.509 certificate from your identity provider for signature verification.",
                    type="string",
                    placeholder="-----BEGIN CERTIFICATE-----\\n...\\n-----END CERTIFICATE-----",
                    dependency="enable_saml",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_saml_x509_cert",
                    i18n_description="schema_login_enterprise_sso_saml_x509_cert_desc",
                ),
                FieldSchema(
                    key="saml_button_text",
                    label="SAML Button Text",
                    description="Text displayed on the SAML sign-in button.",
                    type="string",
                    placeholder="Sign in with SSO",
                    dependency="enable_saml",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_saml_button_text",
                    i18n_description="schema_login_enterprise_sso_saml_button_text_desc",
                ),
                FieldSchema(
                    key="saml_allowed_domains",
                    label="Allowed Email Domains",
                    description="Restrict SAML login to specific email domains. Leave empty to allow all domains.",
                    type="string_list",
                    placeholder="e.g., company.com",
                    dependency="enable_saml",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_saml_allowed_domains",
                    i18n_description="schema_login_enterprise_sso_saml_allowed_domains_desc",
                ),
                FieldSchema(
                    key="saml_enable_jit_provisioning",
                    label="Enable JIT Provisioning",
                    description="Automatically create user accounts on first SAML login (Just-In-Time provisioning).",
                    type="boolean",
                    dependency="enable_saml",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_saml_enable_jit_provisioning",
                    i18n_description="schema_login_enterprise_sso_saml_enable_jit_provisioning_desc",
                ),
                FieldSchema(
                    key="saml_default_role",
                    label="Default User Role",
                    description="Default role for JIT-provisioned users.",
                    type="select",
                    options=[
                        {
                            "value": "user",
                            "label": "User",
                            "i18n_label": "schema_option_role_user",
                        },
                        {
                            "value": "pending",
                            "label": "Pending",
                            "i18n_label": "schema_option_role_pending",
                        },
                    ],
                    dependency="saml_enable_jit_provisioning",
                    dependency_value=True,
                    dependency2="enable_saml",
                    dependency2_value=True,
                    i18n_label="schema_login_enterprise_sso_saml_default_role",
                    i18n_description="schema_login_enterprise_sso_saml_default_role_desc",
                ),
                FieldSchema(
                    key="saml_default_group",
                    label="Default User Group",
                    description="Default group for JIT-provisioned users.",
                    type="select",
                    dependency="saml_enable_jit_provisioning",
                    dependency_value=True,
                    dependency2="enable_saml",
                    dependency2_value=True,
                    i18n_label="schema_login_enterprise_sso_saml_default_group",
                    i18n_description="schema_login_enterprise_sso_saml_default_group_desc",
                ),
                FieldSchema(
                    key="saml_attribute_mapping",
                    label="SAML Attribute Mapping",
                    description="JSON object mapping Omlorix profile fields to SAML attribute names.",
                    type="json",
                    rows=8,
                    dependency="enable_saml",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_saml_attribute_mapping",
                    i18n_description="schema_login_enterprise_sso_saml_attribute_mapping_desc",
                ),
            ],
        ),
        Section(
            title="OpenID Connect",
            description="Configure OpenID Connect for enterprise SSO (custom OIDC providers)",
            i18n_title="schema_login_enterprise_sso_sec2_title",
            i18n_description="schema_login_enterprise_sso_sec2_desc",
            fields=[
                FieldSchema(
                    key="enable_oidc",
                    label="Enable OIDC SSO",
                    description="Enable OpenID Connect authentication.",
                    type="boolean",
                    i18n_label="schema_login_enterprise_sso_enable_oidc",
                    i18n_description="schema_login_enterprise_sso_enable_oidc_desc",
                ),
                FieldSchema(
                    key="oidc_client_id",
                    label="Client ID",
                    description="OAuth 2.0 Client ID from your OIDC provider.",
                    type="string",
                    placeholder="Enter Client ID",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_client_id",
                    i18n_description="schema_login_enterprise_sso_oidc_client_id_desc",
                ),
                FieldSchema(
                    key="oidc_client_secret",
                    label="Client Secret",
                    description="OAuth 2.0 Client Secret from your OIDC provider.",
                    type="string",
                    input_type="password",
                    redact_value=True,
                    masked_placeholder=True,
                    placeholder="Enter Client Secret",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_client_secret",
                    i18n_description="schema_login_enterprise_sso_oidc_client_secret_desc",
                ),
                FieldSchema(
                    key="oidc_discovery_url",
                    label="Discovery URL (Optional)",
                    description="OIDC Discovery endpoint URL (e.g., https://provider.com/.well-known/openid-configuration). If provided, other endpoints will be auto-discovered.",
                    type="string",
                    placeholder="https://provider.com/.well-known/openid-configuration",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_discovery_url",
                    i18n_description="schema_login_enterprise_sso_oidc_discovery_url_desc",
                ),
                FieldSchema(
                    key="oidc_issuer",
                    label="Issuer (Optional)",
                    description="Expected issuer claim for ID token validation. Auto-filled when using Discovery URL.",
                    type="string",
                    placeholder="https://provider.com/",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_issuer",
                    i18n_description="schema_login_enterprise_sso_oidc_issuer_desc",
                ),
                FieldSchema(
                    key="oidc_jwks_uri",
                    label="JWKS URI (Optional)",
                    description="JWKS endpoint for ID token verification. Auto-filled when using Discovery URL.",
                    type="string",
                    placeholder="https://provider.com/.well-known/jwks.json",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_jwks_uri",
                    i18n_description="schema_login_enterprise_sso_oidc_jwks_uri_desc",
                ),
                FieldSchema(
                    key="oidc_authorization_endpoint",
                    label="Authorization Endpoint",
                    description="OAuth 2.0 Authorization endpoint. Only required if not using Discovery URL.",
                    type="string",
                    placeholder="https://provider.com/oauth/authorize",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_authorization_endpoint",
                    i18n_description="schema_login_enterprise_sso_oidc_authorization_endpoint_desc",
                ),
                FieldSchema(
                    key="oidc_token_endpoint",
                    label="Token Endpoint",
                    description="OAuth 2.0 Token endpoint. Only required if not using Discovery URL.",
                    type="string",
                    placeholder="https://provider.com/oauth/token",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_token_endpoint",
                    i18n_description="schema_login_enterprise_sso_oidc_token_endpoint_desc",
                ),
                FieldSchema(
                    key="oidc_userinfo_endpoint",
                    label="UserInfo Endpoint (Optional)",
                    description="OIDC UserInfo endpoint for fetching user profile data.",
                    type="string",
                    placeholder="https://provider.com/oauth/userinfo",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_userinfo_endpoint",
                    i18n_description="schema_login_enterprise_sso_oidc_userinfo_endpoint_desc",
                ),
                FieldSchema(
                    key="oidc_scopes",
                    label="OIDC Scopes",
                    description="Scopes requested during OIDC login. The required openid scope cannot be removed.",
                    type="string_list",
                    placeholder="openid",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_scopes",
                    i18n_description="schema_login_enterprise_sso_oidc_scopes_desc",
                ),
                FieldSchema(
                    key="oidc_attribute_mapping",
                    label="OIDC Attribute Mapping",
                    description="JSON object mapping first_name, last_name, and display_name to OIDC claim names. Identity and verified email claims cannot be remapped.",
                    type="json",
                    rows=8,
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_attribute_mapping",
                    i18n_description="schema_login_enterprise_sso_oidc_attribute_mapping_desc",
                ),
                FieldSchema(
                    key="oidc_button_text",
                    label="OIDC Button Text",
                    description="Text displayed on the OIDC sign-in button.",
                    type="string",
                    placeholder="Sign in with SSO",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_button_text",
                    i18n_description="schema_login_enterprise_sso_oidc_button_text_desc",
                ),
                FieldSchema(
                    key="oidc_allowed_domains",
                    label="Allowed Email Domains",
                    description="Restrict OIDC login to specific email domains. Leave empty to allow all domains.",
                    type="string_list",
                    placeholder="e.g., company.com",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_allowed_domains",
                    i18n_description="schema_login_enterprise_sso_oidc_allowed_domains_desc",
                ),
                FieldSchema(
                    key="oidc_enable_jit_provisioning",
                    label="Enable JIT Provisioning",
                    description="Automatically create user accounts on first OIDC login (Just-In-Time provisioning).",
                    type="boolean",
                    dependency="enable_oidc",
                    dependency_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_enable_jit_provisioning",
                    i18n_description="schema_login_enterprise_sso_oidc_enable_jit_provisioning_desc",
                ),
                FieldSchema(
                    key="oidc_default_role",
                    label="Default User Role",
                    description="Default role for JIT-provisioned users.",
                    type="select",
                    options=[
                        {
                            "value": "user",
                            "label": "User",
                            "i18n_label": "schema_option_role_user",
                        },
                        {
                            "value": "pending",
                            "label": "Pending",
                            "i18n_label": "schema_option_role_pending",
                        },
                    ],
                    dependency="oidc_enable_jit_provisioning",
                    dependency_value=True,
                    dependency2="enable_oidc",
                    dependency2_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_default_role",
                    i18n_description="schema_login_enterprise_sso_oidc_default_role_desc",
                ),
                FieldSchema(
                    key="oidc_default_group",
                    label="Default User Group",
                    description="Default group for JIT-provisioned users.",
                    type="select",
                    dependency="oidc_enable_jit_provisioning",
                    dependency_value=True,
                    dependency2="enable_oidc",
                    dependency2_value=True,
                    i18n_label="schema_login_enterprise_sso_oidc_default_group",
                    i18n_description="schema_login_enterprise_sso_oidc_default_group_desc",
                ),
            ],
        ),
    ],
)


def _advanced_enterprise_field(
    *,
    key: str,
    label: str,
    description: str,
    field_type: str,
    provider_toggle: str,
    i18n_label: str,
    i18n_description: str,
    input_type: str | None = None,
    redact_value: bool = False,
) -> FieldSchema:
    """Build a consistently gated advanced federation setting control."""

    return FieldSchema(
        key=key,
        label=label,
        description=description,
        type=field_type,
        input_type=input_type,
        redact_value=redact_value,
        masked_placeholder=redact_value,
        dependency=provider_toggle,
        dependency_value=True,
        i18n_label=i18n_label,
        i18n_description=i18n_description,
    )


# Advanced controls belong to the provider they configure. Keeping them inside
# the SAML and OIDC subsections avoids a fourth top-level concept and makes the
# page follow the operator's mental model: provider, then provisioning.
_enterprise_sections_by_title = {
    section.title: section for section in login_enterprise_sso_schema.sections
}
_saml_section = _enterprise_sections_by_title["SAML 2.0"]
_oidc_section = _enterprise_sections_by_title["OpenID Connect"]
_scim_section = _enterprise_sections_by_title["SCIM 2.0"]

_saml_section.fields.extend(
    [
        _advanced_enterprise_field(
            key="saml_advanced_settings",
            label="SAML security settings",
            description="Validated JSON object for the IdP entity ID, NameID format, signing-certificate rotation, and optional signed AuthnRequests.",
            field_type="json",
            provider_toggle="enable_saml",
            i18n_label="schema_login_enterprise_sso_saml_advanced_settings",
            i18n_description="schema_login_enterprise_sso_saml_advanced_settings_desc",
        ),
        _advanced_enterprise_field(
            key="saml_identity_policy",
            label="SAML identity policy",
            description="Validated JSON object for account linking, profile sync, required groups, and group-to-Omlorix mappings.",
            field_type="json",
            provider_toggle="enable_saml",
            i18n_label="schema_login_enterprise_sso_saml_identity_policy",
            i18n_description="schema_login_enterprise_sso_saml_identity_policy_desc",
        ),
    ]
)
_oidc_section.fields.extend(
    [
        _advanced_enterprise_field(
            key="oidc_advanced_settings",
            label="OIDC protocol settings",
            description="Validated JSON object for token-endpoint client authentication and the optional authorization prompt.",
            field_type="json",
            provider_toggle="enable_oidc",
            i18n_label="schema_login_enterprise_sso_oidc_advanced_settings",
            i18n_description="schema_login_enterprise_sso_oidc_advanced_settings_desc",
        ),
        _advanced_enterprise_field(
            key="oidc_identity_policy",
            label="OIDC identity policy",
            description="Validated JSON object for account linking, profile sync, required groups, and group-to-Omlorix mappings.",
            field_type="json",
            provider_toggle="enable_oidc",
            i18n_label="schema_login_enterprise_sso_oidc_identity_policy",
            i18n_description="schema_login_enterprise_sso_oidc_identity_policy_desc",
        ),
    ]
)

for _section in (_saml_section, _oidc_section):
    _section.group_title = "Identity Providers"
    _section.i18n_group_title = (
        "schema_login_enterprise_sso_group_identity_providers"
    )

_scim_section.group_title = "User Provisioning"
_scim_section.i18n_group_title = (
    "schema_login_enterprise_sso_group_user_provisioning"
)
# The rendered order is intentional and mirrors the page hierarchy shown to
# administrators. Sections remain flat for persistence and autosave behavior.
login_enterprise_sso_schema.sections = [
    _saml_section,
    _oidc_section,
    _scim_section,
]
