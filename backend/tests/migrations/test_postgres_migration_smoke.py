from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text


pytestmark = pytest.mark.skipif(
    os.getenv("CI_POSTGRES_MIGRATION_SMOKE") != "1",
    reason="Postgres migration smoke test only runs in the CI migration job.",
)


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _alembic_heads(config_filename: str) -> tuple[str, ...]:
    backend_root = _backend_root()
    config = Config(str(backend_root / config_filename))

    if config_filename == "alembic_main.ini":
        script_location = backend_root / "alembic_main"
    elif config_filename == "alembic_audit.ini":
        script_location = backend_root / "alembic_audit"
    else:
        raise AssertionError(f"Unsupported Alembic config: {config_filename}")

    config.set_main_option("script_location", str(script_location))
    return tuple(
        sorted(str(head) for head in ScriptDirectory.from_config(config).get_heads())
    )


def _version_rows(target_engine, schema_name: str, table_name: str) -> tuple[str, ...]:
    with target_engine.connect() as connection:
        rows = connection.execute(
            text(
                f'SELECT version_num FROM "{schema_name}"."{table_name}" ORDER BY version_num'
            )
        ).fetchall()
    return tuple(sorted(str(row[0]) for row in rows))


def _assert_table_exists(target_engine, schema_name: str, table_name: str) -> None:
    with target_engine.connect() as connection:
        exists = connection.execute(
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
    assert bool(exists)


def _assert_column_missing(
    target_engine, schema_name: str, table_name: str, column_name: str
) -> None:
    """Verify removed persistent fields do not survive the migration chain."""

    with target_engine.connect() as connection:
        exists = connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = :schema_name
                      AND table_name = :table_name
                      AND column_name = :column_name
                )
                """
            ),
            {
                "schema_name": schema_name,
                "table_name": table_name,
                "column_name": column_name,
            },
        ).scalar()
    assert not bool(exists)


def _assert_column_exists(
    target_engine, schema_name: str, table_name: str, column_name: str
) -> None:
    """Verify a persistent field is present after applying every migration."""

    with target_engine.connect() as connection:
        exists = connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = :schema_name
                      AND table_name = :table_name
                      AND column_name = :column_name
                )
                """
            ),
            {
                "schema_name": schema_name,
                "table_name": table_name,
                "column_name": column_name,
            },
        ).scalar()
    assert bool(exists)


