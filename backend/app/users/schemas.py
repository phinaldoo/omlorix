from pydantic import BaseModel, ConfigDict, EmailStr, constr, Field, field_validator
from datetime import datetime
from typing import Optional, Dict, Any, Union, Literal
from enum import Enum

from app.users.timezones import normalize_user_timezone
from app.utils.email import normalize_email


PASSWORD_MAX_LENGTH = 1024
PasswordValue = constr(min_length=1, max_length=PASSWORD_MAX_LENGTH)  # type: ignore


def _reject_new_password_edge_whitespace(value: str) -> str:
    if value != value.strip():
        raise ValueError("New password must not start or end with whitespace.")
    return value


def _normalize_required_email(value: str) -> str:
    normalized = normalize_email(value)
    if not normalized:
        raise ValueError("Email is required.")
    return normalized


# -------------------
# Update User General Settings Toogle
# -------------------
class LLMAccessPresetEnum(str, Enum):
    none = "none"
    all = "all"
    custom = "custom"


class PersonalityPresetEnum(str, Enum):
    none = "none"
    standard = "standard"
    professional = "professional"
    friendly = "friendly"
    honest = "honest"
    quirky = "quirky"
    efficient = "efficient"
    cynical = "cynical"
    custom = "custom"


class LLMAccessPermissions(BaseModel):
    first_name: bool = False
    language: bool = False
    country: bool = False
    timezone: bool = False
    location: bool = False


class UpdateUserGeneralSettingsToogle(BaseModel):
    allow_llm_to_access_personal_information: Optional[Union[bool, LLMAccessPermissions, Dict[str, bool]]] = None
    allow_llm_to_access_personal_information_preset: Optional[Union[LLMAccessPresetEnum, str]] = None
    render_user_messages_markdown: Optional[bool] = None
    render_assistant_messages_markdown: Optional[bool] = None
    ctrl_enter_to_send: Optional[bool] = None
    always_use_temporary_chat: Optional[bool] = None
    chat_full_width: Optional[bool] = None
    byok_statistics_enabled: Optional[bool] = None
    byok_statistics_retention_days: Optional[int] = Field(default=None, ge=1, le=365)
    show_message_nav: Optional[bool] = None
    show_model_settings: Optional[bool] = None
    show_assistant_message_metadata: Optional[bool] = None
    speech_playback_speed: Optional[float] = Field(default=None, ge=0.5, le=2.0)


