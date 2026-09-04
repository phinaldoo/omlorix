"""Schema metadata and response construction for the admin user-settings editor.

The persisted defaults remain in ``app.users.defaults`` because both normal
user flows and administrative flows consume them. This module owns only the
admin-facing representation used by ``GET /api/v1/admin/user/settings``.
"""

from typing import Any, Dict

from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.external_management import is_externally_managed_setting_hidden
from app.users.timezones import SUPPORTED_USER_TIMEZONE_OPTIONS
from app.utils.schemas import FieldAttributes, FieldSchema, Option, Section, Sections


class UserSettingsFormSchema(Sections):
    """Schema definition (sections + fields) for the admin user-settings editor."""


USER_SETTINGS_PAGE_TITLES: Dict[str, str] = {
    "security": "Security",
    "general": "General",
    "appearance": "Appearance",
    "chat": "Chat",
    "login_2fa": "Two-Factor Authentication",
    "secret": "Secrets",
    "states": "User State",
    "social_login": "Social Login",
    "sso_login": "SSO Login",
    "ldap_login": "LDAP Login",
}

_PAGE_I18N_OVERRIDES: Dict[str, Dict[str, str]] = {
    "security": {
        "label": "us_nav_security",
        "description": "admin_user_settings_page_security_desc",
    },
    "general": {
        "label": "us_nav_general",
        "description": "admin_user_settings_page_general_desc",
    },
    "appearance": {
        "label": "us_nav_appearance",
        "description": "admin_user_settings_page_appearance_desc",
    },
    "chat": {
        "label": "us_nav_chat",
        "description": "admin_user_settings_page_chat_desc",
    },
    "login_2fa": {
        "label": "us_security_2fa_title",
        "description": "us_security_2fa_card_desc",
    },
    "secret": {
        "label": "admin_user_settings_page_secret_label",
        "description": "admin_user_settings_page_secret_desc",
    },
    "states": {
        "label": "admin_user_settings_page_states_label",
        "description": "admin_user_settings_page_states_desc",
    },
    "social_login": {
        "label": "admin_user_settings_page_social_login_label",
        "description": "admin_user_settings_page_social_login_desc",
    },
    "sso_login": {
        "label": "admin_user_settings_page_sso_login_label",
        "description": "admin_user_settings_page_sso_login_desc",
    },
    "scim": {
        "label": "admin_user_settings_page_scim_label",
        "description": "admin_user_settings_page_scim_desc",
    },
    "ldap_login": {
        "label": "admin_user_settings_page_ldap_login_label",
        "description": "admin_user_settings_page_ldap_login_desc",
    },
}

