"""Schemas for general login and password-policy settings."""

from typing import List, Literal

from app.auth.password_policy import MINIMUM_SECURE_PASSWORD_LENGTH
from app.settings.defaults import DEFAULT_SETTINGS
from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, Field, model_validator


_LOGIN_GENERAL_DEFAULTS = DEFAULT_SETTINGS["login_general"]


class LoginGeneralSettings(BaseModel):
    enable_signin: bool = _LOGIN_GENERAL_DEFAULTS["enable_signin"]
    enable_signup: bool = _LOGIN_GENERAL_DEFAULTS["enable_signup"]
    enable_password_reset: bool = _LOGIN_GENERAL_DEFAULTS["enable_password_reset"]
    enable_2fa: bool = _LOGIN_GENERAL_DEFAULTS["enable_2fa"]
    force_2fa: bool = _LOGIN_GENERAL_DEFAULTS["force_2fa"]
    twofa_provider: Literal["totp", "email"] = _LOGIN_GENERAL_DEFAULTS["twofa_provider"]
    otp_length: int = _LOGIN_GENERAL_DEFAULTS["otp_length"]
    otp_ttl_seconds: int = _LOGIN_GENERAL_DEFAULTS["otp_ttl_seconds"]
    otp_resend_cooldown_seconds: int = _LOGIN_GENERAL_DEFAULTS[
        "otp_resend_cooldown_seconds"
    ]
    otp_max_attempts: int = _LOGIN_GENERAL_DEFAULTS["otp_max_attempts"]
    email_from_address: str = _LOGIN_GENERAL_DEFAULTS["email_from_address"]
    smtp_host: str = _LOGIN_GENERAL_DEFAULTS["smtp_host"]
    smtp_port: int = _LOGIN_GENERAL_DEFAULTS["smtp_port"]
    smtp_username: str = _LOGIN_GENERAL_DEFAULTS["smtp_username"]
    smtp_password: str = _LOGIN_GENERAL_DEFAULTS["smtp_password"]
    smtp_use_tls: bool = _LOGIN_GENERAL_DEFAULTS["smtp_use_tls"]
    smtp_use_ssl: bool = _LOGIN_GENERAL_DEFAULTS["smtp_use_ssl"]
    enable_passkeys: bool = _LOGIN_GENERAL_DEFAULTS.get("enable_passkeys", True)
    specific_signup_domain: List[str] = Field(
        default_factory=lambda: list(_LOGIN_GENERAL_DEFAULTS["specific_signup_domain"])
    )
    contact_support_email: str = _LOGIN_GENERAL_DEFAULTS["contact_support_email"]
    default_user_role: Literal["user", "pending"] = _LOGIN_GENERAL_DEFAULTS[
        "default_user_role"
    ]
    default_user_group: str = _LOGIN_GENERAL_DEFAULTS["default_user_group"]
    minimum_password_length: int = Field(
        default=_LOGIN_GENERAL_DEFAULTS["minimum_password_length"],
        ge=MINIMUM_SECURE_PASSWORD_LENGTH,
    )
    minimum_special_characters: int = _LOGIN_GENERAL_DEFAULTS[
        "minimum_special_characters"
    ]
    minimum_uppercase_characters: int = _LOGIN_GENERAL_DEFAULTS[
        "minimum_uppercase_characters"
    ]
    minimum_lowercase_characters: int = _LOGIN_GENERAL_DEFAULTS[
        "minimum_lowercase_characters"
    ]
    minimum_number_characters: int = _LOGIN_GENERAL_DEFAULTS[
        "minimum_number_characters"
    ]
    show_privacy_notice_link: bool = _LOGIN_GENERAL_DEFAULTS["show_privacy_notice_link"]
    show_terms_of_service_link: bool = _LOGIN_GENERAL_DEFAULTS[
        "show_terms_of_service_link"
    ]
    enforce_terms_of_service_signup_acceptance: bool = _LOGIN_GENERAL_DEFAULTS.get(
        "enforce_terms_of_service_signup_acceptance", False
    )
    enforce_terms_of_service_access_acceptance: bool = _LOGIN_GENERAL_DEFAULTS.get(
        "enforce_terms_of_service_access_acceptance", False
    )

    @model_validator(mode="after")
    def disable_forced_enrollment_when_two_factor_authentication_is_disabled(self):
        """Keep the dependent enrollment policy off when its master switch is off."""
        if not self.enable_2fa:
            self.force_2fa = False
        return self


