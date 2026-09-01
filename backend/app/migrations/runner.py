from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
import importlib
from pathlib import Path
import re

from alembic.autogenerate import api as alembic_autogenerate
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import UniqueConstraint, text, tuple_

from app.database import (
    AUDIT_DATABASE_SCHEMA,
    AuditBase,
    DATABASE_SCHEMA,
    DATABASE_CONFIG,
    LOGS_DATABASE_SCHEMA,
    Base,
    audit_engine,
    engine,
)
from app.model_modules import MODEL_MODULES


logger = logging.getLogger(__name__)


_MIGRATION_LOCK_ID = 815427061
_MIGRATION_MODE_OFF = {"off", "disabled", "false", "0"}
_MIGRATION_MODE_ON = {"auto", "on", "run", "true", "1"}
_MAIN_VERSION_TABLE = "alembic_version"
_AUDIT_VERSION_TABLE = "alembic_version_audit"
_RUNTIME_PARTITION_TABLE_RE = re.compile(r"^(adminnotifications|logs|authenticationlogs)_\d{4}_\d{2}$")
_MANUAL_SCHEMA_INDEXES = {
    "ix_chat_messages_content_trgm",
    "ix_chats_title_trgm",
}
_CANONICAL_EQUIVALENT_INDEXES = {
    "ux_users_email_canonical",
}


class MigrationLockTimeoutError(RuntimeError):
    pass


def migration_mode() -> str:
    raw = str(os.getenv("DB_MIGRATIONS_MODE") or "auto").strip().lower()
    if raw in _MIGRATION_MODE_OFF:
        return "off"
    if raw in _MIGRATION_MODE_ON:
        return "run"
    raise RuntimeError(
        "Invalid DB_MIGRATIONS_MODE value. Supported values: auto, run, on, true, off, disabled, false."
    )


def migration_lock_timeout_seconds() -> int:
    raw = str(os.getenv("DB_MIGRATIONS_LOCK_TIMEOUT_SECONDS") or "120").strip()
    try:
        return max(1, int(raw))
    except ValueError as exc:
        raise RuntimeError(
            "Invalid DB_MIGRATIONS_LOCK_TIMEOUT_SECONDS value. Use a positive integer, e.g. 120."
        ) from exc


def _is_postgres_configured() -> bool:
    return str(DATABASE_CONFIG.get("driver") or "").lower().startswith("postgres")


def _import_model_modules() -> None:
    """Import all model modules so metadata is fully populated."""
    for module_name in MODEL_MODULES:
        importlib.import_module(module_name)


def _bootstrap_sqlite_schema() -> None:
    """Create the current schema directly for SQLite-based desktop/dev installs.

    Alembic migrations in this project contain PostgreSQL-oriented DDL that is not
    portable to SQLite. For a fresh local SQLite install, creating the current
    metadata is sufficient and avoids migration-time failures.
    """
    logger.info("SQLite backend detected; bootstrapping schema from SQLAlchemy metadata instead of Alembic")
    _import_model_modules()
    _dedupe_metadata_indexes(Base.metadata)
    _dedupe_metadata_indexes(AuditBase.metadata)
    Base.metadata.create_all(bind=engine)
    AuditBase.metadata.create_all(bind=audit_engine)


def _dedupe_metadata_indexes(metadata) -> None:
    """Remove duplicate index declarations with the same generated name.

    Some models declare the same index twice via ``index=True`` and an explicit
    ``Index(...)`` entry. PostgreSQL migrations handle this through Alembic, but
    direct SQLite schema creation needs the metadata cleaned up first.
    """
    for table in metadata.tables.values():
        seen_names: set[str] = set()
        duplicates = []
        for index in list(table.indexes):
            index_name = str(index.name or "")
            if not index_name:
                continue
            if index_name in seen_names:
                duplicates.append(index)
                continue
            seen_names.add(index_name)
        for duplicate in duplicates:
            table.indexes.discard(duplicate)


