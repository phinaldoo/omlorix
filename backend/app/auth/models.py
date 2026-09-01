import hashlib
import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    case,
    delete,
    func,
    or_,
    text,
)
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import DateTime
from sqlalchemy.exc import IntegrityError
import uuid

from app.auth.session_store import (
    cache_session,
    revoke_all_sessions,
    revoke_token_digests,
    revoke_user_sessions,
    rotate_access_token as rotate_cached_access_token,
    rotate_session_tokens as rotate_cached_session_tokens,
)
from app.database import Base
from app.utils.ip_restrictions import ip_restrictions_disabled_by_environment
from app.utils.sqlalchemy_encryption import EncryptedString


def _token_hash(token: str | None) -> str:
    normalized = str(token or "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _first_major_version(pattern: str, value: str) -> str:
    match = re.search(pattern, value, re.IGNORECASE)
    return match.group(1) if match else ""


def _coarse_user_agent_platform(user_agent: str) -> str:
    if re.search(r"Macintosh", user_agent, re.IGNORECASE) and re.search(r"Mobile", user_agent, re.IGNORECASE):
        return "iPad; CPU OS"
    if re.search(r"iPad", user_agent, re.IGNORECASE):
        return "iPad; CPU OS"
    if re.search(r"iPhone|iPod", user_agent, re.IGNORECASE):
        return "iPhone; CPU iPhone OS"
    if re.search(r"Android", user_agent, re.IGNORECASE):
        major = _first_major_version(r"Android\s+(\d+)", user_agent)
        device_hint = "; Mobile" if re.search(r"Mobile", user_agent, re.IGNORECASE) else "; Tablet"
        return f"Android {major}{device_hint}".strip()
    if re.search(r"Windows NT", user_agent, re.IGNORECASE):
        major_minor = _first_major_version(r"Windows NT\s+(\d+(?:\.\d+)?)", user_agent)
        return f"Windows NT {major_minor}" if major_minor else "Windows"
    if re.search(r"Macintosh|Mac OS X", user_agent, re.IGNORECASE):
        return "Macintosh; Intel Mac OS X"
    if re.search(r"Ubuntu", user_agent, re.IGNORECASE):
        return "Ubuntu; Linux"
    if re.search(r"Fedora", user_agent, re.IGNORECASE):
        return "Fedora; Linux"
    if re.search(r"Debian", user_agent, re.IGNORECASE):
        return "Debian; Linux"
    if re.search(r"Linux", user_agent, re.IGNORECASE):
        return "Linux"
    return ""


def _coarse_user_agent_browser(user_agent: str) -> str:
    browser_patterns = (
        ("Edg", r"Edg/(\d+)"),
        ("OPR", r"OPR/(\d+)"),
        ("SamsungBrowser", r"SamsungBrowser/(\d+)"),
        ("CriOS", r"CriOS/(\d+)"),
        ("FxiOS", r"FxiOS/(\d+)"),
        ("Firefox", r"Firefox/(\d+)"),
        ("Chrome", r"Chrome/(\d+)"),
        ("Version", r"Version/(\d+)"),
    )
    for name, pattern in browser_patterns:
        major = _first_major_version(pattern, user_agent)
        if major:
            if name == "Version" and "Safari/" not in user_agent:
                continue
            return f"{name}/{major}"
    if "Safari/" in user_agent:
        return "Safari"
    if re.search(r"MSIE|Trident", user_agent, re.IGNORECASE):
        return "Trident"
    return ""


def minimize_session_device_info(device_info: str | None) -> str:
    """Return only coarse, parser-compatible browser/device session details."""
    raw = str(device_info or "").strip()
    if not raw:
        return "Unknown Device"

    platform = _coarse_user_agent_platform(raw)
    browser = _coarse_user_agent_browser(raw)
    if not platform and not browser:
        return "Unknown Device"

    parts = ["Mozilla/5.0"]
    if platform:
        parts.append(f"({platform})")
    if browser:
        parts.append(browser)
    if browser.startswith(("CriOS/", "FxiOS/", "Version/")) or "Safari/" in raw:
        parts.append("Safari/")
    return " ".join(parts)[:160]


def minimize_session_ip_address(ip_address: str | None) -> str | None:
    """Return a coarse network prefix instead of an exact client address."""
    raw = str(ip_address or "").strip()
    if not raw:
        return None

    if "/" in raw:
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            return "Unknown"
        prefix = 24 if network.version == 4 else 64
        return str(network.supernet(new_prefix=prefix)) if network.prefixlen > prefix else str(network)

    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError:
        return "Unknown"

    prefix = 24 if parsed.version == 4 else 64
    network = ipaddress.ip_network(f"{parsed}/{prefix}", strict=False)
    return str(network)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class AuthenticationSigningKeyState(Base):
    """Store only the fingerprint of the operator-managed JWT signing key.

    The singleton row lets startup detect an environment key rotation without
    ever copying the signing key itself into application-managed storage.
    """

    __tablename__ = "authentication_signing_key_state"

    id = Column(Integer, primary_key=True)
    fingerprint = Column(String(64), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class Authentication(Base):
    __tablename__ = "authentication"
    __table_args__ = (
        Index("ix_auth_user", "user_id"),
        Index("ix_auth_access_hash", "access_token_hash"),
        Index("ix_auth_refresh_hash", "refresh_token_hash"),
        UniqueConstraint("access_token_hash", name="uq_access_token_hash"),
        UniqueConstraint("refresh_token_hash", name="uq_refresh_token_hash"),
    )
    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)  # Foreign key to the User table
    device_info = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    access_token = Column(EncryptedString, nullable=False)
    refresh_token = Column(EncryptedString, nullable=False)
    access_token_hash = Column(String(64), nullable=False)
    refresh_token_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False)
    last_active_at = Column(DateTime, nullable=False)
    step_up_authenticated_at = Column(DateTime, nullable=True)
    step_up_method = Column(String, nullable=True)