def _partition_count(
    target_engine,
    schema_name: str,
    parent_tables: tuple[str, ...],
) -> int:
    with target_engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM pg_inherits inheritance
                    JOIN pg_class parent ON parent.oid = inheritance.inhparent
                    JOIN pg_namespace parent_namespace
                      ON parent_namespace.oid = parent.relnamespace
                    WHERE parent_namespace.nspname = :schema_name
                      AND parent.relname = ANY(:parent_tables)
                    """
                ),
                {
                    "schema_name": schema_name,
                    "parent_tables": list(parent_tables),
                },
            ).scalar_one()
        )


def test_postgres_migration_runner_reaches_heads_and_ready_state():
    from app.database import (
        AUDIT_DATABASE_SCHEMA,
        DATABASE_SCHEMA,
        LOGS_DATABASE_SCHEMA,
        audit_engine,
        engine,
    )
    from app.migrations.runner import run_all_migrations

    assert run_all_migrations() is True

    assert _version_rows(engine, DATABASE_SCHEMA, "alembic_version") == _alembic_heads(
        "alembic_main.ini"
    )
    assert _version_rows(
        audit_engine, AUDIT_DATABASE_SCHEMA, "alembic_version_audit"
    ) == _alembic_heads("alembic_audit.ini")

    _assert_table_exists(engine, DATABASE_SCHEMA, "users")
    _assert_table_exists(
        engine,
        DATABASE_SCHEMA,
        "import_staging_reservations",
    )
    _assert_table_exists(engine, DATABASE_SCHEMA, "memory_profiles")
    _assert_table_exists(engine, DATABASE_SCHEMA, "memory_deletions")
    _assert_table_exists(engine, DATABASE_SCHEMA, "memory_states")
    _assert_column_exists(engine, DATABASE_SCHEMA, "memory_profiles", "source_revision")
    _assert_column_missing(engine, DATABASE_SCHEMA, "memory_profiles", "last_run_status")
    for column_name in (
        "memory_key",
        "stability",
        "last_confirmed_at",
        "review_at",
        "expires_at",
    ):
        _assert_column_exists(engine, DATABASE_SCHEMA, "memories", column_name)
    _assert_table_exists(engine, LOGS_DATABASE_SCHEMA, "adminnotifications")
    _assert_table_exists(audit_engine, AUDIT_DATABASE_SCHEMA, "logs")
    _assert_table_exists(audit_engine, AUDIT_DATABASE_SCHEMA, "authenticationlogs")

    from app.main import root_readiness_check

    readiness = root_readiness_check()
    assert readiness["status"] == "ready"
    assert readiness["checks"]["database"] == "ok"
    expected_redis = "disabled" if os.getenv("REDIS_ENABLED", "true").lower() == "false" else "ok"
    assert readiness["checks"]["redis"] == expected_redis


def test_fresh_postgres_prepares_audit_partitions_before_security_events(monkeypatch):
    from app import main as app_main
    from app.auth import jwt_material
    from app.database import AUDIT_DATABASE_SCHEMA, audit_engine
    from app.logging import models as logging_models

    parent_tables = ("logs", "authenticationlogs")
    assert _partition_count(audit_engine, AUDIT_DATABASE_SCHEMA, parent_tables) == 0

    class StartupEventsRecorded(BaseException):
        pass

    monkeypatch.setenv("JWT_SECRET_KEY", "fresh-postgres-startup-test-signing-key-" + ("x" * 64))
    jwt_material.get_jwt_material.cache_clear()
    monkeypatch.setattr(jwt_material, "revoke_all_sessions", lambda: None)
    monkeypatch.setattr(app_main, "log_startup_status", lambda: None)
    monkeypatch.setattr(app_main, "ensure_data_directories", lambda: None)
    monkeypatch.setattr(app_main, "ensure_backup_directories", lambda: None)
    monkeypatch.setattr(app_main, "initialize_settings", lambda _db: None)
    monkeypatch.setattr(app_main, "validate_ip_hash_salt_configuration", lambda: None)

    def stop_after_security_events(_db):
        raise StartupEventsRecorded

    monkeypatch.setattr(app_main, "initialize_concurrency_metrics", stop_after_security_events)

    async def enter_lifespan() -> None:
        async with app_main.lifespan(app_main.app):
            pass

    def run_startup(_index: int) -> None:
        try:
            asyncio.run(enter_lifespan())
        except StartupEventsRecorded:
            return
        raise AssertionError("startup continued past the security-event checkpoint")

    # Independent replicas can race through fresh startup. PostgreSQL advisory
    # locks must serialize their partition DDL without rejecting either one.
    logging_models._KNOWN_MONTHLY_PARTITIONS.clear()
    logging_models._PARTITIONED_TABLE_CACHE.clear()
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run_startup, range(2)))

    assert _partition_count(audit_engine, AUDIT_DATABASE_SCHEMA, parent_tables) == 6

    # A partition prepared during the initial startup must also accept the
    # first startup event after the calendar advances into the next month.
    now = datetime.now(timezone.utc)
    next_month = (
        now.replace(
            year=now.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        if now.month == 12
        else now.replace(
            month=now.month + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    )

    class NextMonthDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return next_month if tz is not None else next_month.replace(tzinfo=None)

    monkeypatch.setattr(logging_models, "datetime", NextMonthDateTime)
    run_startup(2)

    with audit_engine.connect() as connection:
        persisted_events = list(
            connection.execute(
                select(logging_models.Logs.action, logging_models.Logs.timestamp)
                .where(
                    logging_models.Logs.action.in_(
                        (
                            "APPLICATION_START",
                            "AUTH_SIGNING_KEY_CHANGED",
                        )
                    )
                )
                .order_by(logging_models.Logs.timestamp)
            )
        )

    start_timestamps = [
        timestamp
        for action, timestamp in persisted_events
        if action == "APPLICATION_START"
    ]
    assert len(start_timestamps) == 3
    assert sum(timestamp >= next_month for timestamp in start_timestamps) == 1
    assert sum(
        action == "AUTH_SIGNING_KEY_CHANGED" for action, _timestamp in persisted_events
    ) == 1