_FIELD_I18N_OVERRIDES: Dict[tuple[str, str], Dict[str, str]] = {
    ("security", "profile_visibility"): {
        "label": "us_security_profile_visibility_title",
        "description": "us_security_profile_visibility_desc",
    },
    ("security", "allow_llm_to_access_personal_information"): {
        "label": "us_security_allow_llm_title",
        "description": "us_security_allow_llm_desc",
    },
    ("general", "language"): {
        "label": "us_general_language_title",
        "description": "us_general_language_desc",
    },
    ("general", "country"): {
        "label": "us_general_country_title",
        "description": "us_general_country_desc",
    },
    ("general", "timezone"): {
        "label": "us_general_timezone_title",
        "description": "us_general_timezone_desc",
    },
    ("general", "location"): {
        "label": "us_general_location_title",
        "description": "us_general_location_desc",
    },
    ("appearance", "theme"): {
        "label": "us_appearance_theme_mode_title",
        "description": "us_appearance_theme_mode_desc",
    },
    ("appearance", "color_theme"): {
        "label": "us_appearance_color_theme_title",
        "description": "us_appearance_color_theme_desc",
    },
    ("appearance", "font"): {
        "label": "us_appearance_font_title",
        "description": "us_appearance_font_desc",
    },
    ("chat", "show_message_nav"): {
        "label": "chat_experience_show_message_nav_title",
        "description": "chat_experience_show_message_nav_desc",
    },
    ("chat", "show_model_settings"): {
        "label": "chat_experience_show_model_settings_title",
        "description": "chat_experience_show_model_settings_desc",
    },
    ("chat", "show_assistant_message_metadata"): {
        "label": "chat_experience_show_assistant_message_metadata_title",
        "description": "chat_experience_show_assistant_message_metadata_desc",
    },
    ("chat", "render_user_messages_markdown"): {
        "label": "chat_experience_render_user_markdown_title",
        "description": "chat_experience_render_user_markdown_desc",
    },
    ("chat", "render_assistant_messages_markdown"): {
        "label": "chat_experience_render_assistant_markdown_title",
        "description": "chat_experience_render_assistant_markdown_desc",
    },
    ("chat", "ctrl_enter_to_send"): {
        "label": "chat_experience_ctrl_enter_to_send_title",
        "description": "chat_experience_ctrl_enter_to_send_desc",
    },
    ("chat", "always_use_temporary_chat"): {
        "label": "chat_experience_temporary_chat_title",
        "description": "chat_experience_temporary_chat_desc",
    },
    ("chat", "chat_full_width"): {
        "label": "chat_customization_full_width_title",
        "description": "chat_customization_full_width_desc",
    },
    ("security", "has_to_change_password"): {
        "label": "admin_user_settings_field_security_has_to_change_password_label",
        "description": "admin_user_settings_field_security_has_to_change_password_desc",
    },
    ("security", "allow_llm_to_access_personal_information_preset"): {
        "label": "admin_user_settings_field_security_allow_llm_to_access_personal_information_preset_label",
        "description": "admin_user_settings_field_security_allow_llm_to_access_personal_information_preset_desc",
    },
    ("chat", "last_model"): {
        "label": "admin_user_settings_field_chat_last_model_label",
        "description": "admin_user_settings_field_chat_last_model_desc",
    },
    ("chat", "speech_playback_speed"): {
        "label": "admin_user_settings_field_chat_speech_playback_speed_label",
        "description": "admin_user_settings_field_chat_speech_playback_speed_desc",
    },
    ("chat", "personality_preset"): {
        "label": "chat_personality_section_title",
        "description": "chat_personality_section_desc",
    },
    ("chat", "personality_custom_instruction"): {
        "label": "chat_personality_custom_label",
        "description": "chat_personality_custom_helper",
    },
    ("chat", "pinned_models"): {
        "label": "chat_experience_pinned_models_title",
        "description": "chat_experience_pinned_models_desc",
    },
    ("chat", "pinned_models_customized"): {
        "label": "admin_user_settings_field_chat_pinned_models_customized_label",
        "description": "admin_user_settings_field_chat_pinned_models_customized_desc",
    },
    ("chat", "byok_statistics_enabled"): {
        "label": "admin_user_settings_field_chat_byok_statistics_enabled_label",
        "description": "admin_user_settings_field_chat_byok_statistics_enabled_desc",
    },
    ("chat", "byok_statistics_retention_days"): {
        "label": "admin_user_settings_field_chat_byok_statistics_retention_days_label",
        "description": "admin_user_settings_field_chat_byok_statistics_retention_days_desc",
    },
    ("chat", "sidebar_button_visibility"): {
        "label": "sidebar_button_visibility_title",
        "description": "sidebar_button_visibility_desc",
    },
    ("login_2fa", "enable_2fa"): {
        "label": "admin_user_settings_field_login_2fa_enable_2fa_label",
        "description": "admin_user_settings_field_login_2fa_enable_2fa_desc",
    },
    ("login_2fa", "provider"): {
        "label": "admin_user_settings_field_login_2fa_provider_label",
        "description": "admin_user_settings_field_login_2fa_provider_desc",
    },
    ("secret", "2fa_secret"): {
        "label": "admin_user_settings_field_secret_2fa_secret_label",
        "description": "admin_user_settings_field_secret_2fa_secret_desc",
    },
    ("secret", "2fa_secret_pending"): {
        "label": "admin_user_settings_field_secret_2fa_secret_pending_label",
        "description": "admin_user_settings_field_secret_2fa_secret_pending_desc",
    },
    ("secret", "2fa_otp_hash"): {
        "label": "admin_user_settings_field_secret_2fa_otp_hash_label",
        "description": "admin_user_settings_field_secret_2fa_otp_hash_desc",
    },
    ("secret", "2fa_otp_expires_at"): {
        "label": "admin_user_settings_field_secret_2fa_otp_expires_at_label",
        "description": "admin_user_settings_field_secret_2fa_otp_expires_at_desc",
    },
    ("secret", "2fa_otp_last_sent_at"): {
        "label": "admin_user_settings_field_secret_2fa_otp_last_sent_at_label",
        "description": "admin_user_settings_field_secret_2fa_otp_last_sent_at_desc",
    },
    ("secret", "2fa_otp_attempts"): {
        "label": "admin_user_settings_field_secret_2fa_otp_attempts_label",
        "description": "admin_user_settings_field_secret_2fa_otp_attempts_desc",
    },
    ("secret", "2fa_otp_purpose"): {
        "label": "admin_user_settings_field_secret_2fa_otp_purpose_label",
        "description": "admin_user_settings_field_secret_2fa_otp_purpose_desc",
    },
    ("secret", "2fa_otp_provider"): {
        "label": "admin_user_settings_field_secret_2fa_otp_provider_label",
        "description": "admin_user_settings_field_secret_2fa_otp_provider_desc",
    },
    ("secret", "2fa_otp_destination"): {
        "label": "admin_user_settings_field_secret_2fa_otp_destination_label",
        "description": "admin_user_settings_field_secret_2fa_otp_destination_desc",
    },
    ("secret", "passkey_pending_token"): {
        "label": "admin_user_settings_field_secret_passkey_pending_token_label",
        "description": "admin_user_settings_field_secret_passkey_pending_token_desc",
    },
    ("secret", "passkey_pending_token_expires_at"): {
        "label": "admin_user_settings_field_secret_passkey_pending_token_expires_at_label",
        "description": "admin_user_settings_field_secret_passkey_pending_token_expires_at_desc",
    },
    ("secret", "passkey_pending_setup_material_allowed"): {
        "label": "admin_user_settings_field_secret_passkey_pending_setup_material_allowed_label",
        "description": "admin_user_settings_field_secret_passkey_pending_setup_material_allowed_desc",
    },
    ("secret", "signin_pending_token"): {
        "label": "admin_user_settings_field_secret_signin_pending_token_label",
        "description": "admin_user_settings_field_secret_signin_pending_token_desc",
    },
    ("secret", "signin_pending_token_expires_at"): {
        "label": "admin_user_settings_field_secret_signin_pending_token_expires_at_label",
        "description": "admin_user_settings_field_secret_signin_pending_token_expires_at_desc",
    },
    ("secret", "signin_pending_setup_material_allowed"): {
        "label": "admin_user_settings_field_secret_signin_pending_setup_material_allowed_label",
        "description": "admin_user_settings_field_secret_signin_pending_setup_material_allowed_desc",
    },
    ("secret", "wrong_sign_in_attempts"): {
        "label": "admin_user_settings_field_secret_wrong_sign_in_attempts_label",
        "description": "admin_user_settings_field_secret_wrong_sign_in_attempts_desc",
    },
    ("states", "welcome_card_dismissed"): {
        "label": "admin_user_settings_field_states_welcome_card_dismissed_label",
        "description": "admin_user_settings_field_states_welcome_card_dismissed_desc",
    },
    ("states", "has_new_notifications"): {
        "label": "admin_user_settings_field_states_has_new_notifications_label",
        "description": "admin_user_settings_field_states_has_new_notifications_desc",
    },
    ("states", "privacy_policy_last_interacted_revision"): {
        "label": "admin_user_settings_field_states_privacy_policy_last_interacted_revision_label",
        "description": "admin_user_settings_field_states_privacy_policy_last_interacted_revision_desc",
    },
    ("states", "privacy_policy_accepted"): {
        "label": "admin_user_settings_field_states_privacy_policy_accepted_label",
        "description": "admin_user_settings_field_states_privacy_policy_accepted_desc",
    },
    ("states", "terms_of_service_accepted_revision"): {
        "label": "admin_user_settings_field_states_terms_of_service_accepted_revision_label",
        "description": "admin_user_settings_field_states_terms_of_service_accepted_revision_desc",
    },
    ("states", "terms_of_service_accepted_at"): {
        "label": "admin_user_settings_field_states_terms_of_service_accepted_at_label",
        "description": "admin_user_settings_field_states_terms_of_service_accepted_at_desc",
    },
    ("social_login", "needs_password_setup"): {
        "label": "admin_user_settings_field_social_login_needs_password_setup_label",
        "description": "admin_user_settings_field_social_login_needs_password_setup_desc",
    },
    ("social_login", "google_linked"): {
        "label": "admin_user_settings_field_social_login_google_linked_label",
        "description": "admin_user_settings_field_social_login_google_linked_desc",
    },
    ("social_login", "google_user_id"): {
        "label": "admin_user_settings_field_social_login_google_user_id_label",
        "description": "admin_user_settings_field_social_login_google_user_id_desc",
    },
    ("social_login", "github_linked"): {
        "label": "admin_user_settings_field_social_login_github_linked_label",
        "description": "admin_user_settings_field_social_login_github_linked_desc",
    },
    ("social_login", "github_user_id"): {
        "label": "admin_user_settings_field_social_login_github_user_id_label",
        "description": "admin_user_settings_field_social_login_github_user_id_desc",
    },
    ("social_login", "slack_linked"): {
        "label": "admin_user_settings_field_social_login_slack_linked_label",
        "description": "admin_user_settings_field_social_login_slack_linked_desc",
    },
    ("social_login", "slack_user_id"): {
        "label": "admin_user_settings_field_social_login_slack_user_id_label",
        "description": "admin_user_settings_field_social_login_slack_user_id_desc",
    },
    ("social_login", "microsoft_linked"): {
        "label": "admin_user_settings_field_social_login_microsoft_linked_label",
        "description": "admin_user_settings_field_social_login_microsoft_linked_desc",
    },
    ("social_login", "microsoft_user_id"): {
        "label": "admin_user_settings_field_social_login_microsoft_user_id_label",
        "description": "admin_user_settings_field_social_login_microsoft_user_id_desc",
    },
    ("social_login", "apple_linked"): {
        "label": "admin_user_settings_field_social_login_apple_linked_label",
        "description": "admin_user_settings_field_social_login_apple_linked_desc",
    },
    ("social_login", "apple_user_id"): {
        "label": "admin_user_settings_field_social_login_apple_user_id_label",
        "description": "admin_user_settings_field_social_login_apple_user_id_desc",
    },
    ("social_login", "oauth_profile_picture_present"): {
        "label": "admin_user_settings_field_social_login_oauth_profile_picture_present_label",
        "description": "admin_user_settings_field_social_login_oauth_profile_picture_present_desc",
    },
    ("social_login", "oauth_profile_picture_provider"): {
        "label": "admin_user_settings_field_social_login_oauth_profile_picture_provider_label",
        "description": "admin_user_settings_field_social_login_oauth_profile_picture_provider_desc",
    },
    ("social_login", "oauth_profile_picture_last_synced_at"): {
        "label": "admin_user_settings_field_social_login_oauth_profile_picture_last_synced_at_label",
        "description": "admin_user_settings_field_social_login_oauth_profile_picture_last_synced_at_desc",
    },
    ("social_login", "oauth_profile_picture_sync_disabled"): {
        "label": "admin_user_settings_field_social_login_oauth_profile_picture_sync_disabled_label",
        "description": "admin_user_settings_field_social_login_oauth_profile_picture_sync_disabled_desc",
    },
    ("social_login", "pending_social_token"): {
        "label": "admin_user_settings_field_social_login_pending_social_token_label",
        "description": "admin_user_settings_field_social_login_pending_social_token_desc",
    },
    ("social_login", "pending_social_token_expires"): {
        "label": "admin_user_settings_field_social_login_pending_social_token_expires_label",
        "description": "admin_user_settings_field_social_login_pending_social_token_expires_desc",
    },
    ("social_login", "pending_provider"): {
        "label": "admin_user_settings_field_social_login_pending_provider_label",
        "description": "admin_user_settings_field_social_login_pending_provider_desc",
    },
    ("social_login", "pending_setup_material_allowed"): {
        "label": "admin_user_settings_field_social_login_pending_setup_material_allowed_label",
        "description": "admin_user_settings_field_social_login_pending_setup_material_allowed_desc",
    },
    ("social_login", "pending_auth_code"): {
        "label": "admin_user_settings_field_social_login_pending_auth_code_label",
        "description": "admin_user_settings_field_social_login_pending_auth_code_desc",
    },
    ("social_login", "pending_auth_code_expires"): {
        "label": "admin_user_settings_field_social_login_pending_auth_code_expires_label",
        "description": "admin_user_settings_field_social_login_pending_auth_code_expires_desc",
    },
    ("sso_login", "needs_password_setup"): {
        "label": "admin_user_settings_field_sso_login_needs_password_setup_label",
        "description": "admin_user_settings_field_sso_login_needs_password_setup_desc",
    },
    ("sso_login", "saml_linked"): {
        "label": "admin_user_settings_field_sso_login_saml_linked_label",
        "description": "admin_user_settings_field_sso_login_saml_linked_desc",
    },
    ("sso_login", "saml_user_id"): {
        "label": "admin_user_settings_field_sso_login_saml_user_id_label",
        "description": "admin_user_settings_field_sso_login_saml_user_id_desc",
    },
    ("sso_login", "oidc_linked"): {
        "label": "admin_user_settings_field_sso_login_oidc_linked_label",
        "description": "admin_user_settings_field_sso_login_oidc_linked_desc",
    },
    ("sso_login", "oidc_user_id"): {
        "label": "admin_user_settings_field_sso_login_oidc_user_id_label",
        "description": "admin_user_settings_field_sso_login_oidc_user_id_desc",
    },
    ("sso_login", "provider_id"): {
        "label": "admin_user_settings_field_sso_login_provider_id_label",
        "description": "admin_user_settings_field_sso_login_provider_id_desc",
    },
    ("sso_login", "pending_sso_token"): {
        "label": "admin_user_settings_field_sso_login_pending_sso_token_label",
        "description": "admin_user_settings_field_sso_login_pending_sso_token_desc",
    },
    ("sso_login", "pending_sso_token_expires"): {
        "label": "admin_user_settings_field_sso_login_pending_sso_token_expires_label",
        "description": "admin_user_settings_field_sso_login_pending_sso_token_expires_desc",
    },
    ("sso_login", "pending_provider_type"): {
        "label": "admin_user_settings_field_sso_login_pending_provider_type_label",
        "description": "admin_user_settings_field_sso_login_pending_provider_type_desc",
    },
    ("sso_login", "pending_setup_material_allowed"): {
        "label": "admin_user_settings_field_sso_login_pending_setup_material_allowed_label",
        "description": "admin_user_settings_field_sso_login_pending_setup_material_allowed_desc",
    },
    ("sso_login", "pending_auth_code"): {
        "label": "admin_user_settings_field_sso_login_pending_auth_code_label",
        "description": "admin_user_settings_field_sso_login_pending_auth_code_desc",
    },
    ("sso_login", "pending_auth_code_expires"): {
        "label": "admin_user_settings_field_sso_login_pending_auth_code_expires_label",
        "description": "admin_user_settings_field_sso_login_pending_auth_code_expires_desc",
    },
    ("scim", "external_id"): {
        "label": "admin_user_settings_field_scim_external_id_label",
        "description": "admin_user_settings_field_scim_external_id_desc",
    },
    ("scim", "last_synced_at"): {
        "label": "admin_user_settings_field_scim_last_synced_at_label",
        "description": "admin_user_settings_field_scim_last_synced_at_desc",
    },
    ("ldap_login", "linked"): {
        "label": "admin_user_settings_field_ldap_login_linked_label",
        "description": "admin_user_settings_field_ldap_login_linked_desc",
    },
    ("ldap_login", "directory_user_id"): {
        "label": "admin_user_settings_field_ldap_login_directory_user_id_label",
        "description": "admin_user_settings_field_ldap_login_directory_user_id_desc",
    },
    ("ldap_login", "directory_dn"): {
        "label": "admin_user_settings_field_ldap_login_directory_dn_label",
        "description": "admin_user_settings_field_ldap_login_directory_dn_desc",
    },
    ("ldap_login", "directory_username"): {
        "label": "admin_user_settings_field_ldap_login_directory_username_label",
        "description": "admin_user_settings_field_ldap_login_directory_username_desc",
    },
    ("ldap_login", "last_login_identifier"): {
        "label": "admin_user_settings_field_ldap_login_last_login_identifier_label",
        "description": "admin_user_settings_field_ldap_login_last_login_identifier_desc",
    },
    ("ldap_login", "last_synced_at"): {
        "label": "admin_user_settings_field_ldap_login_last_synced_at_label",
        "description": "admin_user_settings_field_ldap_login_last_synced_at_desc",
    },
    ("ldap_login", "last_synced_groups"): {
        "label": "admin_user_settings_field_ldap_login_last_synced_groups_label",
        "description": "admin_user_settings_field_ldap_login_last_synced_groups_desc",
    },
}

