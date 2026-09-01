from sqlalchemy import Column, Integer, String, JSON
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException
from sqlalchemy import DateTime
from sqlalchemy import Index
from copy import deepcopy
from collections.abc import Callable
from typing import Any
import os
from cryptography.fernet import Fernet

from app.utils.encryption import encrypt_value, decrypt_value

from app.database import Base
from app.settings.defaults import DEFAULT_SETTINGS
from app.settings.public_urls import normalize_public_urls


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class Settings(Base):
    __tablename__ = "settings"
    __table_args__ = (Index("ix_settings_updated_at", "updated_at"),)
    id = Column(Integer, primary_key=True, index=True)
    page_name = Column(String, unique=True, index=True)
    data = Column(JSON, nullable=False)
    updated_at = Column(DateTime, nullable=False)


_SENSITIVE_VALUE_PREFIX = "enc:v1:"
SENSITIVE_SETTING_RESPONSE_MASK = "********"
_SENSITIVE_SETTINGS_KEYS: set[tuple[str, str]] = {
    ("api_keys", "ipinfo"),
    ("api_keys", "ipstack"),
    ("login_general", "smtp_password"),
    ("login_social", "google_client_secret"),
    ("login_social", "github_client_secret"),
    ("login_social", "slack_client_secret"),
    ("login_social", "microsoft_client_secret"),
    ("login_social", "apple_private_key"),
    ("login_enterprise_sso", "oidc_client_secret"),
    ("login_enterprise_sso", "scim_bearer_token"),
    ("login_enterprise_sso", "scim_previous_bearer_token"),
    ("login_ldap", "ldap_bind_password"),
    ("weather_tool", "api_key"),
    ("secret", "google_client_secret"),
    ("secret", "passkey_padding_secret"),
}


_NESTED_SENSITIVE_SETTINGS_KEYS: dict[tuple[str, str], frozenset[str]] = {
    ("login_enterprise_sso", "saml_advanced_settings"): frozenset({"sp_private_key"}),
}


def _is_sensitive_setting_key(page_name: str, key_name: str) -> bool:
    """Check if the setting key is sensitive."""
    return (page_name, key_name) in _SENSITIVE_SETTINGS_KEYS


def _get_nested_sensitive_setting_keys(page_name: str, key_name: str) -> frozenset[str]:
    """Return nested sensitive keys for a settings page value."""
    return _NESTED_SENSITIVE_SETTINGS_KEYS.get((page_name, key_name), frozenset())


