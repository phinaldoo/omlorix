from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.auth.email_delivery import (
    is_email_delivery_config_ready,
    load_login_email_delivery_config,
)
from app.auth.email_localization import resolve_email_language
from app.auth.models import (
    delete_authentication_all,
    delete_user_transient_auth_state,
    invalidate_user_password_reset_tokens,
)
from app.auth.session_store import revoke_user_sessions
from app.email.models import (
    EMAIL_CHANGE_CANCELLED,
    EMAIL_CHANGE_COMPLETED,
    EMAIL_CHANGE_EXPIRED,
    EMAIL_CHANGE_PENDING,
    OUTBOX_CANCELLED,
    OUTBOX_PENDING,
    OUTBOX_PROCESSING,
    OUTBOX_RETRY,
    EmailOutbox,
    PendingEmailChange,
    cancel_user_email,
    create_email_change_secrets,
    enqueue_email,
    hash_secret,
)
from app.settings.utils import get_public_url
from app.users.init import get_user_setting_value
from app.users.models import (
    User,
    build_user_email_match,
    canonicalize_user_email,
    normalize_utc_datetime,
)


EMAIL_CHANGE_TTL = timedelta(hours=24)
EMAIL_CHANGE_COOLDOWN = timedelta(seconds=60)
EMAIL_CHANGE_MAX_PER_DAY = 5


def _language(user, db) -> str:
    return resolve_email_language(
        get_user_setting_value(
            user.id,
            "general",
            "language",
            db,
            commit=False,
        )
    )


