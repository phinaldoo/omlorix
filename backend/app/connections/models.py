from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import logging
import uuid

from fastapi import HTTPException
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm.attributes import flag_modified

from app.database import Base
from app.utils.sqlalchemy_encryption import EncryptedJSON


PROVIDER_NOTION = "notion"
PROVIDER_GITHUB = "github"
PROVIDER_GMAIL = "gmail"
PROVIDER_GOOGLE_CALENDAR = "google_calendar"
PROVIDER_GOOGLE_DRIVE = "google_drive"
PROVIDER_SLACK = "slack"
VALID_CONNECTION_PROVIDERS = {
    PROVIDER_NOTION,
    PROVIDER_GITHUB,
    PROVIDER_GMAIL,
    PROVIDER_GOOGLE_CALENDAR,
    PROVIDER_GOOGLE_DRIVE,
    PROVIDER_SLACK,
}
_OAUTH_AUDIT_STATE_MAX_LENGTH = 512
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_utc_datetime(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _mask(value: str | None, *, keep: int = 6) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    if len(text) <= keep:
        return text
    return f"{text[:keep]}..."


class UserConnection(Base):
    __tablename__ = "user_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_connections_user_provider"),
        CheckConstraint(
            "auth_mode IN ('oauth', 'pat')",
            name="ck_user_connections_auth_mode",
        ),
        Index("ix_user_connections_user_id", "user_id"),
        Index("ix_user_connections_provider", "provider"),
        Index("ix_user_connections_mcp_server_id", "mcp_server_id"),
    )

    id = Column(String, primary_key=True, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    # Authentication is the only connection-specific runtime choice. Server
    # identity, namespace, and timeout belong to the global provider catalog.
    auth_mode = Column(String, nullable=False, default="oauth")
    secrets = Column(EncryptedJSON, nullable=True)
    status = Column(JSON, nullable=False, default=dict)
    mcp_server_id = Column(String, nullable=True)
    connected_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow)


class ConnectionOAuthState(Base):
    __tablename__ = "connection_oauth_states"
    __table_args__ = (
        Index("ix_connection_oauth_states_provider", "provider"),
        Index("ix_connection_oauth_states_user_id", "user_id"),
    )

    state = Column(String, primary_key=True, unique=True, nullable=False)
    provider = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    return_path = Column(String, nullable=False)
    redirect_uri = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    secrets = Column(EncryptedJSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)


def _normalize_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    if value not in VALID_CONNECTION_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported connection provider.")
    return value


