from datetime import datetime, timedelta, timezone
import logging
import hashlib
import hmac
import secrets
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from urllib.parse import urlparse
from fastapi import HTTPException
from typing import Any
import json
import re
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)

from app.auth.models import PasskeyCredential, WebAuthnChallenge
from app.database import SessionLocal
from app.logging.models import create_admin_notification
from app.settings.models import update_settings_page
from app.settings.utils import get_login_passkey_policy, get_public_urls, get_value_by_page_and_key
from app.settings.public_urls import normalize_public_url
from app.users.models import get_user
from app.users.external_management import (
    is_externally_managed,
    require_locally_managed_account,
)



_CHALLENGE_TTL = timedelta(minutes=5)
_AUTH_ALLOW_CREDENTIALS_SIZE = 16
logger = logging.getLogger(__name__)


def _looks_like_byte_values(values: list[Any]) -> bool:
    """Check if values look like byte array."""
    return all(isinstance(item, int) and 0 <= item <= 255 for item in values)


def _coerce_binary_value_to_base64url(value: Any) -> Any:
    """Coerce binary value to base64url string."""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes_to_base64url(bytes(value))
    if isinstance(value, list) and _looks_like_byte_values(value):
        return bytes_to_base64url(bytes(value))
    if isinstance(value, dict):
        if value.get("type") == "Buffer" and isinstance(value.get("data"), list) and _looks_like_byte_values(value["data"]):
            return bytes_to_base64url(bytes(value["data"]))

        digit_keys = [key for key in value.keys() if isinstance(key, str) and key.isdigit()]
        if digit_keys and len(digit_keys) == len(value):
            ordered = [value[key] for key in sorted(digit_keys, key=int)]
            if _looks_like_byte_values(ordered):
                return bytes_to_base64url(bytes(ordered))

    return value


def _normalize_webauthn_credential_payload(credential: Any, *, registration: bool) -> dict[str, Any]:
    """Normalize WebAuthn credential payload."""
    if not isinstance(credential, dict):
        raise HTTPException(status_code=400, detail="Invalid WebAuthn credential payload")

    normalized = dict(credential)
    cred_id = _coerce_binary_value_to_base64url(normalized.get("id"))
    raw_id = _coerce_binary_value_to_base64url(normalized.get("rawId"))

    if isinstance(cred_id, str) and cred_id:
        normalized["id"] = cred_id
    elif isinstance(raw_id, str) and raw_id:
        normalized["id"] = raw_id

    if isinstance(raw_id, str) and raw_id:
        normalized["rawId"] = raw_id
    elif isinstance(normalized.get("id"), str) and normalized["id"]:
        normalized["rawId"] = normalized["id"]

    response = normalized.get("response")
    if isinstance(response, dict):
        normalized_response = dict(response)
        for key in ("clientDataJSON", "attestationObject", "authenticatorData", "signature", "userHandle"):
            if key in normalized_response and normalized_response[key] is not None:
                normalized_response[key] = _coerce_binary_value_to_base64url(normalized_response[key])
        normalized["response"] = normalized_response

    has_attestation = isinstance(normalized.get("response"), dict) and isinstance(normalized["response"].get("attestationObject"), str)
    has_signature = isinstance(normalized.get("response"), dict) and isinstance(normalized["response"].get("signature"), str)
    if registration and not has_attestation:
        raise HTTPException(status_code=400, detail="Invalid WebAuthn registration payload")
    if not registration and not has_signature:
        raise HTTPException(status_code=400, detail="Invalid WebAuthn authentication payload")

    return normalized


def _is_loopback_host(host: str | None) -> bool:
    """Check if host is a loopback address."""
    if not isinstance(host, str):
        return False
    normalized = host.strip().lower()
    return normalized in {"localhost", "127.0.0.1", "::1"}


def _extract_client_origin_from_credential(credential: dict[str, Any]) -> str | None:
    """Extract client origin from WebAuthn credential."""
    try:
        response = credential.get("response")
        if not isinstance(response, dict):
            return None
        client_data_b64 = response.get("clientDataJSON")
        if not isinstance(client_data_b64, str) or not client_data_b64:
            return None
        client_data = json.loads(base64url_to_bytes(client_data_b64).decode("utf-8"))
        origin = str(client_data.get("origin") or "").strip()
        return origin or None
    except Exception:
        return None


