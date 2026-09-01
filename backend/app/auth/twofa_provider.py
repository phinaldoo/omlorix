from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import secrets
from typing import Any

import pyotp
from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified

from app.auth.models import (
    delete_authentication_all,
    delete_user_transient_auth_state,
)
from app.auth.session_store import revoke_user_sessions
from app.auth.email_delivery import (
    is_email_delivery_config_ready,
    load_login_email_delivery_config,
)
from app.auth.email_localization import resolve_email_language
from app.auth.jwt_material import get_jwt_material
from app.email.models import (
    consume_email_security_cooldown,
    get_email_security_action_epoch,
)
from app.settings.utils import get_value_by_page_and_key
from app.users.init import (
    _parse_settings,
    _sync_with_defaults,
    get_user_setting_value,
    update_user_settings,
)
from app.users.models import User
from app.users.external_management import (
    is_externally_managed,
    require_locally_managed_account,
)

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = {"totp", "email"}
LEGACY_PROVIDER_MAP = {"pyotp": "totp"}
_TOTP_LOCKOUT_SECONDS = 15 * 60


@dataclass
class TwoFAConfig:
    provider: str
    otp_length: int
    otp_ttl_seconds: int
    otp_resend_cooldown_seconds: int
    otp_max_attempts: int


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Coerce value to boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_int(value: Any, default: int) -> int:
    """Coerce value to positive integer."""
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def normalize_otp_action(otp_action: str | None, otp_type: str | None, otp_code: str | None) -> str | None:
    """Normalize OTP action from various input formats."""
    action = (otp_action or "").strip().lower() or None
    if not action:
        legacy = (otp_type or "").strip().lower()
        if legacy in {"setup", "verify", "login", "resend"}:
            action = "verify" if legacy in {"verify", "login"} else legacy
    if not action:
        action = "verify" if otp_code else None
    if action not in {"setup", "verify", "resend", None}:
        return None
    return action

def _get_settings_value(db, page: str, key: str, default: Any = None) -> Any:
    """Get settings value with fallback."""
    try:
        value = get_value_by_page_and_key(page, key, db)
    except Exception:
        return default
    return default if value is None else value


def _get_login_general_value(db, key: str, default: Any = None) -> Any:
    """Get login_general settings value."""
    return _get_settings_value(db, "login_general", key, default)


def _get_application_name(db) -> str:
    """Get application name from settings."""
    value = _get_settings_value(db, "general", "application_name", "Omlorix")
    return str(value or "Omlorix").replace("\r", " ").replace("\n", " ").strip() or "Omlorix"


def _get_user_language(user_id: str, db) -> str:
    """Get user language preference."""
    return resolve_email_language(
        get_user_setting_value(
            user_id,
            "general",
            "language",
            db,
            commit=False,
        )
    )


def resolve_user_2fa_provider(_user, db) -> str:
    """Resolve the globally configured 2FA provider for a user flow."""
    return resolve_global_2fa_provider(db)


def resolve_global_2fa_provider(db) -> str:
    """Resolve global 2FA provider from settings."""
    raw = _get_login_general_value(db, "twofa_provider", "totp")
    provider = str(raw).strip().lower()
    provider = LEGACY_PROVIDER_MAP.get(provider, provider)
    if provider not in SUPPORTED_PROVIDERS:
        provider = "totp"
    return provider


def get_global_2fa_config(db) -> TwoFAConfig:
    """Get global 2FA configuration."""
    return TwoFAConfig(
        provider=resolve_global_2fa_provider(db),
        otp_length=max(4, min(10, _coerce_int(_get_login_general_value(db, "otp_length", 6), 6))),
        otp_ttl_seconds=max(60, min(3600, _coerce_int(_get_login_general_value(db, "otp_ttl_seconds", 300), 300))),
        otp_resend_cooldown_seconds=max(
            5,
            min(
                600,
                _coerce_int(_get_login_general_value(db, "otp_resend_cooldown_seconds", 30), 30),
            ),
        ),
        otp_max_attempts=max(1, min(20, _coerce_int(_get_login_general_value(db, "otp_max_attempts", 5), 5))),
    )


