from sqlalchemy import Boolean, CheckConstraint, Column, String, Index, DateTime, JSON, Table, text, Integer, cast, select
from sqlalchemy.exc import SQLAlchemyError
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from typing import Any
from contextlib import contextmanager
import logging
import uuid
import os
import re
import hashlib
import secrets

from app.database import AuditBase, Base, DATABASE_CONFIG, LOGS_DATABASE_SCHEMA, SessionLocal
from app.network.outbound_http import public_web_request
from app.network.policy import OutboundRequestBlockedError, assert_public_webhook_url_allowed
from app.paths import LOG_DIR
from app.settings.utils import get_value_by_page_and_key
from app.utils.client_ip import resolve_audit_request_client_ip


# -------------------
# Logging + Helper
# -------------------
AUDIT_LOG_FILE = str(LOG_DIR / "audit.log")
AUTH_LOG_FILE = str(LOG_DIR / "auth.log")
os.makedirs(os.path.dirname(AUDIT_LOG_FILE), exist_ok=True)

_LOG_FORMATTER = logging.Formatter(
    fmt="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)

audit_logger = logging.getLogger("audit_file_logger")
if not audit_logger.handlers:
    audit_handler = RotatingFileHandler(
        AUDIT_LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    audit_handler.setFormatter(_LOG_FORMATTER)
    audit_logger.addHandler(audit_handler)
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False

auth_logger = logging.getLogger("auth_file_logger")
if not auth_logger.handlers:
    auth_handler = RotatingFileHandler(
        AUTH_LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    auth_handler.setFormatter(_LOG_FORMATTER)
    auth_logger.addHandler(auth_handler)
    auth_logger.setLevel(logging.INFO)
    auth_logger.propagate = False


_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_AUTH_LOG_LEVELS = {
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_SENSITIVE_DETAIL_KEYWORDS: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "key",
    "apikey",
    "api_key",
    "authorization",
    "auth",
    "session",
    "cookie",
    "credential",
    "ssn",
    "credit",
    "card",
)
_SHARE_CAPABILITY_FINGERPRINT_PATTERN = re.compile(r"^share_fp_[0-9a-f]{12}$")
_SHARE_CAPABILITY_URL_FINGERPRINT_PATTERN = re.compile(r"^share_url_fp_[0-9a-f]{12}$")
logger = logging.getLogger(__name__)

_IP_HASH_SALT = str(os.getenv("LOG_IP_HASH_SALT") or "").strip()
_GENERATED_IP_HASH_SALT: str | None = None
_GENERATED_IP_HASH_SALT_WARNED = False
_POSTGRES_RUNTIME = str(DATABASE_CONFIG.get("driver") or "").lower().startswith("postgresql")
_ADMIN_NOTIFICATION_TABLE_KWARGS = {"postgresql_partition_by": "RANGE (timestamp)"}
if _POSTGRES_RUNTIME:
    _ADMIN_NOTIFICATION_TABLE_KWARGS["schema"] = LOGS_DATABASE_SCHEMA


def _admin_notifications_schema_query() -> tuple[str, dict[str, str]]:
    schema_name = _ADMIN_NOTIFICATION_TABLE_KWARGS.get("schema")
    if schema_name:
        return "AND table_schema = :schema_name", {"schema_name": str(schema_name)}
    return "AND table_schema = ANY (current_schemas(true))", {}


def _get_ip_hash_salt() -> str:
    global _GENERATED_IP_HASH_SALT, _GENERATED_IP_HASH_SALT_WARNED

    if _IP_HASH_SALT:
        return _IP_HASH_SALT

    if _GENERATED_IP_HASH_SALT is None:
        _GENERATED_IP_HASH_SALT = secrets.token_urlsafe(32)

    if not _GENERATED_IP_HASH_SALT_WARNED:
        logger.warning(
            "LOG_IP_HASH_SALT is not configured; using a random per-process audit IP hash salt. "
            "Set LOG_IP_HASH_SALT to keep pseudonymous audit IP hashes stable across restarts."
        )
        _GENERATED_IP_HASH_SALT_WARNED = True

    return _GENERATED_IP_HASH_SALT


def validate_ip_hash_salt_configuration() -> None:
    """Require an independent, stable IP hash salt outside development mode."""
    mode = str(os.getenv("MODE", "production") or "production").strip().lower()
    if mode in {"dev", "development", "local", "test"}:
        return
    if len(_IP_HASH_SALT) < 16:
        raise RuntimeError(
            "LOG_IP_HASH_SALT is required in production and must contain at least 16 characters."
        )


def _hash_text(value: str, *, prefix: str, keep: int = 12) -> str:
    digest = hashlib.sha256(f"{prefix}:{_get_ip_hash_salt()}:{value}".encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:keep]}"


def _hash_email(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        return "email_empty"
    return _hash_text(normalized, prefix="email")


_PARTITIONED_TABLE_CACHE: dict[tuple[str | None, str], bool] = {}
_KNOWN_MONTHLY_PARTITIONS: set[tuple[str | None, str, int, int]] = set()
_NON_PARTITIONED_WARNED: set[tuple[str | None, str]] = set()
_ADMIN_NOTIFICATIONS_HAS_USER_ID: bool | None = None
_ADMIN_NOTIFICATIONS_HAS_TYPE: bool | None = None
_NOTIFICATION_WEBHOOK_TIMEOUT = 10.0
_AUDIT_SHARE_SCRUB_BATCH_SIZE = 500
_PARTITION_LOCK_NAMESPACE = "omlorix.monthly_partition"
_PARTITION_MONTHS_AHEAD = 2


def _sanitize_log_message(message: str | None) -> str | None:
    if message is None:
        return None
    # The flat audit mirrors are deliberately line-oriented. Keep every
    # semantic event on one physical line even when a caller passes a reason
    # or detail containing terminal controls or Unicode line separators.
    single_line = re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+", " ", str(message))
    sanitized = _EMAIL_PATTERN.sub(lambda match: _hash_email(match.group(0)), single_line)
    return sanitized[:2000]


def _sanitize_device_info(device_info: str | None) -> str | None:
    if device_info is None:
        return None
    normalized = str(device_info).strip()
    if not normalized:
        return None
    return _hash_text(normalized, prefix="device")


def _sanitize_ip(ip_address: str | None) -> str | None:
    if ip_address is None:
        return None
    normalized = str(ip_address).strip()
    if not normalized:
        return None
    return _hash_text(normalized, prefix="ip")


def _is_sensitive_detail_key(key: str | None) -> bool:
    if not key:
        return False
    key_lower = key.lower()
    return any(keyword in key_lower for keyword in _SENSITIVE_DETAIL_KEYWORDS)


def _is_share_capability_detail_key(key: str | None) -> bool:
    if not key:
        return False
    key_lower = key.lower()
    return key_lower == "share_id" or key_lower.endswith("_share_id")


def _is_share_capability_url_detail_key(key: str | None) -> bool:
    if not key:
        return False
    key_lower = key.lower()
    return key_lower == "share_url" or key_lower.endswith("_share_url")


def _fingerprint_share_capability(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return normalized
    if _SHARE_CAPABILITY_FINGERPRINT_PATTERN.fullmatch(normalized):
        return normalized
    return _hash_text(normalized, prefix="share_fp")


def _fingerprint_share_capability_url(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return normalized
    if _SHARE_CAPABILITY_URL_FINGERPRINT_PATTERN.fullmatch(normalized):
        return normalized
    return _hash_text(normalized, prefix="share_url_fp")


def _scrub_share_capability_references(value: Any, *, parent_key: str | None = None) -> Any:
    if value is None:
        return None

    if isinstance(value, dict):
        updated: dict[Any, Any] = {}
        changed = False
        for key, item in value.items():
            new_item = _scrub_share_capability_references(item, parent_key=str(key))
            updated[key] = new_item
            if new_item != item:
                changed = True
        return updated if changed else value

    if isinstance(value, list):
        updated_items = [
            _scrub_share_capability_references(item, parent_key=parent_key)
            for item in value
        ]
        return updated_items if updated_items != value else value

    if isinstance(value, tuple):
        updated_items = tuple(
            _scrub_share_capability_references(item, parent_key=parent_key)
            for item in value
        )
        return updated_items if updated_items != value else value

    if isinstance(value, set):
        updated_items = {
            _scrub_share_capability_references(item, parent_key=parent_key)
            for item in value
        }
        return updated_items if updated_items != value else value

    if isinstance(value, str):
        key_lower = parent_key.lower() if parent_key else ""
        if _is_share_capability_detail_key(key_lower):
            return _fingerprint_share_capability(value)
        if _is_share_capability_url_detail_key(key_lower):
            return _fingerprint_share_capability_url(value)

    return value


def _sanitize_detail_value(value: Any, *, parent_key: str | None = None) -> Any:
    if _is_sensitive_detail_key(parent_key):
        return "<redacted>"

    if value is None:
        return None

    if isinstance(value, dict):
        return {
            key: _sanitize_detail_value(val, parent_key=str(key))
            for key, val in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [_sanitize_detail_value(item, parent_key=parent_key) for item in value]

    if isinstance(value, bool | int | float):
        return value

    if isinstance(value, str):
        key_lower = parent_key.lower() if parent_key else ""
        if _is_share_capability_detail_key(key_lower):
            return _fingerprint_share_capability(value)
        if _is_share_capability_url_detail_key(key_lower):
            return _fingerprint_share_capability_url(value)
        if key_lower in {"ip", "ip_address", "client_ip"}:
            return _sanitize_ip(value)
        if "device" in key_lower:
            return _sanitize_device_info(value)
        sanitized = _sanitize_log_message(value)
        return sanitized if sanitized is not None else value

    # Fallback: convert to string to avoid leaking reprs with sensitive info
    return _sanitize_log_message(str(value))


def _sanitize_audit_details(details: Any) -> Any:
    if details is None:
        return None
    return _sanitize_detail_value(details)


def _deleted_user_reference(user_id: str) -> str:
    normalized = str(user_id or "").strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"deleted-user:{digest}"


def _replace_deleted_user_references(value: Any, *, user_id: str, replacement: str) -> Any:
    if isinstance(value, dict):
        updated: dict[Any, Any] = {}
        changed = False
        for key, item in value.items():
            new_item = _replace_deleted_user_references(item, user_id=user_id, replacement=replacement)
            updated[key] = new_item
            if new_item != item:
                changed = True
        return updated if changed else value

    if isinstance(value, list):
        updated_items = [
            _replace_deleted_user_references(item, user_id=user_id, replacement=replacement)
            for item in value
        ]
        return updated_items if updated_items != value else value

    if isinstance(value, tuple):
        updated_items = tuple(
            _replace_deleted_user_references(item, user_id=user_id, replacement=replacement)
            for item in value
        )
        return updated_items if updated_items != value else value

    if isinstance(value, set):
        updated_items = {
            _replace_deleted_user_references(item, user_id=user_id, replacement=replacement)
            for item in value
        }
        return updated_items if updated_items != value else value

    if isinstance(value, str) and value == user_id:
        return replacement

    return value


def pseudonymize_deleted_user_details(details: Any, user_id: str) -> Any:
    return _replace_deleted_user_references(
        details,
        user_id=str(user_id or "").strip(),
        replacement=_deleted_user_reference(user_id),
    )


def _pseudonymize_deleted_user_details_references(db: Session, model: Any, *, user_id: str) -> int:
    details_matches = (
        db.query(model)
        .filter(model.details.isnot(None))
        .filter(cast(model.details, String).contains(user_id))
        .all()
    )

    updated = 0
    for row in details_matches:
        details = getattr(row, "details", None)
        redacted_details = pseudonymize_deleted_user_details(details, user_id)
        if redacted_details != details:
            row.details = redacted_details
            updated += 1

    return updated


def scrub_share_capability_references_in_audit_logs(db: Session, *, max_batches: int | None = None) -> int:
    try:
        updated = 0
        batches_processed = 0

        while True:
            if max_batches is not None and batches_processed >= max(max_batches, 0):
                break

            batch = (
                db.query(Logs)
                .filter(Logs.share_refs_scrubbed.is_(False))
                .order_by(Logs.timestamp.asc(), Logs.id.asc())
                .limit(_AUDIT_SHARE_SCRUB_BATCH_SIZE)
                .all()
            )
            if not batch:
                break

            for row in batch:
                details = getattr(row, "details", None)
                scrubbed_details = _scrub_share_capability_references(details)
                if scrubbed_details != details:
                    row.details = scrubbed_details
                    updated += 1
                row.share_refs_scrubbed = True

            db.commit()
            batches_processed += 1

        return updated
    except Exception:
        db.rollback()
        raise


def _get_notifications_webhook_url() -> str | None:
    """Return webhook URL if notifications are enabled, otherwise None."""
    session = SessionLocal()
    try:
        enabled = get_value_by_page_and_key("notifications", "enable_notifications", session)
        if not bool(enabled):
            return None
        raw_url = get_value_by_page_and_key("notifications", "webhook_url", session)
        if not isinstance(raw_url, str):
            return None
        url = raw_url.strip()
        return url or None
    except Exception:
        logger.exception("Failed to load notification webhook settings")
        return None
    finally:
        session.close()


def _send_notification_webhook(payload: dict[str, Any]) -> None:
    """Dispatch notification payload to configured webhook, ignoring failures."""
    webhook_url = _get_notifications_webhook_url()
    if not webhook_url:
        return
    session = SessionLocal()
    try:
        try:
            assert_public_webhook_url_allowed(
                session,
                url=webhook_url,
                feature="Admin notification webhook delivery",
            )
        except OutboundRequestBlockedError as exc:
            logger.warning("Skipping admin notification webhook because policy blocked it: %s", exc)
            return
    finally:
        session.close()

    message_text = payload.get("message")
    text_block = (
        f"[{payload.get('type', 'info').upper()}] {payload.get('category', 'general')}: {message_text}"
        if message_text
        else None
    )
    body = {
        "content": text_block,
        "text": text_block,
        "payload": payload,
    }

    try:
        response = public_web_request(
            "POST",
            webhook_url,
            feature="Admin notification webhook delivery",
            json=body,
            timeout=_NOTIFICATION_WEBHOOK_TIMEOUT,
            allow_redirects=False,
        )
        response.raise_for_status()
    except Exception:
        logger.exception("Failed to deliver admin notification webhook")


def send_notification_webhook(payload: dict[str, Any]) -> None:
    """Public helper for dispatching notification payloads to the configured webhook."""
    _send_notification_webhook(payload)


# -------------------
# Partition helpers
# -------------------
def _quote_identifier(name: str) -> str:
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def _qualified_table_name(table: Table) -> str:
    if table.schema:
        return f"{_quote_identifier(table.schema)}.{_quote_identifier(table.name)}"
    return _quote_identifier(table.name)


def _compute_month_bounds(instant: datetime) -> tuple[datetime, datetime]:
    utc_instant = instant.astimezone(timezone.utc)
    month_start = utc_instant.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)
    return month_start, month_end


def _is_partitioned_table(session: Session, table: Table) -> bool:
    table_key = (table.schema, table.name)
    is_partitioned = _PARTITIONED_TABLE_CACHE.get(table_key)

    if is_partitioned is None:
        schema_filter = "AND ns.nspname = :schema_name" if table.schema else "AND ns.nspname = ANY (current_schemas(true))"
        try:
            is_partitioned = bool(
                session.execute(
                    text(
                        f"""
                        SELECT EXISTS (
                            SELECT 1
                            FROM pg_partitioned_table pt
                            JOIN pg_class parent ON parent.oid = pt.partrelid
                            JOIN pg_namespace ns ON ns.oid = parent.relnamespace
                            WHERE parent.relname = :table_name
                            {schema_filter}
                        )
                        """
                    ),
                    (
                        {"table_name": table.name, "schema_name": table.schema}
                        if table.schema
                        else {"table_name": table.name}
                    ),
                ).scalar()
            )
        except SQLAlchemyError as exc:
            logger.warning(
                "Could not determine partition state for %s: %s",
                _qualified_table_name(table),
                exc,
            )
            is_partitioned = False
        _PARTITIONED_TABLE_CACHE[table_key] = is_partitioned

    if not is_partitioned:
        if table_key not in _NON_PARTITIONED_WARNED:
            _NON_PARTITIONED_WARNED.add(table_key)
            logger.warning(
                "Skipping partition creation for %s because the parent table is not partitioned in the database.",
                _qualified_table_name(table),
            )
        return False

    return True


def _partition_cache_key(table: Table, month_start: datetime) -> tuple[str | None, str, int, int]:
    return (table.schema, table.name, month_start.year, month_start.month)


def _partition_lock_key(table: Table, month_start: datetime) -> int:
    lock_input = (
        f"{_PARTITION_LOCK_NAMESPACE}:{table.schema or 'default'}:{table.name}:"
        f"{month_start.year:04d}-{month_start.month:02d}"
    )
    return int.from_bytes(hashlib.sha256(lock_input.encode("utf-8")).digest()[:8], "big", signed=True)


def _partition_exists(session: Session, table: Table, partition_name: str) -> bool:
    if table.schema:
        params = {"schema_name": table.schema, "table_name": partition_name}
        schema_filter = "table_schema = :schema_name"
    else:
        params = {"table_name": partition_name}
        schema_filter = "table_schema = ANY (current_schemas(true))"

    return bool(
        session.execute(
            text(
                f"""
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE {schema_filter}
                      AND table_name = :table_name
                )
                """
            ),
            params,
        ).scalar()
    )


def _ensure_monthly_partition(session: Session, table: Table, instant: datetime) -> bool:
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return False

    if not _is_partitioned_table(session, table):
        return False

    month_start, month_end = _compute_month_bounds(instant)
    cache_key = _partition_cache_key(table, month_start)
    if cache_key in _KNOWN_MONTHLY_PARTITIONS:
        return False

    base_name = table.name
    partition_name = f"{base_name}_{month_start.year:04d}_{month_start.month:02d}"
    qualified_parent = _qualified_table_name(table)
    if table.schema:
        qualified_partition = f"{_quote_identifier(table.schema)}.{_quote_identifier(partition_name)}"
    else:
        qualified_partition = _quote_identifier(partition_name)

    if _partition_exists(session, table, partition_name):
        _KNOWN_MONTHLY_PARTITIONS.add(cache_key)
        return False

    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _partition_lock_key(table, month_start)},
    )
    if _partition_exists(session, table, partition_name):
        _KNOWN_MONTHLY_PARTITIONS.add(cache_key)
        return False

    session.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {qualified_partition}
            PARTITION OF {qualified_parent}
            FOR VALUES FROM (:start_ts) TO (:end_ts)
            """
        ),
        {"start_ts": month_start, "end_ts": month_end},
    )
    _KNOWN_MONTHLY_PARTITIONS.add(cache_key)
    return True


def _month_offset(instant: datetime, offset: int) -> datetime:
    month_start, _ = _compute_month_bounds(instant)
    total_months = (month_start.year * 12) + (month_start.month - 1) + offset
    target_year = total_months // 12
    target_month = (total_months % 12) + 1
    return month_start.replace(year=target_year, month=target_month)


def ensure_audit_log_partitions(
    session: Session,
    *,
    months_ahead: int = _PARTITION_MONTHS_AHEAD,
    instant: datetime | None = None,
) -> int:
    if months_ahead < 0:
        raise ValueError("months_ahead must be non-negative")

    base_instant = instant or datetime.now(timezone.utc)
    created = 0
    for offset in range(months_ahead + 1):
        target_instant = _month_offset(base_instant, offset)
        created += int(_ensure_monthly_partition(session, Logs.__table__, target_instant))
        created += int(_ensure_monthly_partition(session, AuthenticationLogs.__table__, target_instant))
    return created


def ensure_admin_notification_partitions(
    session: Session,
    *,
    months_ahead: int = _PARTITION_MONTHS_AHEAD,
    instant: datetime | None = None,
) -> int:
    if months_ahead < 0:
        raise ValueError("months_ahead must be non-negative")

    base_instant = instant or datetime.now(timezone.utc)
    created = 0
    for offset in range(months_ahead + 1):
        target_instant = _month_offset(base_instant, offset)
        created += int(_ensure_monthly_partition(session, AdminNotifications.__table__, target_instant))
    return created


def _ensure_admin_notifications_user_id(session: Session) -> None:
    global _ADMIN_NOTIFICATIONS_HAS_USER_ID

    if _ADMIN_NOTIFICATIONS_HAS_USER_ID is True:
        return

    bind = session.get_bind()

    if bind is None or bind.dialect.name != "postgresql":
        _ADMIN_NOTIFICATIONS_HAS_USER_ID = True
        return

    if _ADMIN_NOTIFICATIONS_HAS_USER_ID is None:
        schema_filter, schema_params = _admin_notifications_schema_query()
        try:
            column_exists = bool(
                session.execute(
                    text(
                        f"""
                        SELECT EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_name = :table_name
                              AND column_name = 'user_id'
                              {schema_filter}
                        )
                        """
                    ),
                    {"table_name": AdminNotifications.__tablename__, **schema_params},
                ).scalar()
            )
        except SQLAlchemyError as exc:
            logger.warning(
                "Could not verify user_id column for %s: %s",
                _qualified_table_name(AdminNotifications.__table__),
                exc,
            )
            column_exists = True
        _ADMIN_NOTIFICATIONS_HAS_USER_ID = column_exists

    if _ADMIN_NOTIFICATIONS_HAS_USER_ID:
        return

    qualified_parent = _qualified_table_name(AdminNotifications.__table__)

    try:
        session.execute(
            text(
                f"ALTER TABLE {qualified_parent} ADD COLUMN user_id VARCHAR(64)"
            )
        )
        _ADMIN_NOTIFICATIONS_HAS_USER_ID = True
        logger.info("Added missing user_id column to %s", qualified_parent)
    except SQLAlchemyError as exc:
        logger.error(
            "Failed to add user_id column to %s: %s",
            qualified_parent,
            exc,
        )
        raise


def _ensure_admin_notifications_type(session: Session) -> None:
    """Ensure the type column exists (runtime migration for partitioned table)."""
    global _ADMIN_NOTIFICATIONS_HAS_TYPE

    if _ADMIN_NOTIFICATIONS_HAS_TYPE is True:
        return

    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        _ADMIN_NOTIFICATIONS_HAS_TYPE = True
        return

    if _ADMIN_NOTIFICATIONS_HAS_TYPE is None:
        schema_filter, schema_params = _admin_notifications_schema_query()
        try:
            column_exists = bool(
                session.execute(
                    text(
                        f"""
                        SELECT EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_name = :table_name
                              AND column_name = 'type'
                              {schema_filter}
                        )
                        """
                    ),
                    {"table_name": AdminNotifications.__tablename__, **schema_params},
                ).scalar()
            )
        except SQLAlchemyError as exc:
            logger.warning(
                "Could not verify type column for %s: %s",
                _qualified_table_name(AdminNotifications.__table__),
                exc,
            )
            column_exists = True
        _ADMIN_NOTIFICATIONS_HAS_TYPE = column_exists

    if _ADMIN_NOTIFICATIONS_HAS_TYPE:
        return

    qualified_parent = _qualified_table_name(AdminNotifications.__table__)

    try:
        session.execute(
            text(
                f"ALTER TABLE {qualified_parent} ADD COLUMN type VARCHAR(16) NOT NULL DEFAULT 'info'"
            )
        )
        _ADMIN_NOTIFICATIONS_HAS_TYPE = True
        logger.info("Added missing type column to %s", qualified_parent)
    except SQLAlchemyError as exc:
        logger.error(
            "Failed to add type column to %s: %s",
            qualified_parent,
            exc,
        )
        raise


# The Admin Logs are not allowed to be deleted or edited
# ---------------------------------------------------------------------------
# Admin Audit Logs
# ---------------------------------------------------------------------------
class Logs(AuditBase):
    __tablename__ = "logs"
    __table_args__ = (
        CheckConstraint("subject_fenced", name="ck_logs_subject_fenced"),
        Index("ix_logs_timestamp", "timestamp"),
        Index("ix_logs_timestamp_id", "timestamp", "id"),
        Index("ix_logs_user_id", "user_id"),
        Index("ix_logs_user_timestamp_id", "user_id", "timestamp", "id"),
        Index("ix_logs_action", "action"),
        Index("ix_logs_action_timestamp_id", "action", "timestamp", "id"),
        Index("ix_logs_category_timestamp_id", "category", "timestamp", "id"),
        Index("ix_logs_share_refs_scrubbed", "share_refs_scrubbed"),
        {"postgresql_partition_by": "RANGE (timestamp)"},
    )

    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(String(64), nullable=False)
    action = Column(String(128), nullable=False)
    reason = Column(String(255), nullable=True)
    timestamp = Column(DateTime(timezone=True), primary_key=True, nullable=False)

    # Erweiterungen
    details = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    category = Column(String(64), nullable=False, default="admin")
    share_refs_scrubbed = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    # A false server default makes inserts from pre-fence application versions
    # fail the table constraint. Current writers always supply the safe value.
    subject_fenced = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("false"),
    )


def get_audit_request_ip(request, db: Session | None = None) -> str | None:
    """Resolve the audit IP for a request using configured trusted-proxy policy."""
    if request is None:
        return None
    return resolve_audit_request_client_ip(request, db, default=None)


# -------------------
# Create audit log
# -------------------
def _normalized_audit_log_payload(
    *,
    user_id: str | None,
    action: str | None,
    reason: str | None,
    details: dict | None,
    ip_address: str | None,
    user_agent: str | None,
    category: str | None,
) -> dict[str, Any]:
    """Sanitize the immutable audit payload before either delivery path."""

    return {
        "user_id": (_sanitize_log_message(user_id) or "")[:64],
        "action": (_sanitize_log_message(action) or "")[:128],
        "reason": (_sanitize_log_message(reason) or "")[:255] or None,
        "details": _sanitize_audit_details(details),
        "ip_address": _sanitize_ip(ip_address),
        "user_agent": _sanitize_device_info(user_agent),
        "category": (_sanitize_log_message(category or "admin") or "admin")[:64],
    }


def write_audit_log_record(
    db_log: Session,
    *,
    log_id: str,
    timestamp: datetime,
    payload: dict[str, Any],
    close_session: bool = False,
) -> Logs:
    """Idempotently persist one already-sanitized audit event."""

    try:
        normalized_id = str(log_id or "").strip()[:32]
        existing = (
            db_log.query(Logs)
            .filter(Logs.id == normalized_id, Logs.timestamp == timestamp)
            .first()
        )
        if existing is not None:
            return existing
        log_row = Logs(
            id=normalized_id,
            user_id=str(payload.get("user_id") or "")[:64],
            action=str(payload.get("action") or "")[:128],
            reason=payload.get("reason"),
            details=payload.get("details"),
            ip_address=payload.get("ip_address"),
            user_agent=payload.get("user_agent"),
            category=str(payload.get("category") or "admin")[:64],
            timestamp=timestamp,
            share_refs_scrubbed=True,
            subject_fenced=True,
        )
        db_log.add(log_row)
        db_log.commit()
        db_log.refresh(log_row)
        audit_logger.info(
            "user_id=%s action=%s category=%s ip=%s reason=%s details=%s",
            payload.get("user_id") or "-",
            payload.get("action") or "-",
            payload.get("category") or "admin",
            payload.get("ip_address") or "-",
            payload.get("reason") or "-",
            payload.get("details") if payload.get("details") is not None else "-",
        )
        return log_row
    except Exception:
        db_log.rollback()
        raise
    finally:
        if close_session:
            db_log.close()


def stage_audit_log_event(
    db: Session,
    *,
    user_id: str | None = None,
    action: str | None = None,
    reason: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    category: str | None = None,
):
    """Stage sanitized audit intent in the caller's main-DB transaction."""

    timestamp = datetime.now(timezone.utc)
    payload = _normalized_audit_log_payload(
        user_id=user_id,
        action=action,
        reason=reason,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
        category=category,
    )
    from app.workers.events import stage_audit_event

    return stage_audit_event(
        db,
        payload=payload,
        occurred_at=timestamp,
    )


def create_audit_log(
    db_log: Session | None = None,
    user_id: str | None = None,
    action: str | None = None,
    reason: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    category: str | None = None,
) -> Logs:
    timestamp = datetime.now(timezone.utc)
    payload = _normalized_audit_log_payload(
        user_id=user_id,
        action=action,
        reason=reason,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
        category=category,
    )
    try:
        from app.workers.events import (
            enqueue_audit_event,
            external_audit_event_enabled,
            write_inline_audit_event,
        )

        if external_audit_event_enabled():
            outbox = enqueue_audit_event(payload=payload, occurred_at=timestamp)
            # Preserve the long-standing return shape for callers even though
            # the durable worker owns persistence to the audit database.
            return Logs(
                id=outbox.id,
                user_id=outbox.user_id,
                action=outbox.action,
                reason=outbox.reason,
                details=outbox.details,
                ip_address=outbox.ip_address,
                user_agent=outbox.user_agent,
                category=outbox.category,
                timestamp=timestamp,
                share_refs_scrubbed=True,
            )
        return write_inline_audit_event(
            db_log=db_log,
            payload=payload,
            occurred_at=timestamp,
        )
    finally:
        db_log.close()



# ---------------------------------------------------------------------------
# Authentication Logs
# ---------------------------------------------------------------------------
class AuthenticationLogs(AuditBase):
    __tablename__ = "authenticationlogs"
    __table_args__ = (
        Index("ix_authenticationlogs_user_id", "user_id"),
        Index("ix_authenticationlogs_timestamp", "timestamp"),
        Index("ix_authenticationlogs_correlation_id", "correlation_id"),
        Index("ix_authenticationlogs_error_code", "error_code"),
        {"postgresql_partition_by": "RANGE (timestamp)"},
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    auth_type = Column(String, nullable=False)
    status = Column(String, nullable=False)
    message = Column(String, nullable=True, default="")
    user_id = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), primary_key=True, nullable=False)
    device_info = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    # Optional machine-readable context for administrator-facing authentication
    # diagnostics.  These fields deliberately exclude credentials and identity
    # provider payloads; ``details`` may only contain explicitly allowlisted,
    # sanitized metadata such as endpoint hosts and issuer values.
    correlation_id = Column(String(64), nullable=True)
    flow = Column(String(32), nullable=True)
    provider = Column(String(64), nullable=True)
    stage = Column(String(64), nullable=True)
    error_code = Column(String(128), nullable=True)
    details = Column(JSON, nullable=True)