def _can_use_localhost_origin_fallback(expected_origin: str, client_origin: str, rp_id: str) -> bool:
    """Check if localhost origin fallback is appropriate.

    WebAuthn origins are security boundaries. The fallback may only recover
    harmless textual differences in otherwise equivalent loopback origins; it
    must not accept a different scheme or effective port from clientDataJSON.
    """
    try:
        expected = urlparse(expected_origin)
        client = urlparse(client_origin)
        expected_port = expected.port or (
            443 if expected.scheme == "https" else 80 if expected.scheme == "http" else None
        )
        client_port = client.port or (
            443 if client.scheme == "https" else 80 if client.scheme == "http" else None
        )
    except Exception:
        return False

    expected_scheme = (expected.scheme or "").strip().lower()
    client_scheme = (client.scheme or "").strip().lower()
    expected_host = (expected.hostname or "").strip().lower()
    client_host = (client.hostname or "").strip().lower()
    normalized_rp_id = str(rp_id or "").strip().lower()

    if not expected_host or not client_host or not normalized_rp_id:
        return False
    if expected_host != client_host or client_host != normalized_rp_id:
        return False
    if not _is_loopback_host(client_host):
        return False
    if expected_scheme not in {"http", "https"} or client_scheme not in {"http", "https"}:
        return False
    if expected_scheme != client_scheme or expected_port != client_port:
        return False
    return expected_origin != client_origin


def _record_passkey_origin_notification(
    *,
    message: str,
    expected_origin: str,
    client_origin: str,
    rp_id: str,
    user_id: str | None,
    recovered: bool,
) -> None:
    """Record passkey origin mismatch notification."""
    details = {
        "event": "passkey_origin_mismatch",
        "expected_origin": expected_origin,
        "client_origin": client_origin,
        "rp_id": rp_id,
        "user_id": user_id,
        "recovered": recovered,
    }
    try:
        db = SessionLocal()
        try:
            create_admin_notification(
                db,
                "auth",
                message,
                details=details,
                notification_type="warning" if recovered else "error",
            )
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to create admin notification for passkey origin mismatch")


def _origin_mismatch_detail(expected_origin: str, client_origin: str) -> str:
    """Generate detail message for origin mismatch."""
    return (
        f'Passkey verification failed because WebAuthn origin mismatch: browser sent "{client_origin}" '
        f'but server expects "{expected_origin}". Update Settings > General > Public URL so it exactly matches '
        "the URL users open in the browser (including http/https and port)."
    )


def _derive_passkey_device_name(user_agent: str | None) -> str:
    """Derive device name from user agent string."""
    ua = str(user_agent or "").strip()
    if not ua:
        return "Unknown device"

    browser = "Browser"
    if "Firefox/" in ua or "FxiOS/" in ua:
        browser = "Firefox"
    elif "Edg/" in ua:
        browser = "Edge"
    elif "OPR/" in ua or "Opera/" in ua:
        browser = "Opera"
    elif "SamsungBrowser/" in ua:
        browser = "Samsung Browser"
    elif "CriOS/" in ua or ("Chrome/" in ua and "Chromium/" not in ua):
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"

    os_name = "Unknown OS"
    if re.search(r"Android", ua, re.IGNORECASE):
        os_name = "Android"
    elif re.search(r"iPhone|iPad|iPod", ua, re.IGNORECASE) or (
        re.search(r"AppleWebKit", ua, re.IGNORECASE)
        and re.search(r"Mobile", ua, re.IGNORECASE)
        and re.search(r"Safari", ua, re.IGNORECASE)
    ):
        os_name = "iOS"
    elif re.search(r"Windows NT", ua, re.IGNORECASE):
        os_name = "Windows"
    elif re.search(r"Macintosh|Mac OS X", ua, re.IGNORECASE):
        os_name = "macOS"
    elif re.search(r"Linux", ua, re.IGNORECASE):
        os_name = "Linux"

    device_model = ""
    if re.search(r"iPhone", ua, re.IGNORECASE):
        device_model = "iPhone"
    elif re.search(r"iPad", ua, re.IGNORECASE):
        device_model = "iPad"
    elif re.search(r"iPod", ua, re.IGNORECASE):
        device_model = "iPod"
    elif re.search(r"Pixel", ua, re.IGNORECASE):
        device_model = "Google Pixel"
    elif re.search(r"SM-G|Galaxy S", ua, re.IGNORECASE):
        device_model = "Samsung Galaxy"

    if device_model:
        return f"{browser} on {device_model}"
    return f"{browser} on {os_name}"



