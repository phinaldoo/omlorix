from sqlalchemy.orm.attributes import flag_modified
from fastapi import HTTPException, status
from typing import Any, Dict, Tuple
from sqlalchemy.orm import Session
from copy import deepcopy
import json

from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.models import get_user
from app.users.timezones import normalize_user_timezone



# In this file we will initialize the user settings.
# Everytime a settings is requestet or changed, we will check if the settings in the db and default settings are the same.
# If not, we will update the settings in the db.
# If the settings are not in the db, we will create them.
# If settings are in the db, but not in the default settings, we will delete them in the db.
# Functions: get_user_settings, update_user_settings, delete_user_settings

"""User-level settings synchronisation helpers.

This module keeps the *settings* column of the ``users`` table in sync with
:pydata:`app.users.defaults.DEFAULT_USER_SETTINGS`.

Design principles (similar to settings/init.py):

1. The structure defined in ``DEFAULT_USER_SETTINGS`` is the single source
   of truth for the schema of user settings.
2. Every time a user setting is accessed or mutated we first make sure the
   DB representation contains **exactly** the keys defined in the defaults:
   •  Missing pages/keys are added with the default value.
   •  Obsolete pages/keys are removed.
3. A thin public API is provided:
   •  ``get_user_settings`` – fetches the *fully synchronised* settings dict.
   •  ``update_user_settings`` – updates an individual key.

The helper is intentionally stateless – no cross-request cache – because the
settings payload is typically tiny and the overhead is negligible compared to
DB latency.
"""


def _normalize_positive_revision(value: Any) -> int | None:
    """Return a positive policy revision, or ``None`` for an invalid value."""

    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _normalize_privacy_policy_state(
    existing_page: Dict[str, Any],
) -> tuple[bool, Dict[str, Any]]:
    """Keep the current privacy-policy interaction fields internally consistent."""

    if not isinstance(existing_page, dict):
        return False, existing_page

    normalized = deepcopy(existing_page)
    changed = False

    normalized_revision = _normalize_positive_revision(
        normalized.get("privacy_policy_last_interacted_revision")
    )
    normalized_accepted = bool(normalized.get("privacy_policy_accepted"))
    if normalized_accepted and normalized_revision is None:
        normalized_accepted = False
        changed = True

    if normalized.get("privacy_policy_last_interacted_revision") != normalized_revision:
        normalized["privacy_policy_last_interacted_revision"] = normalized_revision
        changed = True
    if normalized.get("privacy_policy_accepted") is not normalized_accepted:
        normalized["privacy_policy_accepted"] = normalized_accepted
        changed = True

    return changed, normalized


def _coerce_sidebar_button_visibility_value(value: Any) -> tuple[dict[str, bool], bool]:
    defaults = DEFAULT_USER_SETTINGS.get("chat", {}).get("sidebar_button_visibility", {})
    if not isinstance(defaults, dict):
        return {}, not isinstance(value, dict)

    if not isinstance(value, dict):
        return deepcopy(defaults), True

    normalized = {}
    changed = False
    for key, default_value in defaults.items():
        if key in value:
            normalized_value = bool(value[key])
        else:
            normalized_value = bool(default_value)
            changed = True
        normalized[key] = normalized_value
        if value.get(key) is not normalized_value:
            changed = True

    if set(value) - set(defaults):
        changed = True

    return normalized, changed