def _normalize_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _normalize_auth_mode(value, *, default: str = "oauth") -> str:
    """Return a supported per-user authentication mode."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"oauth", "pat"} else default


def _normalize_secrets(value) -> dict:
    payload = value if isinstance(value, dict) else {}
    result: dict[str, str | int | None] = {}
    for key in (
        "access_token",
        "refresh_token",
        "code_verifier",
        "client_id",
        "client_secret",
        "token_endpoint",
        "authorization_endpoint",
        "registration_endpoint",
        "issuer",
        "account_name",
        "account_email",
    ):
        raw = payload.get(key)
        if raw is None:
            result[key] = None
            continue
        result[key] = str(raw).strip() or None
    expires_at = payload.get("expires_at")
    try:
        result["expires_at"] = int(expires_at) if expires_at is not None else None
    except (TypeError, ValueError):
        result["expires_at"] = None
    scopes = payload.get("scopes")
    result["scopes"] = _normalize_list(scopes)
    return result


def _normalize_status(value) -> dict:
    payload = value if isinstance(value, dict) else {}
    tool_count = payload.get("tool_count")
    try:
        tool_count_value = max(int(tool_count or 0), 0)
    except (TypeError, ValueError):
        tool_count_value = 0
    state = str(payload.get("state") or "not_connected").strip().lower()
    if state not in {"not_connected", "connected", "error", "reauthorization_required"}:
        state = "not_connected"
    return {
        "state": state,
        "last_error": str(payload.get("last_error") or "").strip(),
        "last_error_code": str(payload.get("last_error_code") or "").strip()[:100],
        "tool_count": tool_count_value,
        "tool_names": _normalize_list(payload.get("tool_names")),
        "checked_at": payload.get("checked_at"),
        "connected_at": payload.get("connected_at"),
        "last_sync_at": payload.get("last_sync_at"),
    }


def create_user_connection(
    db,
    *,
    user_id: str,
    provider: str,
    enabled: bool = True,
    auth_mode: str = "oauth",
    secrets: dict | None = None,
    status: dict | None = None,
    mcp_server_id: str | None = None,
    connected_at: datetime | None = None,
    before_commit: Callable[[UserConnection], None] | None = None,
):
    normalized_user_id = str(user_id).strip()
    normalized_provider = _normalize_provider(provider)
    normalized_auth_mode = _normalize_auth_mode(auth_mode)
    normalized_secrets = _normalize_secrets(secrets)
    normalized_status = _normalize_status(status)
    logger.info(
        "connections.model.create.begin user=%s provider=%s auth_mode=%s has_access=%s has_refresh=%s connected_at=%s",
        _mask(normalized_user_id),
        normalized_provider,
        normalized_auth_mode,
        bool(normalized_secrets.get("access_token")),
        bool(normalized_secrets.get("refresh_token")),
        connected_at.isoformat() if isinstance(connected_at, datetime) else None,
    )
    connection = UserConnection(
        user_id=normalized_user_id,
        provider=normalized_provider,
        enabled=bool(enabled),
        auth_mode=normalized_auth_mode,
        secrets=normalized_secrets,
        status=normalized_status,
        mcp_server_id=str(mcp_server_id or "").strip() or None,
        connected_at=connected_at,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    try:
        db.add(connection)
        db.flush()
        if before_commit is not None:
            before_commit(connection)
        db.commit()
        db.refresh(connection)
    except Exception as exc:
        db.rollback()
        logger.exception(
            "connections.model.create.failed user=%s provider=%s error=%s",
            _mask(normalized_user_id),
            normalized_provider,
            exc,
        )
        raise
    logger.info(
        "connections.model.create.success user=%s provider=%s connection=%s",
        _mask(normalized_user_id),
        normalized_provider,
        _mask(connection.id),
    )
    return connection


def list_user_connections(db, user_id: str) -> list[UserConnection]:
    records = (
        db.query(UserConnection)
        .filter(UserConnection.user_id == str(user_id).strip())
        .order_by(UserConnection.provider.asc(), UserConnection.created_at.asc())
        .all()
    )
    logger.info("connections.model.list user=%s count=%s", _mask(user_id), len(records))
    return records


def get_user_connection(db, user_id: str, connection_id: str) -> UserConnection:
    connection = (
        db.query(UserConnection)
        .filter(
            UserConnection.id == str(connection_id or "").strip(),
            UserConnection.user_id == str(user_id or "").strip(),
        )
        .first()
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found.")
    return connection


def get_user_connection_by_provider(db, user_id: str, provider: str) -> UserConnection | None:
    return (
        db.query(UserConnection)
        .filter(
            UserConnection.user_id == str(user_id or "").strip(),
            UserConnection.provider == _normalize_provider(provider),
        )
        .first()
    )


def update_user_connection(
    db,
    connection_id: str,
    *,
    before_commit: Callable[[UserConnection], None] | None = None,
    **updates,
) -> UserConnection:
    connection = db.query(UserConnection).filter(UserConnection.id == str(connection_id or "").strip()).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found.")
    logger.info(
        "connections.model.update.begin connection=%s user=%s fields=%s",
        _mask(connection.id),
        _mask(connection.user_id),
        sorted(updates.keys()),
    )
    if "enabled" in updates:
        connection.enabled = bool(updates.get("enabled"))
    if "auth_mode" in updates:
        connection.auth_mode = _normalize_auth_mode(
            updates.get("auth_mode"),
            default=connection.auth_mode or "oauth",
        )
    if "secrets" in updates:
        connection.secrets = _normalize_secrets(updates.get("secrets"))
    if "status" in updates:
        connection.status = _normalize_status(updates.get("status"))
        flag_modified(connection, "status")
    if "mcp_server_id" in updates:
        connection.mcp_server_id = str(updates.get("mcp_server_id") or "").strip() or None
    if "connected_at" in updates:
        connection.connected_at = updates.get("connected_at")
    connection.updated_at = _utcnow()
    try:
        if before_commit is not None:
            before_commit(connection)
        db.commit()
        db.refresh(connection)
    except Exception as exc:
        db.rollback()
        logger.exception(
            "connections.model.update.failed connection=%s user=%s error=%s",
            _mask(connection.id),
            _mask(connection.user_id),
            exc,
        )
        raise
    logger.info(
        "connections.model.update.success connection=%s user=%s enabled=%s has_access=%s server=%s",
        _mask(connection.id),
        _mask(connection.user_id),
        connection.enabled,
        bool(_normalize_secrets(connection.secrets).get("access_token")),
        _mask(connection.mcp_server_id),
    )
    return connection


def delete_user_connection(db, connection_id: str) -> None:
    connection = db.query(UserConnection).filter(UserConnection.id == str(connection_id or "").strip()).first()
    if connection:
        db.delete(connection)
        db.commit()


def serialize_user_connection(connection: UserConnection) -> dict:
    secrets = _normalize_secrets(connection.secrets)
    status = _normalize_status(connection.status)
    access_token = str(secrets.get("access_token") or "").strip()
    connected_at = connection.connected_at.isoformat() if connection.connected_at else None
    if not status.get("connected_at") and connected_at:
        status["connected_at"] = connected_at
    return {
        "id": connection.id,
        "provider": connection.provider,
        "enabled": bool(connection.enabled),
        "connected": bool(access_token),
        "mcp_server_id": connection.mcp_server_id,
        "auth_mode": _normalize_auth_mode(connection.auth_mode),
        "status": deepcopy(status),
        "created_at": connection.created_at.isoformat() if connection.created_at else None,
        "updated_at": connection.updated_at.isoformat() if connection.updated_at else None,
        "connected_at": connected_at,
    }


def save_connection_oauth_state(
    db,
    *,
    state: str,
    provider: str,
    user_id: str,
    return_path: str,
    redirect_uri: str,
    payload: dict | None = None,
    secrets: dict | None = None,
    expires_at: datetime,
):
    normalized_state = str(state).strip()
    normalized_provider = _normalize_provider(provider)
    normalized_user_id = str(user_id).strip()
    normalized_redirect_uri = str(redirect_uri or "").strip()
    normalized_secrets = _normalize_secrets(secrets)
    logger.info(
        "connections.oauth_state.save.begin state=%s user=%s provider=%s redirect_uri=%s has_verifier=%s client_id=%s expires_at=%s",
        _mask(normalized_state),
        _mask(normalized_user_id),
        normalized_provider,
        normalized_redirect_uri,
        bool(normalized_secrets.get("code_verifier")),
        _mask(normalized_secrets.get("client_id")),
        expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at,
    )
    record = ConnectionOAuthState(
        state=normalized_state,
        provider=normalized_provider,
        user_id=normalized_user_id,
        return_path=str(return_path or "/workspace/connections").strip() or "/workspace/connections",
        redirect_uri=normalized_redirect_uri,
        payload=payload if isinstance(payload, dict) else {},
        secrets=normalized_secrets,
        created_at=_utcnow(),
        expires_at=expires_at,
    )
    try:
        record = db.merge(record)
        db.commit()
        db.refresh(record)
    except Exception as exc:
        db.rollback()
        logger.exception(
            "connections.oauth_state.save.failed state=%s user=%s provider=%s error=%s",
            _mask(normalized_state),
            _mask(normalized_user_id),
            normalized_provider,
            exc,
        )
        raise
    logger.info(
        "connections.oauth_state.save.success state=%s user=%s provider=%s",
        _mask(normalized_state),
        _mask(normalized_user_id),
        normalized_provider,
    )
    return record


def _connection_oauth_audit_lookup(
    *,
    state: str | None,
    provider: str,
) -> tuple[str, str] | None:
    """Normalize the bounded identifiers permitted for an OAuth audit lookup."""

    normalized_state = str(state or "").strip()
    normalized_provider = str(provider or "").strip().lower()
    if (
        not normalized_state
        or len(normalized_state) > _OAUTH_AUDIT_STATE_MAX_LENGTH
        or normalized_provider not in VALID_CONNECTION_PROVIDERS
    ):
        return None
    return normalized_state, normalized_provider


def _connection_oauth_audit_subject(record: ConnectionOAuthState) -> dict[str, str] | None:
    expires_at = _normalize_utc_datetime(record.expires_at)
    if not expires_at or expires_at <= _utcnow():
        return None
    user_id = str(record.user_id or "").strip()
    provider = str(record.provider or "").strip().lower()
    if not user_id or provider not in VALID_CONNECTION_PROVIDERS:
        return None
    # Deliberately expose no state, redirect, provider payload, or credentials.
    return {"user_id": user_id, "provider": provider}


def resolve_connection_oauth_audit_subject(
    db,
    *,
    state: str | None,
    provider: str,
) -> dict[str, str] | None:
    """Resolve a valid callback state to the minimal safe audit subject."""

    lookup = _connection_oauth_audit_lookup(state=state, provider=provider)
    if lookup is None:
        return None
    normalized_state, normalized_provider = lookup
    record = (
        db.query(ConnectionOAuthState)
        .filter(
            ConnectionOAuthState.state == normalized_state,
            ConnectionOAuthState.provider == normalized_provider,
        )
        .first()
    )
    if record is None:
        return None
    return _connection_oauth_audit_subject(record)


def consume_connection_oauth_audit_subject(
    db,
    *,
    state: str | None,
    provider: str,
) -> dict[str, str] | None:
    """Consume a matching callback state and return only its safe audit subject."""

    lookup = _connection_oauth_audit_lookup(state=state, provider=provider)
    if lookup is None:
        return None
    normalized_state, normalized_provider = lookup
    record = (
        db.query(ConnectionOAuthState)
        .filter(
            ConnectionOAuthState.state == normalized_state,
            ConnectionOAuthState.provider == normalized_provider,
        )
        .with_for_update()
        .first()
    )
    if record is None:
        return None
    subject = _connection_oauth_audit_subject(record)
    try:
        db.delete(record)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return subject


def consume_connection_oauth_state(db, state: str) -> dict | None:
    normalized_state = str(state or "").strip()
    logger.info("connections.oauth_state.consume.begin state=%s", _mask(normalized_state))
    record = (
        db.query(ConnectionOAuthState)
        .filter(ConnectionOAuthState.state == normalized_state)
        .first()
    )
    if not record:
        logger.warning("connections.oauth_state.consume.miss state=%s", _mask(normalized_state))
        return None
    expires_at = _normalize_utc_datetime(record.expires_at)
    if expires_at and expires_at <= _utcnow():
        logger.warning(
            "connections.oauth_state.consume.expired state=%s user=%s provider=%s expires_at=%s",
            _mask(record.state),
            _mask(record.user_id),
            record.provider,
            expires_at.isoformat(),
        )
        try:
            db.delete(record)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception("connections.oauth_state.consume.delete_failed state=%s error=%s", _mask(record.state), exc)
            raise
        logger.info("connections.oauth_state.consume.expired_deleted state=%s", _mask(record.state))
        return None
    payload = {
        "state": record.state,
        "provider": record.provider,
        "user_id": record.user_id,
        "return_path": record.return_path,
        "redirect_uri": record.redirect_uri,
        "payload": deepcopy(record.payload if isinstance(record.payload, dict) else {}),
        "secrets": _normalize_secrets(record.secrets),
        "created_at": record.created_at,
        "expires_at": record.expires_at,
    }
    logger.info(
        "connections.oauth_state.consume.hit state=%s user=%s provider=%s redirect_uri=%s",
        _mask(record.state),
        _mask(record.user_id),
        record.provider,
        record.redirect_uri,
    )
    try:
        db.delete(record)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("connections.oauth_state.consume.delete_failed state=%s error=%s", _mask(record.state), exc)
        raise
    logger.info("connections.oauth_state.consume.success state=%s", _mask(record.state))
    return payload