# -------------------
# Derive RP ID from Public URL
# -------------------
def _derive_rp_id_from_public_url(public_url: str) -> str:
    """Derive RP ID from public URL."""
    parsed = urlparse(public_url)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise HTTPException(status_code=500, detail="Public URL is misconfigured for passkeys")
    return host



# -------------------
# Derive Origin from Public URL
# -------------------
def _derive_origin_from_public_url(public_url: str) -> str:
    """Derive a normalized WebAuthn origin from the configured public URL."""
    try:
        # Browser origins retain brackets around IPv6 literals, so use the
        # shared normalizer instead of rebuilding the URL from ``hostname``.
        return normalize_public_url(public_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail="Public URL is misconfigured for passkeys",
        ) from exc



# -------------------
# Get Passkey Policy
# -------------------
def get_passkey_policy(db: Session) -> dict[str, Any]:
    return get_login_passkey_policy(db)



# -------------------
# Resolve WebAuthn Config
# -------------------
def _resolve_webauthn_config(db: Session) -> tuple[str, str, str]:
    """Resolve WebAuthn configuration from the primary public URL."""
    policy = get_passkey_policy(db)
    try:
        public_url = get_public_urls(db)[0]
    except (HTTPException, IndexError):
        raise HTTPException(
            status_code=500,
            detail="Passkeys require general.public_url to be configured.",
        )

    rp_id = _derive_rp_id_from_public_url(public_url)
    rp_name = str(get_value_by_page_and_key("general", "application_name", db) or "Omlorix").strip() or "Omlorix"
    expected_origin = _derive_origin_from_public_url(public_url)
    return rp_id, rp_name, expected_origin


def _resolve_webauthn_config_for_origin(
    db: Session,
    public_origin: str | None,
) -> tuple[str, str, str]:
    """Resolve WebAuthn configuration for a matching configured public origin.

    WebAuthn credentials are scoped to an RP ID. Selecting the URL actually in
    use allows passkeys to work independently on each configured public host.
    An unrecognized origin intentionally falls back to the primary URL so the
    existing mismatch handling rejects it instead of trusting request input.
    """
    primary_config = _resolve_webauthn_config(db)
    if public_origin is None:
        return primary_config
    try:
        candidate = normalize_public_url(public_origin)
    except ValueError:
        return primary_config
    if candidate == primary_config[2]:
        return primary_config

    try:
        configured_urls = get_public_urls(db)
    except HTTPException:
        return primary_config
    if candidate not in configured_urls:
        return primary_config

    rp_name = primary_config[1]
    return (
        _derive_rp_id_from_public_url(candidate),
        rp_name,
        _derive_origin_from_public_url(candidate),
    )



