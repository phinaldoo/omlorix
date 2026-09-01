"""Authoritative policy helpers for organization-managed user accounts.

Enterprise identity providers must remain the only authentication authority for
managed accounts.  Keeping this decision on the user row lets every backend
entry point enforce the same policy without trusting frontend visibility or a
mutable provider-link flag.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException


AUTH_MANAGEMENT_LOCAL = "local"
AUTH_MANAGEMENT_EXTERNAL = "external"
EXTERNAL_MANAGEMENT_DENIED_DETAIL = (
    "This account is managed by your organization. Use enterprise sign-in."
)

# These pages contain local authentication factors or provider-owned identity
# metadata. They must never be presented as ordinary admin-editable user
# preferences once the organization is authoritative for the account.
EXTERNALLY_MANAGED_HIDDEN_SETTINGS_PAGES = frozenset(
    {
        "login_2fa",
        "social_login",
        "sso_login",
        "scim",
        "ldap_login",
    }
)
EXTERNALLY_MANAGED_HIDDEN_SETTINGS_FIELDS = {
    "security": frozenset({"has_to_change_password"}),
}


def is_externally_managed(user) -> bool:
    """Return whether local profile and authentication controls are disabled."""

    return (
        str(getattr(user, "auth_management_mode", AUTH_MANAGEMENT_LOCAL) or "")
        .strip()
        .lower()
        == AUTH_MANAGEMENT_EXTERNAL
    )


def require_locally_managed_account(user) -> None:
    """Reject a local account-security action for an externally managed user."""

    if is_externally_managed(user):
        raise HTTPException(status_code=403, detail=EXTERNAL_MANAGEMENT_DENIED_DETAIL)


def is_externally_managed_setting_hidden(page: str, field: str | None = None) -> bool:
    """Return whether an admin setting belongs to external identity control."""

    normalized_page = str(page or "").strip().lower()
    if normalized_page in EXTERNALLY_MANAGED_HIDDEN_SETTINGS_PAGES:
        return True
    if field is None:
        return False
    normalized_field = str(field or "").strip()
    return normalized_field in EXTERNALLY_MANAGED_HIDDEN_SETTINGS_FIELDS.get(
        normalized_page,
        frozenset(),
    )


def require_externally_managed_settings_update_allowed(user, updates) -> None:
    """Reject admin writes to IdP-owned settings for a managed account."""

    if not is_externally_managed(user) or not isinstance(updates, dict):
        return
    for page, page_updates in updates.items():
        if is_externally_managed_setting_hidden(page):
            raise HTTPException(
                status_code=409,
                detail="Authentication settings for this account are managed by the organization.",
            )
        if not isinstance(page_updates, dict):
            continue
        if any(
            is_externally_managed_setting_hidden(page, field)
            for field in page_updates
        ):
            raise HTTPException(
                status_code=409,
                detail="Authentication settings for this account are managed by the organization.",
            )


def _lock_user_for_external_management(db, user_id: str):
    """Lock and refresh the authority row before changing its auth mode."""

    from app.users.models import User

    return (
        db.query(User)
        .populate_existing()
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )


def mark_user_externally_managed(
    db,
    user,
    provider: str,
    *,
    commit: bool = True,
) -> bool:
    """Persist one-way organization management and revoke older local sessions.

    The public application intentionally exposes no reverse operation.  A
    future administrative recovery workflow can only convert an account back
    after assigning a new local credential and recording an explicit audit
    reason.  Existing password, passkey, social, and 2FA material is retained
    only for forensic/admin cleanup; all runtime entry points reject it.
    """

    user = _lock_user_for_external_management(db, str(getattr(user, "id", "") or ""))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    normalized_provider = str(provider or "enterprise_sso").strip().lower()
    was_managed = is_externally_managed(user)
    previous_provider = str(getattr(user, "external_auth_provider", "") or "")
    if normalized_provider == "scim" and was_managed and previous_provider:
        # SCIM owns lifecycle/profile synchronization, but a concrete OIDC or
        # SAML provider remains the useful sign-in label.
        normalized_provider = previous_provider

    user.auth_management_mode = AUTH_MANAGEMENT_EXTERNAL
    user.external_auth_provider = normalized_provider
    if getattr(user, "externally_managed_at", None) is None:
        user.externally_managed_at = datetime.now(timezone.utc)
    db.add(user)

    # Federated placeholder-password flags must not redirect the fresh SSO
    # session into a local password setup flow.
    from app.users.init import update_user_settings_bulk

    update_user_settings_bulk(
        user.id,
        {
            "security": {"has_to_change_password": False},
            "social_login": {"needs_password_setup": False},
            "sso_login": {"needs_password_setup": False},
        },
        db,
        commit=False,
    )

    changed = (not was_managed) or previous_provider != normalized_provider
    if changed:
        # A pre-existing local browser session is an alternate route around the
        # newly authoritative IdP. Revoke it before the callback issues the new
        # enterprise-authenticated session.
        from app.auth.models import (
            delete_authentication_all,
            delete_user_transient_auth_state,
            invalidate_user_password_reset_tokens,
        )
        from app.email.change import cancel_pending_email_changes
        from app.email.models import cancel_user_email

        delete_authentication_all(
            db,
            user.id,
            commit=False,
            revoke_cached=False,
        )
        delete_user_transient_auth_state(db, user.id, commit=False)
        invalidate_user_password_reset_tokens(db, user.id, commit=False)
        cancel_pending_email_changes(db, user.id)
        # Keep already-staged security notices, but prevent reset codes, OTPs,
        # and email-change proofs created under the former local authority from
        # being delivered after the transition.
        cancel_user_email(
            db,
            user.id,
            preserve_template_types=("security_event",),
            commit=False,
        )

    if commit:
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(user)
    else:
        # Let SCIM include this state in its wider user/group transaction.
        db.flush()

    if changed:
        # Cached tokens are outside the SQL transaction. Revoke them only once
        # all SQL changes are staged successfully; an early cache revocation is
        # still fail-closed if the wider SCIM transaction later rolls back.
        from app.auth.session_store import revoke_user_sessions

        revoke_user_sessions(user.id)
    return changed