class UpdateUserPersonalitySettings(BaseModel):
    preset: Optional[PersonalityPresetEnum] = None
    custom_instruction: Optional[str] = None

    @field_validator("custom_instruction")
    @classmethod
    def validate_custom_instruction(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if len(normalized) > 1000:
            raise ValueError("Custom personality instructions must be 1000 characters or fewer.")
        return normalized



# -------------------
# Update User Settings Select
# -------------------
class UpdateUserSettingsSelect(BaseModel):
    profile_visibility: Optional[str] = None
    language: Optional["LanguageEnum"] = None
    country: Optional["CountryEnum"] = None
    timezone: Optional[str] = None
    font: Optional[str] = None
    allow_llm_to_access_personal_information_preset: Optional[Union[LLMAccessPresetEnum, str]] = None



class UserDeletionPolicy(BaseModel):
    mode: Literal["delete_instantly", "delete_after_days", "retain"]
    effect: Literal["erasure", "scheduled_deletion", "deactivation"]
    restorable: bool
    retention_days: Optional[int] = None
    purge_scheduled_at: Optional[datetime] = None


class DeleteAccountResponse(BaseModel):
    status: str
    account_deletion: UserDeletionPolicy


class SharedItemsSectionError(BaseModel):
    section: str
    code: Literal["inventory_unavailable"]


class SharedItemsResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    items: list[dict[str, Any]] = Field(default_factory=list)
    section_errors: list[SharedItemsSectionError] = Field(default_factory=list)



# -------------------
# Update User General Settings
# -------------------
class LanguageEnum(str, Enum):
    en = "en"
    de = "de"
    es = "es"
    fr = "fr"
    hi = "hi"
    ar = "ar"
    zh = "zh"
    ja = "ja"
    it = "it"
    pt = "pt"
    ru = "ru"
class CountryEnum(str, Enum):
    au = "au"
    ca = "ca"
    de = "de"
    us = "us"
    it = "it"
    jp = "jp"
    cn = "cn"
    ar = "ar"
    es = "es"
    fr = "fr"
    gb = "gb"
    in_ = "in"
class UpdatePrivacyPolicyNoticeState(BaseModel):
    action: Literal["dismiss", "accept"]
    revision: int = Field(..., ge=1)


class AcceptTermsOfServiceState(BaseModel):
    revision: int = Field(..., ge=1)


class PublicUserSharingSummary(BaseModel):
    id: str
    display_name: str




# -------------------
# Update User Location
# -------------------
class UpdateUserLocationRequest(BaseModel):
    location: str


# -------------------
# Update User Color Theme
# -------------------
class ThemeEnum(str, Enum):
    system = "system"
    light = "light"
    dark = "dark"
class ColorThemeEnum(str, Enum):
    blue = "blue"
    green = "green"
    coral = "coral"
    purple = "purple"
    teal = "teal"
    amber = "amber"
    mono = "mono"
class UpdateUserColorTheme(BaseModel):
    theme: Optional[ThemeEnum] = None
    color_theme: Optional[ColorThemeEnum] = None



# -------------------
# Update User Last Model
# -------------------
class UpdateUserLastModel(BaseModel):
    model_id: str


# -------------------
# Update User Pinned Models
# -------------------
class UpdateUserPinnedModels(BaseModel):
    pinned_models: list[str] = Field(default_factory=list, max_length=8)


class SidebarButtonVisibilityUpdate(BaseModel):
    create_chat: Optional[bool] = None
    search_chats: Optional[bool] = None
    workspace: Optional[bool] = None
    automations: Optional[bool] = None
    projects: Optional[bool] = None



# -------------------
# User
# -------------------
class Users(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    hashed_password: str
    is_active: bool



# -------------------
# User Base
# -------------------
class UserBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, from_attributes=True)

    email: str = Field(..., max_length=100)
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_required_email(value)

# -------------------
# User Create
# -------------------
class UserCreate(UserBase):
    # Signup creates a new credential, so validate the email format and apply
    # the shared password bounds before dynamic admin policy is evaluated.
    # Sign-in intentionally keeps UserBase's broader credential input so this
    # stricter creation contract cannot make an existing password unusable.
    email: EmailStr = Field(..., max_length=100)
    password: PasswordValue
    first_name: constr(min_length=1, max_length=100)  # type: ignore
    last_name: constr(min_length=1, max_length=100)   # type: ignore
    accept_terms_of_service: bool = False
    terms_of_service_revision: Optional[int] = Field(default=None, ge=1)
    account_mode: str = "primary"
    replace_slot: Optional[int] = Field(default=None, ge=1, le=5)
    return_url: Optional[str] = None

    @field_validator("password", mode="before")
    @classmethod
    def validate_password(cls, value: Any) -> Any:
        """Reject ambiguous edge whitespace in a newly created password."""
        if isinstance(value, str):
            return _reject_new_password_edge_whitespace(value)
        # Let the declared PasswordValue type produce the canonical type error
        # for non-string JSON values.
        return value



# -------------------
# Password
# -------------------
class Password(BaseModel):
    old_password: PasswordValue
    new_password: PasswordValue

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return _reject_new_password_edge_whitespace(value)



# -------------------
# User Personal Details
# -------------------
class UserPersonalDetails(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value: EmailStr | str | None) -> str | None:
        if value is None:
            return value
        return _normalize_required_email(str(value))



# -------------------
# Change Password
# -------------------
class ChangePassword(BaseModel):
    old_password: PasswordValue
    new_password: PasswordValue

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return _reject_new_password_edge_whitespace(value)


# -------------------
# Set Password (for social login users)
# -------------------
class SetPassword(BaseModel):
    new_password: PasswordValue

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return _reject_new_password_edge_whitespace(value)



class DetectedLocaleDefaults(BaseModel):
    """Browser-detected locale values used only to fill blank preferences."""

    language: Optional[LanguageEnum] = None
    country: Optional[CountryEnum] = None
    timezone: Optional[str] = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return normalize_user_timezone(value)


class LocaleDefaultsResult(BaseModel):
    status: Literal["success"] = "success"
    updated: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class UpdateLLMAccessSettings(BaseModel):
    preset: Optional[LLMAccessPresetEnum] = None
    permissions: Optional[Union[bool, LLMAccessPermissions, Dict[str, bool]]] = None
