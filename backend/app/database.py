import logging
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import OperationalError, errorcodes, sql
from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from app.paths import DATA_DIR


logger = logging.getLogger(__name__)


_DEFAULT_SQLITE_PATH = DATA_DIR / "app.db"

_PG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NON_RETRYABLE_DB_SQLSTATES = {
    errorcodes.INVALID_AUTHORIZATION_SPECIFICATION,
    errorcodes.INVALID_PASSWORD,
}
_NON_RETRYABLE_DB_ERROR_MARKERS = (
    "password authentication failed",
    "invalid_password",
    "no password supplied",
    "role \"",
    "\" does not exist",
    "invalid authorization specification",
    "no pg_hba.conf entry",
)
_ENABLE_VALUES = {"1", "true", "yes", "on"}
_DISABLE_VALUES = {"0", "false", "no", "off"}
_POSTGRES_PSYCOPG2_DRIVERS = frozenset(
    {
        "postgresql",
        "postgresql+psycopg2",
    }
)


def _sqlite_fallback_allowed() -> bool:
    allow_fallback = (os.getenv("OMLORIX_ALLOW_SQLITE_FALLBACK") or "").strip().lower()
    if allow_fallback in _ENABLE_VALUES:
        return True
    mode = (os.getenv("MODE") or "").strip().lower()
    return mode not in {"production", "prod"}


def _database_autocreate_enabled() -> bool:
    """Return whether startup should try to create missing PostgreSQL databases."""

    raw = (os.getenv("OMLORIX_AUTO_CREATE_DATABASES") or "").strip().lower()
    if raw in _ENABLE_VALUES:
        return True
    if raw in _DISABLE_VALUES:
        return False
    if raw:
        allowed_values = ", ".join(sorted(_ENABLE_VALUES | _DISABLE_VALUES))
        raise RuntimeError(
            f"Invalid OMLORIX_AUTO_CREATE_DATABASES={raw!r}. Expected one of: {allowed_values}."
        )

    return True


def _normalize_exception_text(exc: Exception) -> str:
    return " ".join(str(exc).split())


def _is_non_retryable_connection_error(exc: OperationalError) -> bool:
    sqlstate = getattr(exc, "pgcode", None)
    if sqlstate in _NON_RETRYABLE_DB_SQLSTATES:
        return True

    lowered = _normalize_exception_text(exc).lower()
    return any(marker in lowered for marker in _NON_RETRYABLE_DB_ERROR_MARKERS)


