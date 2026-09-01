"""Persistence and policy for user-managed social sign-in identities.

Social login providers are authentication methods, not reusable OAuth service
connections. This module therefore stores no access or refresh tokens: it keeps
only the immutable upstream identity needed to recognize a future sign-in.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import (
    Authentication,
    PasskeyCredential,
    SocialAuthIdentity,
    delete_user_transient_auth_state,
)
from app.auth.social import SocialAuthProviderFactory
from app.network.policy import OutboundRequestBlockedError, assert_url_allowed
from app.settings.utils import get_login_passkey_policy
from app.users.init import get_user_setting_value, update_user_settings_bulk
from app.users.models import User, normalize_utc_datetime
from app.users.external_management import (
    is_externally_managed,
    require_locally_managed_account,
)


SOCIAL_PROVIDER_ISSUERS = {
    "google": "https://accounts.google.com",
    "github": "https://github.com",
    "slack": "https://slack.com",
    "microsoft": "https://login.microsoftonline.com",
    "apple": "https://appleid.apple.com",
}
SOCIAL_PROVIDER_LABELS = {
    "google": "Google",
    "github": "GitHub",
    "slack": "Slack",
    "microsoft": "Microsoft",
    "apple": "Apple",
}
SOCIAL_PROVIDER_ORDER = ("google", "github", "microsoft", "apple", "slack")
SOCIAL_LINK_STEP_UP_MAX_AGE_SECONDS = 10 * 60


def normalize_social_provider(provider: str) -> str:
    """Validate and normalize a supported social-login provider name."""
    normalized = str(provider or "").strip().lower()
    if normalized not in SocialAuthProviderFactory.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown social login provider.")
    return normalized


def social_identity_subject(user_info: dict[str, Any]) -> str:
    """Extract the provider's immutable account subject."""
    for key in ("sub", "id", "provider_user_id"):
        subject = str(user_info.get(key) or "").strip()
        if subject:
            return subject
    return ""


def social_identity_issuer(provider: str, user_info: dict[str, Any]) -> str:
    """Scope subjects that are only unique inside a workspace or tenant."""
    base_issuer = SOCIAL_PROVIDER_ISSUERS[provider]
    if provider == "slack":
        workspace_id = str(user_info.get("workspace_id") or "").strip().upper()
        return f"{base_issuer}/workspace/{workspace_id}" if workspace_id else base_issuer
    if provider == "microsoft":
        tenant_id = str(user_info.get("tenant_id") or "").strip().lower()
        return f"{base_issuer}/{tenant_id}/v2.0" if tenant_id else base_issuer
    return base_issuer


def social_identity_hash(provider: str, issuer: str, subject: str) -> str:
    """Return a stable, non-reversible ownership key for a provider subject."""
    payload = f"{provider}\x00{issuer}\x00{subject}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _provider_is_available(provider: str, db: Session) -> bool:
    """Return whether a provider can currently complete a login OAuth flow."""
    try:
        social_provider = SocialAuthProviderFactory.get_provider(provider, db)
        if not social_provider.is_enabled():
            return False
        assert_url_allowed(
            db,
            url=getattr(social_provider, "AUTHORIZATION_URL", None),
            feature=f"social login provider '{provider}'",
        )
        return True
    except (HTTPException, OutboundRequestBlockedError):
        return False


def _password_is_configured(
    user_id: str,
    db: Session,
    *,
    commit: bool = True,
) -> bool:
    """Distinguish a real local password from a federated placeholder hash."""
    needs_social_password = bool(
        get_user_setting_value(
            user_id,
            "social_login",
            "needs_password_setup",
            db,
            commit=commit,
        )
    )
    needs_sso_password = bool(
        get_user_setting_value(
            user_id,
            "sso_login",
            "needs_password_setup",
            db,
            commit=commit,
        )
    )
    return not (needs_social_password or needs_sso_password)


def _active_passkey_count(user_id: str, db: Session) -> int:
    """Count passkeys only when passkey sign-in is enabled globally."""
    if not bool(get_login_passkey_policy(db).get("enable_passkeys")):
        return 0
    return int(
        db.query(PasskeyCredential)
        .filter(
            PasskeyCredential.user_id == user_id,
            PasskeyCredential.is_active.is_(True),
        )
        .count()
    )