# ---------------------------------------------------------------------------
# Auth Log Deletion Queue (per-user retention)
# ---------------------------------------------------------------------------
class AuthLogDeletionQueue(AuditBase):
    __tablename__ = "authlogdeletionqueue"
    __table_args__ = (
        Index("ix_authlogdeletionqueue_user_id", "user_id"),
        Index("ix_authlogdeletionqueue_status", "status"),
        Index("ix_authlogdeletionqueue_scheduled_for", "scheduled_for"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(64), nullable=False)
    scheduled_for = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String(2000), nullable=True)


class AuditLogDeletionQueue(AuditBase):
    __tablename__ = "auditlogdeletionqueue"
    __table_args__ = (
        Index("ix_auditlogdeletionqueue_user_id", "user_id"),
        Index("ix_auditlogdeletionqueue_status", "status"),
        Index("ix_auditlogdeletionqueue_scheduled_for", "scheduled_for"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(64), nullable=False)
    scheduled_for = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String(2000), nullable=True)


# -------------------
# Create authentication log
# -------------------
def create_authentication_log(
    db: Session,
    auth_type,
    status,
    message,
    user_id,
    device_info,
    ip_address,
    *,
    correlation_id: str | None = None,
    flow: str | None = None,
    provider: str | None = None,
    stage: str | None = None,
    error_code: str | None = None,
    details: dict | None = None,
):
    """Persist a safe authentication event and mirror it to the auth log file.

    Structured diagnostic arguments are keyword-only so existing authentication
    call sites keep their original behavior.  Values are bounded and sanitized
    before storage, and no token, code, secret, or claims payload belongs here.
    """
    try:
        sanitized_message = _sanitize_log_message(message)
        sanitized_device_info = _sanitize_device_info(device_info)
        sanitized_ip = _sanitize_ip(ip_address)
        timestamp = datetime.now(timezone.utc)
        log_entry = AuthenticationLogs(
            auth_type=auth_type,
            status=status,
            message=sanitized_message,
            user_id=user_id,
            timestamp=timestamp,
            device_info=sanitized_device_info,
            ip_address=sanitized_ip,
            correlation_id=str(correlation_id or "")[:64] or None,
            flow=str(flow or "")[:32] or None,
            provider=str(provider or "")[:64] or None,
            stage=str(stage or "")[:64] or None,
            error_code=str(error_code or "")[:128] or None,
            details=_sanitize_audit_details(details),
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)
        level = _AUTH_LOG_LEVELS.get(str(status).lower(), logging.INFO)
        auth_logger.log(
            level,
            "type=%s user_id=%s ip=%s device=%s reference=%s flow=%s provider=%s stage=%s code=%s msg=%s",
            auth_type,
            user_id or "-",
            sanitized_ip or "-",
            sanitized_device_info or "-",
            correlation_id or "-",
            flow or "-",
            provider or "-",
            stage or "-",
            error_code or "-",
            sanitized_message or "-",
        )
        return log_entry
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()