_OPTION_I18N_OVERRIDES: Dict[tuple[str, str, str], str] = {
    ("security", "profile_visibility", "public"): "us_security_profile_visibility_option_public",
    ("security", "profile_visibility", "private"): "us_security_profile_visibility_option_private",
    ("security", "allow_llm_to_access_personal_information_preset", "none"): "admin_user_settings_option_security_allow_llm_to_access_personal_information_preset_none",
    ("security", "allow_llm_to_access_personal_information_preset", "all"): "admin_user_settings_option_security_allow_llm_to_access_personal_information_preset_all",
    ("security", "allow_llm_to_access_personal_information_preset", "custom"): "admin_user_settings_option_security_allow_llm_to_access_personal_information_preset_custom",
    ("appearance", "theme", "system"): "us_appearance_theme_mode_system",
    ("appearance", "theme", "light"): "us_appearance_theme_mode_light",
    ("appearance", "theme", "dark"): "us_appearance_theme_mode_dark",
    ("appearance", "color_theme", "mono"): "schema_option_theme_mono",
    ("appearance", "color_theme", "blue"): "schema_option_theme_blue",
    ("appearance", "color_theme", "green"): "schema_option_theme_green",
    ("appearance", "color_theme", "coral"): "schema_option_theme_coral",
    ("appearance", "color_theme", "purple"): "schema_option_theme_purple",
    ("appearance", "color_theme", "teal"): "schema_option_theme_teal",
    ("appearance", "color_theme", "amber"): "schema_option_theme_amber",
    ("chat", "personality_preset", "none"): "chat_personality_none_title",
    ("chat", "personality_preset", "standard"): "chat_personality_standard_title",
    ("chat", "personality_preset", "professional"): "chat_personality_professional_title",
    ("chat", "personality_preset", "friendly"): "chat_personality_friendly_title",
    ("chat", "personality_preset", "honest"): "chat_personality_honest_title",
    ("chat", "personality_preset", "quirky"): "chat_personality_quirky_title",
    ("chat", "personality_preset", "efficient"): "chat_personality_efficient_title",
    ("chat", "personality_preset", "cynical"): "chat_personality_cynical_title",
    ("chat", "personality_preset", "custom"): "chat_personality_custom_title",
    ("login_2fa", "provider", "totp"): "schema_group_option_settings_login_2fa_2fa_provider_totp",
    ("login_2fa", "provider", "email"): "schema_group_option_settings_login_2fa_2fa_provider_email",
}

