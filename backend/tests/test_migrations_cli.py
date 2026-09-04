import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    Column,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import database
from app.database import AuditBase, Base
from app.logging.models import Logs
from app.migrations import cli, runner
from app.users import erasure_ledger
from app.workers import events as worker_events
from app.workers.models import AuditEventOutbox


def test_ensure_main_version_state_refuses_to_auto_stamp(monkeypatch):
    stamped = []

    monkeypatch.setattr(
        runner, "_get_alembic_heads", lambda config_file: ("main-head",)
    )
    monkeypatch.setattr(runner, "_version_rows", lambda *args, **kwargs: ())
    monkeypatch.setattr(runner, "_missing_tables", lambda *args, **kwargs: ())
    monkeypatch.setattr(
        runner, "_stamp_alembic", lambda *args, **kwargs: stamped.append(True)
    )

    with pytest.raises(RuntimeError) as exc_info:
        runner._ensure_main_version_state()

    message = str(exc_info.value)
    assert "Refusing to stamp the revision automatically" in message
    assert "repair-version-state --config alembic_main.ini" in message
    assert stamped == []


def test_repair_main_version_state_validates_before_stamping(monkeypatch):
    steps = []

    monkeypatch.setattr(
        runner,
        "_validate_schema_matches_metadata",
        lambda **kwargs: steps.append("validate"),
    )
    monkeypatch.setattr(
        runner, "_get_alembic_heads", lambda config_file: ("main-head",)
    )
    monkeypatch.setattr(runner, "_version_rows", lambda *args, **kwargs: ())
    monkeypatch.setattr(
        runner, "_stamp_alembic", lambda *args, **kwargs: steps.append("stamp")
    )

    stamped = runner._repair_main_version_state()

    assert stamped is True
    assert steps == ["validate", "stamp"]


def test_migration_graphs_expose_only_the_expected_heads():
    """Both migration histories must expose their single expected head."""

    assert runner._get_alembic_heads("alembic_main.ini") == (
        "workspace_reads_20260904",
    )
    assert runner._get_alembic_heads("alembic_audit.ini") == (
        "audit_subject_fence_20260830",
    )


def test_migration_reconciliation_purges_already_delivered_unfenced_event(
    monkeypatch,
):
    main_engine = create_engine("sqlite:///:memory:")
    audit_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=main_engine, tables=[AuditEventOutbox.__table__])
    AuditBase.metadata.create_all(bind=audit_engine, tables=[Logs.__table__])
    main_factory = sessionmaker(bind=main_engine)
    audit_factory = sessionmaker(bind=audit_engine)
    monkeypatch.setattr(database, "SessionLocal", main_factory)
    monkeypatch.setattr(database, "AuditSessionLocal", audit_factory)
    occurred_at = datetime.now(timezone.utc)

    main_db = main_factory()
    audit_db = audit_factory()
    try:
        main_db.add(
            AuditEventOutbox(
                id="legacy-delivered-event",
                user_id="",
                action="LEGACY_EVENT",
                category="users",
                subjects_indexed=True,
                status="delivered",
                error_code="migration_privacy_fence",
                occurred_at=occurred_at,
            )
        )
        audit_db.add(
            Logs(
                id="legacy-delivered-event",
                user_id="already-erased-user",
                action="LEGACY_EVENT",
                category="users",
                details={"owner_id": "already-erased-user"},
                timestamp=occurred_at,
                share_refs_scrubbed=True,
                subject_fenced=True,
            )
        )
        main_db.commit()
        audit_db.commit()
    finally:
        main_db.close()
        audit_db.close()

    assert runner._purge_unfenced_legacy_audit_records() == 1
    assert runner._purge_unfenced_legacy_audit_records() == 0

    main_db = main_factory()
    audit_db = audit_factory()
    try:
        assert audit_db.query(Logs).count() == 0
        assert main_db.query(AuditEventOutbox).one().error_code == (
            "migration_privacy_purged"
        )
    finally:
        audit_db.close()
        main_db.close()