def _legacy_social_linked(
    user_id: str,
    provider: str,
    db: Session,
    *,
    commit: bool = True,
) -> bool:
    """Return whether a complete legacy provider binding remains usable."""
    linked = bool(
        get_user_setting_value(
            user_id,
            "social_login",
            f"{provider}_linked",
            db,
            commit=commit,
        )
    )
    subject = str(
        get_user_setting_value(
            user_id,
            "social_login",
            f"{provider}_user_id",
            db,
            commit=commit,
        )
        or ""
    ).strip()
    return linked and bool(subject)


def _legacy_social_identity_owner_ids(
    db: Session,
    provider: str,
    subject: str,
) -> set[str]:
    """Find every user that still owns a subject in compatibility settings.

    Legacy settings may be encrypted and therefore cannot reliably be matched
    with a database JSON expression. Linking is an infrequent account-security
    operation, so scanning users in bounded batches is the reliable migration
    bridge until each legacy binding has been normalized.
    """
    # Database failures must propagate. Treating an unavailable ownership scan
    # as "unowned" would turn an operational error into an account takeover
    # opportunity.
    users = db.query(User).yield_per(200)

    owner_ids: set[str] = set()
    for user in users:
        settings = getattr(user, "settings", None)
        social_settings = settings.get("social_login") if isinstance(settings, dict) else None
        if not isinstance(social_settings, dict):
            continue
        stored_subject = str(social_settings.get(f"{provider}_user_id") or "").strip()
        if stored_subject and secrets.compare_digest(stored_subject, subject):
            owner_ids.add(str(user.id))
    return owner_ids


def _identity_rows_by_provider(user_id: str, db: Session) -> dict[str, SocialAuthIdentity]:
    """Load normalized identities for one user without exposing their subjects."""
    rows = (
        db.query(SocialAuthIdentity)
        .filter(SocialAuthIdentity.user_id == user_id)
        .all()
    )
    return {str(row.provider): row for row in rows}


def _masked_account_hint(value: str | None) -> str | None:
    """Return a useful provider-account hint without exposing the full address."""
    text = str(value or "").strip()
    if not text:
        return None
    local, separator, domain = text.partition("@")
    if not separator:
        return f"{text[:2]}***" if len(text) > 2 else "***"
    visible = local[:2] if len(local) > 1 else local[:1]
    return f"{visible}***@{domain}"


def _social_method_snapshot(
    user_id: str,
    db: Session,
    *,
    commit: bool = True,
) -> list[dict[str, Any]]:
    """Build current linked/configured state in a stable provider order."""
    rows = _identity_rows_by_provider(user_id, db)
    snapshot: list[dict[str, Any]] = []
    for provider in SOCIAL_PROVIDER_ORDER:
        row = rows.get(provider)
        linked = row is not None or _legacy_social_linked(
            user_id,
            provider,
            db,
            commit=commit,
        )
        snapshot.append(
            {
                "provider": provider,
                "label": SOCIAL_PROVIDER_LABELS[provider],
                "linked": linked,
                "available": _provider_is_available(provider, db),
                "account_hint": _masked_account_hint(row.account_hint) if row else None,
            }
        )
    return snapshot