def _cancel_request_jobs(db, request_id: str, user_id: str) -> None:
    rows = (
        db.query(EmailOutbox)
        .filter(
            EmailOutbox.template_type == "email_change",
            EmailOutbox.user_id == user_id,
            EmailOutbox.status.in_((OUTBOX_PENDING, OUTBOX_RETRY, OUTBOX_PROCESSING)),
        )
        .with_for_update()
        .all()
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        if str((row.payload or {}).get("request_id") or "") != request_id:
            continue
        row.status = OUTBOX_CANCELLED
        row.recipient = None
        row.payload = None
        row.lease_owner = None
        row.leased_at = None
        row.lease_expires_at = None
        row.last_error_type = "cancelled"
        row.last_error = "email change ended"
        row.updated_at = now


def cancel_pending_email_changes(db, user_id: str) -> int:
    """Consume every pending address proof while the user row is locked."""

    current = datetime.now(timezone.utc)
    pending = (
        db.query(PendingEmailChange)
        .filter(
            PendingEmailChange.user_id == user_id,
            PendingEmailChange.status == EMAIL_CHANGE_PENDING,
        )
        .with_for_update()
        .all()
    )
    for request_row in pending:
        request_row.status = EMAIL_CHANGE_CANCELLED
        request_row.cancelled_at = current
        _cancel_request_jobs(db, request_row.id, user_id)
    return len(pending)


def request_email_change(db, user: User, new_email: str) -> PendingEmailChange:
    normalized = canonicalize_user_email(new_email)
    if not normalized:
        raise HTTPException(status_code=422, detail="A valid email address is required.")
    if normalized == canonicalize_user_email(user.email):
        raise HTTPException(status_code=409, detail="This is already your email address.")

    config = load_login_email_delivery_config(db)
    if not is_email_delivery_config_ready(config):
        raise HTTPException(
            status_code=409,
            detail="Email changes are unavailable until system email is configured.",
        )
    public_url = get_public_url(db).rstrip("/")

    locked_user = (
        db.query(User)
        .filter(User.id == user.id)
        .with_for_update()
        .first()
    )
    if not locked_user:
        raise HTTPException(status_code=404, detail="User not found.")
    email_match = build_user_email_match(normalized)
    if email_match is not None and db.query(User.id).filter(
        email_match,
        User.id != locked_user.id,
    ).first():
        raise HTTPException(status_code=409, detail="Email already in use.")

    current = datetime.now(timezone.utc)
    recent_requests = (
        db.query(PendingEmailChange)
        .filter(
            PendingEmailChange.user_id == locked_user.id,
            PendingEmailChange.created_at >= current - timedelta(days=1),
        )
        .order_by(PendingEmailChange.created_at.desc())
        .limit(EMAIL_CHANGE_MAX_PER_DAY)
        .all()
    )
    if recent_requests:
        latest_created_at = normalize_utc_datetime(recent_requests[0].created_at)
        if latest_created_at and latest_created_at > current - EMAIL_CHANGE_COOLDOWN:
            retry_after = max(
                1,
                int(
                    (
                        latest_created_at + EMAIL_CHANGE_COOLDOWN - current
                    ).total_seconds()
                ),
            )
            raise HTTPException(
                status_code=429,
                detail="Please wait before requesting another email change.",
                headers={"Retry-After": str(retry_after)},
            )
    if len(recent_requests) >= EMAIL_CHANGE_MAX_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail="Too many email-change requests. Please try again later.",
            headers={"Retry-After": str(24 * 60 * 60)},
        )
    cancel_pending_email_changes(db, locked_user.id)

    verify_secret, cancel_secret = create_email_change_secrets()
    request_row = PendingEmailChange(
        user_id=locked_user.id,
        new_email=normalized,
        old_email=canonicalize_user_email(locked_user.email),
        verify_token_hash=hash_secret(verify_secret),
        cancel_token_hash=hash_secret(cancel_secret),
        status=EMAIL_CHANGE_PENDING,
        created_at=current,
        expires_at=current + EMAIL_CHANGE_TTL,
    )
    db.add(request_row)
    db.flush()

    language_code = _language(locked_user, db)
    verify_url = f"{public_url}/login#email_change_token={quote(verify_secret, safe='')}"
    cancel_url = f"{public_url}/login#email_change_cancel_token={quote(cancel_secret, safe='')}"
    enqueue_email(
        db,
        user_id=locked_user.id,
        recipient=normalized,
        template_type="email_change",
        language_code=language_code,
        priority=5,
        expires_at=request_row.expires_at,
        idempotency_key=f"email-change:verify:{request_row.id}",
        payload={
            "kind": "verify",
            "request_id": request_row.id,
            "action_url": verify_url,
            "expires_in_hours": 24,
        },
    )
    enqueue_email(
        db,
        user_id=locked_user.id,
        recipient=locked_user.email,
        template_type="email_change",
        language_code=language_code,
        priority=5,
        expires_at=request_row.expires_at,
        idempotency_key=f"email-change:requested:{request_row.id}",
        payload={
            "kind": "requested",
            "request_id": request_row.id,
            "action_url": cancel_url,
        },
    )
    db.flush()
    return request_row


