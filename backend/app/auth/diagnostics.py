"""Safe, structured diagnostics for enterprise authentication flows.

The public login page receives only a short reference.  Administrators can use
that reference to locate a bounded, machine-readable event without exposing
authorization codes, tokens, provider responses, or user claims.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import secrets
from urllib.parse import urlencode

from fastapi import HTTPException

from app.database import AuditSessionLocal, SessionLocal
from app.logging.models import (
    AdminNotifications,
    AuthenticationLogs,
    create_admin_notification,
    create_authentication_log,
)

logger = logging.getLogger(__name__)

_NOTIFICATION_THRESHOLD = 3
_NOTIFICATION_WINDOW = timedelta(minutes=5)
_NOTIFICATION_DEDUPE_WINDOW = timedelta(minutes=30)


def new_auth_reference() -> str:
    """Return a non-secret reference suitable for URLs and support tickets."""

    return f"AUTH-{secrets.token_hex(5).upper()}"


def build_sso_failure_url(error: str, reference: str | None) -> str:
    """Build a login redirect that is unambiguously owned by the SSO handler."""

    query = {"error": error, "auth_flow": "sso"}
    if reference:
        query["reference"] = reference
    return f"/login?{urlencode(query)}"


def classify_sso_exception(exc: Exception) -> tuple[str, str]:
    """Map provider exceptions to stable codes and processing stages.

    Matching is intentionally local and conservative.  The raw exception text
    is never persisted because upstream error bodies can contain credentials.
    """

    detail = str(exc.detail if isinstance(exc, HTTPException) else exc).lower()
    mappings = (
        ("invalid issuer", "oidc_issuer_mismatch", "id_token_validation"),
        ("audience", "oidc_audience_mismatch", "id_token_validation"),
        ("could not fetch jwks", "oidc_jwks_unavailable", "jwks"),
        ("matching key", "oidc_jwks_key_missing", "id_token_validation"),
        ("signature", "oidc_invalid_signature", "id_token_validation"),
        ("nonce is missing", "oidc_nonce_missing", "id_token_validation"),
        ("nonce mismatch", "oidc_nonce_mismatch", "id_token_validation"),
        ("pkce verifier", "oidc_pkce_missing", "token_exchange"),
        ("exchange code", "oidc_token_exchange_failed", "token_exchange"),
        ("token endpoint", "oidc_token_endpoint_missing", "token_exchange"),
        ("authorization code", "oidc_authorization_code_missing", "callback"),
        ("access token", "oidc_access_token_missing", "token_exchange"),
        ("id token", "oidc_id_token_invalid", "id_token_validation"),
        ("user info", "oidc_userinfo_unavailable", "userinfo"),
        ("required upstream group", "sso_required_group_missing", "policy"),
        ("saml response", "saml_response_invalid", "assertion_validation"),
    )
    for needle, code, stage in mappings:
        if needle in detail:
            return code, stage
    return "sso_callback_failed", "callback"


def classify_sso_rejection(error: str) -> tuple[str, str]:
    """Map expected policy/account rejections to stable diagnostic codes."""

    return {
        "no_email": ("sso_email_missing", "userinfo"),
        "email_not_verified": ("sso_email_not_verified", "policy"),
        "domain_not_allowed": ("sso_domain_not_allowed", "policy"),
        "signup_not_allowed": ("sso_signup_not_allowed", "provisioning"),
        "user_creation_failed": ("sso_user_creation_failed", "provisioning"),
        "account_inactive": ("sso_account_inactive", "policy"),
        "account_deleted": ("sso_account_deleted", "policy"),
        "account_pending": ("sso_account_pending", "policy"),
        "sso_state_missing": ("sso_state_missing", "state_validation"),
        "sso_state_invalid": ("sso_state_invalid", "state_validation"),
        "sso_security_missing": ("sso_security_missing", "state_validation"),
    }.get(str(error or ""), ("sso_login_rejected", "policy"))


def record_sso_diagnostic(
    db_log,
    *,
    reference: str,
    provider: str,
    error_code: str,
    stage: str,
    user_agent: str | None,
    ip_address: str | None,
    details: dict | None = None,
    status: str = "error",
    notify_on_repeat: bool = True,
) -> None:
    """Persist one SSO failure and raise a deduplicated admin notification.

    Notification creation is best-effort and never changes the login outcome.
    Only repeated infrastructure/configuration errors trigger a notification.
    """

    message = f"Enterprise SSO failed ({error_code}) at {stage}. Reference: {reference}"
    create_authentication_log(
        db_log,
        "sso_login",
        status,
        message,
        None,
        user_agent,
        ip_address,
        correlation_id=reference,
        flow="sso",
        provider=provider,
        stage=stage,
        error_code=error_code,
        details=details or {},
    )
    if notify_on_repeat:
        _notify_repeated_sso_failure(
            reference=reference,
            provider=provider,
            error_code=error_code,
            stage=stage,
        )


def _notify_repeated_sso_failure(
    *, reference: str, provider: str, error_code: str, stage: str
) -> None:
    """Create at most one notification per provider/code every 30 minutes."""

    audit_db = AuditSessionLocal()
    app_db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        count = (
            audit_db.query(AuthenticationLogs)
            .filter(
                AuthenticationLogs.flow == "sso",
                AuthenticationLogs.provider == provider,
                AuthenticationLogs.error_code == error_code,
                AuthenticationLogs.timestamp >= now - _NOTIFICATION_WINDOW,
            )
            .count()
        )
        if count < _NOTIFICATION_THRESHOLD:
            return

        recent = (
            app_db.query(AdminNotifications)
            .filter(
                AdminNotifications.category == "auth",
                AdminNotifications.timestamp >= now - _NOTIFICATION_DEDUPE_WINDOW,
            )
            .order_by(AdminNotifications.timestamp.desc())
            .limit(100)
            .all()
        )
        if any(
            isinstance(item.details, dict)
            and item.details.get("provider") == provider
            and item.details.get("error_code") == error_code
            for item in recent
        ):
            return

        create_admin_notification(
            app_db,
            "auth",
            "Repeated enterprise authentication failures require attention.",
            notification_type="error",
            details={
                "reference": reference,
                "provider": provider,
                "stage": stage,
                "error_code": error_code,
                "count": count,
                "window_minutes": int(_NOTIFICATION_WINDOW.total_seconds() / 60),
            },
        )
    except Exception:
        logger.exception("Unable to create a repeated SSO failure notification")
    finally:
        audit_db.close()
        app_db.close()