def _validate_pg_identifier(name: str, *, field: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{field} must be a non-empty string")
    normalized = name.strip()
    if not _PG_IDENTIFIER_RE.match(normalized):
        raise ValueError(
            f"{field} contains invalid characters; only letters, digits, and underscores are allowed, "
            "and it must not start with a digit"
        )
    return normalized


def _quote_identifier(name: str) -> str:
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def _resolve_schema_name(env_var: str, default: str) -> str:
    raw = str(os.getenv(env_var) or default).strip() or default
    return _validate_pg_identifier(raw, field=env_var)


DATABASE_SCHEMA = _resolve_schema_name("DATABASE_SCHEMA", "app")
AUDIT_DATABASE_SCHEMA = _resolve_schema_name("DATABASE_AUDIT_LOG_SCHEMA", "audit")
LOGS_DATABASE_SCHEMA = _resolve_schema_name("DATABASE_LOGS_SCHEMA", "logs")


def _resolve_database_configuration(env_prefix: str, default_sqlite_path: Path) -> dict[str, str]:
    """Return a configuration dictionary for the database connection."""

    direct_var = f"{env_prefix}_URL"
    direct_url = os.getenv(direct_var)
    if direct_url and direct_url.strip():
        trimmed = direct_url.strip()
        url_obj = make_url(trimmed)
        driver = url_obj.drivername.lower()
        if driver.startswith("postgresql") and driver not in _POSTGRES_PSYCOPG2_DRIVERS:
            raise RuntimeError(
                f"{direct_var} must use the psycopg2 PostgreSQL dialect "
                "('postgresql://' or 'postgresql+psycopg2://')."
            )
        if driver.startswith("sqlite") and not _sqlite_fallback_allowed():
            raise RuntimeError(
                f"{direct_var} points to SQLite while MODE=production. Configure PostgreSQL or set "
                "OMLORIX_ALLOW_SQLITE_FALLBACK=true only for intentional local deployments."
            )
        config: dict[str, str] = {
            "url": trimmed,
            "driver": driver,
        }
        if driver.startswith("postgresql"):
            if url_obj.username:
                config["database_user"] = url_obj.username
            if url_obj.password:
                config["database_password"] = url_obj.password
            if url_obj.host:
                config["database_host"] = url_obj.host
            if url_obj.port is not None:
                config["database_port"] = str(url_obj.port)
            if url_obj.database:
                config["database_name"] = url_obj.database
        return config

    creds: dict[str, str] = {}
    missing: list[str] = []
    key_map = {
        "USER": "database_user",
        "PASSWORD": "database_password",
        "HOST": "database_host",
        "PORT": "database_port",
        "NAME": "database_name",
    }
    for suffix, key in key_map.items():
        var = f"{env_prefix}_{suffix}"
        value = os.getenv(var)
        if value and value.strip():
            creds[key] = value.strip()
        else:
            missing.append(var)

    if missing:
        if not _sqlite_fallback_allowed():
            raise RuntimeError(
                f"Database credentials missing ({', '.join(missing)}) while MODE=production. Configure "
                f"{direct_var} or the full {env_prefix}_USER/PASSWORD/HOST/PORT/NAME set."
            )
        default_sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_url = f"sqlite:///{default_sqlite_path}"
        logger.warning(
            "Database credentials missing (%s). Falling back to SQLite at %s.",
            ", ".join(missing),
            default_sqlite_path,
        )
        return {
            "url": fallback_url,
            "driver": "sqlite",
        }

    quoted_user = urllib.parse.quote_plus(creds["database_user"])
    quoted_password = urllib.parse.quote_plus(creds["database_password"])
    url = (
        f"postgresql://{quoted_user}:{quoted_password}"
        f"@{creds['database_host']}:{creds['database_port']}/{creds['database_name']}"
    )
    creds.update({"url": url, "driver": "postgresql"})
    return creds


def _is_postgres(config: dict[str, str] | None) -> bool:
    return str((config or {}).get("driver") or "").lower().startswith("postgresql")


def build_postgres_connection_kwargs(
    config: dict[str, str],
    *,
    database_name: str | None = None,
    application_name: str | None = None,
) -> dict[str, Any]:
    """Return the effective SQLAlchemy/libpq connection parameters.

    PostgreSQL connection URLs can carry substantially more policy than the
    familiar host, port, user, password, and database fields. TLS verification,
    channel binding, authentication requirements, multi-host selection, and
    client-certificate settings all live in the URL query string. Asking the
    configured SQLAlchemy dialect to build its DBAPI arguments preserves the
    same precedence and normalization used by the application's normal engine,
    including repeated query-string hosts.

    ``database_name`` and ``application_name`` are deliberate caller-owned
    overrides. They let bootstrap connections target the maintenance database
    and let backup/restore connections be identified without discarding any
    other configured policy.
    """
    if not _is_postgres(config):
        raise RuntimeError("PostgreSQL connection parameters require a PostgreSQL configuration")

    database_url = str(config.get("url") or "").strip()
    if database_url:
        url_obj = make_url(database_url)
        url_driver = str(url_obj.drivername or "").lower()
        if url_driver not in _POSTGRES_PSYCOPG2_DRIVERS:
            raise RuntimeError(
                "PostgreSQL connection URL must use the psycopg2 dialect "
                "('postgresql://' or 'postgresql+psycopg2://')"
            )

        # create_connect_args() is the dialect's canonical conversion from a
        # SQLAlchemy URL to the arguments passed to the DBAPI. Reusing it avoids
        # a security-sensitive, version-specific allowlist of libpq options.
        dialect = url_obj.get_dialect()()
        positional_args, keyword_args = dialect.create_connect_args(url_obj)
        if positional_args:
            raise RuntimeError(
                "PostgreSQL connection URL produced unsupported positional DBAPI arguments"
            )
        connection_kwargs: dict[str, Any] = dict(keyword_args)
    else:
        # Tests and internal callers sometimes provide the already-resolved
        # configuration fields without a URL. Preserve that established input
        # form while production DATABASE_CONFIG continues to use the URL path.
        connection_kwargs = {}
        for config_key, connection_key in (
            ("database_name", "dbname"),
            ("database_user", "user"),
            ("database_password", "password"),
            ("database_host", "host"),
            ("database_port", "port"),
        ):
            if config.get(config_key) is not None:
                connection_kwargs[connection_key] = config[config_key]

    if database_name is not None:
        connection_kwargs["dbname"] = database_name
    if application_name is not None:
        connection_kwargs["application_name"] = application_name

    # DBAPI connection arguments must not contain None. Leaving an option out
    # allows libpq to apply an inherited environment setting or its default,
    # matching the behavior of the application's SQLAlchemy engine.
    return {
        str(key): value
        for key, value in connection_kwargs.items()
        if value is not None
    }


DATABASE_CONFIG = _resolve_database_configuration("DATABASE", _DEFAULT_SQLITE_PATH)
DATABASE_URL = DATABASE_CONFIG["url"]
AUDIT_DATABASE_CONFIG = dict(DATABASE_CONFIG)
AUDIT_DATABASE_URL = DATABASE_URL


def create_database_if_not_exists(
    target_config: dict[str, str] = DATABASE_CONFIG,
    *,
    config_label: str = "DATABASE",
    max_retries: int = 12,
    retry_interval: int = 3,
) -> None:
    """Ensure the target PostgreSQL database exists."""

    if not _is_postgres(target_config):
        return

    required_keys = {
        "database_user",
        "database_password",
        "database_host",
        "database_port",
        "database_name",
    }
    if not required_keys.issubset(target_config):
        return

    conn = None
    attempts = 0
    while conn is None:
        try:
            conn = psycopg2.connect(
                **build_postgres_connection_kwargs(
                    target_config,
                    database_name="postgres",
                )
            )
        except OperationalError as exc:
            reason = _normalize_exception_text(exc)
            if _is_non_retryable_connection_error(exc):
                raise RuntimeError(
                    f"Invalid PostgreSQL credentials/configuration for {config_label}. "
                    f"Check {config_label}_USER/{config_label}_PASSWORD in .env "
                    f"(host={target_config['database_host']}, "
                    f"port={target_config['database_port']}, "
                    f"user={target_config['database_user']}). "
                    f"PostgreSQL said: {reason}"
                ) from exc

            logger.warning(
                "Database not available (reason: %s). Retrying in %s seconds...",
                reason,
                retry_interval,
            )
            attempts += 1
            if attempts >= max_retries:
                raise RuntimeError(
                    "Maximum attempts to connect to the PostgreSQL server exceeded. "
                    f"Tried {max_retries} times at {retry_interval}s intervals for {config_label}. "
                    f"Last error: {reason}"
                ) from exc
            time.sleep(retry_interval)
        except Exception:
            raise

    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_config["database_name"],))
    exists = cur.fetchone()

    if not exists:
        database_name = _validate_pg_identifier(target_config["database_name"], field="database_name")
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        logger.info("Database '%s' has been created.", database_name)
    else:
        logger.info("Database '%s' already exists.", target_config["database_name"])

    cur.close()
    conn.close()


