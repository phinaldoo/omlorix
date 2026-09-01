from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from app.auth.email_localization import resolve_email_language
from app.auth.models import minimize_session_device_info, minimize_session_ip_address
from app.email.address import normalize_single_mailbox
from app.email.models import enqueue_email
from app.users.init import get_user_setting_value
from app.users.models import ACCOUNT_TYPE_TEMPORARY


def user_email_language(user, db) -> str:
    return resolve_email_language(
        get_user_setting_value(
            user.id,
            "general",
            "language",
            db,
            commit=False,
        )
    )


def security_request_context(request, db) -> dict[str, str]:
    """Return deliberately coarse context safe for an out-of-band notice."""

    from app.middleware.ip_restriction import get_client_ip

    try:
        raw_ip = get_client_ip(request, db)
    except Exception:
        raw_ip = getattr(getattr(request, "client", None), "host", None)
    return {
        "device": minimize_session_device_info(
            getattr(request, "headers", {}).get("user-agent")
        ),
        "network": minimize_session_ip_address(raw_ip),
    }


def enqueue_security_event(
    db,
    *,
    user=None,
    recipient: str | None = None,
    user_id: str | None = None,
    language_code: str | None = None,
    event_type: str,
    source_id: str | None = None,
    occurred_at: datetime | None = None,
    device: str | None = None,
    network: str | None = None,
    purge_at: str | None = None,
    priority: int = 20,
    detach_user_id: bool = False,
):
    """Stage a localized security notice without committing the transaction."""

    if user is not None:
        if getattr(user, "account_type", None) == ACCOUNT_TYPE_TEMPORARY:
            return None
        user_id = user.id
        recipient = getattr(user, "email", None)
        language_code = user_email_language(user, db)
    try:
        normalized_recipient = normalize_single_mailbox(recipient).lower()
    except ValueError:
        return None
    if normalized_recipient.endswith(".temporary.local"):
        return None

    current = occurred_at or datetime.now(timezone.utc)
    normalized_source = str(source_id or uuid.uuid4())
    return enqueue_email(
        db,
        recipient=normalized_recipient,
        user_id=None if detach_user_id else user_id,
        template_type="security_event",
        language_code=resolve_email_language(language_code),
        priority=priority,
        expires_at=current + timedelta(days=7),
        idempotency_key=f"security:{event_type}:{normalized_source}",
        payload={
            "event_type": str(event_type or "")[:64],
            "occurred_at": current.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "device": str(device or "")[:160],
            "network": str(network or "")[:128],
            "purge_at": str(purge_at or "")[:64],
        },
    )
