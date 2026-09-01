"""Secure native-app handoff for social and enterprise authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.models import NativeAuthGrant


NATIVE_CALLBACK_ORIGIN_ENV = "OMLORIX_NATIVE_CALLBACK_ORIGIN"
NATIVE_CALLBACK_PATH_PREFIX = "/auth"
NATIVE_START_TTL_SECONDS = 10 * 60
NATIVE_EXCHANGE_TTL_SECONDS = 5 * 60
NATIVE_CONSUMED_RETENTION_SECONDS = 60

_PKCE_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


def get_native_callback_origin() -> str:
    """Return the explicitly configured app-associated HTTPS origin."""
    raw_origin = str(os.getenv(NATIVE_CALLBACK_ORIGIN_ENV) or "").strip()
    try:
        parsed = urlsplit(raw_origin)
        _ = parsed.port
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{NATIVE_CALLBACK_ORIGIN_ENV} must be configured as a valid HTTPS origin.",
        ) from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(
            status_code=503,
            detail=f"{NATIVE_CALLBACK_ORIGIN_ENV} must be configured as a valid HTTPS origin.",
        )
    return f"https://{parsed.netloc}"


@dataclass(frozen=True)
class NativeAuthGrantData:
    purpose: str
    provider: str
    user_id: str | None
    authentication_id: str | None
    code_challenge: str
    state_hash: str
    account_mode: str
    replace_slot: int | None
    accepts_terms_of_service: bool
    terms_of_service_revision: int | None
    identity_claims: dict[str, str] | None
    twofa_satisfied: bool


def validate_native_code_challenge(value: str) -> str:
    normalized = str(value or "").strip()
    if not _PKCE_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="A valid S256 PKCE code challenge is required.")
    return normalized


def validate_native_state(value: str) -> str:
    normalized = str(value or "").strip()
    if not _STATE_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="A valid native authentication state is required.")
    return normalized


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_account_mode(value: str) -> str:
    return "add" if str(value or "").strip().lower() == "add" else "primary"


def _normalize_identity_claims(value: dict | None) -> dict[str, str] | None:
    """Keep only the provider identity fields needed for deferred linking."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="Native identity claims are invalid.")
    limits = {
        "sub": 2048,
        "email": 320,
        "workspace_id": 255,
        "tenant_id": 255,
    }
    claims = {
        key: str(value.get(key) or "").strip()[:limit]
        for key, limit in limits.items()
        if str(value.get(key) or "").strip()
    }
    if not claims.get("sub") or not claims.get("email"):
        raise HTTPException(status_code=400, detail="Native identity claims are incomplete.")
    return claims