def _coerce_llm_access_permissions_value(value: Any) -> tuple[dict[str, bool], bool]:
    """Keep persisted personal-context permissions aligned with supported fields.

    Permissions are nested inside one settings key, so the page-level settings
    synchronizer cannot otherwise remove an obsolete permission such as the
    retired user locale field.
    """

    defaults = DEFAULT_USER_SETTINGS.get("security", {}).get(
        "allow_llm_to_access_personal_information", {}
    )
    if not isinstance(defaults, dict):
        return {}, not isinstance(value, dict)

    if isinstance(value, bool):
        return {key: value for key in defaults}, True
    if not isinstance(value, dict):
        return deepcopy(defaults), True

    normalized = {}
    for key, default_value in defaults.items():
        raw_value = value.get(key, default_value)
        # Permission synchronization is a security boundary. Values imported
        # from legacy archives must be real booleans; truthy strings such as
        # ``"false"`` must never grant personal-information access.
        normalized[key] = (
            raw_value if isinstance(raw_value, bool) else default_value
        )
    changed = set(value) != set(defaults) or any(
        value.get(key) is not normalized_value
        for key, normalized_value in normalized.items()
    )
    return normalized, changed


def _coerce_user_setting_value(page_name: str, key_name: str, value: Any) -> tuple[Any, bool]:
    changed = False
    if page_name == "chat" and key_name == "sidebar_button_visibility":
        value, sidebar_changed = _coerce_sidebar_button_visibility_value(value)
        changed = changed or sidebar_changed
    if (
        page_name == "security"
        and key_name == "allow_llm_to_access_personal_information"
    ):
        value, permissions_changed = _coerce_llm_access_permissions_value(value)
        changed = changed or permissions_changed
    return value, changed


# -------------------
# Parse settings
# -------------------
def _parse_settings(raw: Any) -> Dict[str, Any]:
    """Parse the JSON stored in ``users.settings``.

    Returns an empty dict if the field is empty/invalid.
    """
    if isinstance(raw, dict):
        return deepcopy(raw)
    if not raw or raw in {"-", "null", "None"}:
        return {}
    try:
        parsed: Dict[str, Any] = json.loads(raw)
        if not isinstance(parsed, dict):
            # Malformed structure – start fresh.
            return {}
        return parsed
    except (TypeError, ValueError):
        return {}