def _mask_email(email: str | None) -> str:
    """Mask email address for display."""
    if not email:
        return ""
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    if len(local) <= 2:
        local_masked = local[:1] + "*"
    else:
        local_masked = local[:2] + "*" * max(1, len(local) - 2)
    return f"{local_masked}@{domain}"


def _clear_delivery_otp_state(user_id: str, db):
    """Clear delivery OTP state for user."""
    update_user_settings(user_id, "secret", "2fa_otp_hash", "", db)
    update_user_settings(user_id, "secret", "2fa_otp_expires_at", "", db)
    update_user_settings(user_id, "secret", "2fa_otp_last_sent_at", "", db)
    update_user_settings(user_id, "secret", "2fa_otp_attempts", 0, db)
    update_user_settings(user_id, "secret", "2fa_otp_purpose", "", db)
    update_user_settings(user_id, "secret", "2fa_otp_provider", "", db)
    update_user_settings(user_id, "secret", "2fa_otp_destination", "", db)


def _clear_delivery_otp_settings(settings: dict[str, Any]) -> None:
    secret = settings.setdefault("secret", {})
    secret["2fa_otp_hash"] = ""
    secret["2fa_otp_expires_at"] = ""
    secret["2fa_otp_last_sent_at"] = ""
    secret["2fa_otp_attempts"] = 0
    secret["2fa_otp_purpose"] = ""
    secret["2fa_otp_provider"] = ""
    secret["2fa_otp_destination"] = ""


def _disable_twofa_settings(settings: dict[str, Any]) -> None:
    """Clear every enrolled, pending, and throttling field in one payload."""

    login_2fa = settings.setdefault("login_2fa", {})
    login_2fa["enable_2fa"] = False
    login_2fa["provider"] = ""
    secret = settings.setdefault("secret", {})
    for key, value in (
        ("2fa_secret", ""),
        ("2fa_secret_pending", ""),
        ("2fa_totp_attempts", 0),
        ("2fa_totp_locked_until", ""),
        ("2fa_totp_ip_hash", ""),
        ("2fa_totp_ip_attempts", 0),
        ("2fa_totp_ip_locked_until", ""),
    ):
        secret[key] = value
    _clear_delivery_otp_settings(settings)


def _locked_user_settings(user_id: str, db) -> tuple[User | None, dict[str, Any]]:
    locked_user = (
        db.query(User)
        .populate_existing()
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )
    if not locked_user:
        return None, {}

    current = _parse_settings(locked_user.settings)
    changed, settings = _sync_with_defaults(current)
    if changed or not isinstance(locked_user.settings, dict):
        locked_user.settings = settings
        flag_modified(locked_user, "settings")
        db.flush()
    return locked_user, settings