def get_sign_in_methods(
    user_id: str,
    db: Session,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Return user-safe sign-in method state and server-calculated actions."""
    # External management is an authentication policy boundary. Database and
    # model errors must propagate instead of being reinterpreted as a local
    # account, which would make this policy check fail open.
    user = db.query(User).filter(User.id == user_id).first()
    externally_managed = bool(user and is_externally_managed(user))
    if externally_managed:
        return {
            "password_configured": False,
            "passkey_count": 0,
            "providers": [],
            "externally_managed": True,
            "external_auth_provider": getattr(user, "external_auth_provider", None),
        }

    password_configured = _password_is_configured(user_id, db, commit=commit)
    passkey_count = _active_passkey_count(user_id, db)
    methods = _social_method_snapshot(user_id, db, commit=commit)

    for method in methods:
        alternatives = int(password_configured) + int(passkey_count > 0)
        alternatives += sum(
            1
            for candidate in methods
            if candidate["provider"] != method["provider"]
            and candidate["linked"]
            and candidate["available"]
        )
        method["can_link"] = bool(method["available"] and not method["linked"])
        method["can_unlink"] = bool(method["linked"] and alternatives > 0)
        method["unlink_blocked_reason"] = (
            None if method["can_unlink"] else "last_sign_in_method"
        ) if method["linked"] else None

    return {
        "password_configured": password_configured,
        "passkey_count": passkey_count,
        "providers": methods,
        "externally_managed": False,
        "external_auth_provider": None,
    }


def find_user_by_social_identity(
    db: Session,
    provider: str,
    user_info: dict[str, Any],
) -> User | None:
    """Resolve an upstream subject through the normalized identity table."""
    normalized = normalize_social_provider(provider)
    subject = social_identity_subject(user_info)
    if not subject:
        return None
    issuer = social_identity_issuer(normalized, user_info)
    subject_hash = social_identity_hash(normalized, issuer, subject)
    identity = (
        db.query(SocialAuthIdentity)
        .filter(
            SocialAuthIdentity.provider == normalized,
            SocialAuthIdentity.issuer == issuer,
            SocialAuthIdentity.subject_hash == subject_hash,
        )
        .first()
    )
    if identity is None:
        return None
    return db.query(User).filter(User.id == identity.user_id).first()


def record_social_identity(
    user_id: str,
    provider: str,
    user_info: dict[str, Any],
    db: Session,
    *,
    commit: bool = True,
) -> SocialAuthIdentity:
    """Create or refresh one verified identity while enforcing global ownership."""
    normalized = normalize_social_provider(provider)
    subject = social_identity_subject(user_info)
    if not subject:
        raise HTTPException(status_code=403, detail="Provider account identity is missing.")
    issuer = social_identity_issuer(normalized, user_info)
    subject_hash = social_identity_hash(normalized, issuer, subject)
    account_hint = str(user_info.get("email") or "").strip() or None

    # A legacy settings binding is already an ownership claim even when its
    # normalized row has not yet been created. Refuse to migrate that claim to
    # another user; successful sign-in by the existing owner lazily normalizes
    # it through this same function.
    legacy_owner_ids = _legacy_social_identity_owner_ids(db, normalized, subject)
    if any(owner_id != user_id for owner_id in legacy_owner_ids):
        raise HTTPException(
            status_code=409,
            detail="This provider account is already connected to another Omlorix user.",
        )

    owner = (
        db.query(SocialAuthIdentity)
        .filter(
            SocialAuthIdentity.provider == normalized,
            SocialAuthIdentity.issuer == issuer,
            SocialAuthIdentity.subject_hash == subject_hash,
        )
        .first()
    )
    if owner is not None and owner.user_id != user_id:
        raise HTTPException(
            status_code=409,
            detail="This provider account is already connected to another Omlorix user.",
        )

    existing = (
        db.query(SocialAuthIdentity)
        .filter(
            SocialAuthIdentity.user_id == user_id,
            SocialAuthIdentity.provider == normalized,
        )
        .first()
    )
    if existing is not None:
        if not secrets.compare_digest(str(existing.subject_hash), subject_hash):
            raise HTTPException(
                status_code=409,
                detail="A different account from this provider is already connected.",
            )
        existing.account_hint = account_hint
        existing.last_used_at = datetime.now(timezone.utc)
        identity = existing
    else:
        identity = SocialAuthIdentity(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider=normalized,
            issuer=issuer,
            subject=subject,
            subject_hash=subject_hash,
            account_hint=account_hint,
            created_at=datetime.now(timezone.utc),
            last_used_at=datetime.now(timezone.utc),
        )
        db.add(identity)

    # Keep compatibility fields synchronized while older import/export and
    # administration surfaces still understand them.
    update_user_settings_bulk(
        user_id,
        {
            "social_login": {
                f"{normalized}_linked": True,
                f"{normalized}_user_id": subject,
            }
        },
        db,
        commit=False,
    )
    if commit:
        try:
            db.commit()
            db.refresh(identity)
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="This provider account could not be connected because its ownership changed.",
            ) from exc
    return identity


def validate_social_link_session(
    context: dict[str, str],
    provider: str,
    db: Session,
) -> str:
    """Validate callback purpose, provider, live session, and fresh step-up."""
    normalized = normalize_social_provider(provider)
    if context.get("provider") != normalized:
        raise HTTPException(status_code=403, detail="Social link provider mismatch.")
    authentication = (
        db.query(Authentication)
        .filter(
            Authentication.id == context.get("authentication_id"),
            Authentication.user_id == context.get("user_id"),
        )
        .first()
    )
    if authentication is None:
        raise HTTPException(status_code=401, detail="The linking session is no longer active.")
    stepped_up_at = normalize_utc_datetime(authentication.step_up_authenticated_at)
    if stepped_up_at is None:
        raise HTTPException(status_code=403, detail="Step-up authentication required.")
    age = (datetime.now(timezone.utc) - stepped_up_at).total_seconds()
    if age < 0 or age > SOCIAL_LINK_STEP_UP_MAX_AGE_SECONDS:
        raise HTTPException(status_code=403, detail="Step-up authentication required.")
    return str(authentication.user_id)


def validated_social_link_identity_claims(
    provider: str,
    user_info: dict[str, Any],
    db: Session,
) -> dict[str, str]:
    """Validate policy and reduce a verified provider response for deferred linking."""
    normalized = normalize_social_provider(provider)
    social_provider = SocialAuthProviderFactory.get_provider(normalized, db)
    email = str(user_info.get("email") or "").strip()
    normalized_email = email.lower()
    if not normalized_email:
        raise HTTPException(status_code=403, detail="The provider did not return an email address.")
    if not social_provider.validate_domain(normalized_email):
        raise HTTPException(status_code=403, detail="This provider account is not allowed by the domain policy.")
    validate_identity = getattr(social_provider, "validate_identity", lambda _info: True)
    if not validate_identity(user_info):
        raise HTTPException(status_code=403, detail="This provider account is not allowed by the identity policy.")

    subject = social_identity_subject(user_info)
    if not subject:
        raise HTTPException(status_code=403, detail="Provider account identity is missing.")
    claims = {
        "sub": subject,
        "email": email,
    }
    for key in ("workspace_id", "tenant_id"):
        value = str(user_info.get(key) or "").strip()
        if value:
            claims[key] = value
    return claims


def link_social_identity(
    user_id: str,
    provider: str,
    user_info: dict[str, Any],
    db: Session,
) -> SocialAuthIdentity:
    """Validate provider policy and link the proven upstream identity."""
    normalized = normalize_social_provider(provider)
    claims = validated_social_link_identity_claims(normalized, user_info, db)

    # Serialize identity changes for this account so two requests cannot both
    # make decisions from stale ownership or fallback-method state.
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    require_locally_managed_account(user)
    identity = record_social_identity(user_id, normalized, claims, db, commit=False)
    from app.email.service import enqueue_security_event

    try:
        enqueue_security_event(
            db,
            user=user,
            event_type="social_linked",
            source_id=f"{normalized}:{identity.id}",
        )
        db.commit()
        db.refresh(identity)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This provider account could not be connected because its ownership changed.",
        ) from exc
    except Exception:
        db.rollback()
        raise
    return identity


def unlink_social_identity(user_id: str, provider: str, db: Session) -> dict[str, Any]:
    """Remove an identity atomically without allowing account lockout."""
    normalized = normalize_social_provider(provider)
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    require_locally_managed_account(user)

    methods = get_sign_in_methods(user_id, db, commit=False)
    selected = next(
        (item for item in methods["providers"] if item["provider"] == normalized),
        None,
    )
    if not selected or not selected["linked"]:
        raise HTTPException(status_code=404, detail="This provider is not connected.")
    if not selected["can_unlink"]:
        raise HTTPException(
            status_code=409,
            detail="Set a password, add a passkey, or connect another provider before disconnecting this sign-in method.",
        )

    (
        db.query(SocialAuthIdentity)
        .filter(
            SocialAuthIdentity.user_id == user_id,
            SocialAuthIdentity.provider == normalized,
        )
        .delete(synchronize_session=False)
    )
    update_user_settings_bulk(
        user_id,
        {
            "social_login": {
                f"{normalized}_linked": False,
                f"{normalized}_user_id": "",
            }
        },
        db,
        commit=False,
    )
    from app.email.service import enqueue_security_event

    try:
        delete_user_transient_auth_state(db, user_id, commit=False)
        enqueue_security_event(
            db,
            user=user,
            event_type="social_unlinked",
            source_id=f"{normalized}:{datetime.now(timezone.utc).isoformat()}",
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_sign_in_methods(user_id, db)