def test_external_restore_marker_overrides_restored_sql_checkpoint(monkeypatch):
    steps = []
    monkeypatch.setattr(runner, "migration_mode", lambda: "auto")
    monkeypatch.setattr(runner, "_is_postgres_configured", lambda: False)
    monkeypatch.setattr(
        runner,
        "_bootstrap_sqlite_schema",
        lambda: steps.append("schema"),
    )
    monkeypatch.setattr(
        erasure_ledger,
        "restore_erasure_reconciliation_pending",
        lambda: True,
    )
    monkeypatch.setattr(
        erasure_ledger,
        "reconcile_completed_user_erasures_after_restore",
        lambda: steps.append("full_restore_reconciliation") or {},
    )
    monkeypatch.setattr(
        erasure_ledger,
        "resolve_pending_user_erasure_intents",
        lambda: steps.append("live_intent_resolution") or {},
    )
    monkeypatch.setattr(
        erasure_ledger,
        "reconcile_completed_audit_erasures",
        lambda: steps.append("checkpointed_audit_reconciliation")
        or {
            "subjects_reconciled": 0,
            "audit_logs_deleted": 0,
            "notifications_deleted": 0,
        },
    )
    monkeypatch.setattr(
        worker_events,
        "reconcile_pending_audit_erasures",
        lambda: steps.append("audit_handoffs") or 0,
    )

    assert runner.run_all_migrations() is False
    assert steps == [
        "schema",
        "full_restore_reconciliation",
        "audit_handoffs",
        "checkpointed_audit_reconciliation",
    ]


def test_runtime_partition_tables_are_ignored_by_schema_validation():
    assert (
        runner._should_ignore_reflected_table(
            "adminnotifications_2026_05", runner.LOGS_DATABASE_SCHEMA
        )
        is True
    )
    assert (
        runner._should_ignore_reflected_table(
            "logs_2026_05", runner.AUDIT_DATABASE_SCHEMA
        )
        is True
    )
    assert (
        runner._should_ignore_reflected_table("users_2026_05", runner.DATABASE_SCHEMA)
        is False
    )


def test_default_schema_reflection_duplicates_are_ignored():
    metadata = MetaData()
    table = Table("users", metadata, Column("id", String, primary_key=True))

    assert runner._should_ignore_reflected_object(table, "users", "table", None) is True


def test_manual_trigram_indexes_are_ignored_by_schema_validation():
    metadata = MetaData(schema=runner.DATABASE_SCHEMA)
    table = Table("chat_messages", metadata, Column("content", String))
    index = Index("ix_chat_messages_content_trgm", table.c.content)

    assert (
        runner._should_ignore_reflected_object(
            index,
            "ix_chat_messages_content_trgm",
            "index",
            runner.DATABASE_SCHEMA,
        )
        is True
    )


def test_redundant_primary_key_unique_constraint_diffs_are_filtered():
    metadata = MetaData(schema=runner.DATABASE_SCHEMA)
    table = Table(
        "files",
        metadata,
        Column("id", String, primary_key=True),
        UniqueConstraint("id"),
    )
    diff = (
        "add_constraint",
        next(c for c in table.constraints if isinstance(c, UniqueConstraint)),
    )

    assert runner._filter_schema_diffs([diff]) == []


def test_cmd_repair_version_state_prints_result(monkeypatch, capsys):
    monkeypatch.setattr(runner, "repair_version_state", lambda **kwargs: True)

    exit_code = cli._cmd_repair_version_state(
        SimpleNamespace(config="alembic_main.ini", no_lock=True)
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == '{"stamped": true}'


def test_build_parser_supports_repair_version_state():
    parser = cli.build_parser()

    args = parser.parse_args(
        ["repair-version-state", "--config", "alembic_main.ini", "--no-lock"]
    )

    assert args.command == "repair-version-state"
    assert args.config == "alembic_main.ini"
    assert args.no_lock is True
