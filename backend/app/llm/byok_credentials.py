"""Issue and resolve sealed, expiring BYOK credential tokens.

The browser needs a value that survives a same-tab reload, but persisting the
raw third-party API key in Web Storage exposes that key to every script running
on the Omlorix origin.  These helpers keep the browser value opaque: the API key
is authenticated and encrypted with the server's existing Fernet key, and the
result is bound to one Omlorix user, provider type, and local provider instance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import secrets
from typing import Any

from cryptography.fernet import InvalidToken

from app.llm.schemas import normalize_provider_value
from app.utils.encryption import get_cipher_suite


BYOK_CREDENTIAL_TOKEN_VERSION = 1
BYOK_CREDENTIAL_TOKEN_TTL_DAYS = 30
BYOK_CREDENTIAL_TOKEN_TTL_SECONDS = BYOK_CREDENTIAL_TOKEN_TTL_DAYS * 24 * 60 * 60
BYOK_CREDENTIAL_TOKEN_MAX_LENGTH = 32768
BYOK_API_KEY_MAX_LENGTH = 16384


class ByokCredentialTokenError(ValueError):
    """Raised when a sealed credential cannot be safely issued or resolved."""


def _utc_now() -> datetime:
    """Return an aware UTC timestamp from one testable boundary."""

    return datetime.now(timezone.utc)


def _normalize_required(value: Any, field_name: str, *, max_length: int) -> str:
    """Normalize a required token claim without ever including its value in errors."""

    normalized = str(value or "").strip()
    if not normalized or len(normalized) > max_length:
        raise ByokCredentialTokenError(f"Invalid BYOK credential {field_name}.")
    return normalized


def issue_byok_credential_token(
    *,
    user_id: str,
    provider: str,
    provider_id: str,
    api_key: str,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Seal an API key for one user and local provider instance.

    The returned Fernet token is safe to persist in tab-scoped Web Storage
    because only the backend owns the encryption key.  It is still treated as
    sensitive and must never be logged or returned by any endpoint other than
    the issuance response.
    """

    normalized_user_id = _normalize_required(user_id, "user", max_length=255)
    normalized_provider = _normalize_required(
        normalize_provider_value(provider),
        "provider",
        max_length=120,
    )
    normalized_provider_id = _normalize_required(provider_id, "provider instance", max_length=255)
    normalized_api_key = _normalize_required(api_key, "API key", max_length=BYOK_API_KEY_MAX_LENGTH)
    issued_at = now or _utc_now()
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    expires_at = issued_at + timedelta(seconds=BYOK_CREDENTIAL_TOKEN_TTL_SECONDS)

    payload = {
        "v": BYOK_CREDENTIAL_TOKEN_VERSION,
        "sub": normalized_user_id,
        "provider": normalized_provider,
        "provider_id": normalized_provider_id,
        "api_key": normalized_api_key,
        "exp": int(expires_at.timestamp()),
    }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    token = get_cipher_suite().encrypt(serialized).decode("ascii")
    return token, expires_at


def resolve_byok_credential_token(
    token: str,
    *,
    user_id: str,
    provider: str,
    provider_id: str,
    now: datetime | None = None,
) -> str:
    """Resolve a sealed credential after validating every binding and its TTL.

    All malformed, tampered, expired, or mismatched tokens intentionally share
    one exception type.  This avoids turning the resolver into an oracle and
    keeps user-facing handling independent from cryptographic failure details.
    """

    normalized_token = str(token or "").strip()
    if not normalized_token or len(normalized_token) > BYOK_CREDENTIAL_TOKEN_MAX_LENGTH:
        raise ByokCredentialTokenError("BYOK credential is unavailable.")

    try:
        decrypted = get_cipher_suite().decrypt(normalized_token.encode("ascii"))
        payload = json.loads(decrypted.decode("utf-8"))
    except (InvalidToken, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ByokCredentialTokenError("BYOK credential is unavailable.") from exc

    if not isinstance(payload, dict) or payload.get("v") != BYOK_CREDENTIAL_TOKEN_VERSION:
        raise ByokCredentialTokenError("BYOK credential is unavailable.")

    expected_user = _normalize_required(user_id, "user", max_length=255)
    expected_provider = _normalize_required(
        normalize_provider_value(provider),
        "provider",
        max_length=120,
    )
    expected_provider_id = _normalize_required(provider_id, "provider instance", max_length=255)

    # Constant-time comparisons avoid leaking which user/provider binding was
    # wrong if an attacker can repeatedly submit a captured token.
    binding_comparisons = (
        secrets.compare_digest(
            str(payload.get("sub") or "").encode("utf-8"),
            expected_user.encode("utf-8"),
        ),
        secrets.compare_digest(
            str(payload.get("provider") or "").encode("utf-8"),
            expected_provider.encode("utf-8"),
        ),
        secrets.compare_digest(
            str(payload.get("provider_id") or "").encode("utf-8"),
            expected_provider_id.encode("utf-8"),
        ),
    )
    if not all(binding_comparisons):
        raise ByokCredentialTokenError("BYOK credential is unavailable.")

    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    try:
        expires_at = int(payload.get("exp"))
    except (TypeError, ValueError) as exc:
        raise ByokCredentialTokenError("BYOK credential is unavailable.") from exc
    if expires_at <= int(current_time.timestamp()):
        raise ByokCredentialTokenError("BYOK credential is unavailable.")

    return _normalize_required(payload.get("api_key"), "API key", max_length=BYOK_API_KEY_MAX_LENGTH)