@contextmanager
def migration_lock(timeout_seconds: int):
    if not _is_postgres_configured():
        yield
        return

    import psycopg2

    connect_kwargs = {
        "dbname": DATABASE_CONFIG.get("database_name"),
        "user": DATABASE_CONFIG.get("database_user"),
        "password": DATABASE_CONFIG.get("database_password"),
        "host": DATABASE_CONFIG.get("database_host"),
        "port": DATABASE_CONFIG.get("database_port"),
    }

    conn = psycopg2.connect(**connect_kwargs)
    conn.autocommit = True
    lock_acquired = False
    started_at = time.time()
    try:
        with conn.cursor() as cursor:
            while time.time() - started_at < timeout_seconds:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", (_MIGRATION_LOCK_ID,))
                row = cursor.fetchone()
                if row and bool(row[0]):
                    lock_acquired = True
                    break
                time.sleep(1.0)

        if not lock_acquired:
            raise MigrationLockTimeoutError(
                "Timed out waiting for migration lock. "
                "Another migration process is likely running. "
                f"Increase DB_MIGRATIONS_LOCK_TIMEOUT_SECONDS (current={timeout_seconds})."
            )

        yield
    finally:
        if lock_acquired:
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_ID,))
            except Exception:
                logger.warning("Failed to release migration lock", exc_info=True)
        conn.close()


def _run_alembic(config_file: str, target: str = "heads") -> None:
    config = _build_alembic_config(config_file)
    logger.info("Running migration: alembic -c %s upgrade %s", Path(config.config_file_name).name, target)
    command.upgrade(config, target)


def _build_alembic_config(config_file: str) -> Config:
    root = Path(__file__).resolve().parents[2]
    config_path = root / config_file
    if not config_path.exists():
        raise RuntimeError(f"Missing Alembic config file: {config_path}")

    config = Config(str(config_path))
    if config_file == "alembic_main.ini":
        script_location = root / "alembic_main"
    elif config_file == "alembic_audit.ini":
        script_location = root / "alembic_audit"
    else:
        raise RuntimeError(f"Unsupported Alembic config: {config_file}")

    config.set_main_option("script_location", str(script_location))
    return config


def _get_alembic_heads(config_file: str) -> tuple[str, ...]:
    config = _build_alembic_config(config_file)
    script = ScriptDirectory.from_config(config)
    return tuple(script.get_heads())


def _stamp_alembic(config_file: str, target: str = "heads", *, purge: bool = False) -> None:
    config = _build_alembic_config(config_file)
    logger.warning("Stamping Alembic revision state: alembic -c %s stamp %s", Path(config.config_file_name).name, target)
    command.stamp(config, target, purge=purge)


def _version_rows(schema_name: str, table_name: str, target_engine=engine) -> tuple[str, ...]:
    with target_engine.connect() as connection:
        exists = bool(
            connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = :schema_name
                          AND table_name = :table_name
                    )
                    """
                ),
                {"schema_name": schema_name, "table_name": table_name},
            ).scalar()
        )
        if not exists:
            return ()

        rows = connection.execute(
            text(
                f'SELECT version_num FROM "{schema_name}"."{table_name}" ORDER BY version_num'
            )
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _table_exists(target_engine, schema_name: str, table_name: str) -> bool:
    with target_engine.connect() as connection:
        return bool(
            connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = :schema_name
                          AND table_name = :table_name
                    )
                    """
                ),
                {"schema_name": schema_name, "table_name": table_name},
            ).scalar()
        )