# -------------------
# Create Challenge
# -------------------
def _create_challenge(db: Session, *, user_id: str | None, flow: str) -> WebAuthnChallenge:
    """Create WebAuthn challenge."""
    challenge_bytes = secrets.token_bytes(32)
    challenge_b64 = bytes_to_base64url(challenge_bytes)
    now = datetime.now(timezone.utc)
    entry = WebAuthnChallenge(
        user_id=user_id,
        flow=flow,
        challenge=challenge_b64,
        created_at=now,
        expires_at=now + _CHALLENGE_TTL,
        used_at=None,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry



# -------------------
# Consume Challenge
# -------------------
def _consume_challenge(db: Session, *, challenge_b64: str, flow: str, user_id: str | None) -> WebAuthnChallenge:
    """Consume WebAuthn challenge."""
    now = datetime.now(timezone.utc)
    try:
        entry = (
            db.query(WebAuthnChallenge)
            .filter(
                WebAuthnChallenge.challenge == challenge_b64,
                WebAuthnChallenge.flow == flow,
                WebAuthnChallenge.used_at.is_(None),
            )
            .with_for_update()
            .first()
        )
        if not entry:
            db.rollback()
            raise HTTPException(status_code=400, detail="Invalid or expired WebAuthn challenge")
        expires_at = entry.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            db.rollback()
            raise HTTPException(status_code=400, detail="WebAuthn challenge has expired")
        if user_id is not None and entry.user_id != user_id:
            db.rollback()
            raise HTTPException(status_code=400, detail="WebAuthn challenge user mismatch")

        entry.used_at = now
        db.commit()
        db.refresh(entry)
        return entry
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise



# -------------------
# List User Passkeys
# -------------------
def list_user_passkeys(db: Session, user_id: str) -> list[PasskeyCredential]:
    """List active passkeys for user."""
    return (
        db.query(PasskeyCredential)
        .filter(PasskeyCredential.user_id == user_id, PasskeyCredential.is_active.is_(True))
        .all()
    )


def _passkey_padding_secret(db: Session) -> bytes:
    """Resolve stable secret material for indistinguishable fake passkey descriptors."""

    try:
        stored_padding_secret = str(get_value_by_page_and_key("secret", "passkey_padding_secret", db) or "").strip()
        if stored_padding_secret:
            return stored_padding_secret.encode("utf-8")
    except Exception:
        logger.exception("Failed to load stored passkey discovery padding secret")

    secret = ""
    if not secret:
        secret = secrets.token_urlsafe(48)
        try:
            update_settings_page(db, "secret", "passkey_padding_secret", secret)
        except Exception:
            logger.warning(
                "Generated an ephemeral passkey discovery padding secret because neither "
                "secret.passkey_padding_secret is configured, and persisting the generated "
                "secret failed. Configure secret.passkey_padding_secret to keep padded passkey "
                "descriptor ordering stable across restarts.",
                exc_info=True,
            )
        else:
            logger.warning(
                "Generated and stored secret.passkey_padding_secret because no existing padding secret was configured."
            )
    return secret.encode("utf-8")


def _fake_passkey_credential_id(secret: bytes, normalized_identifier: str, index: int) -> bytes:
    """Create a stable fake credential id for passkey discovery padding."""

    return hmac.new(
        secret,
        f"passkey-discovery:{normalized_identifier}:{index}".encode("utf-8"),
        hashlib.sha256,
    ).digest()


def _passkey_descriptor_sort_key(secret: bytes, credential_id: bytes) -> bytes:
    """Return a deterministic order key that does not place real descriptors first."""

    return hmac.new(secret, b"passkey-discovery-order:" + credential_id, hashlib.sha256).digest()


def _padded_authentication_descriptors(
    db: Session,
    *,
    normalized_identifier: str,
    credentials: list[PasskeyCredential],
) -> list[PublicKeyCredentialDescriptor]:
    """Build a fixed-size allowCredentials list without exposing account existence."""

    secret = _passkey_padding_secret(db)
    descriptor_ids: list[bytes] = []
    seen_ids: set[bytes] = set()
    for cred in credentials:
        if not isinstance(cred.credential_id, str) or not cred.credential_id:
            continue
        try:
            credential_id = base64url_to_bytes(cred.credential_id)
        except Exception:
            continue
        if credential_id in seen_ids:
            continue
        descriptor_ids.append(credential_id)
        seen_ids.add(credential_id)

    # WebAuthn discovery responses use a fixed 16 descriptor budget; keep real credentials first.
    descriptor_ids = descriptor_ids[:_AUTH_ALLOW_CREDENTIALS_SIZE]

    fake_index = 0
    while len(descriptor_ids) < _AUTH_ALLOW_CREDENTIALS_SIZE:
        fake_id = _fake_passkey_credential_id(secret, normalized_identifier, fake_index)
        fake_index += 1
        if fake_id in seen_ids:
            continue
        descriptor_ids.append(fake_id)
        seen_ids.add(fake_id)

    descriptor_ids.sort(key=lambda credential_id: _passkey_descriptor_sort_key(secret, credential_id))
    return [PublicKeyCredentialDescriptor(id=credential_id) for credential_id in descriptor_ids]



# -------------------
# Begin Registration
# -------------------
def begin_registration(
    db: Session,
    *,
    user_id: str,
    public_origin: str | None = None,
) -> dict[str, Any]:
    """Begin passkey registration."""
    policy = get_passkey_policy(db)
    if not policy.get("enable_passkeys"):
        raise HTTPException(status_code=400, detail="Passkeys are disabled")

    rp_id, rp_name, expected_origin = _resolve_webauthn_config_for_origin(db, public_origin)

    user = get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_locally_managed_account(user)

    existing = list_user_passkeys(db, user_id)
    exclude = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred.credential_id))
        for cred in existing
        if isinstance(cred.credential_id, str) and cred.credential_id
    ]

    challenge_entry = _create_challenge(db, user_id=user_id, flow="registration")

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=str(user.id).encode("utf-8"),
        user_name=str(user.email),
        user_display_name=str(user.email),
        challenge=base64url_to_bytes(challenge_entry.challenge),
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED
        ),
    )

    return {
        "status": "ok",
        "challenge_id": challenge_entry.id,
        "challenge": challenge_entry.challenge,
        "expected_origin": expected_origin,
        "publicKey": json.loads(options_to_json(options)),
    }



