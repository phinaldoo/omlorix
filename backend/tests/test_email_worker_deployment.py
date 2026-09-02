from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_email_worker_is_always_on_and_operational_in_every_server_topology():
    for compose_name, next_service in (
        ("docker-compose.server.yml", "operations_worker"),
        ("docker-compose.managed-cloud.yml", "operations_worker"),
    ):
        source = (REPO_ROOT / compose_name).read_text(encoding="utf-8")
        assert "  email_worker:\n" in source
        worker = source.split("  email_worker:\n", 1)[1].split(
            f"\n  {next_service}:\n", 1
        )[0]
        assert "python -m app.email.worker run" in worker
        assert (
            'test: ["CMD", "python", "-m", "app.worker_heartbeat", '
            '"email", "email", "90"]'
        ) in worker
        assert "interval: 31s" in worker
        assert "timeout: 3s" in worker
        assert "restart: unless-stopped" in worker
        assert "DB_MIGRATIONS_MODE: off" in worker
        assert "profiles:" not in worker

    source_build = (REPO_ROOT / "docker-compose.source-build.yml").read_text(
        encoding="utf-8"
    )
    launcher_network = (
        REPO_ROOT / "docker-compose.launcher-services.yml"
    ).read_text(encoding="utf-8")
    observability = (REPO_ROOT / "docker-compose.observability.yml").read_text(
        encoding="utf-8"
    )
    assert "  email_worker:\n    build:\n      context: ./backend" in source_build
    assert "  email_worker:\n    networks:" in launcher_network
    assert "OTEL_SERVICE_NAME: omlorix-email-worker" in observability


def test_launcher_and_cli_restore_the_email_worker_after_offline_backups():
    electron = (REPO_ROOT / "electron/server-manager.js").read_text(encoding="utf-8")
    cli = (REPO_ROOT / "cmd/omlorix-server-cli/operations.go").read_text(
        encoding="utf-8"
    )
    restore_script = (REPO_ROOT / "script/coordinated-backup-restore.sh").read_text(
        encoding="utf-8"
    )

    for source in (electron, cli, restore_script):
        assert "email_worker" in source
    assert "names.push('email_worker', ...DEDICATED_WORKER_SERVICE_NAMES" in electron
    assert 'names = append(names, "email_worker")' in cli
    assert "SERVICES_TO_STOP=(frontend email_worker" in restore_script
    assert "SERVICES_TO_START=(frontend email_worker" in restore_script