def confirm_email_change(db, raw_token: str) -> dict:
    token_hash = hash_secret(raw_token)
    current = datetime.now(timezone.utc)
    preliminary = (
        db.query(PendingEmailChange)
        .filter(PendingEmailChange.verify_token_hash == token_hash)
        .first()
    )
    if not preliminary or preliminary.status != EMAIL_CHANGE_PENDING:
        raise HTTPException(status_code=400, detail="This email-change link is invalid or has already been used.")
    user = (
        db.query(User)
        .filter(User.id == preliminary.user_id)
        .with_for_update()
        .first()
    )
    request_row = (
        db.query(PendingEmailChange)
        .populate_existing()
        .filter(PendingEmailChange.verify_token_hash == token_hash)
        .with_for_update()
        .first()
    )
    if (
        not user
        or not request_row
        or request_row.status != EMAIL_CHANGE_PENDING
        or request_row.user_id != user.id
    ):
        raise HTTPException(status_code=400, detail="This email-change link is invalid or has already been used.")
    if normalize_utc_datetime(request_row.expires_at) <= current:
        request_row.status = EMAIL_CHANGE_EXPIRED
        _cancel_request_jobs(db, request_row.id, request_row.user_id)
        db.commit()
        raise HTTPException(status_code=400, detail="This email-change link has expired.")

    if canonicalize_user_email(user.email) != canonicalize_user_email(
        request_row.old_email
    ):
        request_row.status = EMAIL_CHANGE_CANCELLED
        request_row.cancelled_at = current
        _cancel_request_jobs(db, request_row.id, request_row.user_id)
        db.commit()
        raise HTTPException(status_code=400, detail="This email-change link is no longer valid.")

    email_match = build_user_email_match(request_row.new_email)
    if email_match is not None and db.query(User.id).filter(
        email_match,
        User.id != user.id,
    ).first():
        raise HTTPException(status_code=409, detail="That email address is already in use.")

    old_email = user.email
    new_email = canonicalize_user_email(request_row.new_email)
    language_code = _language(user, db)
    request_row.status = EMAIL_CHANGE_COMPLETED
    request_row.completed_at = current
    user.email = new_email
    invalidate_user_password_reset_tokens(db, user.id, commit=False)
    delete_user_transient_auth_state(db, user.id, commit=False)
    delete_authentication_all(db, user.id, commit=False, revoke_cached=False)
    cancel_user_email(
        db,
        user.id,
        # Completed/cancelled change notices are immutable security history.
        # Preserve them when a later change completes, while explicitly
        # redacting the request-specific verify/cancel jobs below.
        preserve_template_types=("security_event", "email_change"),
        commit=False,
    )
    _cancel_request_jobs(db, request_row.id, user.id)
    try:
        for audience, recipient in (("old", old_email), ("new", new_email)):
            enqueue_email(
                db,
                user_id=user.id,
                recipient=recipient,
                template_type="email_change",
                language_code=language_code,
                priority=1,
                expires_at=current + timedelta(days=7),
                idempotency_key=f"email-change:changed:{request_row.id}:{audience}",
                payload={"kind": "changed", "request_id": request_row.id},
            )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="That email address is already in use.",
        ) from exc
    except Exception:
        db.rollback()
        raise
    revoke_user_sessions(user.id)
    return {
        "status": "success",
        "sessions_revoked": True,
        "user_id": user.id,
    }


def cancel_email_change(db, raw_token: str) -> dict:
    token_hash = hash_secret(raw_token)
    current = datetime.now(timezone.utc)
    preliminary = (
        db.query(PendingEmailChange)
        .filter(PendingEmailChange.cancel_token_hash == token_hash)
        .first()
    )
    if not preliminary or preliminary.status != EMAIL_CHANGE_PENDING:
        raise HTTPException(status_code=400, detail="This email-change link is invalid or has already been used.")
    user = (
        db.query(User)
        .filter(User.id == preliminary.user_id)
        .with_for_update()
        .first()
    )
    request_row = (
        db.query(PendingEmailChange)
        .populate_existing()
        .filter(PendingEmailChange.cancel_token_hash == token_hash)
        .with_for_update()
        .first()
    )
    if (
        not user
        or not request_row
        or request_row.status != EMAIL_CHANGE_PENDING
        or request_row.user_id != user.id
    ):
        raise HTTPException(status_code=400, detail="This email-change link is invalid or has already been used.")
    if normalize_utc_datetime(request_row.expires_at) <= current:
        request_row.status = EMAIL_CHANGE_EXPIRED
        _cancel_request_jobs(db, request_row.id, request_row.user_id)
        db.commit()
        raise HTTPException(status_code=400, detail="This email-change link has expired.")
    request_row.status = EMAIL_CHANGE_CANCELLED
    request_row.cancelled_at = current
    _cancel_request_jobs(db, request_row.id, request_row.user_id)
    invalidate_user_password_reset_tokens(db, user.id, commit=False)
    delete_user_transient_auth_state(db, user.id, commit=False)
    delete_authentication_all(db, user.id, commit=False, revoke_cached=False)
    try:
        enqueue_email(
            db,
            user_id=user.id,
            recipient=request_row.old_email,
            template_type="email_change",
            language_code=_language(user, db),
            priority=5,
            expires_at=current + timedelta(days=7),
            idempotency_key=f"email-change:cancelled:{request_row.id}",
            payload={"kind": "cancelled", "request_id": request_row.id},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    revoke_user_sessions(user.id)
    return {
        "status": "success",
        "sessions_revoked": True,
        "user_id": request_row.user_id,
    }