# -------------------
# Sync with defaults
# -------------------
def _sync_with_defaults(current: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """Return a *new* dict that matches DEFAULT_USER_SETTINGS.

    The returned tuple is (changed?, new_data).
    """
    changed = False

    # Start with a deep copy of defaults so we always have required pages/keys
    merged = deepcopy(DEFAULT_USER_SETTINGS)

    # Overlay *existing* values that are still valid
    for page, page_data in merged.items():
        if not isinstance(page_data, dict):
            # A page in defaults must be a dict – skip overlay if not.
            continue
        existing_page = current.get(page, {})
        if not isinstance(existing_page, dict):
            changed = True
            continue

        if page == "states":
            privacy_state_changed, existing_page = _normalize_privacy_policy_state(
                existing_page
            )
            changed = changed or privacy_state_changed

        # Copy over keys that still exist in defaults
        for key in page_data:
            if key in existing_page:
                value, value_changed = _coerce_user_setting_value(page, key, existing_page[key])
                merged[page][key] = value
                changed = changed or value_changed

        # Detect obsolete keys present in DB but not in defaults
        obsolete_keys = set(existing_page) - set(page_data)
        if obsolete_keys:
            changed = True  # keys will be dropped

    # Detect pages that have been removed from defaults
    removed_pages = set(current) - set(DEFAULT_USER_SETTINGS)
    if removed_pages:
        changed = True

    # Detect completely new pages added in defaults (already covered by deepcopy)
    # and missing pages in DB.
    if changed is False:
        # Quick path: if the structure lengths differ there was a change.
        if len(current) != len(merged):
            changed = True

    return changed, merged



# -------------------
# Get user settings
# -------------------
def get_user_settings(user_id: str, db: Session, commit: bool = True) -> Dict[str, Any]:  # noqa: D401
    """Return the synchronised settings for *user_id*.

    Automatic synchronisation is committed by default. With ``commit=False``
    it is staged in the caller's transaction so security mutations and their
    durable outbox records cannot be split by an implicit commit.
    """
    user = get_user(db, user_id)

    # Load & sync
    current_settings = _parse_settings(user.settings)
    changed, merged = _sync_with_defaults(current_settings)

    if not isinstance(user.settings, dict):
        changed = True

    if changed:
        user.settings = merged
        flag_modified(user, "settings")  # Ensure update even if JSON string is identical length
        if commit:
            db.commit()
            db.refresh(user)

    return merged



# -------------------
# Update user settings
# -------------------
def update_user_settings(
    user_id: str,
    page_name: str,
    key_name: str,
    value: Any,
    db: Session,
    commit: bool = True,
) -> Dict[str, Any]:
    """Update *value* for ``page_name.key_name`` and return the full settings dict."""

    # First, synchronise settings so the DB state is guaranteed to match the
    # current defaults before we attempt any validation or mutation.
    current = get_user_settings(user_id, db, commit=commit)

    if page_name not in DEFAULT_USER_SETTINGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    if key_name not in DEFAULT_USER_SETTINGS[page_name]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    if page_name == "general" and key_name == "timezone":
        try:
            value = normalize_user_timezone(value if isinstance(value, str) else str(value or ""))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    value, _ = _coerce_user_setting_value(page_name, key_name, value)

    user = get_user(db, user_id)

    if current[page_name][key_name] == value:
        return current  # No change required

    current[page_name][key_name] = value
    user.settings = current
    flag_modified(user, "settings")
    if commit:
        db.commit()
        db.refresh(user)

    # Dont return the whole settings, only the page and key
    return {page_name: {key_name: value}}



# -------------------
# Bulk update user settings
# -------------------
def update_user_settings_bulk(
    user_id: str,
    updates: Dict[str, Dict[str, Any]] | None,
    db: Session,
    commit: bool = True,
    *,
    allow_secret_page: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Update multiple settings pages/keys in a single transaction."""

    if not updates:
        return {}

    current = get_user_settings(user_id, db, commit=commit)
    user = get_user(db, user_id)

    changed: Dict[str, Dict[str, Any]] = {}

    for page_name, page_updates in updates.items():
        if page_name not in DEFAULT_USER_SETTINGS:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Page '{page_name}' not found")
        if not allow_secret_page and page_name == "secret":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The 'secret' settings page cannot be updated via this endpoint.",
            )
        if page_updates is None:
            continue
        if not isinstance(page_updates, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid payload for page '{page_name}'")

        for key_name, value in page_updates.items():
            if key_name not in DEFAULT_USER_SETTINGS[page_name]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Key '{page_name}.{key_name}' not found")

            if page_name == "general" and key_name == "timezone":
                try:
                    value = normalize_user_timezone(value if isinstance(value, str) else str(value or ""))
                except ValueError as exc:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
            value, _ = _coerce_user_setting_value(page_name, key_name, value)

            if current[page_name].get(key_name) == value:
                continue

            current[page_name][key_name] = value
            changed.setdefault(page_name, {})[key_name] = value

    if not changed:
        return {}

    user.settings = current
    flag_modified(user, "settings")
    if commit:
        db.commit()
        db.refresh(user)

    return changed


# -------------------
# Get user setting value
# -------------------
def get_user_setting_value(
    user_id: str,
    page_name: str,
    key_name: str,
    db: Session,
    *,
    commit: bool = True,
) -> Any:
    """Return the value of ``page_name.key_name`` for a specific user.

    The function automatically synchronises settings with defaults first.
    Raises 404 errors if the page or key does not exist in *defaults* (keeps
    behaviour consistent with other helpers).
    """

    # Always synchronise the persisted structure first so that obsolete pages/keys
    # are removed even if the caller references them (they will still result in
    # a 404 after the sync, but the DB stays clean).
    settings_dict = get_user_settings(user_id, db, commit=commit)

    if page_name not in DEFAULT_USER_SETTINGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    if key_name not in DEFAULT_USER_SETTINGS[page_name]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    return settings_dict[page_name][key_name]