_USER_SETTING_FIELD_META: Dict[tuple[str, str], Dict[str, Any]] = {
    ("security", "profile_visibility"): {
        "type": "select",
        "options": [
            {"value": "public", "label": "Public"},
            {"value": "private", "label": "Private"},
        ],
        "description": "Who can see the user's profile.",
    },
    ("security", "allow_llm_to_access_personal_information_preset"): {
        "type": "select",
        "hidden": True,
        "options": [
            {"value": "none", "label": "None"},
            {"value": "all", "label": "All"},
            {"value": "custom", "label": "Custom"},
        ],
        "description": "Stores the preset mode used for the personal-information access control.",
    },
    ("general", "language"): {
        "type": "select",
        "options": [
            {
                "value": "en",
                "label": "English",
                "i18n_label": "us_general_language_en",
            },
            {
                "value": "de",
                "label": "German",
                "i18n_label": "us_general_language_de",
            },
            {
                "value": "es",
                "label": "Spanish",
                "i18n_label": "us_general_language_es",
            },
            {
                "value": "fr",
                "label": "French",
                "i18n_label": "us_general_language_fr",
            },
            {
                "value": "zh",
                "label": "Chinese",
                "i18n_label": "us_general_language_zh",
            },
            {
                "value": "hi",
                "label": "Hindi",
                "i18n_label": "us_general_language_hi",
            },
            {
                "value": "ar",
                "label": "Arabic",
                "i18n_label": "us_general_language_ar",
            },
            {
                "value": "ja",
                "label": "Japanese",
                "i18n_label": "us_general_language_ja",
            },
            {
                "value": "it",
                "label": "Italian",
                "i18n_label": "us_general_language_it",
            },
            {
                "value": "pt",
                "label": "Portuguese",
                "i18n_label": "us_general_language_pt",
            },
            {
                "value": "ru",
                "label": "Russian",
                "i18n_label": "us_general_language_ru",
            },
        ],
        "description": "Preferred language (ISO 639-1).",
    },
    ("general", "country"): {
        "type": "select",
        "options": [
            {
                "value": "",
                "label": "Select country",
                "i18n_label": "us_general_country_default",
            },
            {
                "value": "us",
                "label": "United States",
                "i18n_label": "us_general_country_us",
            },
            {
                "value": "de",
                "label": "Germany",
                "i18n_label": "us_general_country_de",
            },
            {
                "value": "gb",
                "label": "United Kingdom",
                "i18n_label": "us_general_country_gb",
            },
            {
                "value": "fr",
                "label": "France",
                "i18n_label": "us_general_country_fr",
            },
            {"value": "it", "label": "Italy", "i18n_label": "us_general_country_it"},
            {"value": "es", "label": "Spain", "i18n_label": "us_general_country_es"},
            {"value": "ca", "label": "Canada", "i18n_label": "us_general_country_ca"},
            {"value": "au", "label": "Australia", "i18n_label": "us_general_country_au"},
            {"value": "jp", "label": "Japan", "i18n_label": "us_general_country_jp"},
            {"value": "cn", "label": "China", "i18n_label": "us_general_country_cn"},
            {"value": "in", "label": "India", "i18n_label": "us_general_country_in"},
            {"value": "ar", "label": "Argentina", "i18n_label": "us_general_country_ar"},
        ],
        "description": "Country of residence (ISO 3166-1 alpha-2).",
    },
    ("general", "timezone"): {
        "type": "select",
        "options": [dict(option) for option in SUPPORTED_USER_TIMEZONE_OPTIONS],
    },
    ("appearance", "theme"): {
        "type": "select",
        "options": [
            {"value": "system", "label": "System"},
            {"value": "light", "label": "Light"},
            {"value": "dark", "label": "Dark"},
        ],
    },
    ("appearance", "color_theme"): {
        "type": "select",
        "options": [
            {"value": "blue", "label": "Blue"},
            {"value": "green", "label": "Green"},
            {"value": "coral", "label": "Coral"},
            {"value": "purple", "label": "Purple"},
            {"value": "teal", "label": "Teal"},
            {"value": "amber", "label": "Amber"},
            {"value": "mono", "label": "Mono"},
        ],
    },
    ("appearance", "font"): {
        "type": "select",
        "options": [
            {
                "value": "inter",
                "label": "Inter",
                "i18n_label": "us_appearance_font_option_inter",
            },
            {
                "value": "system",
                "label": "System",
                "i18n_label": "us_appearance_font_option_system",
            },
            {
                "value": "roboto",
                "label": "Roboto",
                "i18n_label": "us_appearance_font_option_roboto",
            },
            {
                "value": "verdana",
                "label": "Verdana",
                "i18n_label": "us_appearance_font_option_verdana",
            },
            {
                "value": "georgia",
                "label": "Georgia",
                "i18n_label": "us_appearance_font_option_georgia",
            },
            {
                "value": "times",
                "label": "Times New Roman",
                "i18n_label": "us_appearance_font_option_times",
            },
            {
                "value": "courier",
                "label": "Courier New",
                "i18n_label": "us_appearance_font_option_courier",
            },
        ],
        "description": "UI font preference.",
    },
    ("chat", "last_model"): {
        "type": "select",
        "description": "Identifier of the last used model.",
    },
    ("chat", "pinned_models"): {
        "type": "string_list",
        "hidden": True,
        "description": "Ordered list of pinned model identifiers.",
    },
    ("chat", "pinned_models_customized"): {
        "type": "boolean",
        "hidden": True,
        "description": "Tracks whether pinned models are inherited or user-defined.",
    },
    ("chat", "sidebar_button_visibility"): {
        "type": "boolean_map",
        "description": "Choose which buttons are visible in the chat sidebar.",
        "metadata": {
            "items": [
                {
                    "key": "create_chat",
                    "label": "Create Chat",
                    "description": "Show the Create Chat button in the sidebar.",
                    "i18n_label": "sidebar_button_create_chat",
                    "i18n_description": "sidebar_button_create_chat_desc",
                },
                {
                    "key": "search_chats",
                    "label": "Search Chats",
                    "description": "Show the Search Chats button in the sidebar.",
                    "i18n_label": "sidebar_button_search_chats",
                    "i18n_description": "sidebar_button_search_chats_desc",
                },
                {
                    "key": "workspace",
                    "label": "Workspace",
                    "description": "Show the Workspace button in the sidebar.",
                    "i18n_label": "sidebar_button_workspace",
                    "i18n_description": "sidebar_button_workspace_desc",
                },
                {
                    "key": "automations",
                    "label": "Automations",
                    "description": "Show the Automations button in the sidebar.",
                    "i18n_label": "sidebar_button_automations",
                    "i18n_description": "sidebar_button_automations_desc",
                },
                {
                    "key": "projects",
                    "label": "Projects",
                    "description": "Show the Projects button in the sidebar.",
                    "i18n_label": "sidebar_button_projects",
                    "i18n_description": "sidebar_button_projects_desc",
                },
            ],
        },
    },
    ("chat", "speech_playback_speed"): {
        "type": "number",
        "attributes": FieldAttributes(min=0, max=2),
        "description": "Controls the default playback speed for spoken assistant responses.",
    },
    ("chat", "personality_preset"): {
        "type": "select",
        "label": "Customize Personality Preset",
        "description": "Select the reply personality preset for this user.",
        "options": [
            {"value": "none", "label": "None"},
            {"value": "standard", "label": "Standard"},
            {"value": "professional", "label": "Professional"},
            {"value": "friendly", "label": "Friendly"},
            {"value": "honest", "label": "Honest"},
            {"value": "quirky", "label": "Quirky"},
            {"value": "efficient", "label": "Efficient"},
            {"value": "cynical", "label": "Cynical"},
            {"value": "custom", "label": "Custom"},
        ],
    },
    ("chat", "personality_custom_instruction"): {
        "type": "string",
        "label": "Customize Personality Custom Instruction",
        "description": "Free-form reply-style guidance used when the custom personality preset is selected.",
        "dependency": "personality_preset",
        "dependency_value": "custom",
    },
    ("chat", "byok_statistics_retention_days"): {
        "dependency": "byok_statistics_enabled",
        "dependency_value": True,
    },
    ("login_2fa", "provider"): {
        "type": "select",
        "dependency": "enable_2fa",
        "dependency_value": True,
        "description": "Selected personal 2FA provider for this user.",
        "options": [
            {"value": "totp", "label": "Authenticator App (TOTP)"},
            {"value": "email", "label": "Email OTP"},
        ],
    },
    ("ldap_login", "last_synced_groups"): {
        "type": "string_list",
        "description": "Groups returned during the most recent LDAP synchronization.",
    },
    ("secret", "wrong_sign_in_attempts"): {
        "type": "number",
        "attributes": FieldAttributes(min=0),
    },
}


