from __future__ import annotations

from datetime import datetime, timezone
import hmac

from app.auth.models import PasswordResetToken
from app.email.models import (
    EMAIL_CHANGE_PENDING,
    PendingEmailChange,
)
from app.users.models import User, normalize_utc_datetime


def _otp_job_is_current(db, row, payload: dict, current: datetime) -> bool:
    user = db.query(User).filter(User.id == row.user_id).first()
    if not user or not isinstance(user.settings, dict):
        return False
    secret = user.settings.get("secret")
    if not isinstance(secret, dict):
        return False
    expires_at_raw = str(secret.get("2fa_otp_expires_at") or "")
    try:
        expires_at = normalize_utc_datetime(datetime.fromisoformat(expires_at_raw))
    except (TypeError, ValueError):
        return False
    return bool(
        expires_at
        and expires_at > current
        and hmac.compare_digest(
            str(secret.get("2fa_otp_hash") or ""),
            str(payload.get("otp_hash") or ""),
        )
        and str(secret.get("2fa_otp_provider") or "") == str(payload.get("provider") or "")
        and str(secret.get("2fa_otp_purpose") or "") == str(payload.get("purpose") or "")
        and str(secret.get("2fa_otp_destination") or "").strip().lower()
        == str(row.recipient or "").strip().lower()
        and str(user.email or "").strip().lower()
        == str(row.recipient or "").strip().lower()
    )


def _parse_payload_datetime(value: object):
    try:
        return normalize_utc_datetime(datetime.fromisoformat(str(value or "")))
    except (TypeError, ValueError):
        return None


def validate_outbox_row(db, row, *, now: datetime | None = None) -> tuple[bool, str]:
    """Revalidate mutable authorization state immediately before SMTP."""

    current = now or datetime.now(timezone.utc)
    if not row.recipient or not isinstance(row.payload, dict):
        return False, "redacted"
    if int(getattr(row, "template_version", 0) or 0) != 1:
        return False, "unsupported_template_version"
    if row.expires_at and normalize_utc_datetime(row.expires_at) <= current:
        return False, "expired"
    payload = row.payload

    if row.template_type == "password_reset":
        token = (
            db.query(PasswordResetToken)
            .filter(PasswordResetToken.id == str(payload.get("token_id") or ""))
            .first()
        )
        if not token or token.consumed_at is not None:
            return False, "reset_token_inactive"
        if normalize_utc_datetime(token.expires_at) <= current:
            return False, "reset_token_expired"
        if str(row.user_id or "") != str(token.user_id or ""):
            return False, "reset_identity_changed"
        user = db.query(User).filter(User.id == token.user_id).first()
        if not user or str(user.email).strip().lower() != str(row.recipient).strip().lower():
            return False, "reset_identity_changed"
        return True, "valid"

    if row.template_type == "twofa_otp":
        return (
            (True, "valid")
            if _otp_job_is_current(db, row, payload, current)
            else (False, "otp_superseded")
        )

    if row.template_type == "email_change":
        kind = str(payload.get("kind") or "")
        if kind not in {"verify", "requested", "changed", "cancelled"}:
            return False, "unsupported_email_change_kind"
        if kind in {"verify", "requested"}:
            request_row = (
                db.query(PendingEmailChange)
                .filter(PendingEmailChange.id == str(payload.get("request_id") or ""))
                .first()
            )
            if (
                not request_row
                or request_row.status != EMAIL_CHANGE_PENDING
                or normalize_utc_datetime(request_row.expires_at) <= current
                or str(request_row.user_id) != str(row.user_id or "")
            ):
                return False, "email_change_inactive"
            if kind == "verify" and str(request_row.new_email).strip().lower() != str(row.recipient).strip().lower():
                return False, "email_change_recipient_changed"
            if kind == "requested":
                user = db.query(User).filter(User.id == request_row.user_id).first()
                if (
                    not user
                    or str(user.email).strip().lower()
                    != str(request_row.old_email).strip().lower()
                    or str(user.email).strip().lower()
                    != str(row.recipient).strip().lower()
                ):
                    return False, "email_change_recipient_changed"
        return True, "valid"

    if row.template_type == "security_event":
        event_type = str(payload.get("event_type") or "")
        if event_type in {
            "account_deleted",
            "email_changed",
            "admin_email_changed",
        }:
            return True, "valid"
        user = db.query(User).filter(User.id == row.user_id).first()
        if not user or str(user.email).strip().lower() != str(row.recipient).strip().lower():
            return False, "security_recipient_changed"
        if event_type == "account_deactivated":
            if user.deleted_at is None or user.deletion_scheduled_for is not None:
                return False, "account_state_changed"
            return True, "valid"
        if event_type == "account_deletion_scheduled":
            scheduled_for = normalize_utc_datetime(user.deletion_scheduled_for)
            payload_purge_at = _parse_payload_datetime(payload.get("purge_at"))
            if (
                user.deleted_at is None
                or scheduled_for is None
                or payload_purge_at is None
                or scheduled_for != payload_purge_at
            ):
                return False, "account_state_changed"
            return True, "valid"
        return True, "valid"

    return False, "unsupported_template"
