from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tarfile

from psycopg2.extensions import parse_dsn
import pytest

from app.backups import service as backup_service


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


def test_restore_migrations_use_backend_configuration_paths(tmp_path, monkeypatch):
    """Restore must locate Alembic configs in source and container layouts."""
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    main_config = backend_dir / "alembic_main.ini"
    audit_config = backend_dir / "alembic_audit.ini"
    main_config.write_text("[alembic]\nscript_location = alembic_main\n", encoding="utf-8")
    audit_config.write_text("[alembic]\nscript_location = alembic_audit\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(backup_service, "BACKEND_DIR", backend_dir)
    monkeypatch.setattr(
        backup_service.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    cache_invalidated = []
    monkeypatch.setattr(
        backup_service,
        "invalidate_settings_cache",
        lambda: cache_invalidated.append(True),
    )

    backup_service._run_migrations()

    assert [call[0] for call in calls] == [
        ["alembic", "-c", str(main_config), "upgrade", "head"],
        ["alembic", "-c", str(audit_config), "upgrade", "head"],
    ]
    assert all(call[1]["cwd"] == str(backend_dir) for call in calls)
    assert all(call[1]["check"] is True for call in calls)
    assert cache_invalidated == [True]


def test_restore_validates_all_migration_configs_before_running_alembic(
    tmp_path,
    monkeypatch,
):
    """A missing audit config must stop restore before the main migration runs."""
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "alembic_main.ini").write_text("[alembic]\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(backup_service, "BACKEND_DIR", backend_dir)
    monkeypatch.setattr(
        backup_service.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    with pytest.raises(RuntimeError, match="Missing Alembic configuration:.*alembic_audit.ini"):
        backup_service._run_migrations()

    assert calls == []


def test_directory_restore_preserves_mounted_root_and_can_roll_back(tmp_path):
    """Volume roots stay in place while their child entries are exchanged."""
    target_dir = tmp_path / "mounted-data"
    target_dir.mkdir()
    (target_dir / "original.txt").write_text("original", encoding="utf-8")
    original_inode = target_dir.stat().st_ino

    archive_source = tmp_path / "archive-source"
    archive_source.mkdir()
    (archive_source / "restored.txt").write_text("restored", encoding="utf-8")
    archive_path = tmp_path / "data.tar"
    with tarfile.open(archive_path, mode="w") as archive:
        archive.add(archive_source / "restored.txt", arcname="restored.txt")

    backup_dir = backup_service._swap_dir_from_tar(
        archive_path,
        target_dir,
        "test-job",
    )

    assert target_dir.stat().st_ino == original_inode
    assert (target_dir / "restored.txt").read_text(encoding="utf-8") == "restored"
    assert not (target_dir / "original.txt").exists()
    assert (backup_dir / "original.txt").read_text(encoding="utf-8") == "original"

    backup_service._rollback_replaced_directory_contents(target_dir, backup_dir)

    assert target_dir.stat().st_ino == original_inode
    assert (target_dir / "original.txt").read_text(encoding="utf-8") == "original"
    assert not (target_dir / "restored.txt").exists()
    assert not backup_dir.exists()


def test_directory_restore_reverses_a_partially_failed_swap(tmp_path, monkeypatch):
    """A child-move error leaves the original mounted volume contents intact."""
    target_dir = tmp_path / "mounted-data"
    target_dir.mkdir()
    (target_dir / "original.txt").write_text("original", encoding="utf-8")

    archive_source = tmp_path / "archive-source"
    archive_source.mkdir()
    (archive_source / "restored-one.txt").write_text("one", encoding="utf-8")
    (archive_source / "restored-two.txt").write_text("two", encoding="utf-8")
    archive_path = tmp_path / "data.tar"
    with tarfile.open(archive_path, mode="w") as archive:
        archive.add(archive_source / "restored-one.txt", arcname="restored-one.txt")
        archive.add(archive_source / "restored-two.txt", arcname="restored-two.txt")

    real_move = backup_service.shutil.move
    failed_once = False

    def fail_second_incoming_move(source, destination):
        nonlocal failed_once
        if not failed_once and Path(source).name == "restored-two.txt":
            failed_once = True
            raise OSError("simulated move failure")
        return real_move(source, destination)

    monkeypatch.setattr(backup_service.shutil, "move", fail_second_incoming_move)

    try:
        backup_service._swap_dir_from_tar(archive_path, target_dir, "test-job")
    except OSError as exc:
        assert "simulated move failure" in str(exc)
    else:
        raise AssertionError("The simulated child-move failure should propagate")

    assert (target_dir / "original.txt").read_text(encoding="utf-8") == "original"
    assert not (target_dir / "restored-one.txt").exists()
    assert not (target_dir / "restored-two.txt").exists()
    assert not (target_dir / ".restore_tmp_test-job").exists()
    assert not (target_dir / ".pre_restore_test-job").exists()


def test_empty_target_check_uses_the_configured_postgres_schema(monkeypatch):
    """A populated app schema must never pass the destructive empty-target gate."""
    inspected = {}
    executed = []

    class Inspector:
        def get_table_names(self, *, schema=None):
            inspected["schema"] = schema
            return ["users"]

    class Session:
        def get_bind(self):
            return object()

        def execute(self, statement):
            executed.append(str(statement))
            return _ScalarResult(1)

    monkeypatch.setattr(backup_service, "inspect", lambda bind: Inspector())
    monkeypatch.setitem(backup_service.DATABASE_CONFIG, "driver", "postgresql")

    assert backup_service.is_target_instance_empty(Session()) is False
    assert inspected["schema"] == backup_service.DATABASE_SCHEMA
    assert executed == [f'SELECT COUNT(*) FROM "{backup_service.DATABASE_SCHEMA}"."users"']


def test_postgres_restore_is_atomic_fail_fast_and_bounded(tmp_path, monkeypatch):
    """Schema replacement and archive loading must share one bounded transaction."""
    dump_path = tmp_path / "main.dump"
    dump_path.write_bytes(b"test")
    calls = []

    @contextmanager
    def isolated(_config):
        yield "omlorix-restore-test"

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if "--list" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "; archive header\n"
                    "1; 2615 100 SCHEMA - app owner\n"
                    "2; 2615 101 SCHEMA - logs owner\n"
                    "3; 1259 102 TABLE app users owner\n"
                ),
            )
        if command[0] == "pg_restore":
            Path(command[command.index("--file") + 1]).write_text(
                "CREATE SCHEMA app;",
                encoding="utf-8",
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(backup_service, "_quiesce_postgres_connections", isolated)
    monkeypatch.setattr(backup_service.subprocess, "run", run)

    backup_service._restore_database(
        {
            "driver": "postgresql",
            "database_name": "omlorix",
            "database_user": "omlorix",
            "database_password": "secret",
            "database_host": "postgres",
            "database_port": "5432",
        },
        dump_path,
        schema_names=["app", "logs"],
        required_extensions={"pg_trgm": "app"},
    )

    list_command, list_kwargs = calls[0]
    assert list_command[:2] == ["pg_restore", "--list"]
    assert list_kwargs["capture_output"] is True

    render_command, render_kwargs = calls[1]
    assert render_command[0] == "pg_restore"
    assert "--clean" not in render_command
    assert "--exit-on-error" in render_command
    assert "--use-list" in render_command
    assert render_kwargs["check"] is True

    restore_command, restore_kwargs = calls[2]
    assert restore_command[0] == "psql"
    assert "--single-transaction" in restore_command
    reset_sql = restore_command[restore_command.index("--command") + 1]
    assert 'DROP EXTENSION IF EXISTS "pg_trgm" CASCADE;' in reset_sql
    assert 'DROP SCHEMA IF EXISTS "app" CASCADE;' in reset_sql
    assert 'DROP SCHEMA IF EXISTS "logs" CASCADE;' in reset_sql
    assert 'CREATE SCHEMA "app";' in reset_sql
    assert 'CREATE EXTENSION "pg_trgm" WITH SCHEMA "app";' in reset_sql
    assert restore_kwargs["check"] is True
    assert restore_kwargs["timeout"] == backup_service.BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS
    assert restore_kwargs["env"]["PGAPPNAME"] == "omlorix-restore-test"
    assert restore_kwargs["env"]["PGPASSWORD"] == "secret"
    assert "secret" not in " ".join(restore_command)
    assert (
        f"lock_timeout={backup_service.BACKUP_RESTORE_LOCK_TIMEOUT_SECONDS}s"
        in restore_kwargs["env"]["PGOPTIONS"]
    )


def test_postgres_restore_preserves_complete_database_url_policy(tmp_path, monkeypatch):
    """Every restore connection must retain the effective DATABASE_URL policy."""
    dump_path = tmp_path / "main.dump"
    dump_path.write_bytes(b"test")
    calls = []

    @contextmanager
    def isolated(_config):
        yield "omlorix-restore-test"

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    config = {
        "driver": "postgresql",
        "url": (
            "postgresql://omlorix:database-secret@ignored.example/omlorix"
            "?host=db-primary.example%3A5432&host=db-secondary.example%3A5433"
            "&sslmode=verify-full&sslrootcert=%2Fcerts%2Froot.crt"
            "&sslcert=%2Fcerts%2Fclient.crt&sslkey=%2Fcerts%2Fclient.key"
            "&channel_binding=require&require_auth=scram-sha-256"
            "&sslnegotiation=direct&sslcertmode=require"
            "&target_session_attrs=primary&load_balance_hosts=random"
            "&options=-c%20statement_timeout%3D120s"
        ),
        "database_name": "omlorix",
        "database_user": "omlorix",
        "database_password": "database-secret",
        "database_host": "ignored.example",
    }
    monkeypatch.setenv("PGSSLMODE", "disable")
    monkeypatch.setattr(backup_service, "_quiesce_postgres_connections", isolated)
    monkeypatch.setattr(backup_service.subprocess, "run", run)

    backup_service._restore_database(config, dump_path)

    restore_command, restore_kwargs = calls[-1]
    connection_value = restore_command[restore_command.index("--dbname") + 1]

    # Query-string hosts override the hierarchical host in the same way as the
    # application's SQLAlchemy/psycopg2 connection.
    for parameter, expected_value in {
        "host": "db-primary.example,db-secondary.example",
        "port": "5432,5433",
        "sslmode": "verify-full",
        "sslrootcert": "/certs/root.crt",
        "sslcert": "/certs/client.crt",
        "sslkey": "/certs/client.key",
        "channel_binding": "require",
        "require_auth": "scram-sha-256",
        "sslnegotiation": "direct",
        "sslcertmode": "require",
        "target_session_attrs": "primary",
        "load_balance_hosts": "random",
    }.items():
        assert f"{parameter}='{expected_value}'" in connection_value
    assert "-c statement_timeout=120s" in connection_value
    assert (
        f"-c lock_timeout={backup_service.BACKUP_RESTORE_LOCK_TIMEOUT_SECONDS}s"
        in connection_value
    )

    # The configured URL must take precedence over an inherited, weaker
    # PGSSLMODE without returning the database password to process argv.
    assert restore_kwargs["env"]["PGSSLMODE"] == "disable"
    assert restore_kwargs["env"]["PGPASSWORD"] == "database-secret"
    assert "database-secret" not in " ".join(restore_command)

    coordinator_kwargs = backup_service._postgres_connection_kwargs(
        config,
        application_name="omlorix-restore-coordinator-test",
    )
    assert coordinator_kwargs["host"] == "db-primary.example,db-secondary.example"
    assert coordinator_kwargs["port"] == "5432,5433"
    assert coordinator_kwargs["sslmode"] == "verify-full"
    assert coordinator_kwargs["sslrootcert"] == "/certs/root.crt"
    assert coordinator_kwargs["channel_binding"] == "require"
    assert coordinator_kwargs["require_auth"] == "scram-sha-256"
    assert coordinator_kwargs["sslnegotiation"] == "direct"
    assert coordinator_kwargs["target_session_attrs"] == "primary"


def test_postgres_cli_connection_uses_private_service_file_for_key_password(
    tmp_path,
    monkeypatch,
):
    """File-only libpq secrets must be preserved without entering process argv."""
    monkeypatch.setattr(backup_service.tempfile, "tempdir", str(tmp_path))
    config = {
        "driver": "postgresql",
        "url": (
            "postgresql://omlorix:database-secret@db.example/omlorix"
            "?sslmode=verify-full&sslkey=%2Fcerts%2Fclient.key"
            "&sslpassword=private-key-secret"
        ),
        "database_name": "omlorix",
        "database_user": "omlorix",
        "database_password": "database-secret",
        "database_host": "db.example",
    }

    with backup_service._postgres_cli_connection(
        config,
        application_name="omlorix-restore-test",
        add_restore_lock_timeout=True,
    ) as (connection_value, environment):
        service_file = Path(environment["PGSERVICEFILE"])
        assert service_file.exists()
        assert service_file.stat().st_mode & 0o777 == 0o600
        assert "database-secret" not in connection_value
        assert "private-key-secret" not in connection_value

        service_text = service_file.read_text(encoding="utf-8")
        assert "password=database-secret" not in service_text
        assert "sslpassword=private-key-secret" in service_text
        assert "sslmode=verify-full" in service_text
        assert environment["PGPASSWORD"] == "database-secret"

    assert not service_file.exists()


def test_postgres_dump_rejects_service_with_explicit_password(
    tmp_path,
    monkeypatch,
):
    """A service-file password must not override an explicit URL password."""
    config = {
        "driver": "postgresql",
        "url": (
            "postgresql://omlorix:explicit-secret@/omlorix"
            "?service=shared"
        ),
        "database_name": "omlorix",
        "database_user": "omlorix",
        "database_password": "explicit-secret",
    }

    monkeypatch.setattr(
        backup_service.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "pg_dump must not run with ambiguous password precedence"
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="service references cannot be combined with an explicit password",
    ):
        backup_service._dump_database(config, tmp_path / "database.dump")


def test_postgres_restore_rejects_unresolved_service_options(
    tmp_path,
    monkeypatch,
):
    """Restore must not replace an unknown service-file options value."""
    monkeypatch.delenv("PGOPTIONS", raising=False)
    config = {
        "driver": "postgresql",
        "url": "postgresql:///omlorix?service=shared",
        "database_name": "omlorix",
    }

    monkeypatch.setattr(
        backup_service,
        "_quiesce_postgres_connections",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid restore configuration must fail before disconnecting sessions"
        ),
    )
    monkeypatch.setattr(
        backup_service.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "pg_restore must not run after unresolved service options"
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="service references used for restore must define options directly",
    ):
        backup_service._restore_database(config, tmp_path / "database.dump")


def test_postgres_cli_connection_does_not_validate_with_psycopg_libpq(monkeypatch):
    """A newer PostgreSQL CLI may support options unknown to psycopg2's libpq."""
    config = {
        "driver": "postgresql",
        "url": (
            "postgresql://omlorix:database-secret@db.example/omlorix"
            "?sslmode=verify-full&sslnegotiation=direct"
        ),
        "database_name": "omlorix",
        "database_user": "omlorix",
        "database_password": "database-secret",
        "database_host": "db.example",
    }

    def reject_psycopg_validation(**_kwargs):
        raise AssertionError("CLI serialization must not use psycopg2 make_dsn")

    monkeypatch.setattr(
        backup_service.psycopg2.extensions,
        "make_dsn",
        reject_psycopg_validation,
    )

    with backup_service._postgres_cli_connection(
        config,
        application_name="omlorix-backup-test",
    ) as (connection_value, environment):
        assert "sslnegotiation='direct'" in connection_value
        assert "password" not in connection_value
        assert environment["PGPASSWORD"] == "database-secret"


def test_postgres_conninfo_serializer_escapes_values_for_libpq():
    """The version-neutral serializer must still produce valid libpq syntax."""
    connection_value = backup_service._serialize_postgres_conninfo(
        {
            "host": r"db\primary",
            "dbname": "chat ui's",
        }
    )

    assert parse_dsn(connection_value) == {
        "host": r"db\primary",
        "dbname": "chat ui's",
    }


def test_postgres_conninfo_serializer_rejects_parameter_injection():
    """Untrusted URL keys and NUL bytes must not alter the conninfo structure."""
    with pytest.raises(RuntimeError, match="Invalid PostgreSQL connection parameter name"):
        backup_service._serialize_postgres_conninfo(
            {"host\npassword": "attacker-controlled"}
        )

    with pytest.raises(RuntimeError, match="cannot contain NUL bytes"):
        backup_service._serialize_postgres_conninfo(
            {"host": "db.example\x00password=attacker-controlled"}
        )


def test_postgres_restore_merges_lock_timeout_into_direct_service_options(
    monkeypatch,
):
    """Explicit URL options keep their precedence and receive the restore bound."""
    monkeypatch.delenv("PGOPTIONS", raising=False)
    config = {
        "driver": "postgresql",
        "url": (
            "postgresql:///omlorix?service=shared"
            "&options=-c%20statement_timeout%3D120s"
        ),
        "database_name": "omlorix",
    }

    with backup_service._postgres_cli_connection(
        config,
        application_name="omlorix-restore-test",
        add_restore_lock_timeout=True,
    ) as (connection_value, _environment):
        assert "service='shared'" in connection_value
        assert "-c statement_timeout=120s" in connection_value
        assert (
            f"-c lock_timeout={backup_service.BACKUP_RESTORE_LOCK_TIMEOUT_SECONDS}s"
            in connection_value
        )


def test_restore_releases_worker_session_before_schema_replacement(monkeypatch):
    """Regression test for the restore_job AccessShare/AccessExclusive self-deadlock."""
    now = datetime.now(timezone.utc)
    restore_job = SimpleNamespace(
        id="restore-job-id",
        source_uri="local://restore.tar.zst",
        target_mode="empty",
        requested_by_user_id="admin-id",
        confirmed_by_user_id=None,
        options={},
        created_at=now,
        started_at=now,
        status="queued",
        error=None,
        preflight_json=None,
    )
    events = []

    class Session:
        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("close")

    class Engine:
        def dispose(self):
            events.append("dispose")

    @contextmanager
    def noop_lock(*args, **kwargs):
        yield

    @contextmanager
    def prepared(path):
        yield path

    def update_status(db, *, restore_job_id, status, error=None, preflight_json=None):
        restore_job.status = status
        restore_job.error = error
        restore_job.preflight_json = preflight_json
        return restore_job

    def restore_archive(context, path):
        assert events[:3] == ["rollback", "close", "dispose"]
        events.append("restore")
        return {"status": "restored"}

    monkeypatch.setattr(backup_service, "engine", Engine())
    monkeypatch.setattr(backup_service, "audit_engine", backup_service.engine)
    monkeypatch.setattr(backup_service, "get_restore_job", lambda db, job_id: restore_job)
    monkeypatch.setattr(backup_service, "update_restore_job_status", update_status)
    monkeypatch.setattr(backup_service, "distributed_lock", noop_lock)
    monkeypatch.setattr(backup_service, "_materialize_source_artifact", lambda db, uri, job_id: Path("/tmp/archive"))
    monkeypatch.setattr(backup_service, "_prepare_archive_for_restore", prepared)
    monkeypatch.setattr(
        backup_service,
        "preflight_backup_archive",
        lambda path, target_mode, db: {
            "ok": True,
            "manifest": {"backup_job_id": "source-backup-id"},
        },
    )
    monkeypatch.setattr(
        backup_service,
        "_snapshot_backup_catalog_for_recovery",
        lambda *args, **kwargs: SimpleNamespace(id="source-backup-id"),
    )
    monkeypatch.setattr(backup_service, "activate_write_freeze", lambda **kwargs: events.append("freeze"))
    monkeypatch.setattr(backup_service, "deactivate_write_freeze", lambda: events.append("unfreeze"))
    monkeypatch.setattr(backup_service, "_restore_from_archive", restore_archive)
    monkeypatch.setattr(
        backup_service,
        "_record_restore_terminal_status",
        lambda context, *, status, error, preflight_json, backup_catalog_contexts=None: SimpleNamespace(
            status=status,
            error=error,
        ),
    )

    result = backup_service._run_restore_job_with_session(Session(), restore_job.id)

    assert result.status == "success"
    assert events.index("close") < events.index("restore")
    assert events[-2:] == ["restore", "unfreeze"]


def test_postgres_connection_coordinator_drains_competing_sessions(monkeypatch):
    """The coordinator repeatedly terminates non-restore database sessions."""
    executions = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, parameters):
            executions.append((statement, parameters))

    class Connection:
        autocommit = False

        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(backup_service.psycopg2, "connect", lambda **kwargs: Connection())

    config = {
        "driver": "postgresql",
        "database_name": "omlorix",
        "database_user": "omlorix",
        "database_password": "secret",
        "database_host": "postgres",
        "database_port": "5432",
    }
    with backup_service._quiesce_postgres_connections(config) as application_name:
        assert application_name.startswith("omlorix-restore-")

    assert executions
    assert "pg_terminate_backend" in executions[0][0]
    assert executions[0][1] == (application_name,)