def clear_user_twofa_state(user_id: str, db):
    """Atomically clear 2FA, continuations, and notify the account owner."""

    locked_user, settings = _locked_user_settings(user_id, db)
    if not locked_user:
        raise HTTPException(status_code=404, detail="User not found.")
    require_locally_managed_account(locked_user)
    _disable_twofa_settings(settings)
    locked_user.settings = settings
    flag_modified(locked_user, "settings")
    try:
        delete_authentication_all(
            db,
            locked_user.id,
            commit=False,
            revoke_cached=False,
        )
        delete_user_transient_auth_state(db, locked_user.id, commit=False)
        notification = _send_twofa_deactivated_email(locked_user, db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    revoke_user_sessions(locked_user.id)
    return {
        "status": "success",
        "security_notification": notification,
    }


def _get_user_2fa_provider(user_id: str, db) -> str:
    """Get normalized user 2FA provider with legacy fallback."""
    user_provider = str(get_user_setting_value(user_id, "login_2fa", "provider", db) or "").strip().lower()
    user_provider = LEGACY_PROVIDER_MAP.get(user_provider, user_provider)
    if not user_provider and get_user_setting_value(user_id, "secret", "2fa_secret", db):
        user_provider = "totp"
    return user_provider


def _requires_provider_migration(user_id: str, provider: str, db) -> bool:
    """Return whether an enabled user must re-enroll for the configured provider."""
    user_enabled = bool(get_user_setting_value(user_id, "login_2fa", "enable_2fa", db))
    user_provider = _get_user_2fa_provider(user_id, db)
    return user_enabled and bool(user_provider) and user_provider != provider


def ensure_provider_alignment(user_id: str, db, configured_provider: str | None = None):
    """Ensure user 2FA provider matches the effective configured setting."""
    configured_provider = configured_provider or resolve_global_2fa_provider(db)
    user_provider = _get_user_2fa_provider(user_id, db)
    if _requires_provider_migration(user_id, configured_provider, db):
        logger.warning(
            "2FA provider mismatch for user=%s old_provider=%s new_provider=%s; re-enrollment required",
            user_id,
            user_provider,
            configured_provider,
        )


def _is_user_enrolled_for_provider(user_id: str, provider: str, db) -> bool:
    """Check if user is enrolled for specific 2FA provider."""
    enabled = bool(get_user_setting_value(user_id, "login_2fa", "enable_2fa", db))
    if not enabled:
        return False
    enrolled_provider = _get_user_2fa_provider(user_id, db)
    if enrolled_provider and enrolled_provider != provider:
        return False
    if provider == "totp":
        return bool(get_user_setting_value(user_id, "secret", "2fa_secret", db))
    if provider == "email":
        return True
    return False


def get_login_2fa_session_policy(user, db) -> dict[str, Any]:
    """Return the current side-effect-free 2FA policy for an authenticated session."""
    if is_externally_managed(user):
        version_payload = {
            "required": False,
            "mode": "none",
            "provider": "",
            "global_enabled": False,
            "global_force": False,
        }
        version = hashlib.sha256(
            json.dumps(version_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
        return {**version_payload, "version": f"2fa-v1:{version}"}

    global_enabled = _coerce_bool(_get_login_general_value(db, "enable_2fa", True), True)
    provider = resolve_user_2fa_provider(user, db) if global_enabled else ""
    global_force = _coerce_bool(_get_login_general_value(db, "force_2fa", False), False)

    required = False
    mode = "none"
    if global_enabled:
        enrolled = _is_user_enrolled_for_provider(user.id, provider, db)
        migration_required = _requires_provider_migration(user.id, provider, db)
        if enrolled:
            required = True
            mode = "verify"
        elif migration_required or global_force:
            required = True
            mode = "setup"

    version_payload = {
        "required": required,
        "mode": mode,
        "provider": provider,
        "global_enabled": global_enabled,
        "global_force": global_force,
    }
    version = hashlib.sha256(
        json.dumps(version_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return {
        **version_payload,
        "version": f"2fa-v1:{version}",
    }


def build_login_2fa_session_claims(user, db) -> dict[str, Any]:
    """Build JWT claims proving the current login 2FA policy has been satisfied."""
    policy = get_login_2fa_session_policy(user, db)
    if not policy.get("required"):
        return {}
    return {
        "twofa_satisfied": True,
        "twofa_provider": policy.get("provider"),
        "twofa_policy_version": policy.get("version"),
    }


def _verify_totp_code(secret: str, code: str) -> bool:
    """Verify TOTP code against secret for only the current timestep."""
    return pyotp.totp.TOTP(secret).verify(code)


def _parse_utc_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _totp_ip_bucket(client_ip: str | None) -> str:
    normalized = str(client_ip or "").strip().lower()
    if not normalized or normalized == "unknown":
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _clear_totp_attempt_state(user_id: str, db) -> None:
    update_user_settings(user_id, "secret", "2fa_totp_attempts", 0, db)
    update_user_settings(user_id, "secret", "2fa_totp_locked_until", "", db)
    update_user_settings(user_id, "secret", "2fa_totp_ip_hash", "", db)
    update_user_settings(user_id, "secret", "2fa_totp_ip_attempts", 0, db)
    update_user_settings(user_id, "secret", "2fa_totp_ip_locked_until", "", db)


def _totp_throttle_status(user_id: str, db, client_ip: str | None = None) -> str | None:
    now = datetime.now(timezone.utc)
    user_locked_until = _parse_utc_datetime(get_user_setting_value(user_id, "secret", "2fa_totp_locked_until", db))
    if user_locked_until and user_locked_until > now:
        return "user"

    ip_bucket = _totp_ip_bucket(client_ip)
    stored_ip_bucket = str(get_user_setting_value(user_id, "secret", "2fa_totp_ip_hash", db) or "")
    ip_locked_until = _parse_utc_datetime(get_user_setting_value(user_id, "secret", "2fa_totp_ip_locked_until", db))
    if ip_bucket and stored_ip_bucket == ip_bucket and ip_locked_until and ip_locked_until > now:
        return "ip"

    if user_locked_until and user_locked_until <= now:
        update_user_settings(user_id, "secret", "2fa_totp_attempts", 0, db)
        update_user_settings(user_id, "secret", "2fa_totp_locked_until", "", db)
    if ip_locked_until and ip_locked_until <= now:
        update_user_settings(user_id, "secret", "2fa_totp_ip_attempts", 0, db)
        update_user_settings(user_id, "secret", "2fa_totp_ip_locked_until", "", db)
    return None


def _record_totp_failure(user_id: str, db, client_ip: str | None = None) -> str | None:
    config = get_global_2fa_config(db)
    lock_until = (datetime.now(timezone.utc) + timedelta(seconds=max(_TOTP_LOCKOUT_SECONDS, config.otp_ttl_seconds))).isoformat()

    attempts = _coerce_int(get_user_setting_value(user_id, "secret", "2fa_totp_attempts", db), 0) + 1
    update_user_settings(user_id, "secret", "2fa_totp_attempts", attempts, db)
    lock_scope = "user" if attempts >= config.otp_max_attempts else None
    if attempts >= config.otp_max_attempts:
        update_user_settings(user_id, "secret", "2fa_totp_locked_until", lock_until, db)

    ip_bucket = _totp_ip_bucket(client_ip)
    if not ip_bucket:
        return lock_scope

    stored_ip_bucket = str(get_user_setting_value(user_id, "secret", "2fa_totp_ip_hash", db) or "")
    ip_attempts = _coerce_int(get_user_setting_value(user_id, "secret", "2fa_totp_ip_attempts", db), 0)
    if stored_ip_bucket != ip_bucket:
        update_user_settings(user_id, "secret", "2fa_totp_ip_hash", ip_bucket, db)
        ip_attempts = 0

    ip_attempts += 1
    update_user_settings(user_id, "secret", "2fa_totp_ip_attempts", ip_attempts, db)
    if ip_attempts >= config.otp_max_attempts:
        update_user_settings(user_id, "secret", "2fa_totp_ip_locked_until", lock_until, db)
        lock_scope = lock_scope or "ip"
    return lock_scope


def _generate_totp_setup(user, db):
    """Generate TOTP setup for user."""
    secret = pyotp.random_base32()
    update_user_settings(user.id, "secret", "2fa_secret_pending", secret, db)
    return {"status": "otp_setup", "provider": "totp", "setup_material_available": True, "resend_available_in_seconds": 0}


def get_totp_setup_material(user, db):
    """Return pending TOTP setup material for an authenticated short-lived setup session."""
    require_locally_managed_account(user)
    secret = str(get_user_setting_value(user.id, "secret", "2fa_secret_pending", db) or "").strip()
    if not secret:
        raise HTTPException(status_code=404, detail="No pending TOTP setup session.")
    issuer = _get_application_name(db)
    qrcode = pyotp.totp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=issuer)
    return {"provider": "totp", "qrcode": qrcode, "secret": secret}


def _delivery_hint_for_provider(user, provider: str, db) -> str:
    """Get delivery hint for provider."""
    if provider == "email":
        return _mask_email(getattr(user, "email", ""))
    return ""


def _get_hash_secret(db) -> str:
    """Use the operator-managed signing key for short-lived OTP hashes."""
    try:
        secret, _algorithm = get_jwt_material()
    except Exception as exc:
        logger.exception("Unable to load environment signing material for 2FA OTP hashing")
        raise HTTPException(status_code=500, detail="2FA is temporarily unavailable. Please contact an administrator.") from exc
    return secret


def _hash_delivery_otp(code: str, user_id: str, provider: str, purpose: str, db) -> str:
    epoch = get_email_security_action_epoch(db)
    material = f"{code}:{user_id}:{provider}:{purpose}:{epoch}:{_get_hash_secret(db)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _seconds_until_resend_value(
    last_sent_value: object,
    config: TwoFAConfig,
    *,
    now: datetime | None = None,
) -> int:
    last_sent = str(last_sent_value or "").strip()
    if not last_sent:
        return 0
    try:
        sent_at = datetime.fromisoformat(last_sent)
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
    except Exception:
        return 0
    elapsed = ((now or datetime.now(timezone.utc)) - sent_at).total_seconds()
    remaining = config.otp_resend_cooldown_seconds - int(elapsed)
    return min(config.otp_resend_cooldown_seconds, max(0, remaining))


def _seconds_until_resend(user_id: str, config: TwoFAConfig, db) -> int:
    return _seconds_until_resend_value(
        get_user_setting_value(
            user_id,
            "secret",
            "2fa_otp_last_sent_at",
            db,
        ),
        config,
    )


def _send_twofa_deactivated_email(user, db) -> str:
    """Stage the legacy deactivation notice through the durable outbox."""
    from app.email.service import enqueue_security_event

    return "queued" if enqueue_security_event(
        db,
        user=user,
        event_type="twofa_disabled",
    ) else "skipped"




def _issue_delivery_otp(
    user,
    provider: str,
    purpose: str,
    _destination: str | None,
    config: TwoFAConfig,
    db,
) -> tuple[int, str]:
    language_code = _get_user_language(user.id, db)
    if provider == "email" and not is_email_delivery_config_ready(
        load_login_email_delivery_config(db)
    ):
        raise HTTPException(
            status_code=409,
            detail="Email 2FA is not configured.",
        )
    now = datetime.now(timezone.utc)
    locked_user, settings = _locked_user_settings(user.id, db)
    if not locked_user:
        raise HTTPException(status_code=404, detail="User not found.")
    destination = str(getattr(locked_user, "email", "") or "").strip()
    if provider == "email" and not destination:
        raise HTTPException(
            status_code=409,
            detail="User email is required for email-based 2FA.",
        )
    secret = settings.setdefault("secret", {})
    remaining = consume_email_security_cooldown(
        db,
        bucket=f"twofa-otp:{user.id}:{provider}:{purpose}",
        cooldown_seconds=config.otp_resend_cooldown_seconds,
        now=now,
    )
    if remaining > 0:
        db.commit()
        return remaining, destination

    code = "".join(secrets.choice("0123456789") for _ in range(config.otp_length))
    code_hash = _hash_delivery_otp(code, user.id, provider, purpose, db)
    expires_at = now + timedelta(seconds=config.otp_ttl_seconds)
    secret.update(
        {
            "2fa_otp_hash": code_hash,
            "2fa_otp_expires_at": expires_at.isoformat(),
            "2fa_otp_last_sent_at": now.isoformat(),
            "2fa_otp_attempts": 0,
            "2fa_otp_purpose": purpose,
            "2fa_otp_provider": provider,
            "2fa_otp_destination": destination,
        }
    )
    locked_user.settings = settings
    flag_modified(locked_user, "settings")
    try:
        if provider == "email":
            from app.email.models import enqueue_email

            enqueue_email(
                db,
                user_id=user.id,
                recipient=destination,
                template_type="twofa_otp",
                language_code=language_code,
                priority=0,
                expires_at=expires_at,
                idempotency_key=f"twofa-otp:{user.id}:{code_hash}",
                payload={
                    "code": code,
                    "otp_hash": code_hash,
                    "provider": provider,
                    "purpose": purpose,
                    "expires_in_minutes": max(1, config.otp_ttl_seconds // 60),
                },
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return 0, destination


def _begin_delivery_setup(user, provider: str, otp_destination: str | None, db):
    config = get_global_2fa_config(db)
    if provider == "email":
        destination = getattr(user, "email", None)
        if not destination:
            raise HTTPException(status_code=409, detail="User email is required for email-based 2FA.")

    remaining, destination = _issue_delivery_otp(
        user,
        provider,
        "setup",
        destination,
        config,
        db,
    )
    if remaining > 0:
        return {
            "status": "otp_setup",
            "provider": provider,
            "delivery_hint": _mask_email(destination),
            "resend_available_in_seconds": remaining,
        }
    return {
        "status": "otp_setup",
        "provider": provider,
        "delivery_hint": _mask_email(destination),
        "resend_available_in_seconds": config.otp_resend_cooldown_seconds,
    }


def _begin_delivery_verify(user, provider: str, purpose: str, db):
    config = get_global_2fa_config(db)
    if provider == "email":
        destination = getattr(user, "email", None)
        if not destination:
            raise HTTPException(status_code=409, detail="User email is required for email-based 2FA.")
    remaining, destination = _issue_delivery_otp(
        user,
        provider,
        purpose,
        destination,
        config,
        db,
    )
    if remaining > 0:
        return {
            "status": "otp_required_already_setup",
            "provider": provider,
            "delivery_hint": _mask_email(destination),
            "resend_available_in_seconds": remaining,
        }
    return {
        "status": "otp_required_already_setup",
        "provider": provider,
        "delivery_hint": _mask_email(destination),
        "resend_available_in_seconds": config.otp_resend_cooldown_seconds,
    }


def _verify_delivery_code(
    user,
    provider: str,
    purpose: str,
    otp_code: str | None,
    db,
    *,
    commit_on_success: bool = True,
):
    return _consume_delivery_code(
        user,
        provider,
        purpose,
        otp_code,
        db,
        commit_on_success=commit_on_success,
    )


def _consume_delivery_code(
    user,
    provider: str,
    purpose: str,
    otp_code: str | None,
    db,
    *,
    commit_on_success: bool = True,
):
    code = str(otp_code or "").strip()
    if not code:
        return False

    locked_user, settings = _locked_user_settings(user.id, db)
    if not locked_user:
        return False

    secret = settings.get("secret") if isinstance(settings.get("secret"), dict) else {}
    expected_hash = str(secret.get("2fa_otp_hash") or "")
    expires_at_raw = str(secret.get("2fa_otp_expires_at") or "")
    stored_purpose = str(secret.get("2fa_otp_purpose") or "")
    stored_provider = str(secret.get("2fa_otp_provider") or "")
    if stored_purpose != purpose or stored_provider != provider:
        return False
    stored_destination = str(secret.get("2fa_otp_destination") or "").strip().lower()
    current_destination = str(getattr(locked_user, "email", "") or "").strip().lower()
    if provider == "email" and (
        not stored_destination
        or not current_destination
        or not hmac.compare_digest(stored_destination, current_destination)
    ):
        _clear_delivery_otp_settings(settings)
        locked_user.settings = settings
        flag_modified(locked_user, "settings")
        db.commit()
        return False
    attempts = _coerce_int(secret.get("2fa_otp_attempts"), 0)
    config = get_global_2fa_config(db)
    if attempts >= config.otp_max_attempts:
        _clear_delivery_otp_settings(settings)
        locked_user.settings = settings
        flag_modified(locked_user, "settings")
        db.commit()
        return False
    secret["2fa_otp_attempts"] = attempts + 1
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except Exception:
        locked_user.settings = settings
        flag_modified(locked_user, "settings")
        db.commit()
        return False
    if datetime.now(timezone.utc) > expires_at:
        locked_user.settings = settings
        flag_modified(locked_user, "settings")
        db.commit()
        return False
    if _hash_delivery_otp(code, user.id, provider, purpose, db) != expected_hash:
        locked_user.settings = settings
        flag_modified(locked_user, "settings")
        db.commit()
        return False

    _clear_delivery_otp_settings(settings)
    locked_user.settings = settings
    flag_modified(locked_user, "settings")
    if commit_on_success:
        db.commit()
    else:
        db.flush()
    return True


def begin_setup(user, action: str | None, otp_destination: str | None, db, provider: str | None = None):
    """Begin 2FA setup process."""
    provider = provider or resolve_user_2fa_provider(user, db)
    if provider == "totp":
        return _generate_totp_setup(user, db)
    return _begin_delivery_setup(user, provider, otp_destination, db)


def verify_setup(
    user,
    otp_code: str | None,
    action: str | None,
    otp_destination: str | None,
    db,
    provider: str | None = None,
    client_ip: str | None = None,
):
    """Verify 2FA setup code."""
    provider = provider or resolve_user_2fa_provider(user, db)
    if provider == "totp":
        pending = get_user_setting_value(user.id, "secret", "2fa_secret_pending", db)
        if not pending:
            return _generate_totp_setup(user, db)
        if _totp_throttle_status(user.id, db, client_ip):
            return {"status": "otp_locked", "provider": "totp"}
        if not _verify_totp_code(pending, str(otp_code or "").strip()):
            lock_scope = _record_totp_failure(user.id, db, client_ip)
            if lock_scope:
                logger.warning("TOTP setup locked for user %s by %s attempts", user.id, lock_scope)
                return {"status": "otp_locked", "provider": "totp"}
            return {"status": "otp_invalid"}
        locked_user, settings = _locked_user_settings(user.id, db)
        if not locked_user:
            raise HTTPException(status_code=404, detail="User not found.")
        locked_pending = str(
            (settings.get("secret") or {}).get("2fa_secret_pending") or ""
        ).strip()
        if (
            not locked_pending
            or not secrets.compare_digest(locked_pending, str(pending))
            or not _verify_totp_code(
                locked_pending,
                str(otp_code or "").strip(),
            )
        ):
            db.rollback()
            return {"status": "otp_invalid"}
        login_2fa = settings.setdefault("login_2fa", {})
        login_2fa["enable_2fa"] = True
        login_2fa["provider"] = "totp"
        secret = settings.setdefault("secret", {})
        secret["2fa_secret"] = locked_pending
        secret["2fa_secret_pending"] = ""
        for key, value in (
            ("2fa_totp_attempts", 0),
            ("2fa_totp_locked_until", ""),
            ("2fa_totp_ip_hash", ""),
            ("2fa_totp_ip_attempts", 0),
            ("2fa_totp_ip_locked_until", ""),
        ):
            secret[key] = value
        locked_user.settings = settings
        flag_modified(locked_user, "settings")
        from app.email.service import enqueue_security_event

        try:
            enqueue_security_event(db, user=locked_user, event_type="twofa_enabled")
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"status": "success"}

    if not otp_code:
        return _begin_delivery_setup(user, provider, otp_destination, db)
    if not _verify_delivery_code(
        user,
        provider,
        "setup",
        otp_code,
        db,
        commit_on_success=False,
    ):
        return {"status": "otp_invalid"}
    locked_user, settings = _locked_user_settings(user.id, db)
    if not locked_user:
        db.rollback()
        raise HTTPException(status_code=404, detail="User not found.")
    login_2fa = settings.setdefault("login_2fa", {})
    login_2fa["enable_2fa"] = True
    login_2fa["provider"] = provider
    locked_user.settings = settings
    flag_modified(locked_user, "settings")
    from app.email.service import enqueue_security_event

    try:
        enqueue_security_event(db, user=locked_user, event_type="twofa_enabled")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"status": "success"}


def begin_verify(user, action: str | None, db, provider: str | None = None):
    """Begin 2FA verification process."""
    provider = provider or resolve_user_2fa_provider(user, db)
    if provider == "totp":
        return {"status": "otp_required_already_setup", "provider": "totp", "delivery_hint": "", "resend_available_in_seconds": 0}
    purpose = "step_up" if action == "step_up" else "login"
    return _begin_delivery_verify(user, provider, purpose, db)


def verify_login_code(
    user,
    otp_code: str | None,
    db,
    provider: str | None = None,
    client_ip: str | None = None,
    *,
    purpose: str = "login",
):
    """Verify login 2FA code."""
    provider = provider or resolve_user_2fa_provider(user, db)
    if provider == "totp":
        secret = get_user_setting_value(user.id, "secret", "2fa_secret", db)
        if _totp_throttle_status(user.id, db, client_ip):
            return "locked"
        if not _verify_totp_code(str(secret or ""), str(otp_code or "").strip()):
            lock_scope = _record_totp_failure(user.id, db, client_ip)
            if lock_scope:
                logger.warning("TOTP login locked for user %s by %s attempts", user.id, lock_scope)
                return "locked"
            return False
        _clear_totp_attempt_state(user.id, db)
        return True
    valid = _verify_delivery_code(user, provider, purpose, otp_code, db)
    return valid


def evaluate_login_2fa(
    user,
    otp_code: str | None,
    otp_action: str | None,
    otp_destination: str | None,
    db,
    client_ip: str | None = None,
):
    """Evaluate 2FA requirements for login."""
    # Enterprise MFA is enforced by the upstream IdP. A second Omlorix factor
    # would create a separate enrollment and recovery authority.
    if is_externally_managed(user):
        return None

    enable_2fa = _coerce_bool(_get_login_general_value(db, "enable_2fa", True), True)
    if not enable_2fa:
        return None
    force_2fa = _coerce_bool(_get_login_general_value(db, "force_2fa", False), False)
    provider = resolve_user_2fa_provider(user, db)
    ensure_provider_alignment(user.id, db, provider)
    enrolled = _is_user_enrolled_for_provider(user.id, provider, db)

    if enrolled:
        if otp_code:
            verify_result = verify_login_code(user, otp_code, db, provider, client_ip)
            if verify_result is True:
                return None
            if verify_result == "locked":
                return {"status": "otp_locked", "provider": provider}
            return {"status": "otp_invalid", "provider": provider}
        return begin_verify(user, otp_action, db, provider)

    if _requires_provider_migration(user.id, provider, db):
        if otp_code:
            setup_result = verify_setup(user, otp_code, otp_action, otp_destination, db, provider, client_ip)
            if setup_result.get("status") == "success":
                return None
            return setup_result
        return begin_setup(user, otp_action, otp_destination, db, provider)

    if force_2fa:
        if otp_code:
            setup_result = verify_setup(user, otp_code, otp_action, otp_destination, db, provider, client_ip)
            if setup_result.get("status") == "success":
                return None
            return setup_result
        return begin_setup(user, otp_action, otp_destination, db, provider)

    return None


def deactivate(user, db):
    """Deactivate 2FA and notify the account owner out of band when possible."""
    globally_enabled = _coerce_bool(_get_login_general_value(db, "enable_2fa", True), True)
    globally_forced = _coerce_bool(_get_login_general_value(db, "force_2fa", False), False)
    if globally_enabled and globally_forced:
        raise HTTPException(status_code=409, detail="2FA is mandatory and cannot be deactivated for this user.")
    locked_user, settings = _locked_user_settings(user.id, db)
    if not locked_user:
        raise HTTPException(status_code=404, detail="User not found.")
    login_2fa = settings.setdefault("login_2fa", {})
    if not bool(login_2fa.get("enable_2fa")):
        raise HTTPException(status_code=409, detail="2FA is not active for this user.")
    _disable_twofa_settings(settings)
    locked_user.settings = settings
    flag_modified(locked_user, "settings")
    try:
        delete_user_transient_auth_state(db, locked_user.id, commit=False)
        notification = _send_twofa_deactivated_email(locked_user, db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "status": "success",
        "security_notification": notification,
    }
