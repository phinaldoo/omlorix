from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AccountModeMixin(BaseModel):
    account_mode: str = "primary"
    replace_slot: int | None = Field(default=None, ge=1, le=5)
    return_url: str | None = None


class TermsOfServiceAcceptanceMixin(BaseModel):
    accept_terms_of_service: bool = False
    terms_of_service_revision: int | None = Field(default=None, ge=1)


class FederatedTermsConfirmRequest(TermsOfServiceAcceptanceMixin):
    """Confirm terms acceptance for a pending social or SSO signup."""
    pass

from app.users.schemas import PasswordValue, UserBase, _reject_new_password_edge_whitespace



# -------------------
# Signin Request
# -------------------
class SignInRequest(UserBase, AccountModeMixin, TermsOfServiceAcceptanceMixin):
    otp_code: str | None = None  # TOTP code, optional for first signin attempt
    otp_type: str | None = None # "setup" or "login"
    otp_action: str | None = None  # "setup" | "verify" | "resend"
    otp_destination: str | None = None  # phone for sms setup
    admin_only: bool = False  # When True, only allow admin login (used when signin is disabled for users)


class SignInOptionsRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=320)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)


class PasswordResetValidate(BaseModel):
    token: str | None = Field(default=None, max_length=2048)


class PasswordResetConfirm(BaseModel):
    token: str | None = Field(default=None, max_length=2048)
    new_password: PasswordValue

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return _reject_new_password_edge_whitespace(value)


class EmailChangeTokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class EmailChangeResult(BaseModel):
    status: Literal["success"]
    sessions_revoked: bool = False



# -------------------
# Delete Specific Login Request
# -------------------
class DeleteSpecificLoginRequest(BaseModel):
    auth_id: str | None = None
    


# -------------------
# Setup 2FA Request
# -------------------
class SetupTwofaRequest(BaseModel):
    temp_secret: str | None = None
    otp_code: str | None = None
    otp_action: str | None = None
    otp_destination: str | None = None


class StepUpRequest(BaseModel):
    password: str | None = Field(default=None, max_length=1024)
    otp_code: str | None = Field(default=None, min_length=1, max_length=32)
    passkey_credential: dict | None = None
    expected_challenge: str | None = Field(default=None, max_length=2048)


class StepUpOtpBeginResponse(BaseModel):
    """Describe the enrolled OTP method prepared for step-up verification."""

    status: str
    provider: str
    delivery_hint: str = ""
    resend_available_in_seconds: int = Field(default=0, ge=0)


class StepUpMethodsResponse(BaseModel):
    """Verification methods the current user can use for a security step-up."""

    password: bool
    otp: bool
    passkey: bool
    recent_auth_sufficient: bool = False


class SocialSignInProviderResponse(BaseModel):
    """User-safe state for one configurable social sign-in provider."""

    provider: str
    label: str
    linked: bool
    available: bool
    account_hint: str | None = None
    can_link: bool
    can_unlink: bool
    unlink_blocked_reason: str | None = None


class SignInMethodsResponse(BaseModel):
    """Primary authentication methods available to the current user."""

    password_configured: bool
    passkey_count: int
    providers: list[SocialSignInProviderResponse]
    externally_managed: bool = False
    external_auth_provider: str | None = None


class SocialLinkInitResponse(BaseModel):
    """Authorization redirect returned after a protected link initiation."""

    authorization_url: str


class NativeFederatedInitRequest(AccountModeMixin, TermsOfServiceAcceptanceMixin):
    """Begin a system-browser login that returns to the native app with PKCE."""

    model_config = ConfigDict(extra="forbid")

    code_challenge: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    state: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class NativeSocialLinkInitRequest(BaseModel):
    """Create a browser handoff for a recently stepped-up account link."""

    code_challenge: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    state: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class NativeSocialLinkExchangeRequest(BaseModel):
    """Finish a native social link only after proving the PKCE verifier."""

    code: str = Field(min_length=32, max_length=512)
    code_verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    state: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class NativeAuthExchangeRequest(BaseModel):
    """Redeem a one-time native browser result using its PKCE verifier."""

    kind: str = Field(pattern=r"^(social|sso)$")
    code: str = Field(min_length=32, max_length=512)
    code_verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    state: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")



# -------------------
# Social Auth Init Request
# -------------------
class SocialAuthInitRequest(AccountModeMixin, TermsOfServiceAcceptanceMixin):
    pass



# -------------------
# Social Auth Callback Request
# -------------------
class SocialAuthCallbackRequest(BaseModel):
    social_token: str | None = None  # Legacy fallback; cookie-bound flows omit this
    otp_code: str | None = None  # 2FA code if required
    otp_type: str | None = None  # "setup" or "verify"
    otp_action: str | None = None
    otp_destination: str | None = None


# -------------------
# Passkey Begin Registration Request
# -------------------
class PasskeyBeginRegistrationRequest(BaseModel):
    pass



# -------------------
# Passkey Finish Registration Request
# -------------------
class PasskeyFinishRegistrationRequest(BaseModel):
    credential: dict
    expected_challenge: str



# -------------------
# Passkey Begin Authentication Request
# -------------------
class PasskeyBeginAuthenticationRequest(BaseModel):
    identifier: str



# -------------------
# Passkey Finish Authentication Request
# -------------------
class PasskeyFinishAuthenticationRequest(AccountModeMixin):
    credential: dict
    expected_challenge: str


class PasskeyCompleteAuthenticationRequest(AccountModeMixin):
    passkey_token: str | None = None
    otp_code: str | None = None
    otp_type: str | None = None
    otp_action: str | None = None
    otp_destination: str | None = None



# -------------------
# SSO Auth Init Request
# -------------------
EnterpriseSSOProviderType = Literal["saml", "oidc"]


class SSOAuthInitRequest(AccountModeMixin, TermsOfServiceAcceptanceMixin):
    model_config = ConfigDict(extra="forbid")

    provider_type: EnterpriseSSOProviderType

# -------------------
# SSO Auth Callback Request
# -------------------
class SSOAuthCallbackRequest(BaseModel):
    sso_token: str | None = None  # Legacy fallback; cookie-bound flows omit this
    otp_code: str | None = None  # 2FA code if required
    otp_type: str | None = None  # "setup" or "verify"
    otp_action: str | None = None
    otp_destination: str | None = None