def _metadata_schema(driver: str, schema_name: str) -> str | None:
    return schema_name if driver.startswith("postgresql") else None


def _attach_search_path(engine, search_path: tuple[str, ...]) -> None:
    if not search_path:
        return

    deduped: list[str] = []
    seen: set[str] = set()
    for schema_name in search_path:
        normalized = str(schema_name).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)

    statement = "SET search_path TO " + ", ".join(_quote_identifier(schema_name) for schema_name in deduped)

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(statement)
        finally:
            cursor.close()


if _database_autocreate_enabled():
    create_database_if_not_exists(config_label="DATABASE")
else:
    logger.info(
        "Skipping automatic PostgreSQL database creation at startup. "
        "Unset OMLORIX_AUTO_CREATE_DATABASES or set it to true to enable bootstrap creation."
    )


def _enable_sqlite_foreign_keys(engine) -> None:
    if not str(engine.url.drivername).startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

engine = create_engine(DATABASE_URL)
audit_engine = create_engine(AUDIT_DATABASE_URL)

_enable_sqlite_foreign_keys(engine)
_enable_sqlite_foreign_keys(audit_engine)

if _is_postgres(DATABASE_CONFIG):
    _attach_search_path(engine, (DATABASE_SCHEMA, LOGS_DATABASE_SCHEMA, AUDIT_DATABASE_SCHEMA, "public"))
if _is_postgres(AUDIT_DATABASE_CONFIG):
    _attach_search_path(audit_engine, (AUDIT_DATABASE_SCHEMA, LOGS_DATABASE_SCHEMA, DATABASE_SCHEMA, "public"))

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
AuditSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=audit_engine)
Base = declarative_base(metadata=MetaData(schema=_metadata_schema(DATABASE_CONFIG["driver"], DATABASE_SCHEMA)))
AuditBase = declarative_base(
    metadata=MetaData(schema=_metadata_schema(AUDIT_DATABASE_CONFIG["driver"], AUDIT_DATABASE_SCHEMA))
)