class SocialAuthIdentity(Base):
    """Bind one Omlorix user to one immutable upstream social identity.

    The raw subject and account hint are encrypted at rest. A deterministic
    digest provides the uniqueness needed to prevent the same provider account
    from being linked to two Omlorix users during concurrent callbacks.
    """

    __tablename__ = "social_auth_identities"
    __table_args__ = (
        Index("ix_social_auth_identities_user", "user_id"),
        UniqueConstraint(
            "provider",
            "issuer",
            "subject_hash",
            name="uq_social_auth_identity_subject",
        ),
        UniqueConstraint(
            "user_id",
            "provider",
            name="uq_social_auth_identity_user_provider",
        ),
    )

    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(32), nullable=False)
    issuer = Column(String(255), nullable=False)
    subject = Column(EncryptedString, nullable=False)
    subject_hash = Column(String(64), nullable=False)
    account_hint = Column(EncryptedString, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class RefreshTokenHistory(Base):
    """Store consumed refresh-token hashes for one device session."""

    __tablename__ = "refresh_token_history"
    __table_args__ = (
        Index("ix_refresh_token_history_session", "session_id"),
        Index("ix_refresh_token_history_user", "user_id"),
        Index("ix_refresh_token_history_expires", "expires_at"),
        UniqueConstraint("token_hash", name="uq_refresh_token_history_hash"),
    )

    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(
        String,
        ForeignKey("authentication.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), nullable=False)
    rotated_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    rotation_reason = Column(String(32), nullable=False, default="refresh")


@dataclass(frozen=True)
class RefreshTokenResolution:
    """Describe how an incoming refresh token relates to a session family."""

    state: str
    authentication: Authentication | None = None
    rotated_at: datetime | None = None


REFRESH_TOKEN_RACE_GRACE = timedelta(seconds=5)


def _normalize_utc(value: datetime | None) -> datetime | None:
    """Normalize database datetimes for safe age comparisons."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_refresh_token_for_rotation(
    db,
    *,
    user_id: str,
    refresh_token: str,
    session_id: str | None,
    now: datetime | None = None,
) -> RefreshTokenResolution:
    """Lock and classify a refresh token without treating absence as replay.

    A replay is confirmed only when the incoming hash exists in the consumed
    history of an active session. Missing sessions and unknown hashes are
    ordinary revoked credentials and must not affect other device sessions.
    """

    token_hash = _token_hash(refresh_token)
    auth = None

    if session_id:
        auth = (
            db.query(Authentication)
            .filter(Authentication.id == session_id, Authentication.user_id == user_id)
            .with_for_update()
            .first()
        )
    else:
        # Pre-family tokens do not contain ``sid``. They remain refreshable
        # once, and their first successful rotation upgrades the session.
        auth = (
            db.query(Authentication)
            .filter(
                Authentication.user_id == user_id,
                Authentication.refresh_token_hash == token_hash,
            )
            .with_for_update()
            .first()
        )
        if auth is None:
            historical = (
                db.query(RefreshTokenHistory)
                .filter(
                    RefreshTokenHistory.user_id == user_id,
                    RefreshTokenHistory.token_hash == token_hash,
                )
                .first()
            )
            if historical is not None:
                auth = (
                    db.query(Authentication)
                    .filter(
                        Authentication.id == historical.session_id,
                        Authentication.user_id == user_id,
                    )
                    .with_for_update()
                    .first()
                )

    if auth is None:
        return RefreshTokenResolution("unknown")
    if auth.refresh_token_hash == token_hash:
        return RefreshTokenResolution("current", authentication=auth)

    historical = (
        db.query(RefreshTokenHistory)
        .filter(
            RefreshTokenHistory.session_id == auth.id,
            RefreshTokenHistory.user_id == user_id,
            RefreshTokenHistory.token_hash == token_hash,
        )
        .first()
    )
    if historical is None:
        return RefreshTokenResolution("unknown", authentication=auth)

    # Capture production time only after the row lock and history lookup have
    # completed. A concurrent request may wait here while another transaction
    # records ``rotated_at``; sampling before that wait creates a negative age
    # and incorrectly classifies the benign race as token reuse.
    current_time = _normalize_utc(now) or datetime.now(timezone.utc)
    rotated_at = _normalize_utc(historical.rotated_at)
    if rotated_at is not None:
        age = current_time - rotated_at
        if timedelta(0) <= age <= REFRESH_TOKEN_RACE_GRACE:
            return RefreshTokenResolution("race", authentication=auth, rotated_at=rotated_at)

    return RefreshTokenResolution("reused", authentication=auth, rotated_at=rotated_at)



# ---------------------------------------------------------------------------
# Passkeys (WebAuthn)
# ---------------------------------------------------------------------------
class PasskeyCredential(Base):
    __tablename__ = "passkey_credentials"
    __table_args__ = (
        Index("ix_passkey_credentials_user", "user_id"),
        Index("ix_passkey_credentials_credential_id", "credential_id"),
        Index(
            "uq_passkey_credential_id_active",
            "credential_id",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    # Base64url-encoded credential ID
    credential_id = Column(String, nullable=False)
    # Base64url-encoded public key
    public_key = Column(String, nullable=False)
    sign_count = Column(String, nullable=False, default="0")

    # Optional metadata
    transports = Column(String, nullable=True)
    name = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)



class WebAuthnChallenge(Base):
    __tablename__ = "webauthn_challenges"
    __table_args__ = (
        Index("ix_webauthn_challenges_user", "user_id"),
        Index("ix_webauthn_challenges_expires", "expires_at"),
        Index("ix_webauthn_challenges_challenge", "challenge"),
    )

    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    flow = Column(String, nullable=False)  # "registration" | "authentication"
    challenge = Column(String, nullable=False)  # base64url
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime, nullable=True)


class NativeAuthGrant(Base):
    """One-time PKCE grants used to bridge system-browser auth back to apps.

    Only hashes of bearer tickets/codes are stored. Provider authorization
    state and nonce validation continue to use the existing signed, HttpOnly
    browser cookies; this table only joins the browser result to the native
    client that proves possession of the PKCE verifier.
    """

    __tablename__ = "native_auth_grants"
    __table_args__ = (
        Index("ix_native_auth_grants_expires", "expires_at"),
        Index("ix_native_auth_grants_consumed", "consumed_at"),
        Index("ix_native_auth_grants_user", "user_id"),
        Index("ix_native_auth_grants_purpose", "purpose"),
    )

    token_hash = Column(String(64), primary_key=True, nullable=False)
    purpose = Column(String(32), nullable=False)
    provider = Column(String(64), nullable=False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    authentication_id = Column(String, nullable=True)
    code_challenge = Column(String(128), nullable=False)
    state_hash = Column(String(64), nullable=False)
    account_mode = Column(String(16), nullable=False, default="primary")
    replace_slot = Column(Integer, nullable=True)
    accepts_terms_of_service = Column(Boolean, nullable=False, default=False)
    terms_of_service_revision = Column(Integer, nullable=True)
    identity_claims = Column(JSON, nullable=True)
    twofa_satisfied = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Short-lived browser authentication actions
# ---------------------------------------------------------------------------
class PendingAuthAction(Base):
    """Indexed lookup state for one-time browser authentication actions.

    The encrypted ``User.settings`` payload remains the source of the
    flow-specific context during the migration away from legacy storage.  This
    table stores only an epoch-bound token hash and prevents public invalid
    token requests from scanning and decrypting every user's settings.
    """

    __tablename__ = "pending_auth_actions"
    __table_args__ = (
        Index(
            "ix_pending_auth_actions_lookup",
            "purpose",
            "token_hash",
            unique=True,
        ),
        Index("ix_pending_auth_actions_expires_at", "expires_at"),
        UniqueConstraint(
            "user_id",
            "purpose",
            name="uq_pending_auth_actions_user_purpose",
        ),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose = Column(String(64), nullable=False)
    token_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# Password reset tokens
# ---------------------------------------------------------------------------
class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        Index("ix_password_reset_tokens_user_id", "user_id"),
        Index("ix_password_reset_tokens_token_hash", "token_hash"),
        Index("ix_password_reset_tokens_expires_at", "expires_at"),
        UniqueConstraint("token_hash", name="uq_password_reset_token_hash"),
    )

    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String, nullable=False)
    requested_ip = Column(String, nullable=True)
    requested_user_agent = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)



# -------------------
# Create Authentication
# -------------------
def create_authentication(
    db,
    user_id,
    device_info,
    ip_address,
    access_token,
    refresh_token,
    *,
    session_id: str | None = None,
    commit: bool = True,
):
    """Create one device session using the JWT family id when provided.

    ``commit=False`` lets session issuance combine creation, occupied-slot
    replacement, and its audit intent in one transaction. Cache publication is
    deliberately deferred until that caller commits.
    """

    auth = Authentication(
        id=session_id or str(uuid.uuid4()),
        user_id=user_id,
        device_info=minimize_session_device_info(device_info),
        ip_address=minimize_session_ip_address(ip_address),
        access_token=access_token,
        refresh_token=refresh_token,
        access_token_hash=_token_hash(access_token),
        refresh_token_hash=_token_hash(refresh_token),
        created_at=datetime.now(timezone.utc),
        last_active_at=datetime.now(timezone.utc),
    )
    db.add(auth)
    if commit:
        db.commit()
        db.refresh(auth)
        cache_session(user_id, access_token, refresh_token)
    else:
        db.flush()
    return auth



# -------------------
# Get Authentication
# -------------------
def get_authentication(db, user_id, token, token_type):
    token_hash = _token_hash(token)
    column = Authentication.access_token_hash if token_type == "access_token" else Authentication.refresh_token_hash
    return (
        db.query(Authentication)
        .filter(Authentication.user_id == user_id, column == token_hash)
        .first()
    )


def get_authentication_by_token(db, token, token_type):
    token_hash = _token_hash(token)
    column = Authentication.access_token_hash if token_type == "access_token" else Authentication.refresh_token_hash
    return db.query(Authentication).filter(column == token_hash).first()


def get_authentication_user_id_by_token(db, token, token_type) -> str | None:
    """Resolve a login owner without loading encrypted credential columns."""
    token_hash = _token_hash(token)
    column = (
        Authentication.access_token_hash
        if token_type == "access_token"
        else Authentication.refresh_token_hash
    )
    row = db.query(Authentication.user_id).filter(column == token_hash).first()
    return str(row.user_id) if row else None


def get_authentication_by_token_hash(db, user_id, token_hash, token_type):
    column = Authentication.access_token_hash if token_type == "access_token" else Authentication.refresh_token_hash
    return (
        db.query(Authentication)
        .filter(Authentication.user_id == user_id, column == token_hash)
        .first()
    )


def mark_authentication_step_up(db, user_id, access_token, method: str):
    auth = get_authentication(db, user_id, access_token, "access_token")
    if not auth:
        raise HTTPException(status_code=401, detail="Access token is no longer valid (revoked)")
    auth.step_up_authenticated_at = datetime.now(timezone.utc)
    auth.step_up_method = str(method or "unknown")[:32]
    db.commit()
    db.refresh(auth)
    return auth



# -------------------
# List Authentication
# -------------------
def list_authentication(db, user_id):
    auth = db.query(Authentication).filter(Authentication.user_id == user_id).all()
    return auth


def list_authentication_login_metadata(db, user_id):
    """List session metadata without loading encrypted token columns."""
    return (
        db.query(
            Authentication.id,
            Authentication.device_info,
            Authentication.ip_address,
            Authentication.last_active_at,
            Authentication.access_token_hash,
        )
        .filter(Authentication.user_id == user_id)
        .all()
    )


def list_authentication_token_hashes(db, user_id):
    """List authentication token hashes without loading encrypted token columns."""
    return (
        db.query(
            Authentication.id,
            Authentication.access_token_hash,
            Authentication.refresh_token_hash,
        )
        .filter(Authentication.user_id == user_id)
        .all()
    )


def get_authentication_token_hashes(db, user_id, auth_id):
    """Get token hashes for one authentication row without loading encrypted tokens."""
    return (
        db.query(
            Authentication.id,
            Authentication.access_token_hash,
            Authentication.refresh_token_hash,
        )
        .filter(Authentication.user_id == user_id, Authentication.id == auth_id)
        .first()
    )



# -------------------
# Update Access Token
# -------------------
def update_access_token(db, user_id, refresh_token, access_token):
    auth = (
        db.query(Authentication)
        .filter(
            Authentication.user_id == user_id,
            Authentication.refresh_token_hash == _token_hash(refresh_token),
        )
        .first()
    )
    if not auth:
        raise HTTPException(status_code=404, detail="Authentication not found")
    previous_access_token = auth.access_token
    auth.access_token = access_token
    auth.access_token_hash = _token_hash(access_token)
    auth.last_active_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(auth)
    rotate_cached_access_token(user_id, previous_access_token, access_token)
    return True


def rotate_authentication_tokens(
    db,
    user_id,
    old_refresh_token,
    new_access_token,
    new_refresh_token,
    *,
    session_id: str | None = None,
    previous_refresh_expires_at: datetime | None = None,
    rotation_reason: str = "refresh",
):
    """Atomically consume one refresh token and install its replacement pair."""

    filters = [
        Authentication.user_id == user_id,
        Authentication.refresh_token_hash == _token_hash(old_refresh_token),
    ]
    if session_id:
        filters.append(Authentication.id == session_id)

    auth = (
        db.query(Authentication)
        .filter(*filters)
        .with_for_update()
        .first()
    )
    if not auth:
        raise HTTPException(status_code=401, detail="Refresh token is no longer valid (revoked)")

    previous_access_token = auth.access_token
    previous_refresh_token = auth.refresh_token
    rotated_at = datetime.now(timezone.utc)
    race_grace_expiry = rotated_at + REFRESH_TOKEN_RACE_GRACE
    history_expiry = max(
        _normalize_utc(previous_refresh_expires_at) or race_grace_expiry,
        race_grace_expiry,
    )

    # Bound history growth per session without adding a global cleanup query to
    # this latency-sensitive authentication path.
    db.query(RefreshTokenHistory).filter(
        RefreshTokenHistory.session_id == auth.id,
        RefreshTokenHistory.expires_at <= rotated_at,
    ).delete(synchronize_session=False)
    db.add(
        RefreshTokenHistory(
            session_id=auth.id,
            user_id=user_id,
            token_hash=auth.refresh_token_hash,
            rotated_at=rotated_at,
            expires_at=history_expiry,
            rotation_reason=str(rotation_reason or "refresh")[:32],
        )
    )
    auth.access_token = new_access_token
    auth.refresh_token = new_refresh_token
    auth.access_token_hash = _token_hash(new_access_token)
    auth.refresh_token_hash = _token_hash(new_refresh_token)
    auth.last_active_at = rotated_at
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(auth)
    rotate_cached_session_tokens(
        user_id,
        previous_access_token,
        previous_refresh_token,
        new_access_token,
        new_refresh_token,
    )
    return auth
    


# -------------------
# Update Last Active Authentication
# -------------------
def update_last_active_auth(db, user_id, token, token_type: str = "access"):
    column = Authentication.access_token_hash if token_type == "access" else Authentication.refresh_token_hash
    auth = (
        db.query(Authentication)
        .filter(Authentication.user_id == user_id, column == _token_hash(token))
        .first()
    )
    if not auth:
        raise HTTPException(status_code=404, detail="Authentication not found")
    auth.last_active_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(auth)
    return True



# -------------------
# Delete Authentication
# -------------------
def delete_authentication(
    db,
    access_token=None,
    refresh_token=None,
    id=None,
    user_id=None,
    *,
    before_commit: Callable[[list], None] | None = None,
    commit: bool = True,
):
    """Delete authentication records safely ensuring session state stays consistent.

    With ``commit=False`` the delete and callback remain staged in the caller's
    transaction, and cache invalidation is left to the caller after commit.
    """

    token_filters = []
    if access_token:
        token_filters.append(Authentication.access_token_hash == _token_hash(access_token))
    if refresh_token:
        token_filters.append(Authentication.refresh_token_hash == _token_hash(refresh_token))
    if id:
        token_filters.append(Authentication.id == id)

    if not token_filters and not user_id:
        return False

    query = db.query(Authentication)
    if user_id:
        query = query.filter(Authentication.user_id == user_id)
    if token_filters:
        query = query.filter(or_(*token_filters))

    target_rows = query.with_entities(
        Authentication.user_id,
        Authentication.access_token_hash,
        Authentication.refresh_token_hash,
    ).all()

    if not target_rows:
        return False

    try:
        deleted = query.delete(synchronize_session=False)
        if deleted and before_commit is not None:
            before_commit(target_rows)
        if commit:
            db.commit()
        else:
            db.flush()
    except Exception:
        db.rollback()
        raise

    if commit:
        for row in target_rows:
            revoke_token_digests(
                user_id=row.user_id,
                access_token_hash=row.access_token_hash,
                refresh_token_hash=row.refresh_token_hash,
            )

    return bool(deleted)


def delete_authentication_login_rows(
    db,
    *,
    user_id: str,
    auth_id: str | None = None,
    before_commit: Callable[[list], None] | None = None,
):
    """Delete login rows and return the exact non-secret audit/cache metadata.

    PostgreSQL executes the delete and ``RETURNING`` projection as one
    statement, so concurrent sign-ins or revocations cannot make the semantic
    audit count disagree with the rows this operation actually removed.
    """

    statement = delete(Authentication).where(Authentication.user_id == user_id)
    if auth_id is not None:
        statement = statement.where(Authentication.id == auth_id)
    statement = statement.returning(
        Authentication.id,
        Authentication.user_id,
        Authentication.device_info,
        Authentication.ip_address,
        Authentication.last_active_at,
        Authentication.access_token_hash,
        Authentication.refresh_token_hash,
    )
    try:
        rows = list(db.execute(statement).all())
        if rows and before_commit is not None:
            before_commit(rows)
        db.commit()
    except Exception:
        db.rollback()
        raise

    for row in rows:
        revoke_token_digests(
            user_id=row.user_id,
            access_token_hash=row.access_token_hash,
            refresh_token_hash=row.refresh_token_hash,
        )
    if auth_id is None:
        revoke_user_sessions(user_id)
    return rows



# -------------------
# Delete All Authentication
# -------------------
def delete_authentication_all(db, user_id = None, *, commit: bool = True, revoke_cached: bool | None = None):
    if revoke_cached is None:
        revoke_cached = commit

    if not commit and revoke_cached:
        raise ValueError("Cannot revoke cached sessions when commit=False")

    if user_id:
        deleted_count = (
            db.query(Authentication)
            .filter(Authentication.user_id == user_id)
            .delete()
        )
    else:
        deleted_count = db.query(Authentication).delete()

    committed = False
    if commit:
        try:
            db.commit()
            committed = True
        except Exception:
            db.rollback()
            raise

    if revoke_cached and committed:
        if user_id:
            revoke_user_sessions(user_id)
        else:
            revoke_all_sessions()
    return bool(deleted_count)


def create_password_reset_token(
    db,
    *,
    user_id: str,
    token_hash: str,
    requested_ip: str | None,
    requested_user_agent: str | None,
    expires_at: datetime,
    commit: bool = True,
):
    token = PasswordResetToken(
        user_id=user_id,
        token_hash=token_hash,
        requested_ip=requested_ip,
        requested_user_agent=requested_user_agent,
        created_at=datetime.now(timezone.utc),
        expires_at=expires_at,
    )
    db.add(token)
    if commit:
        db.commit()
        db.refresh(token)
    else:
        db.flush()
    return token


def get_password_reset_token_by_hash(db, token_hash: str):
    if not token_hash:
        return None
    return db.query(PasswordResetToken).filter(PasswordResetToken.token_hash == token_hash).first()


def consume_password_reset_token(db, token_id: str, *, now: datetime | None = None) -> bool:
    """Atomically mark an unconsumed, unexpired password reset token as consumed."""
    if not token_id:
        return False
    current_time = now or datetime.now(timezone.utc)
    count = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.id == token_id,
            PasswordResetToken.consumed_at.is_(None),
            PasswordResetToken.expires_at > current_time,
        )
        .update(
            {
                PasswordResetToken.consumed_at: current_time,
                PasswordResetToken.requested_ip: None,
                PasswordResetToken.requested_user_agent: None,
            },
            synchronize_session=False,
        )
    )
    return count == 1


def invalidate_user_password_reset_tokens(db, user_id: str, *, commit: bool = True):
    if not user_id:
        return 0
    now = datetime.now(timezone.utc)
    count = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.consumed_at.is_(None),
        )
        .update(
            {
                PasswordResetToken.consumed_at: now,
                PasswordResetToken.requested_ip: None,
                PasswordResetToken.requested_user_agent: None,
            },
            synchronize_session=False,
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return count


def delete_stale_password_reset_tokens(
    db,
    consumed_retention: timedelta | None = None,
    *,
    now: datetime | None = None,
):
    current_time = now or datetime.now(timezone.utc)
    retention = consumed_retention if consumed_retention is not None else timedelta(minutes=30)
    consumed_before = current_time - retention
    count = (
        db.query(PasswordResetToken)
        .filter(
            or_(
                PasswordResetToken.expires_at < current_time,
                PasswordResetToken.consumed_at < consumed_before,
            )
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return count


def delete_expired_password_reset_tokens(db, *, now: datetime | None = None):
    """Delete expired password reset tokens and their associated request metadata."""
    return delete_stale_password_reset_tokens(db, now=now)


def replace_pending_auth_action(
    db,
    *,
    user_id: str,
    purpose: str,
    token_hash: str,
    expires_at: datetime,
) -> PendingAuthAction:
    """Stage one current action per user and purpose in the caller transaction."""

    normalized_user_id = str(user_id or "").strip()
    normalized_purpose = str(purpose or "").strip()
    normalized_hash = str(token_hash or "").strip()
    if not normalized_user_id or not normalized_purpose or len(normalized_purpose) > 64:
        raise ValueError("A valid user and pending-auth purpose are required.")
    if len(normalized_hash) != 64:
        raise ValueError("A valid pending-auth token hash is required.")

    db.query(PendingAuthAction).filter(
        PendingAuthAction.user_id == normalized_user_id,
        PendingAuthAction.purpose == normalized_purpose,
    ).delete(synchronize_session=False)
    action = PendingAuthAction(
        user_id=normalized_user_id,
        purpose=normalized_purpose,
        token_hash=normalized_hash,
        created_at=datetime.now(timezone.utc),
        expires_at=expires_at,
    )
    db.add(action)
    db.flush()
    return action


def get_active_pending_auth_action(
    db,
    *,
    purpose: str,
    token_hash: str,
    now: datetime | None = None,
    for_update: bool = False,
):
    """Resolve an unexpired action through its unique indexed hash."""

    normalized_purpose = str(purpose or "").strip()
    normalized_hash = str(token_hash or "").strip()
    if not normalized_purpose or len(normalized_hash) != 64:
        return None
    query = db.query(PendingAuthAction).filter(
        PendingAuthAction.purpose == normalized_purpose,
        PendingAuthAction.token_hash == normalized_hash,
        PendingAuthAction.expires_at > (now or datetime.now(timezone.utc)),
    )
    if for_update:
        query = query.with_for_update()
    return query.first()


def delete_pending_auth_action(
    db,
    *,
    user_id: str,
    purpose: str,
    token_hash: str | None = None,
    commit: bool = False,
) -> int:
    """Delete the current action for one user/purpose pair."""

    filters = [
        PendingAuthAction.user_id == str(user_id or "").strip(),
        PendingAuthAction.purpose == str(purpose or "").strip(),
    ]
    if token_hash is not None:
        normalized_hash = str(token_hash or "").strip()
        if len(normalized_hash) != 64:
            return 0
        filters.append(PendingAuthAction.token_hash == normalized_hash)
    count = int(
        db.query(PendingAuthAction)
        .filter(*filters)
        .delete(synchronize_session=False)
        or 0
    )
    if commit:
        db.commit()
    return count


def delete_user_pending_auth_actions(
    db,
    user_id: str,
    *,
    commit: bool = False,
) -> int:
    """Invalidate every browser authentication continuation for one user."""

    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return 0
    count = int(
        db.query(PendingAuthAction)
        .filter(PendingAuthAction.user_id == normalized_user_id)
        .delete(synchronize_session=False)
        or 0
    )
    if commit:
        db.commit()
    return count


def delete_user_transient_auth_state(
    db,
    user_id: str,
    *,
    commit: bool = False,
) -> int:
    """Invalidate every in-flight authentication artifact for one user.

    Security-boundary changes such as enterprise account takeover or an
    administrator resetting 2FA must invalidate more than browser
    continuations. Native-app grants and WebAuthn challenges can otherwise
    outlive the state that authorized them.
    """

    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return 0
    count = delete_user_pending_auth_actions(
        db,
        normalized_user_id,
        commit=False,
    )
    count += int(
        db.query(NativeAuthGrant)
        .filter(NativeAuthGrant.user_id == normalized_user_id)
        .delete(synchronize_session=False)
        or 0
    )
    count += int(
        db.query(WebAuthnChallenge)
        .filter(WebAuthnChallenge.user_id == normalized_user_id)
        .delete(synchronize_session=False)
        or 0
    )
    if commit:
        db.commit()
    return count


def delete_expired_pending_auth_actions(
    db,
    *,
    now: datetime | None = None,
    batch_size: int = 1000,
) -> int:
    """Purge a bounded batch of expired transient-action hashes."""

    current = now or datetime.now(timezone.utc)
    row_ids = [
        row_id
        for (row_id,) in (
            db.query(PendingAuthAction.id)
            .filter(PendingAuthAction.expires_at <= current)
            .order_by(PendingAuthAction.expires_at.asc())
            .limit(max(1, min(int(batch_size), 5000)))
            .all()
        )
    ]
    deleted = 0
    if row_ids:
        deleted = int(
            db.query(PendingAuthAction)
            .filter(PendingAuthAction.id.in_(row_ids))
            .delete(synchronize_session=False)
            or 0
        )
        db.commit()
    return deleted



# ---------------------------------------------------------------------------
# Blockes IPs
# ---------------------------------------------------------------------------
class BlockedIP(Base):
    __tablename__ = 'blocked_ips'
    __table_args__ = (
        Index("ix_blocked_ip", "ip_address"),
    )
    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    ip_address = Column(String(45), unique=True, nullable=False)
    blocked_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    reason = Column(String, nullable=True)


class IPAddressSecurityStatistic(Base):
    __tablename__ = "ip_address_security_statistics"
    __table_args__ = (
        Index("ix_ip_address_security_statistics_ip", "ip_address"),
        Index("ix_ip_address_security_statistics_event_type", "event_type"),
        Index("ix_ip_address_security_statistics_country_code", "country_code"),
        Index("ix_ip_address_security_statistics_created_at", "created_at"),
        Index("ix_ip_address_security_statistics_last_seen_at", "last_seen_at"),
        Index("ix_ip_address_security_statistics_reason_code", "reason_code"),
        Index("ix_ip_address_security_statistics_aggregation_key", "aggregation_key", unique=True),
    )
    id = Column(String, primary_key=True, unique=True, default=lambda: str(uuid.uuid4()))
    ip_address = Column(String(45), nullable=False)
    event_type = Column(String(64), nullable=False)
    event_source = Column(String(64), nullable=True)
    reason_code = Column(String(64), nullable=True)
    route_category = Column(String(64), nullable=True)
    country_code = Column(String(8), nullable=True)
    country_resolved_at = Column(DateTime, nullable=True)
    geo_provider = Column(String(32), nullable=True)
    geo_lookup_status = Column(String(32), nullable=False, default="pending")
    reason = Column(String, nullable=True)
    request_count = Column(Integer, nullable=False, default=1)
    is_automatic = Column(Boolean, nullable=False, default=False)
    bucket_start = Column(DateTime, nullable=True)
    aggregation_key = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)


IP_SECURITY_EVENT_BAN_CREATED = "ban_created"
IP_SECURITY_EVENT_REQUEST_DENIED = "request_denied"
IP_SECURITY_EVENT_BAN_REMOVED = "ban_removed"
IP_SECURITY_EVENT_RATE_LIMITED = "rate_limited"
IP_SECURITY_EVENT_TYPES = frozenset(
    {
        IP_SECURITY_EVENT_BAN_CREATED,
        IP_SECURITY_EVENT_REQUEST_DENIED,
        IP_SECURITY_EVENT_BAN_REMOVED,
        IP_SECURITY_EVENT_RATE_LIMITED,
    }
)
_LEGACY_IP_SECURITY_EVENT_TYPE_MAP = {
    "blocked": IP_SECURITY_EVENT_BAN_CREATED,
    "blocked_attempt": IP_SECURITY_EVENT_REQUEST_DENIED,
    "unblocked": IP_SECURITY_EVENT_BAN_REMOVED,
}
IP_SECURITY_AGGREGATION_MINUTES = 5


def normalize_ip_security_event_type(event_type: str | None) -> str | None:
    """Return one canonical IP-security event type or ``None`` when invalid."""

    normalized = str(event_type or "").strip().lower()
    normalized = _LEGACY_IP_SECURITY_EVENT_TYPE_MAP.get(normalized, normalized)
    return normalized if normalized in IP_SECURITY_EVENT_TYPES else None


def _ip_security_bucket_start(value: datetime) -> datetime:
    """Floor an event timestamp to the configured aggregation window."""

    minute = value.minute - (value.minute % IP_SECURITY_AGGREGATION_MINUTES)
    return value.replace(minute=minute, second=0, microsecond=0)


def _ip_security_aggregation_key(
    *,
    ip_address: str,
    event_type: str,
    event_source: str | None,
    reason_code: str | None,
    route_category: str | None,
    bucket_start: datetime,
) -> str:
    """Build a stable privacy-preserving key for one aggregate event bucket."""

    raw = "|".join(
        (
            ip_address,
            event_type,
            str(event_source or ""),
            str(reason_code or ""),
            str(route_category or ""),
            bucket_start.isoformat(),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _increment_ip_security_aggregate(
    db,
    *,
    aggregation_key: str,
    request_count: int,
    event_created_at: datetime,
    country_code: str | None,
    country_resolved_at: datetime | None,
    geo_provider: str | None,
    geo_lookup_status: str,
    reason: str | None,
) -> int:
    """Atomically merge a request count into an existing aggregate bucket.

    Multiple application workers can update the same five-minute bucket at the
    same time. Performing the arithmetic in SQL prevents the lost-update race
    caused by loading the row and assigning ``old_count + delta`` in Python.
    """

    table = IPAddressSecurityStatistic
    values = {
        "request_count": func.coalesce(table.request_count, 0) + request_count,
        "last_seen_at": case(
            (
                or_(
                    table.last_seen_at.is_(None),
                    table.last_seen_at < event_created_at,
                ),
                event_created_at,
            ),
            else_=table.last_seen_at,
        ),
    }
    if reason:
        values["reason"] = str(reason).strip()
    if country_code:
        # Country enrichment is write-once for a bucket. A later request must
        # not replace the provenance of an already resolved country.
        unresolved = table.country_code.is_(None)
        values.update(
            {
                "country_code": case((unresolved, country_code), else_=table.country_code),
                "country_resolved_at": case(
                    (unresolved, country_resolved_at),
                    else_=table.country_resolved_at,
                ),
                "geo_provider": case(
                    (unresolved, geo_provider),
                    else_=table.geo_provider,
                ),
                "geo_lookup_status": case(
                    (unresolved, geo_lookup_status),
                    else_=table.geo_lookup_status,
                ),
            }
        )
    return int(
        db.query(table)
        .filter(table.aggregation_key == aggregation_key)
        .update(values, synchronize_session=False)
        or 0
    )


def normalize_ip_address_for_storage(ip_address: str | None) -> str | None:
    """Return the canonical representation used for IP security tables."""
    raw_value = str(ip_address or "").strip()
    if not raw_value:
        return None
    if raw_value.lower() == "localhost":
        return "127.0.0.1"
    if "%" in raw_value:
        # Scoped IPv6 zone identifiers are interface-local hints, not stable
        # visitor addresses, so do not allow them in persisted security policy.
        return None
    try:
        return ipaddress.ip_address(raw_value).compressed
    except ValueError:
        return None


LOCALHOST_IP_BLOCK_ERROR = "Cannot block localhost IP addresses"


def is_loopback_ip_address(ip_address: str | None) -> bool:
    """Return whether an address represents this server's local IP stack.

    Besides the standard IPv4 and IPv6 loopback ranges, IPv4-mapped IPv6
    addresses must be checked through their embedded IPv4 value. Python's
    ``IPv6Address.is_loopback`` does not classify values such as
    ``::ffff:127.0.0.1`` as loopback on its own.
    """

    normalized_ip = normalize_ip_address_for_storage(ip_address)
    if not normalized_ip:
        return False

    address = ipaddress.ip_address(normalized_ip)
    if address.is_loopback:
        return True

    mapped_ipv4 = getattr(address, "ipv4_mapped", None)
    return bool(mapped_ipv4 and mapped_ipv4.is_loopback)


def get_ip_address_statistics_settings(db) -> dict:
    from app.settings.models import get_settings_page

    settings_page = get_settings_page(db, "ip_address_statistics")
    if settings_page and isinstance(settings_page.data, dict):
        return settings_page.data
    return {
        "enabled": False,
        "regulatory_confirmed": False,
    }


def is_ip_address_statistics_enabled(db) -> bool:
    settings = get_ip_address_statistics_settings(db)
    return bool(settings.get("enabled")) and bool(settings.get("regulatory_confirmed"))


def get_ip_address_statistics_retention_days(db) -> int:
    settings = get_ip_address_statistics_settings(db)
    try:
        retention_days = int(settings.get("retention_days") or 90)
    except (TypeError, ValueError):
        retention_days = 90
    return max(1, min(retention_days, 3650))


def delete_expired_ip_address_security_statistics(db, *, now: datetime | None = None) -> int:
    retention_days = get_ip_address_statistics_retention_days(db)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    deleted = (
        db.query(IPAddressSecurityStatistic)
        .filter(IPAddressSecurityStatistic.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted or 0)


def record_ip_address_security_event(
    db,
    ip_address: str | None,
    event_type: str,
    *,
    event_source: str | None = None,
    reason_code: str | None = None,
    route_category: str | None = None,
    country_code: str | None = None,
    geo_provider: str | None = None,
    geo_lookup_status: str | None = None,
    reason: str | None = None,
    request_count: int = 1,
    is_automatic: bool | None = None,
    aggregate: bool | None = None,
    created_at: datetime | None = None,
    commit: bool = True,
    statistics_enabled: bool | None = None,
):
    """Record or aggregate one optional IP-security analytics event.

    Request-denial and rate-limit events are aggregated into five-minute
    buckets by default. Administrative lifecycle events remain individual
    records so an incident timeline preserves each explicit action.
    """

    normalized_ip = normalize_ip_address_for_storage(ip_address)
    normalized_event_type = normalize_ip_security_event_type(event_type)
    if not normalized_ip or not normalized_event_type:
        return None

    if statistics_enabled is False:
        return None
    if statistics_enabled is None and not is_ip_address_statistics_enabled(db):
        return None

    event_created_at = created_at or datetime.now(timezone.utc)
    normalized_source = str(event_source).strip()[:64] if event_source else None
    normalized_reason_code = str(reason_code).strip().lower()[:64] if reason_code else None
    normalized_route_category = str(route_category).strip().lower()[:64] if route_category else None
    normalized_country = str(country_code).strip().upper()[:8] if country_code else None
    normalized_geo_provider = str(geo_provider).strip().lower()[:32] if geo_provider else None
    normalized_request_count = max(1, int(request_count or 1))
    reused_country_resolved_at = None
    automatic = bool(is_automatic) if is_automatic is not None else (
        normalized_source not in {None, "admin_manual"}
        and normalized_event_type in {IP_SECURITY_EVENT_BAN_CREATED, IP_SECURITY_EVENT_BAN_REMOVED}
    )

    # Reuse an existing resolved country for the same address before scheduling
    # another external lookup. The provenance remains explicit so reused
    # results are distinguishable from event-time provider responses.
    if not normalized_country:
        existing_geo = (
            db.query(
                IPAddressSecurityStatistic.country_code,
                IPAddressSecurityStatistic.geo_provider,
                IPAddressSecurityStatistic.country_resolved_at,
            )
            .filter(
                IPAddressSecurityStatistic.ip_address == normalized_ip,
                IPAddressSecurityStatistic.country_code.is_not(None),
            )
            .order_by(IPAddressSecurityStatistic.country_resolved_at.desc())
            .first()
        )
        if existing_geo and existing_geo[0]:
            normalized_country = str(existing_geo[0]).strip().upper()[:8]
            normalized_geo_provider = str(existing_geo[1] or "stored").strip().lower()[:32]
            reused_country_resolved_at = existing_geo[2]
            geo_lookup_status = "reused"

    resolved_at = (reused_country_resolved_at or event_created_at) if normalized_country else None
    lookup_status = str(
        geo_lookup_status or ("resolved" if normalized_country else "pending")
    ).strip().lower()[:32]
    should_aggregate = (
        normalized_event_type in {
            IP_SECURITY_EVENT_REQUEST_DENIED,
            IP_SECURITY_EVENT_RATE_LIMITED,
        }
        if aggregate is None
        else bool(aggregate)
    )
    bucket_start = _ip_security_bucket_start(event_created_at) if should_aggregate else None
    aggregation_key = (
        _ip_security_aggregation_key(
            ip_address=normalized_ip,
            event_type=normalized_event_type,
            event_source=normalized_source,
            reason_code=normalized_reason_code,
            route_category=normalized_route_category,
            bucket_start=bucket_start,
        )
        if bucket_start
        else None
    )

    if aggregation_key:
        updated = _increment_ip_security_aggregate(
            db,
            aggregation_key=aggregation_key,
            request_count=normalized_request_count,
            event_created_at=event_created_at,
            country_code=normalized_country,
            country_resolved_at=resolved_at,
            geo_provider=normalized_geo_provider,
            geo_lookup_status=lookup_status,
            reason=reason,
        )
        if updated:
            if commit:
                db.commit()
            # Re-read after the SQL expression so callers receive the merged
            # values rather than a stale identity-map instance.
            return (
                db.query(IPAddressSecurityStatistic)
                .filter(IPAddressSecurityStatistic.aggregation_key == aggregation_key)
                .populate_existing()
                .one()
            )

    statistic = IPAddressSecurityStatistic(
        ip_address=normalized_ip,
        event_type=normalized_event_type,
        event_source=normalized_source,
        reason_code=normalized_reason_code,
        route_category=normalized_route_category,
        country_code=normalized_country,
        country_resolved_at=resolved_at,
        geo_provider=normalized_geo_provider,
        geo_lookup_status=lookup_status,
        reason=str(reason).strip() if reason else None,
        request_count=normalized_request_count,
        is_automatic=automatic,
        bucket_start=bucket_start,
        aggregation_key=aggregation_key,
        created_at=event_created_at,
        last_seen_at=event_created_at,
    )
    if aggregation_key:
        try:
            # Flush the candidate inside a savepoint. This detects a concurrent
            # unique-key winner without rolling back unrelated work in an outer
            # batch transaction.
            with db.begin_nested():
                db.add(statistic)
                db.flush()
        except IntegrityError:
            # Two workers observed an empty bucket concurrently. The unique
            # aggregation key selected one winner; atomically merge this
            # request count into that row.
            updated = _increment_ip_security_aggregate(
                db,
                aggregation_key=aggregation_key,
                request_count=normalized_request_count,
                event_created_at=event_created_at,
                country_code=normalized_country,
                country_resolved_at=resolved_at,
                geo_provider=normalized_geo_provider,
                geo_lookup_status=lookup_status,
                reason=reason,
            )
            if not updated:
                raise
    else:
        db.add(statistic)

    if commit:
        db.commit()
    if aggregation_key:
        return (
            db.query(IPAddressSecurityStatistic)
            .filter(IPAddressSecurityStatistic.aggregation_key == aggregation_key)
            .populate_existing()
            .one()
        )
    if commit:
        db.refresh(statistic)
    return statistic



# -------------------
# Block IP Address
# -------------------
def block_ip_address(ip_address, expires_at, reason, db):
    normalized_ip = normalize_ip_address_for_storage(ip_address)
    if not normalized_ip:
        return {"status": "error", "message": "Invalid IP address"}
    if is_loopback_ip_address(normalized_ip):
        return {"status": "error", "message": LOCALHOST_IP_BLOCK_ERROR}
    now = datetime.now(timezone.utc)

    existing_entry = db.query(BlockedIP).filter(BlockedIP.ip_address == normalized_ip).first()
    if existing_entry:
        existing_entry.blocked_at = now
        existing_entry.expires_at = expires_at
        existing_entry.reason = reason
        db.commit()
        db.refresh(existing_entry)
        return {"status": "success"}
    new_entry = BlockedIP(
        ip_address=normalized_ip,
        blocked_at=now,
        expires_at=expires_at,
        reason=reason
    )
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return {"status": "success"}


def delete_expired_blocked_ip_addresses(db, *, now: datetime | None = None) -> int:
    """Remove expired temporary IP blocks and record their lifecycle closure."""

    current_time = now or datetime.now(timezone.utc)
    expired_rows = (
        db.query(BlockedIP)
        .filter(BlockedIP.expires_at.isnot(None), BlockedIP.expires_at <= current_time)
        .all()
    )
    for entry in expired_rows:
        try:
            # Keep optional analytics writes isolated from the outer cleanup
            # transaction so one failed event cannot restore earlier deletions.
            with db.begin_nested():
                record_ip_address_security_event(
                    db,
                    entry.ip_address,
                    IP_SECURITY_EVENT_BAN_REMOVED,
                    event_source="system_expiry",
                    reason_code="expired",
                    reason="Temporary IP ban expired",
                    is_automatic=True,
                    aggregate=False,
                    created_at=current_time,
                    commit=False,
                )
        except Exception:
            # Ban expiry is enforcement-critical and must continue even when
            # optional analytics storage is temporarily unavailable.
            pass
        db.delete(entry)
    db.commit()
    return len(expired_rows)



# -------------------
# Get Blocked IP
# -------------------
def get_blocked_ip(db, ip_address: str):
    normalized_ip = normalize_ip_address_for_storage(ip_address)
    if not normalized_ip:
        return None
    row = db.query(BlockedIP).filter(BlockedIP.ip_address == normalized_ip).first()
    return row



# -------------------
# Check Blocked IP Address
# -------------------
def check_blocked_ip_address(ip_address, db):
    """Return an active IP-ban duration unless the emergency bypass is enabled."""
    if ip_restrictions_disabled_by_environment():
        return False
    if is_loopback_ip_address(ip_address):
        # Older vulnerable releases could persist loopback bans through the
        # edit route. Never enforce those rows so administrators retain a path
        # to remove or replace the unsafe record after upgrading.
        return False
    blocked_ip = get_blocked_ip(db, ip_address)
    if not blocked_ip:
        return False
    expires_at = blocked_ip.expires_at
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        expires = round((expires_at - now).total_seconds())
        if expires > 0:
            return expires
        try:
            record_ip_address_security_event(
                db,
                ip_address,
                IP_SECURITY_EVENT_BAN_REMOVED,
                event_source="system_expiry",
                reason_code="expired",
                reason="Temporary IP ban expired during request check",
                is_automatic=True,
                aggregate=False,
            )
        except Exception:
            db.rollback()
        deblock_ip_address(ip_address, db)
    return False



# -------------------
# Unblock IP Address
# -------------------
def deblock_ip_address(ip_address, db):
    normalized_ip = normalize_ip_address_for_storage(ip_address)
    if not normalized_ip:
        return {"status": "ip_not_blocked"}
    deleted_count = db.execute(
        delete(BlockedIP).where(BlockedIP.ip_address == normalized_ip)
    ).rowcount
    db.commit()
    if deleted_count:
        return {"status": "success"}
    return {"status": "ip_not_blocked"}