def prune_native_auth_grants(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    """Delete expired and old consumed grants in the caller's transaction."""
    cutoff = now or datetime.now(timezone.utc)
    expired = int(
        db.query(NativeAuthGrant)
        .filter(NativeAuthGrant.expires_at <= cutoff)
        .delete(synchronize_session=False)
    )
    consumed = int(
        db.query(NativeAuthGrant)
        .filter(
            NativeAuthGrant.consumed_at.is_not(None),
            NativeAuthGrant.consumed_at
            <= cutoff - timedelta(seconds=NATIVE_CONSUMED_RETENTION_SECONDS),
        )
        .delete(synchronize_session=False)
    )
    return expired + consumed


def create_native_auth_grant(
    db: Session,
    *,
    purpose: str,
    provider: str,
    code_challenge: str,
    state: str,
    user_id: str | None = None,
    authentication_id: str | None = None,
    account_mode: str = "primary",
    replace_slot: int | None = None,
    accepts_terms_of_service: bool = False,
    terms_of_service_revision: int | None = None,
    identity_claims: dict | None = None,
    twofa_satisfied: bool = False,
    ttl_seconds: int = NATIVE_START_TTL_SECONDS,
) -> str:
    """Persist a hashed one-time grant and return its unguessable bearer value."""
    normalized_challenge = validate_native_code_challenge(code_challenge)
    normalized_state = validate_native_state(state)
    token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    prune_native_auth_grants(db, now=now)
    record = NativeAuthGrant(
        token_hash=_token_hash(token),
        purpose=str(purpose or "").strip().lower(),
        provider=str(provider or "").strip().lower(),
        user_id=str(user_id).strip() if user_id else None,
        authentication_id=str(authentication_id).strip() if authentication_id else None,
        code_challenge=normalized_challenge,
        state_hash=_token_hash(normalized_state),
        account_mode=_normalize_account_mode(account_mode),
        replace_slot=replace_slot if isinstance(replace_slot, int) and 1 <= replace_slot <= 5 else None,
        accepts_terms_of_service=bool(accepts_terms_of_service),
        terms_of_service_revision=terms_of_service_revision,
        identity_claims=_normalize_identity_claims(identity_claims),
        twofa_satisfied=bool(twofa_satisfied),
        created_at=now,
        expires_at=now + timedelta(seconds=max(1, int(ttl_seconds))),
        consumed_at=None,
    )
    db.add(record)
    db.commit()
    return token


def consume_native_auth_grant(
    db: Session,
    token: str,
    *,
    expected_purposes: set[str],
    state: str | None = None,
    code_verifier: str | None = None,
) -> NativeAuthGrantData:
    """Atomically consume a grant after purpose, state, and optional PKCE checks."""
    normalized_token = str(token or "").strip()
    if not normalized_token:
        raise HTTPException(status_code=400, detail="Native authentication grant is missing.")

    record = (
        db.query(NativeAuthGrant)
        .filter(NativeAuthGrant.token_hash == _token_hash(normalized_token))
        .with_for_update()
        .first()
    )
    now = datetime.now(timezone.utc)
    if record is None or record.consumed_at is not None:
        raise HTTPException(status_code=400, detail="Native authentication grant is invalid or already used.")

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        record.consumed_at = now
        db.commit()
        raise HTTPException(status_code=400, detail="Native authentication grant has expired.")

    normalized_purposes = {str(value).strip().lower() for value in expected_purposes}
    if record.purpose not in normalized_purposes:
        record.consumed_at = now
        db.commit()
        raise HTTPException(status_code=400, detail="Native authentication grant purpose mismatch.")

    if state is not None:
        normalized_state = validate_native_state(state)
        if not hmac.compare_digest(record.state_hash, _token_hash(normalized_state)):
            record.consumed_at = now
            db.commit()
            raise HTTPException(status_code=400, detail="Native authentication state mismatch.")

    if code_verifier is not None:
        normalized_verifier = str(code_verifier or "").strip()
        if not _PKCE_RE.fullmatch(normalized_verifier):
            record.consumed_at = now
            db.commit()
            raise HTTPException(status_code=400, detail="A valid PKCE code verifier is required.")
        computed = base64.urlsafe_b64encode(
            hashlib.sha256(normalized_verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(record.code_challenge, computed):
            record.consumed_at = now
            db.commit()
            raise HTTPException(status_code=400, detail="PKCE verification failed.")

    result = NativeAuthGrantData(
        purpose=record.purpose,
        provider=record.provider,
        user_id=record.user_id,
        authentication_id=record.authentication_id,
        code_challenge=record.code_challenge,
        state_hash=record.state_hash,
        account_mode=record.account_mode,
        replace_slot=record.replace_slot,
        accepts_terms_of_service=bool(record.accepts_terms_of_service),
        terms_of_service_revision=record.terms_of_service_revision,
        identity_claims=(
            {str(key): str(value) for key, value in record.identity_claims.items()}
            if isinstance(record.identity_claims, dict)
            else None
        ),
        twofa_satisfied=bool(record.twofa_satisfied),
    )
    record.consumed_at = now
    db.commit()
    return result


def native_callback_url(
    *,
    path: str,
    state: str,
    code: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    reason: str | None = None,
) -> str:
    """Build a configured HTTPS callback with bounded handoff fields.

    Authentication values live in the fragment so a browser fallback never
    sends the one-time code to the callback web server, reverse proxies, or
    access logs.
    """
    parameters: dict[str, str] = {"state": validate_native_state(state)}
    if code:
        parameters["code"] = str(code)
    if provider:
        parameters["provider"] = str(provider).strip().lower()[:64]
    if status:
        parameters["status"] = str(status).strip().lower()[:32]
    if reason:
        parameters["reason"] = str(reason).strip().lower()[:64]
    normalized_path = str(path or "federated").strip().strip("/") or "federated"
    if normalized_path not in {"federated", "link"}:
        raise HTTPException(status_code=400, detail="Native authentication callback path is invalid.")
    return (
        f"{get_native_callback_origin()}{NATIVE_CALLBACK_PATH_PREFIX}/{normalized_path}"
        f"#{urlencode(parameters)}"
    )


def create_native_exchange_callback(
    db: Session,
    *,
    kind: str,
    provider: str,
    user_id: str,
    flow_context: dict,
    twofa_satisfied: bool = False,
) -> str:
    """Create the PKCE exchange code returned after successful browser auth."""
    normalized_kind = "sso" if kind == "sso" else "social"
    state = validate_native_state(str(flow_context.get("native_state") or ""))
    code = create_native_auth_grant(
        db,
        purpose=f"{normalized_kind}_exchange",
        provider=provider,
        user_id=user_id,
        code_challenge=str(flow_context.get("native_code_challenge") or ""),
        state=state,
        account_mode=str(flow_context.get("account_mode") or "primary"),
        replace_slot=flow_context.get("replace_slot"),
        twofa_satisfied=twofa_satisfied,
        ttl_seconds=NATIVE_EXCHANGE_TTL_SECONDS,
    )
    return native_callback_url(
        path="federated",
        state=state,
        code=code,
        provider=provider,
        status=normalized_kind,
    )


def is_native_flow(flow_context: dict | None, *, kind: str | None = None) -> bool:
    if not isinstance(flow_context, dict) or flow_context.get("native_auth") is not True:
        return False
    if kind is None:
        return True
    return str(flow_context.get("native_kind") or "").strip().lower() == kind


def normalize_native_failure_reason(value: str | None) -> str:
    """Collapse server/browser failures to a small, non-sensitive protocol set."""
    key = str(value or "").strip().lower()
    if key in {"cancelled", "unavailable", "invalid_flow", "not_eligible", "failed"}:
        return key
    if key in {"access_denied", "cancelled", "canceled", "user_cancelled"}:
        return "cancelled"
    if key in {"provider_disabled", "provider_unavailable", "unavailable"}:
        return "unavailable"
    if key in {
        "invalid_flow",
        "social_state_missing",
        "social_state_invalid",
        "sso_state_missing",
        "sso_state_invalid",
        "sso_security_missing",
    }:
        return "invalid_flow"
    if key in {
        "account_deleted",
        "account_inactive",
        "account_locked",
        "account_pending",
        "domain_not_allowed",
        "email_not_verified",
        "no_email",
        "provider_subject_missing",
        "provider_subject_mismatch",
        "signup_not_allowed",
        "social_account_conflict",
        "social_account_not_linked",
        "workspace_not_allowed",
    }:
        return "not_eligible"
    return "failed"


def create_native_failure_callback(
    *,
    kind: str,
    provider: str,
    flow_context: dict,
    reason: str | None,
) -> str:
    """Return a correlated app callback without exposing internal error detail."""
    normalized_kind = "sso" if str(kind).strip().lower() == "sso" else "social"
    if not is_native_flow(flow_context, kind=normalized_kind):
        raise HTTPException(status_code=400, detail="Native authentication flow mismatch.")
    return native_callback_url(
        path="federated",
        state=str(flow_context.get("native_state") or ""),
        provider=provider,
        status=normalized_kind,
        reason=normalize_native_failure_reason(reason),
    )