# -------------------
# Finish Registration
# -------------------
def finish_registration(
    db: Session,
    *,
    user_id: str,
    credential: dict[str, Any],
    expected_challenge: str,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Finish passkey registration."""
    policy = get_passkey_policy(db)
    if not policy.get("enable_passkeys"):
        raise HTTPException(status_code=400, detail="Passkeys are disabled")

    _consume_challenge(db, challenge_b64=expected_challenge, flow="registration", user_id=user_id)

    normalized_credential = _normalize_webauthn_credential_payload(credential, registration=True)
    client_origin = _extract_client_origin_from_credential(normalized_credential)
    rp_id, _, expected_origin = _resolve_webauthn_config_for_origin(db, client_origin)

    def _verify_with_origin(origin: str):
        return verify_registration_response(
            credential=normalized_credential,
            expected_challenge=base64url_to_bytes(expected_challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )

    try:
        verification = _verify_with_origin(expected_origin)
    except HTTPException:
        raise
    except Exception as error:
        recovered = False
        if (
            isinstance(client_origin, str)
            and _can_use_localhost_origin_fallback(expected_origin, client_origin, rp_id)
        ):
            try:
                verification = _verify_with_origin(client_origin)
                recovered = True
                _record_passkey_origin_notification(
                    message=(
                        "Passkey registration recovered from localhost origin mismatch. "
                        "Please align the Public URL setting with the URL users open in browser."
                    ),
                    expected_origin=expected_origin,
                    client_origin=client_origin,
                    rp_id=rp_id,
                    user_id=user_id,
                    recovered=True,
                )
            except Exception:
                recovered = False

        if (not recovered) and isinstance(client_origin, str) and client_origin and client_origin != expected_origin:
            _record_passkey_origin_notification(
                message="Passkey registration failed due to WebAuthn origin mismatch.",
                expected_origin=expected_origin,
                client_origin=client_origin,
                rp_id=rp_id,
                user_id=user_id,
                recovered=False,
            )
            raise HTTPException(
                status_code=400,
                detail=_origin_mismatch_detail(expected_origin, client_origin),
            ) from error
        if not recovered:
            logger.exception("Passkey registration verification failed")
            raise HTTPException(status_code=400, detail="WebAuthn registration verification failed") from error

    cred_id_b64 = bytes_to_base64url(verification.credential_id)
    public_key_b64 = bytes_to_base64url(verification.credential_public_key)

    now = datetime.now(timezone.utc)
    device_name = _derive_passkey_device_name(user_agent)
    existing = (
        db.query(PasskeyCredential)
        .filter(PasskeyCredential.credential_id == cred_id_b64)
        .first()
    )
    if existing:
        if existing.is_active or existing.user_id != user_id:
            raise HTTPException(status_code=409, detail="Passkey already exists")

        existing.public_key = public_key_b64
        existing.sign_count = str(verification.sign_count or 0)
        existing.transports = None
        existing.name = device_name
        existing.created_at = now
        existing.last_used_at = None
        existing.is_active = True
        try:
            from app.email.service import enqueue_security_event
            from app.users.models import get_user

            enqueue_security_event(
                db,
                user=get_user(db, user_id),
                event_type="passkey_added",
                source_id=f"{existing.id}:{now.isoformat()}",
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Passkey already exists") from exc
        except Exception:
            db.rollback()
            raise
        db.refresh(existing)
        return {"status": "success", "credential_id": existing.credential_id}

    row = PasskeyCredential(
        user_id=user_id,
        credential_id=cred_id_b64,
        public_key=public_key_b64,
        sign_count=str(verification.sign_count or 0),
        transports=None,
        name=device_name,
        created_at=now,
        last_used_at=None,
        is_active=True,
    )
    db.add(row)
    try:
        db.flush()
        from app.email.service import enqueue_security_event
        from app.users.models import get_user

        enqueue_security_event(
            db,
            user=get_user(db, user_id),
            event_type="passkey_added",
            source_id=row.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Passkey already exists") from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(row)

    return {"status": "success", "credential_id": row.credential_id}



# -------------------
# Begin Authentication
# -------------------
def begin_authentication(
    db: Session,
    *,
    identifier: str,
    public_origin: str | None = None,
) -> dict[str, Any]:
    """Begin passkey authentication."""
    policy = get_passkey_policy(db)
    if not policy.get("enable_passkeys"):
        raise HTTPException(status_code=400, detail="Passkeys are disabled")

    normalized = (identifier or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Identifier is required")

    user = None
    try:
        user = get_user(db, email=normalized.lower())
    except HTTPException as exc:
        if exc.status_code != 404:
            raise

    creds = (
        list_user_passkeys(db, user.id)
        if user and not is_externally_managed(user)
        else []
    )

    allow = _padded_authentication_descriptors(
        db,
        normalized_identifier=normalized.lower(),
        credentials=creds,
    )

    challenge_entry = _create_challenge(db, user_id=(user.id if user else None), flow="authentication")
    rp_id, _, expected_origin = _resolve_webauthn_config_for_origin(db, public_origin)

    options = generate_authentication_options(
        rp_id=rp_id,
        challenge=base64url_to_bytes(challenge_entry.challenge),
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )

    return {
        "status": "ok",
        "challenge": challenge_entry.challenge,
        "expected_origin": expected_origin,
        "publicKey": json.loads(options_to_json(options)),
    }



# -------------------
# Finish Authentication
# -------------------
def finish_authentication(
    db: Session,
    *,
    credential: dict[str, Any],
    expected_challenge: str,
) -> dict[str, Any]:
    """Finish passkey authentication."""
    policy = get_passkey_policy(db)
    if not policy.get("enable_passkeys"):
        raise HTTPException(status_code=400, detail="Passkeys are disabled")

    normalized_credential = _normalize_webauthn_credential_payload(credential, registration=False)
    client_origin = _extract_client_origin_from_credential(normalized_credential)
    rp_id, _, expected_origin = _resolve_webauthn_config_for_origin(db, client_origin)

    raw_id = normalized_credential.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        raise HTTPException(status_code=400, detail="Missing credential id")

    stored = (
        db.query(PasskeyCredential)
        .filter(PasskeyCredential.credential_id == raw_id, PasskeyCredential.is_active.is_(True))
        .first()
    )
    if not stored:
        raise HTTPException(status_code=400, detail="WebAuthn authentication failed")

    stored_user = get_user(db, stored.user_id)
    if is_externally_managed(stored_user):
        raise HTTPException(status_code=400, detail="WebAuthn authentication failed")

    _consume_challenge(db, challenge_b64=expected_challenge, flow="authentication", user_id=stored.user_id)

    try:
        verification = verify_authentication_response(
            credential=normalized_credential,
            expected_challenge=base64url_to_bytes(expected_challenge),
            expected_rp_id=rp_id,
            expected_origin=expected_origin,
            credential_public_key=base64url_to_bytes(stored.public_key),
            credential_current_sign_count=int(stored.sign_count or "0"),
            require_user_verification=True,
        )
    except HTTPException:
        raise
    except Exception as error:
        recovered = False
        if (
            isinstance(client_origin, str)
            and _can_use_localhost_origin_fallback(expected_origin, client_origin, rp_id)
        ):
            try:
                verification = verify_authentication_response(
                    credential=normalized_credential,
                    expected_challenge=base64url_to_bytes(expected_challenge),
                    expected_rp_id=rp_id,
                    expected_origin=client_origin,
                    credential_public_key=base64url_to_bytes(stored.public_key),
                    credential_current_sign_count=int(stored.sign_count or "0"),
                    require_user_verification=True,
                )
                recovered = True
                _record_passkey_origin_notification(
                    message=(
                        "Passkey authentication recovered from localhost origin mismatch. "
                        "Please align general.public_url with the URL users open in browser."
                    ),
                    expected_origin=expected_origin,
                    client_origin=client_origin,
                    rp_id=rp_id,
                    user_id=stored.user_id,
                    recovered=True,
                )
            except Exception:
                recovered = False

        if (not recovered) and isinstance(client_origin, str) and client_origin and client_origin != expected_origin:
            _record_passkey_origin_notification(
                message="Passkey authentication failed due to WebAuthn origin mismatch.",
                expected_origin=expected_origin,
                client_origin=client_origin,
                rp_id=rp_id,
                user_id=stored.user_id,
                recovered=False,
            )
            raise HTTPException(
                status_code=400,
                detail=_origin_mismatch_detail(expected_origin, client_origin),
            ) from error
        if not recovered:
            logger.exception("Passkey authentication verification failed")
            raise HTTPException(status_code=400, detail="WebAuthn authentication verification failed") from error

    # Update sign count and last-used timestamp
    stored.sign_count = str(verification.new_sign_count)
    stored.last_used_at = datetime.now(timezone.utc)
    db.commit()

    return {"status": "success", "user_id": stored.user_id, "credential_id": stored.credential_id}