login_general_schema = Sections(
    sections=[
        Section(
            title="Authentication Entry Points",
            description="Control which authentication touchpoints are available to end users.",
            i18n_title="schema_login_general_sec0_title",
            i18n_description="schema_login_general_sec0_desc",
            fields=[
                FieldSchema(
                    key="enable_signin",
                    label="Enable sign-in for users",
                    description="Allow users to sign in with their credentials. This also accounts for possbile social login options. Admins can always sign in regardless of this setting.",
                    type="boolean",
                    i18n_label="schema_login_general_enable_signin",
                    i18n_description="schema_login_general_enable_signin_desc",
                ),
                FieldSchema(
                    key="enable_signup",
                    label="Enable sign-up",
                    description="Allow new users to create accounts. When disabled, new account creation is blocked for email/password, social login, and enterprise SSO JIT provisioning.",
                    type="boolean",
                    i18n_label="schema_login_general_enable_signup",
                    i18n_description="schema_login_general_enable_signup_desc",
                ),
                FieldSchema(
                    key="enable_password_reset",
                    label="Enable password reset",
                    description="Offer password reset links on the login page. The email settings below on this page must also be configured for this to work.",
                    type="boolean",
                    i18n_label="schema_login_general_enable_password_reset",
                    i18n_description="schema_login_general_enable_password_reset_desc",
                ),
                FieldSchema(
                    key="enable_2fa",
                    label="Enable two-factor authentication",
                    description="Require users to complete a second authentication factor during sign-in.",
                    type="boolean",
                    i18n_label="schema_login_general_enable_2fa",
                    i18n_description="schema_login_general_enable_2fa_desc",
                ),
                FieldSchema(
                    key="force_2fa",
                    label="Force 2FA enrollment",
                    description="Users must enroll in 2FA before they can continue after sign-in.",
                    type="boolean",
                    dependency="enable_2fa",
                    dependency_value=True,
                    i18n_label="schema_login_general_force_2fa",
                    i18n_description="schema_login_general_force_2fa_desc",
                ),
                FieldSchema(
                    key="twofa_provider",
                    label="2FA provider",
                    description="Delivery channel used for two-factor authentication.",
                    type="select",
                    options=[
                        {
                            "value": "totp",
                            "label": "Authenticator App (TOTP)",
                            "i18n_label": "schema_group_option_settings_login_2fa_2fa_provider_totp",
                        },
                        {
                            "value": "email",
                            "label": "Email OTP",
                            "i18n_label": "schema_group_option_settings_login_2fa_2fa_provider_email",
                        },
                    ],
                    dependency="enable_2fa",
                    dependency_value=True,
                    i18n_label="schema_login_general_2fa_provider",
                    i18n_description="schema_login_general_2fa_provider_desc",
                ),
                FieldSchema(
                    key="otp_length",
                    label="OTP length",
                    description="Number of digits for email/SMS OTP codes.",
                    type="number",
                    attributes={"min": 4, "max": 10},
                    dependency="enable_2fa",
                    dependency_value=True,
                    dependency2="twofa_provider",
                    dependency2_value="email",
                    i18n_label="schema_login_general_otp_length",
                    i18n_description="schema_login_general_otp_length_desc",
                ),
                FieldSchema(
                    key="otp_ttl_seconds",
                    label="OTP lifetime (seconds)",
                    description="How long an OTP remains valid.",
                    type="number",
                    attributes={"min": 60, "max": 3600},
                    dependency="enable_2fa",
                    dependency_value=True,
                    dependency2="twofa_provider",
                    dependency2_value="email",
                    i18n_label="schema_login_general_otp_ttl_seconds",
                    i18n_description="schema_login_general_otp_ttl_seconds_desc",
                ),
                FieldSchema(
                    key="otp_resend_cooldown_seconds",
                    label="Resend cooldown (seconds)",
                    description="Minimum delay before requesting another OTP.",
                    type="number",
                    attributes={"min": 5, "max": 600},
                    dependency="enable_2fa",
                    dependency_value=True,
                    dependency2="twofa_provider",
                    dependency2_value="email",
                    i18n_label="schema_login_general_otp_resend_cooldown_seconds",
                    i18n_description="schema_login_general_otp_resend_cooldown_seconds_desc",
                ),
                FieldSchema(
                    key="otp_max_attempts",
                    label="Max OTP attempts",
                    description="Maximum number of wrong OTP entries before challenge invalidation.",
                    type="number",
                    attributes={"min": 1, "max": 20},
                    dependency="enable_2fa",
                    dependency_value=True,
                    dependency2="twofa_provider",
                    dependency2_value="email",
                    i18n_label="schema_login_general_otp_max_attempts",
                    i18n_description="schema_login_general_otp_max_attempts_desc",
                ),
                FieldSchema(
                    key="contact_support_email",
                    label="Support email",
                    description="Email address shown when users need help signing in.",
                    type="string",
                    placeholder="E.g. support@example.com",
                    i18n_label="schema_login_general_contact_support_email",
                    i18n_description="schema_login_general_contact_support_email_desc",
                    i18n_placeholder="schema_login_general_contact_support_email_placeholder",
                ),
                FieldSchema(
                    key="show_privacy_notice_link",
                    label="Show privacy notice link",
                    description="Display a privacy notice link on the login page footer.",
                    type="boolean",
                    i18n_label="schema_login_general_show_privacy_notice_link",
                    i18n_description="schema_login_general_show_privacy_notice_link_desc",
                ),
                FieldSchema(
                    key="show_terms_of_service_link",
                    label="Show terms of service link",
                    description="Display a terms of service link on the login page footer.",
                    type="boolean",
                    i18n_label="schema_login_general_show_terms_of_service_link",
                    i18n_description="schema_login_general_show_terms_of_service_link_desc",
                ),
                FieldSchema(
                    key="enforce_terms_of_service_signup_acceptance",
                    label="Require terms acceptance during signup",
                    description="Require new signups to accept the current Terms of Service before the account is created.",
                    type="boolean",
                    dependency="enable_signup",
                    dependency_value=True,
                    i18n_label="schema_login_general_enforce_terms_of_service_signup_acceptance",
                    i18n_description="schema_login_general_enforce_terms_of_service_signup_acceptance_desc",
                ),
                FieldSchema(
                    key="enforce_terms_of_service_access_acceptance",
                    label="Block app access until terms are accepted",
                    description="Require signed-in users to accept the current Terms of Service revision before using authenticated app routes.",
                    type="boolean",
                    i18n_label="schema_login_general_enforce_terms_of_service_access_acceptance",
                    i18n_description="schema_login_general_enforce_terms_of_service_access_acceptance_desc",
                ),
            ],
        ),
        Section(
            title="Passkeys (WebAuthn)",
            description="Enable passkeys for modern phishing-resistant authentication.",
            i18n_title="schema_login_passkeys_sec0_title",
            i18n_description="schema_login_passkeys_sec0_desc",
            fields=[
                FieldSchema(
                    key="enable_passkeys",
                    label="Enable passkeys",
                    description="Allow users to sign in using passkeys (WebAuthn).",
                    type="boolean",
                    i18n_label="schema_login_passkeys_enable_passkeys",
                    i18n_description="schema_login_passkeys_enable_passkeys_desc",
                ),
            ],
        ),
        Section(
            title="Email delivery",
            description="Central email settings used for password reset and email-based 2FA.",
            i18n_title="schema_login_general_email_delivery_sec_title",
            i18n_description="schema_login_general_email_delivery_sec_desc",
            fields=[
                FieldSchema(
                    key="email_from_address",
                    label="From address",
                    description="Sender address used for password reset and email OTP messages.",
                    type="string",
                    placeholder="E.g. no-reply@example.com",
                    i18n_label="schema_login_general_email_from_address",
                    i18n_description="schema_login_general_email_from_address_desc",
                    i18n_placeholder="schema_login_general_email_from_address_placeholder",
                ),
                FieldSchema(
                    key="smtp_host",
                    label="SMTP host",
                    description="SMTP server hostname.",
                    type="string",
                    placeholder="E.g. smtp.example.com",
                    i18n_label="schema_login_general_smtp_host",
                    i18n_description="schema_login_general_smtp_host_desc",
                    i18n_placeholder="schema_login_general_smtp_host_placeholder",
                ),
                FieldSchema(
                    key="smtp_port",
                    label="SMTP port",
                    description="SMTP server port.",
                    type="number",
                    attributes={"min": 1, "max": 65535},
                    i18n_label="schema_login_general_smtp_port",
                    i18n_description="schema_login_general_smtp_port_desc",
                ),
                FieldSchema(
                    key="smtp_username",
                    label="SMTP username",
                    description="SMTP username (optional if relay allows anonymous).",
                    type="string",
                    placeholder="E.g. smtp-user",
                    i18n_label="schema_login_general_smtp_username",
                    i18n_description="schema_login_general_smtp_username_desc",
                    i18n_placeholder="schema_login_general_smtp_username_placeholder",
                ),
                FieldSchema(
                    key="smtp_password",
                    label="SMTP password",
                    description="SMTP password for authenticated delivery.",
                    type="string",
                    input_type="password",
                    redact_value=True,
                    masked_placeholder=True,
                    i18n_label="schema_login_general_smtp_password",
                    i18n_description="schema_login_general_smtp_password_desc",
                    i18n_placeholder="schema_login_general_smtp_password_placeholder",
                ),
                FieldSchema(
                    key="smtp_use_tls",
                    label="Use SMTP STARTTLS",
                    description="Upgrade plaintext SMTP connections using STARTTLS.",
                    type="boolean",
                    i18n_label="schema_login_general_smtp_use_tls",
                    i18n_description="schema_login_general_smtp_use_tls_desc",
                ),
                FieldSchema(
                    key="smtp_use_ssl",
                    label="Use SMTP SSL",
                    description="Use SMTPS/SSL directly instead of STARTTLS.",
                    type="boolean",
                    i18n_label="schema_login_general_smtp_use_ssl",
                    i18n_description="schema_login_general_smtp_use_ssl_desc",
                ),
            ],
        ),
        Section(
            title="Registration Defaults & Restrictions",
            description="Configure who can register and which defaults new accounts receive.",
            i18n_title="schema_login_general_sec1_title",
            i18n_description="schema_login_general_sec1_desc",
            fields=[
                FieldSchema(
                    key="specific_signup_domain",
                    label="Allowed sign-up domains",
                    description="Restrict email registrations to specific domains. Leave empty to allow all domains.",
                    type="string_list",
                    placeholder="E.g. example.com",
                    i18n_label="schema_login_general_specific_signup_domain",
                    i18n_description="schema_login_general_specific_signup_domain_desc",
                ),
                FieldSchema(
                    key="default_user_role",
                    label="Default user role",
                    description="Role assigned to newly created accounts.",
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
                    i18n_label="schema_login_general_default_user_role",
                    i18n_description="schema_login_general_default_user_role_desc",
                ),
            ],
        ),
        Section(
            title="Password Policy Requirements",
            description="Define the minimum password complexity enforced during signup.",
            i18n_title="schema_login_general_sec2_title",
            i18n_description="schema_login_general_sec2_desc",
            fields=[
                FieldSchema(
                    key="minimum_password_length",
                    label="Minimum password length",
                    description="Shortest password allowed during registration.",
                    type="number",
                    attributes={"min": MINIMUM_SECURE_PASSWORD_LENGTH},
                    i18n_label="schema_login_general_minimum_password_length",
                    i18n_description="schema_login_general_minimum_password_length_desc",
                ),
                FieldSchema(
                    key="minimum_special_characters",
                    label="Minimum special characters",
                    description="Required number of special characters in passwords.",
                    type="number",
                    attributes={"min": 0},
                    i18n_label="schema_login_general_minimum_special_characters",
                    i18n_description="schema_login_general_minimum_special_characters_desc",
                ),
                FieldSchema(
                    key="minimum_uppercase_characters",
                    label="Minimum uppercase characters",
                    description="Required number of uppercase letters in passwords.",
                    type="number",
                    attributes={"min": 0},
                    i18n_label="schema_login_general_minimum_uppercase_characters",
                    i18n_description="schema_login_general_minimum_uppercase_characters_desc",
                ),
                FieldSchema(
                    key="minimum_lowercase_characters",
                    label="Minimum lowercase characters",
                    description="Required number of lowercase letters in passwords.",
                    type="number",
                    attributes={"min": 0},
                    i18n_label="schema_login_general_minimum_lowercase_characters",
                    i18n_description="schema_login_general_minimum_lowercase_characters_desc",
                ),
                FieldSchema(
                    key="minimum_number_characters",
                    label="Minimum number characters",
                    description="Required number of digits in passwords.",
                    type="number",
                    attributes={"min": 0},
                    i18n_label="schema_login_general_minimum_number_characters",
                    i18n_description="schema_login_general_minimum_number_characters_desc",
                ),
            ],
        ),
    ],
)
