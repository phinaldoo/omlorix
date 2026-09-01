from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import secrets

from sqlalchemy.exc import IntegrityError

from app.auth.account_slots import should_secure_auth_cookie
from app.auth.models import minimize_session_device_info, minimize_session_ip_address
from app.email.models import TrustedDeviceNotification, hash_secret
from app.email.service import enqueue_security_event
from app.users.models import ACCOUNT_TYPE_TEMPORARY, User, normalize_utc_datetime


DEVICE_COOKIE = "omlorix_device"
DEVICE_MAX_AGE_SECONDS = 365 * 24 * 60 * 60
MAX_DEVICE_MARKERS_PER_USER = 100
MAX_NEW_DEVICE_NOTICES_PER_DAY = 5
_DEVICE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def _valid_cookie_token(value: str | None) -> bool:
    return bool(value and _DEVICE_TOKEN_PATTERN.fullmatch(value))


def _set_device_cookie(response, request, db, value: str) -> None:
    response.set_cookie(
        key=DEVICE_COOKIE,
        value=value,
        httponly=True,
        secure=should_secure_auth_cookie(db, request),
        samesite="lax",
        max_age=DEVICE_MAX_AGE_SECONDS,
        path="/",
    )


def register_login_device(db, *, request, response, user, client_ip: str | None) -> bool:
    """Register an opaque browser marker and atomically queue first-seen mail."""

    supplied = request.cookies.get(DEVICE_COOKIE)
    device_token = supplied if _valid_cookie_token(supplied) else secrets.token_urlsafe(32)
    _set_device_cookie(response, request, db, device_token)
    if getattr(user, "account_type", None) == ACCOUNT_TYPE_TEMPORARY:
        return False

    current = datetime.now(timezone.utc)
    # Serialize only concurrent logins for the same account. This makes the
    # per-user notification budget deterministic without a global bottleneck.
    locked_user = (
        db.query(User)
        .populate_existing()
        .filter(User.id == user.id)
        .with_for_update()
        .first()
    )
    if locked_user is None:
        return False
    user = locked_user
    token_hash = hash_secret(device_token)
    device_summary = minimize_session_device_info(
        request.headers.get("User-Agent", "Unknown Device")
    )
    network_summary = minimize_session_ip_address(client_ip)
    existing = (
        db.query(TrustedDeviceNotification)
        .filter(
            TrustedDeviceNotification.user_id == user.id,
            TrustedDeviceNotification.device_token_hash == token_hash,
        )
        .first()
    )
    notify_candidate = existing is None
    if existing is None:
        marker_count = (
            db.query(TrustedDeviceNotification)
            .filter(TrustedDeviceNotification.user_id == user.id)
            .count()
        )
        if marker_count >= MAX_DEVICE_MARKERS_PER_USER:
            oldest = (
                db.query(TrustedDeviceNotification)
                .filter(TrustedDeviceNotification.user_id == user.id)
                .order_by(TrustedDeviceNotification.last_seen_at.asc())
                .first()
            )
            if oldest is not None:
                db.delete(oldest)
        existing = TrustedDeviceNotification(
            user_id=user.id,
            device_token_hash=token_hash,
            device_summary=device_summary,
            network_summary=network_summary,
            first_seen_at=current,
            last_seen_at=current,
        )
        db.add(existing)
        db.flush()
    else:
        last_seen = normalize_utc_datetime(existing.last_seen_at)
        notify_candidate = bool(
            existing.last_notified_at is None
            or last_seen is None
            or last_seen < current - timedelta(days=365)
        )
        # Limit write amplification on high-traffic sessions.
        if last_seen is None or last_seen < current - timedelta(hours=24):
            existing.last_seen_at = current
            existing.device_summary = device_summary
            existing.network_summary = network_summary

    notified_in_last_day = 0
    if notify_candidate:
        notified_in_last_day = (
            db.query(TrustedDeviceNotification)
            .filter(
                TrustedDeviceNotification.user_id == user.id,
                TrustedDeviceNotification.last_notified_at.is_not(None),
                TrustedDeviceNotification.last_notified_at
                >= current - timedelta(days=1),
            )
            .count()
        )
    notify = bool(
        notify_candidate
        and notified_in_last_day < MAX_NEW_DEVICE_NOTICES_PER_DAY
    )
    try:
        if notify:
            enqueue_security_event(
                db,
                user=user,
                event_type="new_device",
                source_id=f"{existing.id}:{current.date().isoformat()}",
                occurred_at=current,
                device=device_summary,
                network=network_summary,
                priority=10,
            )
            existing.last_notified_at = current
        db.commit()
    except IntegrityError:
        # A concurrent login with the same cookie won the unique-key race. Its
        # transaction also owns the idempotent notification.
        db.rollback()
        return False
    except Exception:
        db.rollback()
        raise
    return notify