def _transform_nested_sensitive_setting_value(
    value: Any,
    sensitive_keys: frozenset[str],
    transform: Callable[[Any], Any],
) -> Any:
    """Apply a transform to sensitive keys inside a nested setting value."""
    if isinstance(value, dict):
        return {
            nested_key: (
                transform(nested_value)
                if nested_key in sensitive_keys
                else _transform_nested_sensitive_setting_value(
                    nested_value, sensitive_keys, transform
                )
            )
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [
            _transform_nested_sensitive_setting_value(item, sensitive_keys, transform)
            for item in value
        ]
    return value


def is_sensitive_setting_key(page_name: str, key_name: str) -> bool:
    """Return whether a settings key stores sensitive data."""
    return _is_sensitive_setting_key(page_name, key_name)


def get_sensitive_setting_keys_for_page(page_name: str) -> set[str]:
    """Return sensitive key names for a settings page."""
    sensitive_keys = {
        key_name
        for sensitive_page_name, key_name in _SENSITIVE_SETTINGS_KEYS
        if sensitive_page_name == page_name
    }
    return sensitive_keys


def _encrypt_sensitive_scalar_value(
    value: Any, *, treat_value_as_plaintext: bool = True
) -> str:
    """Encrypt one sensitive scalar value."""
    if value is None:
        return ""

    if not isinstance(value, str):
        value = str(value)

    normalized = value
    if not normalized.strip():
        return ""

    if not treat_value_as_plaintext and normalized.startswith(_SENSITIVE_VALUE_PREFIX):
        ciphertext = normalized[len(_SENSITIVE_VALUE_PREFIX) :]
        try:
            decrypt_value(ciphertext)
            return normalized
        except Exception:
            pass

    encrypted = encrypt_value(normalized)
    return f"{_SENSITIVE_VALUE_PREFIX}{encrypted}"


def encrypt_sensitive_setting_value(
    page_name: str,
    key_name: str,
    value: Any,
    *,
    treat_value_as_plaintext: bool = True,
) -> Any:
    """Encrypt a sensitive setting value."""
    if _is_sensitive_setting_key(page_name, key_name):
        return _encrypt_sensitive_scalar_value(
            value, treat_value_as_plaintext=treat_value_as_plaintext
        )

    nested_sensitive_keys = _get_nested_sensitive_setting_keys(page_name, key_name)
    if nested_sensitive_keys:
        return _transform_nested_sensitive_setting_value(
            value,
            nested_sensitive_keys,
            lambda nested_value: _encrypt_sensitive_scalar_value(
                nested_value,
                treat_value_as_plaintext=treat_value_as_plaintext,
            ),
        )

    return value


def _decrypt_sensitive_scalar_value(page_name: str, key_name: str, value: Any) -> str:
    """Decrypt one sensitive scalar value."""
    if value is None:
        return ""

    if not isinstance(value, str):
        raise HTTPException(
            status_code=500,
            detail=f"Stored value for '{page_name}.{key_name}' is invalid.",
        )

    normalized = value
    if not normalized.strip():
        return ""

    if not normalized.startswith(_SENSITIVE_VALUE_PREFIX):
        return normalized

    ciphertext = normalized[len(_SENSITIVE_VALUE_PREFIX) :]
    try:
        return decrypt_value(ciphertext)
    except Exception as exc:
        raise RuntimeError(
            "Unable to decrypt sensitive settings value. "
            "Ensure ENCRYPTION_KEY matches the key used for encryption."
        ) from exc


def decrypt_sensitive_setting_value(page_name: str, key_name: str, value: Any) -> Any:
    """Decrypt a sensitive setting value."""
    if _is_sensitive_setting_key(page_name, key_name):
        return _decrypt_sensitive_scalar_value(page_name, key_name, value)

    nested_sensitive_keys = _get_nested_sensitive_setting_keys(page_name, key_name)
    if nested_sensitive_keys:
        return _transform_nested_sensitive_setting_value(
            value,
            nested_sensitive_keys,
            lambda nested_value: _decrypt_sensitive_scalar_value(
                page_name, key_name, nested_value
            ),
        )

    return value


def ensure_sensitive_settings_page_encrypted(
    page_name: str,
    page_values: dict[str, Any] | None,
    *,
    treat_values_as_plaintext: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """Ensure all sensitive values in a settings page are encrypted."""
    if not isinstance(page_values, dict):
        return False, {}

    encrypted_page = deepcopy(page_values)
    changed = False

    for key_name, raw_value in list(encrypted_page.items()):
        encrypted_value = encrypt_sensitive_setting_value(
            page_name,
            key_name,
            raw_value,
            treat_value_as_plaintext=treat_values_as_plaintext,
        )
        if encrypted_value != raw_value:
            encrypted_page[key_name] = encrypted_value
            changed = True

    return changed, encrypted_page


def decrypt_sensitive_settings_page_data(
    page_name: str,
    page_values: dict[str, Any] | None,
) -> dict[str, Any]:
    """Decrypt all sensitive values in a settings page."""
    if not isinstance(page_values, dict):
        return {}

    decrypted_page = deepcopy(page_values)
    for key_name, raw_value in list(decrypted_page.items()):
        decrypted_page[key_name] = decrypt_sensitive_setting_value(
            page_name, key_name, raw_value
        )
    return decrypted_page


def _mask_sensitive_scalar_value(value: Any) -> str:
    """Mask one sensitive scalar value for API responses without decrypting it."""
    if value is None:
        return ""
    if isinstance(value, str) and not value.strip():
        return ""
    return SENSITIVE_SETTING_RESPONSE_MASK


def mask_sensitive_setting_value(page_name: str, key_name: str, value: Any) -> Any:
    """Mask sensitive setting values for API responses without decrypting them."""
    if _is_sensitive_setting_key(page_name, key_name):
        return _mask_sensitive_scalar_value(value)

    nested_sensitive_keys = _get_nested_sensitive_setting_keys(page_name, key_name)
    if nested_sensitive_keys:
        return _transform_nested_sensitive_setting_value(
            value,
            nested_sensitive_keys,
            _mask_sensitive_scalar_value,
        )

    return value


def mask_sensitive_settings_page_data(
    page_name: str,
    page_values: dict[str, Any] | None,
) -> dict[str, Any]:
    """Mask all sensitive values in a settings page without decrypting them."""
    if not isinstance(page_values, dict):
        return {}

    masked_page = deepcopy(page_values)
    for key_name, raw_value in list(masked_page.items()):
        masked_page[key_name] = mask_sensitive_setting_value(
            page_name, key_name, raw_value
        )
    return masked_page


def _preserve_masked_nested_sensitive_values(
    incoming_value: Any,
    existing_value: Any,
    sensitive_keys: frozenset[str],
) -> Any:
    """Keep stored nested secrets when an import payload uses masked placeholders."""
    if isinstance(incoming_value, dict):
        existing_dict = existing_value if isinstance(existing_value, dict) else {}
        preserved = deepcopy(incoming_value)
        for nested_key, nested_value in list(preserved.items()):
            if nested_key in sensitive_keys:
                if nested_value != SENSITIVE_SETTING_RESPONSE_MASK:
                    continue
                if nested_key in existing_dict:
                    preserved[nested_key] = existing_dict[nested_key]
                else:
                    preserved[nested_key] = ""
                continue
            preserved[nested_key] = _preserve_masked_nested_sensitive_values(
                nested_value,
                existing_dict.get(nested_key),
                sensitive_keys,
            )
        return preserved

    if isinstance(incoming_value, list):
        existing_items = existing_value if isinstance(existing_value, list) else []
        existing_by_id = {
            item.get("id"): item
            for item in existing_items
            if isinstance(item, dict) and item.get("id") is not None
        }
        preserved_items = []
        for index, item in enumerate(incoming_value):
            existing_item = (
                existing_items[index] if index < len(existing_items) else None
            )
            if isinstance(item, dict) and item.get("id") in existing_by_id:
                existing_item = existing_by_id[item.get("id")]
            preserved_items.append(
                _preserve_masked_nested_sensitive_values(
                    item, existing_item, sensitive_keys
                )
            )
        return preserved_items

    return incoming_value


def preserve_masked_sensitive_settings_page_data(
    page_name: str,
    existing_page_values: dict[str, Any] | None,
    incoming_page_values: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep stored secret values when an import payload uses masked placeholders."""
    if not isinstance(incoming_page_values, dict):
        return {}

    preserved_page = deepcopy(incoming_page_values)
    existing_page = (
        existing_page_values if isinstance(existing_page_values, dict) else {}
    )

    for key_name in get_sensitive_setting_keys_for_page(page_name):
        if preserved_page.get(key_name) != SENSITIVE_SETTING_RESPONSE_MASK:
            continue
        if key_name in existing_page:
            preserved_page[key_name] = existing_page[key_name]
        else:
            preserved_page.pop(key_name, None)

    for key_name in list(preserved_page.keys()):
        nested_sensitive_keys = _get_nested_sensitive_setting_keys(page_name, key_name)
        if not nested_sensitive_keys:
            continue
        preserved_page[key_name] = _preserve_masked_nested_sensitive_values(
            preserved_page[key_name],
            existing_page.get(key_name),
            nested_sensitive_keys,
        )

    return preserved_page


def get_settings_page_data(
    db, page_name: str, *, decrypt_sensitive_values: bool = True
) -> dict[str, Any]:
    """Get settings page data with optional decryption."""
    settings_page = get_settings_page(db, page_name)
    if not settings_page or not isinstance(settings_page.data, dict):
        return {}
    if not decrypt_sensitive_values:
        data = deepcopy(settings_page.data)
    else:
        data = decrypt_sensitive_settings_page_data(page_name, settings_page.data)
    return data


def get_settings_value_from_page_data(
    page_name: str, page_data: dict[str, Any] | None, key_name: str
) -> Any:
    """Get a specific value from settings page data."""
    if not isinstance(page_data, dict):
        return None
    value = page_data.get(key_name)
    return decrypt_sensitive_setting_value(page_name, key_name, value)


# -------------------
# Get settings page
# -------------------
def get_settings_page(db, page_name: str):
    """Get a settings page by name."""
    return db.query(Settings).filter(Settings.page_name == page_name).first()


# -------------------
# Update settings page
# -------------------
def update_settings_page(db, page_name: str, key: str, value: str):
    """Update a settings page."""
    settings_page = get_settings_page(db, page_name)
    if not settings_page:
        raise HTTPException(status_code=404, detail="Settings page not found")
    value = encrypt_sensitive_setting_value(
        page_name, key, value, treat_value_as_plaintext=False
    )
    settings_page.data[key] = value
    settings_page.updated_at = datetime.now(timezone.utc)
    flag_modified(settings_page, "data")
    db.commit()
    db.refresh(settings_page)
    return settings_page


# -------------------
# Initialize settings
# -------------------
def initialize_settings(db: Session) -> None:
    """Synchronise the Settings table with DEFAULT_SETTINGS.

    • Removes pages that no longer exist in the defaults.
    • Adds new pages/keys and prunes obsolete keys.
    • Runs everything in a single transaction for atomicity and performance.
    """

    current_time = datetime.now(timezone.utc)

    with db.begin():
        # Load all settings once; build a dict for O(1) look-ups
        existing_pages = {s.page_name: s for s in db.query(Settings).all()}

        legacy_user_retention_values: dict[str, Any] = {}
        legacy_login_passkey_values: dict[str, Any] = {}
        legacy_model_domain_values: dict[str, dict[str, Any]] = {}
        security_page = existing_pages.get("security")
        if security_page and isinstance(security_page.data, dict):
            for key in ("user_deletion_mode", "user_deletion_retention_days"):
                value = security_page.data.get(key)
                if value is not None and value != "":
                    legacy_user_retention_values[key] = value
        login_passkeys_page = existing_pages.get("login_passkeys")
        if login_passkeys_page and isinstance(login_passkeys_page.data, dict):
            value = login_passkeys_page.data.get("enable_passkeys")
            if value is not None and value != "":
                legacy_login_passkey_values["enable_passkeys"] = value

        # Before the settings domains were separated, dictation, read-aloud,
        # and realtime values lived in the models JSON row. Seed a missing new
        # page from those values once, while allowing an already-created page
        # to remain authoritative. The normal key sync below then prunes the
        # moved keys from the models page.
        legacy_models_page = existing_pages.get("models")
        if legacy_models_page and isinstance(legacy_models_page.data, dict):
            for domain_page in ("dictation", "read_aloud", "realtime"):
                domain_defaults = DEFAULT_SETTINGS.get(domain_page, {})
                legacy_model_domain_values[domain_page] = {
                    key: legacy_models_page.data[key]
                    for key in domain_defaults
                    if key in legacy_models_page.data
                }

        # 1. Delete pages that are no longer defined in DEFAULT_SETTINGS
        for page_name in set(existing_pages) - DEFAULT_SETTINGS.keys():
            db.delete(existing_pages[page_name])

        # 2. Add or update pages defined in defaults
        for page_name, default_data in DEFAULT_SETTINGS.items():
            page_default_data = deepcopy(default_data)
            if (
                page_name not in existing_pages
                and page_name in legacy_model_domain_values
            ):
                page_default_data.update(legacy_model_domain_values[page_name])
            if page_name == "users" and legacy_user_retention_values:
                for key, value in legacy_user_retention_values.items():
                    if key in page_default_data:
                        page_default_data[key] = value
            if page_name == "login_general" and legacy_login_passkey_values:
                for key, value in legacy_login_passkey_values.items():
                    if key in page_default_data:
                        page_default_data[key] = value
            if page_name == "about":
                existing_about = existing_pages.get("about")
                if (
                    existing_about
                    and isinstance(existing_about.data, dict)
                    and existing_about.data.get("privacy_policy_notice_mode")
                    == "required_opt_in"
                ):
                    existing_about.data["privacy_policy_notice_mode"] = "modal"
                    existing_about.updated_at = current_time
                    flag_modified(existing_about, "data")
                    page_default_data["privacy_policy_notice_mode"] = "modal"

            _, normalized_default_data = ensure_sensitive_settings_page_encrypted(
                page_name,
                page_default_data,
                treat_values_as_plaintext=False,
            )
            if page_name not in existing_pages:
                # New page → insert
                db.add(
                    Settings(
                        page_name=page_name,
                        data=normalized_default_data,
                        updated_at=current_time,
                    )
                )
                continue

            # Existing page → sync keys
            db_setting = existing_pages[page_name]
            data_changed = False

            if (
                page_name == "about"
                and isinstance(db_setting.data, dict)
                and db_setting.data.get("privacy_policy_notice_mode")
                == "required_opt_in"
            ):
                db_setting.data["privacy_policy_notice_mode"] = "modal"
                data_changed = True

            if page_name == "general" and isinstance(db_setting.data, dict):
                # Public URLs were historically stored as one scalar string.
                # Normalize on startup so every downstream consumer sees the
                # new list representation even before an admin saves the page.
                stored_public_urls = db_setting.data.get("public_url")
                try:
                    normalized_public_urls = normalize_public_urls(
                        stored_public_urls, allow_empty=True
                    )
                except ValueError:
                    # Preserve an invalid legacy value so startup does not erase
                    # operator configuration; validation will surface it in the
                    # admin UI and runtime readiness checks.
                    normalized_public_urls = stored_public_urls
                if normalized_public_urls != stored_public_urls:
                    db_setting.data["public_url"] = normalized_public_urls
                    data_changed = True

            # Add missing keys
            for key, value in normalized_default_data.items():
                if key not in db_setting.data:
                    db_setting.data[key] = value
                    data_changed = True

            # Remove obsolete keys
            for key in list(db_setting.data.keys()):
                if key not in page_default_data:
                    del db_setting.data[key]
                    data_changed = True

            encrypted_changed, encrypted_data = (
                ensure_sensitive_settings_page_encrypted(
                    page_name,
                    db_setting.data,
                    treat_values_as_plaintext=False,
                )
            )
            if encrypted_changed:
                db_setting.data = encrypted_data
                data_changed = True

            if data_changed:
                db_setting.updated_at = datetime.now(timezone.utc)
                flag_modified(db_setting, "data")

    # Validate the independent encryption key used by persisted sensitive settings.
    env_encryption_raw = os.getenv("ENCRYPTION_KEY") or ""
    env_encryption_key = env_encryption_raw.strip()

    if not env_encryption_key:
        raise RuntimeError(
            "\n"
            "══════════════════════════════════════════════════════════════════════\n"
            " ENCRYPTION KEY CONFIGURATION ERROR\n"
            "══════════════════════════════════════════════════════════════════════\n"
            "No encryption key is configured. Set ENCRYPTION_KEY in the environment\n"
            "with a valid Fernet key (32 url-safe base64-encoded bytes).\n"
            'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n'
            "If this key is lost, all encrypted data will be unrecoverable.\n"
            "══════════════════════════════════════════════════════════════════════"
        )

    try:
        Fernet(env_encryption_key)
    except Exception as exc:
        raise RuntimeError(
            "\n"
            "══════════════════════════════════════════════════════════════════════\n"
            " ENCRYPTION KEY CONFIGURATION ERROR\n"
            "══════════════════════════════════════════════════════════════════════\n"
            f"The ENCRYPTION_KEY environment variable is invalid: {exc}\n"
            "Update your .env with a valid Fernet key and restart the service.\n"
            "══════════════════════════════════════════════════════════════════════"
        )
