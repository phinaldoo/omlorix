"""Capability discovery for explicit security step-up authentication.

The frontend must not offer credentials that a user has never configured. The
actual verification endpoints still enforce enrollment independently; this
module only provides authenticated, user-specific presentation hints.
"""

from sqlalchemy.orm import Session

from app.auth.models import PasskeyCredential
from app.auth.twofa_provider import (
    _is_user_enrolled_for_provider,
    resolve_user_2fa_provider,
)
from app.settings.utils import get_login_passkey_policy
from app.users.init import get_user_setting_value
from app.users.external_management import is_externally_managed


def _has_usable_password(user, db: Session) -> bool:
    """Return whether the user knows a local password accepted for step-up.

    Federated accounts receive an internal placeholder hash during creation.
    The setup flags distinguish that placeholder from a password the user has
    deliberately configured and can enter in the dialog.
    """

    if not getattr(user, "hashed_password", None):
        return False
    needs_social_password = bool(
        get_user_setting_value(user.id, "social_login", "needs_password_setup", db)
    )
    needs_sso_password = bool(
        get_user_setting_value(user.id, "sso_login", "needs_password_setup", db)
    )
    # LDAP JIT users also receive a random internal placeholder hash. It is not
    # a credential the user knows and must never be offered as step-up.
    ldap_managed_password = bool(
        get_user_setting_value(user.id, "ldap_login", "linked", db)
    )
    return not (needs_social_password or needs_sso_password or ldap_managed_password)


def _has_active_passkey(user_id: str, db: Session) -> bool:
    """Return whether passkey verification is enabled and one is enrolled."""

    if not bool(get_login_passkey_policy(db).get("enable_passkeys")):
        return False
    return bool(
        db.query(PasskeyCredential)
        .filter(
            PasskeyCredential.user_id == user_id,
            PasskeyCredential.is_active.is_(True),
        )
        .count()
    )


def get_step_up_methods(user, db: Session) -> dict[str, bool]:
    """Return only the step-up methods currently usable by this user."""

    if is_externally_managed(user):
        return {"password": False, "otp": False, "passkey": False}

    otp_provider = resolve_user_2fa_provider(user, db)
    return {
        "password": _has_usable_password(user, db),
        "otp": bool(_is_user_enrolled_for_provider(user.id, otp_provider, db)),
        "passkey": _has_active_passkey(user.id, db),
    }


def require_sensitive_action_auth(user, token: str, db: Session) -> str:
    """Protect factor bootstrap without locking out federated-only accounts.

    A user with any pre-existing usable factor must explicitly prove one of
    those factors. A user with no usable factor may bootstrap only from a
    freshly authenticated session. Refresh rotation does not refresh that
    session creation timestamp, so a long-lived stolen session cannot qualify.
    """

    from app.auth.token import require_recent_auth_token, require_step_up_auth_token
    from app.auth.models import mark_authentication_step_up

    methods = get_step_up_methods(user, db)
    if any(methods.values()):
        require_step_up_auth_token(token, db)
        return "step_up"
    require_recent_auth_token(token, db)
    # Persist the short-lived proof so deferred OAuth callbacks can enforce the
    # same policy without carrying the raw access token through the browser.
    mark_authentication_step_up(db, user.id, token, "recent_auth")
    return "recent_auth"