# -------------------
# Delete authentication logs for user
# -------------------
def delete_authentication_logs_for_user(db: Session, user_id: str):
    try:
        db.query(AuthenticationLogs).filter(AuthenticationLogs.user_id == user_id).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise


def delete_authentication_logs_older_than(db: Session, max_age_days: int) -> int:
    """Delete authentication logs older than the configured age limit."""
    try:
        safe_days = max(int(max_age_days), 1)
        cutoff = datetime.now(timezone.utc) - timedelta(days=safe_days)
        deleted = (
            db.query(AuthenticationLogs)
            .filter(AuthenticationLogs.timestamp < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        return int(deleted or 0)
    except Exception:
        db.rollback()
        raise


def prune_authentication_logs_to_max_count(db: Session, max_count: int) -> int:
    """Keep the newest authentication logs and delete rows beyond ``max_count``."""
    try:
        safe_count = max(int(max_count or 0), 0)
        excess_log_ids = (
            db.query(AuthenticationLogs.id.label("id"))
            .order_by(AuthenticationLogs.timestamp.desc(), AuthenticationLogs.id.desc())
            .offset(safe_count)
            .subquery()
        )
        deleted = (
            db.query(AuthenticationLogs)
            .filter(AuthenticationLogs.id.in_(select(excess_log_ids.c.id)))
            .delete(synchronize_session=False)
        )
        db.commit()
        return int(deleted or 0)
    except Exception:
        db.rollback()
        raise


def cancel_auth_log_deletions_for_user(db: Session, user_id: str, *, commit: bool = True) -> int:
    """
    Mark any pending deletion jobs for ``user_id`` as cancelled.

    Returns the number of queue entries updated.
    """
    now = datetime.now(timezone.utc)
    pending_rows = (
        db.query(AuthLogDeletionQueue)
        .filter(AuthLogDeletionQueue.user_id == user_id)
        .filter(AuthLogDeletionQueue.status.in_(("pending", "retry", "processing")))
        .all()
    )
    for row in pending_rows:
        row.status = "cancelled"
        row.last_error = None
        row.processed_at = now
    if pending_rows and commit:
        db.commit()
    return len(pending_rows)


def schedule_auth_log_deletion(
    db: Session,
    user_id: str,
    delete_after_days: int,
    *,
    scheduled_for: datetime | None = None,
) -> AuthLogDeletionQueue:
    """
    Enqueue a deferred deletion job for ``user_id``.
    """
    safe_days = max(int(delete_after_days or 0), 0)
    effective_scheduled_for = scheduled_for or (
        datetime.now(timezone.utc) + timedelta(days=safe_days)
    )
    if effective_scheduled_for.tzinfo is None:
        effective_scheduled_for = effective_scheduled_for.replace(tzinfo=timezone.utc)
    # Cancel any earlier jobs so we only keep the latest instruction
    cancel_auth_log_deletions_for_user(db, user_id, commit=False)

    queue_entry = AuthLogDeletionQueue(
        user_id=user_id,
        scheduled_for=effective_scheduled_for,
        status="pending",
        attempts=0,
    )
    db.add(queue_entry)
    db.commit()
    db.refresh(queue_entry)
    return queue_entry


@contextmanager
def audit_log_erasure_guard(user_id: str, *, bind=None):
    """Hold a hash-only main-DB lock across audit-database erasure."""

    guard_session = Session(bind=bind) if bind is not None else SessionLocal()
    try:
        from app.workers.models import lock_audit_event_erasure_guard

        lock_audit_event_erasure_guard(guard_session, user_id=user_id)
        yield guard_session
        guard_session.commit()
    except Exception:
        guard_session.rollback()
        raise
    finally:
        guard_session.close()


def delete_audit_logs_for_user(
    db: Session,
    user_id: str,
    *,
    main_db: Session | None = None,
    erasure_guard_db: Session | None = None,
) -> int:
    """Delete user-scoped audit history after fencing queued delivery.

    The audit database and durable event outbox live in different databases.
    Commit cancellation/redaction in the main database first; the event worker
    serializes delivery against the same outbox rows, so a queued event cannot
    recreate an audit record after this deletion returns. A separate hash-only
    guard remains locked across both commits so restoration cannot expose the
    user until the destructive audit-database phase has finished.
    """

    if erasure_guard_db is None:
        guard_bind = main_db.get_bind() if main_db is not None else None
        with audit_log_erasure_guard(user_id, bind=guard_bind) as guard_session:
            return delete_audit_logs_for_user(
                db,
                user_id,
                main_db=main_db,
                erasure_guard_db=guard_session,
            )

    owns_main_session = main_db is None
    main_session = main_db or SessionLocal()
    try:
        from app.workers.models import erase_user_audit_event_state

        erase_user_audit_event_state(
            main_session,
            user_id=user_id,
            commit=True,
        )
    except Exception:
        main_session.rollback()
        raise
    finally:
        if owns_main_session:
            main_session.close()

    try:
        deleted = (
            db.query(Logs)
            .filter(Logs.user_id == user_id)
            .delete(synchronize_session=False)
        )
        _pseudonymize_deleted_user_details_references(db, Logs, user_id=user_id)
        db.commit()
        return int(deleted or 0)
    except Exception:
        db.rollback()
        raise


def cancel_audit_log_deletions_for_user(db: Session, user_id: str, *, commit: bool = True) -> int:
    now = datetime.now(timezone.utc)
    pending_rows = (
        db.query(AuditLogDeletionQueue)
        .filter(AuditLogDeletionQueue.user_id == user_id)
        .filter(AuditLogDeletionQueue.status.in_(("pending", "retry", "processing")))
        .order_by(AuditLogDeletionQueue.id.asc())
        .with_for_update()
        .all()
    )
    for row in pending_rows:
        row.status = "cancelled"
        row.last_error = None
        row.processed_at = now
    if pending_rows and commit:
        db.commit()
    return len(pending_rows)


def schedule_audit_log_deletion(
    db: Session,
    user_id: str,
    delete_after_days: int,
    *,
    scheduled_for: datetime | None = None,
) -> AuditLogDeletionQueue:
    safe_days = max(int(delete_after_days or 0), 0)
    effective_scheduled_for = scheduled_for or (
        datetime.now(timezone.utc) + timedelta(days=safe_days)
    )
    if effective_scheduled_for.tzinfo is None:
        effective_scheduled_for = effective_scheduled_for.replace(tzinfo=timezone.utc)
    cancel_audit_log_deletions_for_user(db, user_id, commit=False)

    queue_entry = AuditLogDeletionQueue(
        user_id=user_id,
        scheduled_for=effective_scheduled_for,
        status="pending",
        attempts=0,
    )
    db.add(queue_entry)
    db.commit()
    db.refresh(queue_entry)
    return queue_entry







class AdminNotifications(Base):
    __tablename__ = "adminnotifications"
    __table_args__ = (
        Index("ix_adminnotifications_user_id", "user_id"),
        Index("ix_adminnotifications_category", "category"),
        Index("ix_adminnotifications_timestamp", "timestamp"),
        _ADMIN_NOTIFICATION_TABLE_KWARGS,
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=True)
    category = Column(String(64), nullable=False, default="general")
    type = Column(String(16), nullable=False, default="info")
    message = Column(String(20000), nullable=False)
    details = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), primary_key=True, nullable=False)