def _title_case(text: str) -> str:
    """Convert a persisted snake-case setting key into a readable fallback."""

    return text.replace("_", " ").replace("-", " ").title()


def _page_i18n_label(page_key: str) -> str:
    override = _PAGE_I18N_OVERRIDES.get(page_key, {})
    return override.get("label") or ""


def _page_i18n_description(page_key: str) -> str:
    override = _PAGE_I18N_OVERRIDES.get(page_key, {})
    return override.get("description") or ""


def _field_i18n_label(page_key: str, key: str) -> str:
    override = _FIELD_I18N_OVERRIDES.get((page_key, key), {})
    return override.get("label") or ""


def _field_i18n_description(page_key: str, key: str) -> str:
    override = _FIELD_I18N_OVERRIDES.get((page_key, key), {})
    return override.get("description") or ""


def _option_i18n_label(page_key: str, key: str, value: str) -> str:
    return _OPTION_I18N_OVERRIDES.get((page_key, key, value)) or ""


def _field_description(page_key: str, key: str) -> str:
    base = f"Configure {key.replace('_', ' ')} in {page_key.replace('_', ' ')}."
    meta = _USER_SETTING_FIELD_META.get((page_key, key), {})
    return str(meta.get("description") or base)


def _field_type_for_value(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _hydrate_last_model_select_options(
    sections: list[Section], db, user_id: str | None
) -> None:
    """Populate the model selector with models visible to the target user."""

    if not user_id:
        return

    from app.llm.utils import list_user_models

    options: list[Option] = []
    for model in list_user_models(db, user_id) or []:
        if not isinstance(model, dict):
            continue
        model_id = str(model.get("model_id") or "").strip()
        if not model_id:
            continue
        label = str(model.get("name") or model_id)
        options.append(Option(value=model_id, label=label))

    for section in sections:
        if section.key != "chat":
            continue
        for field in section.fields:
            if field.key == "last_model":
                field.options = options
                return


def build_user_settings_schema(
    db,
    include_values: bool,
    user_id: str,
    *,
    externally_managed: bool = False,
) -> UserSettingsFormSchema:
    """Build the safe, admin-facing schema for a selected user's settings.

    The schema is derived from the canonical persisted defaults so newly added
    user preferences automatically appear in the editor. Secret settings are
    deliberately excluded from the administrative response.
    """

    from fastapi import HTTPException, status

    from app.groups.init import get_user_group_setting_value
    from app.users.init import get_user_settings

    admin_hidden_pages = {"secret"}
    temporary_chat_allowed = True
    if user_id:
        temporary_chat_allowed = bool(
            get_user_group_setting_value(user_id, "chat", "allow_temporary_chat", db)
        )

    sections: list[Section] = []
    for page_key, defaults in DEFAULT_USER_SETTINGS.items():
        if not isinstance(defaults, dict):
            continue
        if page_key in admin_hidden_pages:
            continue
        fields: list[FieldSchema] = []
        for key, default_value in defaults.items():
            if externally_managed and is_externally_managed_setting_hidden(
                page_key,
                key,
            ):
                continue
            meta = _USER_SETTING_FIELD_META.get((page_key, key), {})
            field_type = meta.get("type") or _field_type_for_value(default_value)
            options = meta.get("options")
            option_models = None
            if options:
                option_models = []
                for option in options:
                    option_payload = dict(option)
                    if option_payload.get("translatable", True):
                        option_payload.setdefault(
                            "i18n_label",
                            _option_i18n_label(
                                page_key, key, str(option_payload["value"])
                            ),
                        )
                    option_models.append(Option(**option_payload))
            attributes = meta.get("attributes")
            placeholder = meta.get("placeholder")
            field_schema = FieldSchema(
                key=key,
                label=meta.get("label") or _title_case(key),
                description=_field_description(page_key, key),
                type=field_type,  # type: ignore[arg-type]
                options=option_models,
                metadata=meta.get("metadata"),
                attributes=attributes,
                placeholder=placeholder,
                default=default_value,
                hidden=bool(meta.get("hidden"))
                or (
                    page_key == "chat"
                    and key == "always_use_temporary_chat"
                    and not temporary_chat_allowed
                ),
                dependency=meta.get("dependency"),
                dependency_value=meta.get("dependency_value"),
                dependency2=meta.get("dependency2"),
                dependency2_value=meta.get("dependency2_value"),
                i18n_label=_field_i18n_label(page_key, key),
                i18n_description=_field_i18n_description(page_key, key),
            )
            fields.append(field_schema)

        if externally_managed and is_externally_managed_setting_hidden(page_key):
            continue
        page_label = USER_SETTINGS_PAGE_TITLES.get(page_key, _title_case(page_key))
        sections.append(
            Section(
                key=page_key,
                title=page_label,
                description=f"Settings for the {page_label.lower()} page.",
                i18n_title=_page_i18n_label(page_key),
                i18n_description=_page_i18n_description(page_key),
                fields=fields,
            )
        )

    _hydrate_last_model_select_options(sections, db, user_id)

    if include_values:
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="user_id is required when include_values=true",
            )
        user_settings = get_user_settings(user_id, db)
        for section in sections:
            section_values = (
                user_settings.get(section.key, {})
                if isinstance(user_settings, dict)
                else {}
            )
            for field in section.fields:
                field.value = section_values.get(field.key)
    return UserSettingsFormSchema(sections=sections)