def _missing_tables(target_engine, schema_name: str, table_names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(table_name for table_name in table_names if not _table_exists(target_engine, schema_name, table_name))


def _describe_schema_diff(diff) -> str:
    if not isinstance(diff, tuple) or not diff:
        return repr(diff)

    kind = str(diff[0])
    if kind in {"add_table", "remove_table"} and len(diff) >= 2:
        table = diff[1]
        schema = getattr(table, "schema", None) or "default"
        name = getattr(table, "name", repr(table))
        action = "missing table" if kind == "add_table" else "unexpected table"
        return f"{action} {schema}.{name}"

    if kind in {"add_column", "remove_column"} and len(diff) >= 4:
        schema_name, table_name, column = diff[1], diff[2], diff[3]
        column_name = getattr(column, "name", repr(column))
        action = "missing column" if kind == "add_column" else "unexpected column"
        return f"{action} {schema_name}.{table_name}.{column_name}"

    if kind in {"add_index", "remove_index"} and len(diff) >= 2:
        index = diff[1]
        table_name = getattr(getattr(index, "table", None), "name", "unknown")
        schema_name = getattr(getattr(index, "table", None), "schema", None) or "default"
        index_name = getattr(index, "name", repr(index))
        action = "missing index" if kind == "add_index" else "unexpected index"
        return f"{action} {schema_name}.{table_name}.{index_name}"

    if kind in {"add_constraint", "remove_constraint"} and len(diff) >= 2:
        constraint = diff[1]
        table = getattr(constraint, "table", None)
        table_name = getattr(table, "name", "unknown")
        schema_name = getattr(table, "schema", None) or "default"
        constraint_name = getattr(constraint, "name", repr(constraint))
        action = "missing constraint" if kind == "add_constraint" else "unexpected constraint"
        return f"{action} {schema_name}.{table_name}.{constraint_name}"

    return repr(diff)


def _format_schema_diffs(diffs, *, limit: int = 8) -> str:
    descriptions = [_describe_schema_diff(diff) for diff in diffs[:limit]]
    if len(diffs) > limit:
        descriptions.append(f"... and {len(diffs) - limit} more differences")
    return "; ".join(descriptions)


def _should_ignore_reflected_table(name: str, schema_name: str | None) -> bool:
    if not _RUNTIME_PARTITION_TABLE_RE.match(name):
        return False
    return schema_name in {LOGS_DATABASE_SCHEMA, AUDIT_DATABASE_SCHEMA}


def _reflected_schema_name(object_, type_: str) -> str | None:
    """Return the schema for a reflected Alembic comparison object."""
    table = object_ if type_ == "table" else getattr(object_, "table", None)
    return getattr(table, "schema", None)


def _should_ignore_reflected_object(object_, name: str, type_: str, schema_name: str | None) -> bool:
    """Skip reflected objects that are expected but not useful for validation.

    PostgreSQL reflects search-path tables twice during metadata comparison:
    once with their real schema and once as the implicit default schema.  The
    SQLAlchemy metadata for PostgreSQL is schema-qualified, so default-schema
    reflections are duplicates rather than missing metadata.
    """
    if schema_name is None:
        return True

    if type_ == "table" and _should_ignore_reflected_table(name, schema_name):
        return True

    if type_ == "index" and name in _MANUAL_SCHEMA_INDEXES:
        return True

    if type_ == "index" and name in _CANONICAL_EQUIVALENT_INDEXES:
        return True

    return False


def _is_redundant_primary_key_unique_constraint(diff, metadata=None) -> bool:
    """Return whether Alembic reported a harmless unique-on-primary-key diff."""
    if not isinstance(diff, tuple) or len(diff) < 2 or diff[0] != "add_constraint":
        return False

    constraint = diff[1]
    if not isinstance(constraint, UniqueConstraint):
        return False

    columns = tuple(getattr(constraint, "columns", ()) or ())
    if not columns:
        return False

    table = getattr(constraint, "table", None)
    primary_key_columns = set()
    if table is not None:
        primary_key_columns = {
            str(getattr(column, "name", ""))
            for column in getattr(getattr(table, "primary_key", None), "columns", ())
            if getattr(column, "name", None)
        }

    constraint_columns = {
        str(getattr(column, "name", ""))
        for column in columns
        if getattr(column, "name", None)
    }
    if primary_key_columns and constraint_columns:
        return constraint_columns.issubset(primary_key_columns)

    if metadata is not None and table is not None and constraint_columns:
        table_name = str(getattr(table, "name", ""))
        schema_name = getattr(table, "schema", None)
        metadata_keys = [f"{schema_name}.{table_name}" if schema_name else table_name, table_name]
        for metadata_key in metadata_keys:
            metadata_table = metadata.tables.get(metadata_key)
            if metadata_table is None:
                continue
            metadata_primary_key_columns = {
                str(getattr(column, "name", ""))
                for column in getattr(metadata_table.primary_key, "columns", ())
                if getattr(column, "name", None)
            }
            if metadata_primary_key_columns and constraint_columns.issubset(metadata_primary_key_columns):
                return True

    return all(bool(getattr(column, "primary_key", False)) for column in columns)


def _is_known_equivalent_index_diff(diff) -> bool:
    """Return whether Alembic reported a known canonical-expression index diff."""
    if not isinstance(diff, tuple) or len(diff) < 2:
        return False
    if diff[0] not in {"add_index", "remove_index"}:
        return False
    index = diff[1]
    return str(getattr(index, "name", "")) in _CANONICAL_EQUIVALENT_INDEXES


def _filter_schema_diffs(diffs, *, metadata=None) -> list:
    """Remove known-safe Alembic comparison noise from schema validation."""
    filtered = []
    for diff in diffs:
        if _is_redundant_primary_key_unique_constraint(diff, metadata=metadata):
            continue
        if _is_known_equivalent_index_diff(diff):
            continue
        filtered.append(diff)
    return filtered


def _validate_schema_matches_metadata(
    *,
    metadata,
    target_engine,
    version_table: str,
    version_table_schema: str,
    schema_label: str,
    managed_schemas: set[str],
) -> None:
    _import_model_modules()
    _dedupe_metadata_indexes(metadata)

    def _include_object(object_, name, type_, reflected, compare_to):
        del compare_to
        schema_name = _reflected_schema_name(object_, type_)
        if reflected and schema_name is not None and schema_name not in managed_schemas:
            return False
        if reflected and _should_ignore_reflected_object(object_, name, type_, schema_name):
            return False
        return True

    with target_engine.connect() as connection:
        context = MigrationContext.configure(
            connection=connection,
            opts={
                "target_metadata": metadata,
                "compare_type": True,
                "compare_server_default": True,
                "include_schemas": True,
                "include_object": _include_object,
                "version_table": version_table,
                "version_table_schema": version_table_schema,
            },
        )
        diffs = alembic_autogenerate.compare_metadata(context, metadata)
    diffs = _filter_schema_diffs(diffs, metadata=metadata)

    if diffs:
        raise RuntimeError(
            f"{schema_label} schema does not match SQLAlchemy metadata; refusing to stamp Alembic heads. "
            f"Differences: {_format_schema_diffs(diffs)}"
        )


def _raise_stale_version_state(
    *,
    schema_label: str,
    config_file: str,
    current_rows: tuple[str, ...],
    expected_heads: tuple[str, ...],
    detail: str | None = None,
) -> None:
    message = (
        f"{schema_label} Alembic version state is stale after migrations. "
        f"Current={current_rows} Expected={expected_heads}. "
        "Refusing to stamp the revision automatically. "
        f"Use `python -m app.migrations.cli repair-version-state --config {config_file}` "
        "only after verifying the schema is complete."
    )
    if detail:
        message = f"{message} {detail}"
    raise RuntimeError(message)


def _ensure_main_version_state() -> None:
    expected_heads = _get_alembic_heads("alembic_main.ini")
    current_rows = _version_rows(DATABASE_SCHEMA, _MAIN_VERSION_TABLE)
    if tuple(sorted(current_rows)) == tuple(sorted(expected_heads)):
        return

    required_tables = (
        "users",
        "settings",
        "adminnotifications",
    )
    app_missing = _missing_tables(engine, DATABASE_SCHEMA, required_tables[:2])
    logs_missing = _missing_tables(engine, LOGS_DATABASE_SCHEMA, required_tables[2:])
    if app_missing or logs_missing:
        _raise_stale_version_state(
            schema_label="Main schema",
            config_file="alembic_main.ini",
            current_rows=current_rows,
            expected_heads=expected_heads,
            detail=(
                "Missing app tables: "
                f"{app_missing or 'none'}, missing logs tables: {logs_missing or 'none'}."
            ),
        )

    _raise_stale_version_state(
        schema_label="Main schema",
        config_file="alembic_main.ini",
        current_rows=current_rows,
        expected_heads=expected_heads,
    )


def _ensure_audit_schema_state() -> None:
    expected_heads = _get_alembic_heads("alembic_audit.ini")
    missing_tables = _missing_tables(
        audit_engine,
        AUDIT_DATABASE_SCHEMA,
        ("logs", "authenticationlogs", "authlogdeletionqueue", "auditlogdeletionqueue"),
    )
    current_rows = _version_rows(AUDIT_DATABASE_SCHEMA, _AUDIT_VERSION_TABLE, audit_engine)
    if missing_tables:
        _raise_stale_version_state(
            schema_label="Audit schema",
            config_file="alembic_audit.ini",
            current_rows=current_rows,
            expected_heads=expected_heads,
            detail=f"Missing audit tables: {', '.join(missing_tables)}.",
        )

    if tuple(sorted(current_rows)) == tuple(sorted(expected_heads)):
        return

    _raise_stale_version_state(
        schema_label="Audit schema",
        config_file="alembic_audit.ini",
        current_rows=current_rows,
        expected_heads=expected_heads,
    )


def _repair_main_version_state() -> bool:
    _validate_schema_matches_metadata(
        metadata=Base.metadata,
        target_engine=engine,
        version_table=_MAIN_VERSION_TABLE,
        version_table_schema=DATABASE_SCHEMA,
        schema_label="Main schema",
        managed_schemas={DATABASE_SCHEMA, LOGS_DATABASE_SCHEMA},
    )
    expected_heads = _get_alembic_heads("alembic_main.ini")
    current_rows = _version_rows(DATABASE_SCHEMA, _MAIN_VERSION_TABLE)
    if tuple(sorted(current_rows)) == tuple(sorted(expected_heads)):
        logger.info("Main Alembic version state already matches heads")
        return False

    logger.warning(
        "Stamping validated main schema Alembic version state. Current=%s Expected=%s.",
        current_rows,
        expected_heads,
    )
    _stamp_alembic("alembic_main.ini", "heads")
    return True


def _repair_audit_version_state() -> bool:
    _validate_schema_matches_metadata(
        metadata=AuditBase.metadata,
        target_engine=audit_engine,
        version_table=_AUDIT_VERSION_TABLE,
        version_table_schema=AUDIT_DATABASE_SCHEMA,
        schema_label="Audit schema",
        managed_schemas={AUDIT_DATABASE_SCHEMA},
    )
    expected_heads = _get_alembic_heads("alembic_audit.ini")
    current_rows = _version_rows(AUDIT_DATABASE_SCHEMA, _AUDIT_VERSION_TABLE, audit_engine)
    if tuple(sorted(current_rows)) == tuple(sorted(expected_heads)):
        logger.info("Audit Alembic version state already matches heads")
        return False

    logger.warning(
        "Stamping validated audit schema Alembic version state. Current=%s Expected=%s.",
        current_rows,
        expected_heads,
    )
    _stamp_alembic("alembic_audit.ini", "heads")
    return True


def _run_main_migrations() -> None:
    _run_alembic("alembic_main.ini")
    _ensure_main_version_state()


def _run_audit_migrations() -> None:
    _run_alembic("alembic_audit.ini")
    _ensure_audit_schema_state()


def _purge_unfenced_legacy_audit_records(*, batch_size: int = 500) -> int:
    """Remove audit rows delivered by pre-fence outbox events, idempotently."""

    from app.database import AuditSessionLocal, SessionLocal
    from app.logging.models import Logs
    from app.workers.models import AuditEventOutbox

    main_db = SessionLocal()
    audit_db = AuditSessionLocal()
    purged = 0
    try:
        while True:
            rows = (
                main_db.query(AuditEventOutbox.id, AuditEventOutbox.occurred_at)
                .filter(
                    AuditEventOutbox.error_code == "migration_privacy_fence"
                )
                .order_by(AuditEventOutbox.id.asc())
                .limit(max(1, min(int(batch_size), 5000)))
                .all()
            )
            if not rows:
                break
            event_ids = [str(event_id) for event_id, _occurred_at in rows]
            identities = [
                (str(event_id), occurred_at)
                for event_id, occurred_at in rows
            ]
            deleted = (
                audit_db.query(Logs)
                .filter(tuple_(Logs.id, Logs.timestamp).in_(identities))
                .delete(synchronize_session=False)
            )
            audit_db.commit()
            main_db.query(AuditEventOutbox).filter(
                AuditEventOutbox.id.in_(event_ids),
                AuditEventOutbox.error_code == "migration_privacy_fence",
            ).update(
                {AuditEventOutbox.error_code: "migration_privacy_purged"},
                synchronize_session=False,
            )
            main_db.commit()
            purged += int(deleted or 0)
        return purged
    except Exception:
        audit_db.rollback()
        main_db.rollback()
        raise
    finally:
        audit_db.close()
        main_db.close()


def repair_version_state(*, config_file: str | None = None, use_lock: bool = True) -> bool:
    if not _is_postgres_configured():
        logger.info("Alembic version state repair is not needed for SQLite installs")
        return False

    if config_file not in {None, "alembic_main.ini", "alembic_audit.ini"}:
        raise RuntimeError(
            "Unsupported repair target. Use one of: alembic_main.ini, alembic_audit.ini."
        )

    timeout_seconds = migration_lock_timeout_seconds()

    def _run_repair() -> bool:
        if config_file == "alembic_main.ini":
            return _repair_main_version_state()
        if config_file == "alembic_audit.ini":
            return _repair_audit_version_state()
        main_changed = _repair_main_version_state()
        audit_changed = _repair_audit_version_state()
        return main_changed or audit_changed

    if use_lock:
        with migration_lock(timeout_seconds):
            return _run_repair()

    return _run_repair()


def run_all_migrations(*, use_lock: bool = True) -> bool:
    mode = migration_mode()
    if mode == "off":
        logger.info("DB migrations disabled via DB_MIGRATIONS_MODE=off")
        return False

    def _reconcile_restore_resistant_state() -> None:
        from app.users.erasure_ledger import (
            reconcile_completed_user_erasures_after_restore,
            resolve_pending_user_erasure_intents,
            restore_erasure_reconciliation_pending,
        )
        from app.workers.events import reconcile_pending_audit_erasures

        if restore_erasure_reconciliation_pending():
            # The external marker overrides any checkpoint restored inside the
            # SQL backup. It is cleared only after user rows and both log stores
            # have been reconciled successfully.
            reconcile_completed_user_erasures_after_restore()
        else:
            resolve_pending_user_erasure_intents()
        while reconcile_pending_audit_erasures():
            pass

    if not _is_postgres_configured():
        _bootstrap_sqlite_schema()
        _reconcile_restore_resistant_state()
        from app.users.erasure_ledger import reconcile_completed_audit_erasures

        reconcile_completed_audit_erasures()
        return False

    timeout_seconds = migration_lock_timeout_seconds()

    def _migrate_and_reconcile() -> dict[str, int]:
        _run_main_migrations()
        _run_audit_migrations()
        _reconcile_restore_resistant_state()
        # The restore-resistant ledger is intentionally outside database
        # backups. Rebuild only the one-way audit fences relevant under its
        # stored policy before any application service starts. The shared
        # checkpoint also prevents a full outbox scan on every normal startup.
        from app.users.erasure_ledger import (
            audit_erasure_reconciliation_pending,
            reconcile_completed_audit_erasures,
        )

        if audit_erasure_reconciliation_pending():
            purged = _purge_unfenced_legacy_audit_records()
            if purged:
                logger.info("Purged %s legacy unfenced audit records", purged)

        return reconcile_completed_audit_erasures()

    if use_lock:
        with migration_lock(timeout_seconds):
            reconciliation = _migrate_and_reconcile()
    else:
        reconciliation = _migrate_and_reconcile()
    if reconciliation["subjects_reconciled"]:
        logger.info(
            "Reconciled %s completed audit erasures (%s logs, %s notifications)",
            reconciliation["subjects_reconciled"],
            reconciliation["audit_logs_deleted"],
            reconciliation["notifications_deleted"],
        )

    return True