def _trim_message(value: str, limit: int = 20000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: max(limit - 3, 0)]}..."


def create_admin_notification(
    db: Session,
    category: str | None,
    message: str,
    *,
    details: dict | None = None,
    user_id: str | None = None,
    notification_type: str | None = None,
) -> AdminNotifications:
    try:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        normalized_category = (category or "general").strip() or "general"
        normalized_type = (notification_type or "info").strip().lower()
        if normalized_type not in {"info", "warning", "error"}:
            normalized_type = "info"
        timestamp = datetime.now(timezone.utc)
        _ensure_admin_notifications_user_id(db)
        _ensure_admin_notifications_type(db)
        notification = AdminNotifications(
            user_id=user_id,
            category=normalized_category[:64],
            type=normalized_type,
            # Keep the actionable message readable for administrators. Structured
            # details and audit/authentication logs retain their stricter sanitizer.
            message=_trim_message(message.strip()),
            details=_sanitize_audit_details(details),
            timestamp=timestamp,
        )
        db.add(notification)
        db.commit()
        db.refresh(notification)
        payload = {
            "id": notification.id,
            "user_id": notification.user_id,
            "category": notification.category,
            "type": notification.type,
            "message": notification.message,
            "details": notification.details,
            "timestamp": notification.timestamp.isoformat() if notification.timestamp else None,
        }
        _send_notification_webhook(payload)
        return notification
    except Exception:
        db.rollback()
        raise


