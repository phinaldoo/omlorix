"""Schemas for LDAP login settings."""

from typing import List, Literal
from urllib.parse import urlsplit

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field, field_validator


_LDAP_TRANSPORT_SCHEMES = {"ldap", "ldaps", "ldap+starttls"}


def _normalize_ldap_server_uris(value: object) -> list[str]:
    """Validate and canonicalize LDAP endpoints without ambiguous TLS flags.

    ``ldap+starttls`` is an Omlorix configuration scheme. It is converted to a
    normal LDAP connection before ldap3 is called, then upgraded before bind.
    Keeping the transport in each endpoint prevents the URI and boolean flags
    from disagreeing about whether credentials are protected.
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("LDAP server URIs must be a list")

    normalized: list[str] = []
    seen: set[str] = set()
    selected_scheme: str | None = None
    for raw_value in value:
        if not isinstance(raw_value, str):
            raise ValueError("LDAP server URI entries must be strings")
        endpoint = raw_value.strip()
        if not endpoint:
            continue
        parsed = urlsplit(endpoint)
        scheme = parsed.scheme.lower()
        if scheme not in _LDAP_TRANSPORT_SCHEMES:
            raise ValueError(
                "LDAP server URIs must use ldap://, ldaps://, or ldap+starttls://"
            )
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(
                "LDAP server URIs must contain a host and must not contain credentials"
            )
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError(
                "LDAP server URIs must not contain a path, query, or fragment"
            )
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("LDAP server URI contains an invalid port") from exc

        if selected_scheme is None:
            selected_scheme = scheme
        elif selected_scheme != scheme:
            raise ValueError(
                "All LDAP failover endpoints must use the same transport scheme"
            )

        host = parsed.hostname.lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        canonical = f"{scheme}://{host}"
        if port is not None:
            canonical += f":{port}"
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    if len(normalized) > 10:
        raise ValueError("At most 10 LDAP failover endpoints may be configured")
    return normalized


class LoginLDAPSettings(BaseModel):
    enable_ldap: bool = False
    ldap_enable_group_sync: bool = False
    ldap_label: str = "Directory Sign-In"
    ldap_identifier_hint: str = "Email or directory login"
    ldap_server_uris: List[str] = Field(default_factory=list)
    ldap_allow_insecure_plaintext_bind: bool = False
    ldap_validate_cert: bool = True
    ldap_ca_cert_file: str | None = None
    ldap_connect_timeout_seconds: int = Field(default=10, ge=1, le=120)
    ldap_receive_timeout_seconds: int = Field(default=10, ge=1, le=120)
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None
    ldap_user_base_dn: str | None = None
    ldap_user_filter: str = (
        "(&(objectClass=person)(|(mail={identifier})(uid={identifier})"
        "(sAMAccountName={identifier})(userPrincipalName={identifier})))"
    )
    ldap_user_search_scope: Literal["base", "level", "subtree"] = "subtree"
    ldap_email_attribute: str = "mail"
    ldap_first_name_attribute: str = "givenName"
    ldap_last_name_attribute: str = "sn"
    ldap_display_name_attribute: str = "displayName"
    ldap_username_attribute: str = "uid"
    ldap_user_id_attribute: str = "entryUUID"
    ldap_group_source: Literal["memberOf", "search"] = "memberOf"
    ldap_group_attribute: str = "memberOf"
    ldap_group_base_dn: str | None = None
    ldap_group_filter: str = "(&(objectClass=group)(member={user_dn}))"
    ldap_group_search_scope: Literal["base", "level", "subtree"] = "subtree"
    ldap_group_name_attribute: str = "cn"
    ldap_required_groups: List[str] = Field(default_factory=list)
    ldap_group_to_app_group: List[str] = Field(default_factory=list)
    ldap_group_to_role: List[str] = Field(default_factory=list)
    ldap_enable_jit_provisioning: bool = True
    ldap_link_existing_users_by_email: bool = True
    ldap_sync_profile_on_login: bool = True
    ldap_sync_email_on_login: bool = True
    ldap_sync_app_group_on_login: bool = True
    ldap_sync_role_on_login: bool = True
    ldap_default_role: Literal["user", "pending"] = "user"
    ldap_default_group: str = "default"

    @field_validator("ldap_server_uris", mode="before")
    @classmethod
    def normalize_server_uris(cls, value: object) -> list[str]:
        """Normalize the ordered LDAP endpoint pool."""

        return _normalize_ldap_server_uris(value)


login_ldap_schema = Sections(
    sections=[
        Section(
            title="LDAP Authentication",
            description="Enable direct LDAP or Active Directory credential authentication.",
            i18n_title="schema_login_ldap_sec0_title",
            i18n_description="schema_login_ldap_sec0_desc",
            fields=[
                FieldSchema(
                    key="enable_ldap",
                    label="Enable LDAP login",
                    description="Allow users to sign in with credentials verified against your LDAP directory.",
                    type="boolean",
                    i18n_label="schema_login_ldap_enable_ldap",
                    i18n_description="schema_login_ldap_enable_ldap_desc",
                ),
                FieldSchema(
                    key="ldap_enable_group_sync",
                    label="Enable group synchronization",
                    description="Show LDAP group settings and allow group-based rules and mappings.",
                    type="boolean",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_enable_group_sync",
                    i18n_description="schema_login_ldap_ldap_enable_group_sync_desc",
                ),
                FieldSchema(
                    key="ldap_label",
                    label="Login label",
                    description="Name shown to end users when LDAP sign-in hints are displayed on the login page.",
                    type="string",
                    placeholder="Directory Sign-In",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_label",
                    i18n_description="schema_login_ldap_ldap_label_desc",
                    i18n_placeholder="schema_login_ldap_ldap_label_placeholder",
                ),
                FieldSchema(
                    key="ldap_identifier_hint",
                    label="Identifier hint",
                    description="Help text for the normal sign-in form when LDAP is enabled.",
                    type="string",
                    placeholder="Email or directory login",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_identifier_hint",
                    i18n_description="schema_login_ldap_ldap_identifier_hint_desc",
                    i18n_placeholder="schema_login_ldap_ldap_identifier_hint_placeholder",
                ),
            ],
        ),
        Section(
            title="Connection & TLS",
            description="Configure how Omlorix connects securely to your LDAP server.",
            i18n_title="schema_login_ldap_sec1_title",
            i18n_description="schema_login_ldap_sec1_desc",
            fields=[
                FieldSchema(
                    key="ldap_server_uris",
                    label="Server URIs",
                    description="Ordered LDAP endpoints used for failover. Use ldaps:// for immediate TLS, ldap+starttls:// for StartTLS, or ldap:// only with the insecure override.",
                    type="string_list",
                    placeholder="ldaps://ldap.example.com:636",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_server_uris",
                    i18n_description="schema_login_ldap_ldap_server_uris_desc",
                ),
                FieldSchema(
                    key="ldap_allow_insecure_plaintext_bind",
                    label="Allow insecure plaintext LDAP bind",
                    description="Admin override for testing only. When enabled, Omlorix may send LDAP bind credentials without LDAPS or StartTLS.",
                    type="boolean",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_allow_insecure_plaintext_bind",
                    i18n_description="schema_login_ldap_ldap_allow_insecure_plaintext_bind_desc",
                ),
                FieldSchema(
                    key="ldap_validate_cert",
                    label="Validate server certificate",
                    description="Verify the LDAP server certificate when TLS is enabled.",
                    type="boolean",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_validate_cert",
                    i18n_description="schema_login_ldap_ldap_validate_cert_desc",
                ),
                FieldSchema(
                    key="ldap_ca_cert_file",
                    label="CA certificate file",
                    description="Optional CA certificate bundle path inside the backend container.",
                    type="string",
                    placeholder="/etc/ssl/certs/internal-ca.pem",
                    dependency="ldap_validate_cert",
                    dependency_value=True,
                    dependency2="enable_ldap",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_ca_cert_file",
                    i18n_description="schema_login_ldap_ldap_ca_cert_file_desc",
                ),
                FieldSchema(
                    key="ldap_connect_timeout_seconds",
                    label="Connect timeout (seconds)",
                    description="Maximum time to wait while opening the TCP connection.",
                    type="number",
                    attributes={"min": 1, "max": 120},
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_connect_timeout_seconds",
                    i18n_description="schema_login_ldap_ldap_connect_timeout_seconds_desc",
                ),
                FieldSchema(
                    key="ldap_receive_timeout_seconds",
                    label="Receive timeout (seconds)",
                    description="Maximum time to wait for LDAP responses after the connection is established.",
                    type="number",
                    attributes={"min": 1, "max": 120},
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_receive_timeout_seconds",
                    i18n_description="schema_login_ldap_ldap_receive_timeout_seconds_desc",
                ),
            ],
        ),
        Section(
            title="User Search",
            description="Define how Omlorix finds user entries and reads profile attributes.",
            i18n_title="schema_login_ldap_sec2_title",
            i18n_description="schema_login_ldap_sec2_desc",
            fields=[
                FieldSchema(
                    key="ldap_bind_dn",
                    label="Bind DN",
                    description="Optional service account DN used for user and group searches. Leave empty for anonymous search.",
                    type="string",
                    placeholder="cn=omlorix,ou=service accounts,dc=example,dc=com",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_bind_dn",
                    i18n_description="schema_login_ldap_ldap_bind_dn_desc",
                ),
                FieldSchema(
                    key="ldap_bind_password",
                    label="Bind password",
                    description="Password for the LDAP bind account.",
                    type="string",
                    input_type="password",
                    redact_value=True,
                    masked_placeholder=True,
                    placeholder="Enter bind password",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_bind_password",
                    i18n_description="schema_login_ldap_ldap_bind_password_desc",
                    i18n_placeholder="schema_login_ldap_ldap_bind_password_placeholder",
                ),
                FieldSchema(
                    key="ldap_user_base_dn",
                    label="User base DN",
                    description="Base DN used to search for user entries.",
                    type="string",
                    placeholder="ou=people,dc=example,dc=com",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_user_base_dn",
                    i18n_description="schema_login_ldap_ldap_user_base_dn_desc",
                ),
                FieldSchema(
                    key="ldap_user_filter",
                    label="User search filter",
                    description="LDAP filter used to find the authenticating user. Supported placeholders: {identifier}, {email}, {username}.",
                    type="string",
                    placeholder="(&(objectClass=person)(uid={identifier}))",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_user_filter",
                    i18n_description="schema_login_ldap_ldap_user_filter_desc",
                ),
                FieldSchema(
                    key="ldap_user_search_scope",
                    label="User search scope",
                    description="How deep user searches should recurse below the base DN.",
                    type="select",
                    options=[
                        {
                            "value": "base",
                            "label": "Base object only",
                            "i18n_label": "schema_login_ldap_ldap_user_search_scope_base",
                        },
                        {
                            "value": "level",
                            "label": "One level",
                            "i18n_label": "schema_login_ldap_ldap_user_search_scope_level",
                        },
                        {
                            "value": "subtree",
                            "label": "Whole subtree",
                            "i18n_label": "schema_login_ldap_ldap_user_search_scope_subtree",
                        },
                    ],
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_user_search_scope",
                    i18n_description="schema_login_ldap_ldap_user_search_scope_desc",
                ),
                FieldSchema(
                    key="ldap_email_attribute",
                    label="Email attribute",
                    description="Directory attribute that contains the user's email address.",
                    type="string",
                    placeholder="mail",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_email_attribute",
                    i18n_description="schema_login_ldap_ldap_email_attribute_desc",
                ),
                FieldSchema(
                    key="ldap_first_name_attribute",
                    label="First name attribute",
                    description="Directory attribute used for the user's first name.",
                    type="string",
                    placeholder="givenName",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_first_name_attribute",
                    i18n_description="schema_login_ldap_ldap_first_name_attribute_desc",
                ),
                FieldSchema(
                    key="ldap_last_name_attribute",
                    label="Last name attribute",
                    description="Directory attribute used for the user's last name.",
                    type="string",
                    placeholder="sn",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_last_name_attribute",
                    i18n_description="schema_login_ldap_ldap_last_name_attribute_desc",
                ),
                FieldSchema(
                    key="ldap_display_name_attribute",
                    label="Display name attribute",
                    description="Optional display name attribute used as a fallback when first or last names are incomplete.",
                    type="string",
                    placeholder="displayName",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_display_name_attribute",
                    i18n_description="schema_login_ldap_ldap_display_name_attribute_desc",
                ),
                FieldSchema(
                    key="ldap_username_attribute",
                    label="Directory login attribute",
                    description="Directory attribute used as the LDAP login name when binding or searching users.",
                    type="string",
                    placeholder="uid",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_username_attribute",
                    i18n_description="schema_login_ldap_ldap_username_attribute_desc",
                ),
                FieldSchema(
                    key="ldap_user_id_attribute",
                    label="Stable user ID attribute",
                    description="Attribute used to persist the external identity between logins. Use objectGUID, entryUUID, or a similar immutable ID.",
                    type="string",
                    placeholder="entryUUID",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_user_id_attribute",
                    i18n_description="schema_login_ldap_ldap_user_id_attribute_desc",
                ),
            ],
        ),
        Section(
            title="Provisioning & Sync",
            description="Control how LDAP users are created, linked, and updated inside Omlorix.",
            i18n_title="schema_login_ldap_sec3_title",
            i18n_description="schema_login_ldap_sec3_desc",
            fields=[
                FieldSchema(
                    key="ldap_enable_jit_provisioning",
                    label="Enable JIT provisioning",
                    description="Automatically create an Omlorix account when an LDAP user signs in for the first time.",
                    type="boolean",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_enable_jit_provisioning",
                    i18n_description="schema_login_ldap_ldap_enable_jit_provisioning_desc",
                ),
                FieldSchema(
                    key="ldap_link_existing_users_by_email",
                    label="Link existing users by email",
                    description="If a local Omlorix user already exists with the same email, link it to the LDAP identity after a successful directory login.",
                    type="boolean",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_link_existing_users_by_email",
                    i18n_description="schema_login_ldap_ldap_link_existing_users_by_email_desc",
                ),
                FieldSchema(
                    key="ldap_sync_profile_on_login",
                    label="Sync profile on login",
                    description="Update first name and last name from LDAP each time the user signs in.",
                    type="boolean",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_sync_profile_on_login",
                    i18n_description="schema_login_ldap_ldap_sync_profile_on_login_desc",
                ),
                FieldSchema(
                    key="ldap_sync_email_on_login",
                    label="Sync email on login",
                    description="Update linked users' email addresses from LDAP when the target email is available.",
                    type="boolean",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_sync_email_on_login",
                    i18n_description="schema_login_ldap_ldap_sync_email_on_login_desc",
                ),
                FieldSchema(
                    key="ldap_sync_app_group_on_login",
                    label="Sync Omlorix group on login",
                    description="Apply LDAP-to-Omlorix group mappings every time the user signs in.",
                    type="boolean",
                    dependency="enable_ldap",
                    dependency_value=True,
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_sync_app_group_on_login",
                    i18n_description="schema_login_ldap_ldap_sync_app_group_on_login_desc",
                ),
                FieldSchema(
                    key="ldap_sync_role_on_login",
                    label="Sync role on login",
                    description="Apply LDAP-to-role mappings every time the user signs in.",
                    type="boolean",
                    dependency="enable_ldap",
                    dependency_value=True,
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_sync_role_on_login",
                    i18n_description="schema_login_ldap_ldap_sync_role_on_login_desc",
                ),
                FieldSchema(
                    key="ldap_default_role",
                    label="Default role",
                    description="Role assigned when no LDAP role mapping matches.",
                    type="select",
                    options=[
                        {
                            "value": "user",
                            "label": "User",
                            "i18n_label": "schema_login_ldap_ldap_default_role_user",
                        },
                        {
                            "value": "pending",
                            "label": "Pending",
                            "i18n_label": "schema_login_ldap_ldap_default_role_pending",
                        },
                    ],
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_default_role",
                    i18n_description="schema_login_ldap_ldap_default_role_desc",
                ),
                FieldSchema(
                    key="ldap_default_group",
                    label="Default Omlorix group",
                    description="Omlorix group assigned when no LDAP group mapping matches.",
                    type="select",
                    dependency="enable_ldap",
                    dependency_value=True,
                    i18n_label="schema_login_ldap_ldap_default_group",
                    i18n_description="schema_login_ldap_ldap_default_group_desc",
                ),
            ],
        ),
        Section(
            title="Group Synchronization",
            description="Read LDAP groups and map them to Omlorix groups and roles. Mapping entries use the format ldap-group=target.",
            i18n_title="schema_login_ldap_sec4_title",
            i18n_description="schema_login_ldap_sec4_desc",
            fields=[
                FieldSchema(
                    key="ldap_group_source",
                    label="Group source",
                    description="Read memberships directly from the user entry or run a second LDAP search for groups.",
                    type="select",
                    options=[
                        {
                            "value": "memberOf",
                            "label": "User memberOf attribute",
                            "i18n_label": "schema_login_ldap_ldap_group_source_memberof",
                        },
                        {
                            "value": "search",
                            "label": "Group search",
                            "i18n_label": "schema_login_ldap_ldap_group_source_search",
                        },
                    ],
                    dependency="enable_ldap",
                    dependency_value=True,
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_group_source",
                    i18n_description="schema_login_ldap_ldap_group_source_desc",
                ),
                FieldSchema(
                    key="ldap_group_attribute",
                    label="Membership attribute",
                    description="Attribute on the user entry that contains group DNs when using memberOf-based synchronization.",
                    type="string",
                    placeholder="memberOf",
                    dependency="ldap_group_source",
                    dependency_value="memberOf",
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_group_attribute",
                    i18n_description="schema_login_ldap_ldap_group_attribute_desc",
                ),
                FieldSchema(
                    key="ldap_group_base_dn",
                    label="Group base DN",
                    description="Base DN used when searching for group memberships.",
                    type="string",
                    placeholder="ou=groups,dc=example,dc=com",
                    dependency="ldap_group_source",
                    dependency_value="search",
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_group_base_dn",
                    i18n_description="schema_login_ldap_ldap_group_base_dn_desc",
                ),
                FieldSchema(
                    key="ldap_group_filter",
                    label="Group search filter",
                    description="LDAP filter used to find matching groups. Supported placeholders: {user_dn}, {identifier}, {email}.",
                    type="string",
                    placeholder="(&(objectClass=group)(member={user_dn}))",
                    dependency="ldap_group_source",
                    dependency_value="search",
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_group_filter",
                    i18n_description="schema_login_ldap_ldap_group_filter_desc",
                ),
                FieldSchema(
                    key="ldap_group_search_scope",
                    label="Group search scope",
                    description="How deep group searches should recurse below the base DN.",
                    type="select",
                    options=[
                        {
                            "value": "base",
                            "label": "Base object only",
                            "i18n_label": "schema_login_ldap_ldap_group_search_scope_base",
                        },
                        {
                            "value": "level",
                            "label": "One level",
                            "i18n_label": "schema_login_ldap_ldap_group_search_scope_level",
                        },
                        {
                            "value": "subtree",
                            "label": "Whole subtree",
                            "i18n_label": "schema_login_ldap_ldap_group_search_scope_subtree",
                        },
                    ],
                    dependency="ldap_group_source",
                    dependency_value="search",
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_group_search_scope",
                    i18n_description="schema_login_ldap_ldap_group_search_scope_desc",
                ),
                FieldSchema(
                    key="ldap_group_name_attribute",
                    label="Group name attribute",
                    description="Attribute used to derive a simple group name such as cn for mapping and auditing.",
                    type="string",
                    placeholder="cn",
                    dependency="enable_ldap",
                    dependency_value=True,
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_group_name_attribute",
                    i18n_description="schema_login_ldap_ldap_group_name_attribute_desc",
                ),
                FieldSchema(
                    key="ldap_required_groups",
                    label="Required LDAP groups",
                    description="Optional list of allowed LDAP groups. If set, users must belong to at least one of them. Enter either full DNs or simple names.",
                    type="string_list",
                    placeholder="engineering",
                    dependency="enable_ldap",
                    dependency_value=True,
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_required_groups",
                    i18n_description="schema_login_ldap_ldap_required_groups_desc",
                ),
                FieldSchema(
                    key="ldap_group_to_app_group",
                    label="LDAP group to Omlorix group",
                    description="Ordered mapping list. Format each entry as ldap-group=omlorix-group-id.",
                    type="string_list",
                    placeholder="engineering=default",
                    dependency="enable_ldap",
                    dependency_value=True,
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_group_to_app_group",
                    i18n_description="schema_login_ldap_ldap_group_to_app_group_desc",
                ),
                FieldSchema(
                    key="ldap_group_to_role",
                    label="LDAP group to role",
                    description="Ordered mapping list. Format each entry as ldap-group=user or ldap-group=pending.",
                    type="string_list",
                    placeholder="contractors=pending",
                    dependency="enable_ldap",
                    dependency_value=True,
                    dependency2="ldap_enable_group_sync",
                    dependency2_value=True,
                    i18n_label="schema_login_ldap_ldap_group_to_role",
                    i18n_description="schema_login_ldap_ldap_group_to_role_desc",
                ),
            ],
        ),
    ],
)
