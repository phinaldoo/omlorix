from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import logging
import os

from sqlalchemy import text

from app.auth.models import (
    AuthenticationSigningKeyState,
    delete_authentication_all,
)
from app.auth.session_store import revoke_all_sessions


logger = logging.getLogger(__name__)
_STATE_ROW_ID = 1
_POSTGRES_ADVISORY_LOCK_ID = 1_163_053_763
JWT_SECRET_MIN_BYTES = 64


def _validated_jwt_secret_from_environment() -> str:
    """Return the operator-managed signing key or fail startup clearly."""
    secret = str(os.getenv("JWT_SECRET_KEY") or "").strip()
    if not secret:
        raise RuntimeError(
            "JWT_SECRET_KEY is required and must be provided through the server environment."
        )
    if len(secret.encode("utf-8")) < JWT_SECRET_MIN_BYTES:
        raise RuntimeError("JWT_SECRET_KEY must contain at least 64 bytes.")
    return secret


@lru_cache(maxsize=1)
def get_jwt_material() -> tuple[str, str]:
    """Return process-local JWT material sourced only from the environment.

    Environment variables are immutable for the lifetime of a deployed server
    process, so validating once avoids a database read on every token operation.
    """
    return _validated_jwt_secret_from_environment(), "HS512"


def _lock_signing_key_state(db) -> None:
    """Serialize fingerprint reconciliation across PostgreSQL replicas."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _POSTGRES_ADVISORY_LOCK_ID},
        )


def reconcile_jwt_signing_key(db) -> bool:
    """Persist the current key fingerprint and revoke sessions after rotation.

    The fingerprint update and authentication-row deletion share one database
    transaction. Shared session caches are cleared only after that transaction
    commits successfully. The first adoption also revokes sessions because no
    trusted fingerprint exists for authentication rows created beforehand.
    """
    secret, _algorithm = get_jwt_material()
    fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    changed = False

    try:
        with db.begin():
            _lock_signing_key_state(db)
            state = (
                db.query(AuthenticationSigningKeyState)
                .filter(AuthenticationSigningKeyState.id == _STATE_ROW_ID)
                .with_for_update()
                .first()
            )
            if state is not None and state.fingerprint == fingerprint:
                return False

            changed = True
            now = datetime.now(timezone.utc)
            if state is None:
                db.add(
                    AuthenticationSigningKeyState(
                        id=_STATE_ROW_ID,
                        fingerprint=fingerprint,
                        updated_at=now,
                    )
                )
            else:
                state.fingerprint = fingerprint
                state.updated_at = now

            delete_authentication_all(db, commit=False)
    except Exception:
        db.rollback()
        raise

    if changed:
        revoke_all_sessions()
        logger.info("JWT signing-key fingerprint changed; all sessions were revoked")
    return changed