def get_admin_notifications(
    db: Session,
    count: int = 10,
    *,
    category: str | None = None,
) -> list[AdminNotifications]:
    limit = max(int(count), 0)
    if limit == 0:
        return []
    query = db.query(AdminNotifications)
    if category:
        query = query.filter(AdminNotifications.category == category)
    return (
        query.order_by(AdminNotifications.timestamp.desc())
        .limit(limit)
        .all()
    )



def list_admin_notifications_paginated(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    category: str | None = None,
    categories: list[str] | None = None,
    types: list[str] | None = None,
) -> tuple[list[AdminNotifications], int, set[str], set[str]]:
    """Return paginated admin notifications, total count, and available filter options.
    
    Returns:
        tuple: (items, total, all_categories, all_types)
    """

    safe_page = max(int(page), 1)
    safe_page_size = max(int(page_size), 1)

    # Get all unique categories and types for filter options (before applying filters)
    all_categories_result = db.query(AdminNotifications.category).distinct().all()
    all_types_result = db.query(AdminNotifications.type).distinct().all()
    all_categories = {r[0] for r in all_categories_result if r[0]}
    all_types = {r[0] for r in all_types_result if r[0]}

    query = db.query(AdminNotifications)
    
    # Apply category filter (single or multiple)
    if categories:
        query = query.filter(AdminNotifications.category.in_(categories))
    elif category:
        query = query.filter(AdminNotifications.category == category)
    
    # Apply type filter (multiple)
    if types:
        query = query.filter(AdminNotifications.type.in_(types))

    total = query.count()
    items = (
        query.order_by(AdminNotifications.timestamp.desc())
        .offset((safe_page - 1) * safe_page_size)
        .limit(safe_page_size)
        .all()
    )

    return items, total, all_categories, all_types


def delete_all_admin_notifications(db: Session) -> int:
    """Delete all admin notifications and return the count of deleted items."""
    count = db.query(AdminNotifications).delete()
    db.commit()
    return count


def delete_admin_notifications_for_user(db: Session, user_id: str) -> int:
    try:
        count = (
            db.query(AdminNotifications)
            .filter(AdminNotifications.user_id == user_id)
            .delete(synchronize_session=False)
        )
        _pseudonymize_deleted_user_details_references(db, AdminNotifications, user_id=user_id)
        db.commit()
        return int(count or 0)
    except Exception:
        db.rollback()
        raise
    
