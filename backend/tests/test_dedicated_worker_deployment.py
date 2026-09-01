from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS = {
    "operations_worker": "app.workers.operations",
    "generation_worker": "app.workers.generation",
    "research_worker": "app.workers.research",
    "file_processing_worker": "app.workers.files",
    "account_lifecycle_worker": "app.workers.lifecycle",
    "maintenance_worker": "app.workers.maintenance",
    "rendering_worker": "app.workers.rendering",
    "media_worker": "app.workers.media",
    "connector_worker": "app.workers.ingestion",
    "audit_event_worker": "app.workers.events",
}


def _service(source: str, name: str) -> str:
    tail = source.split(f"  {name}:\n", 1)[1]
    boundary = re.search(r"\n  [a-zA-Z0-9_-]+:\n", tail)
    return tail[: boundary.start()] if boundary else tail


def test_all_dedicated_workers_are_always_on_in_server_topologies():
    for compose_name in (
        "docker-compose.server.yml",
        "docker-compose.managed-cloud.yml",
    ):
        source = (REPO_ROOT / compose_name).read_text(encoding="utf-8")
        fastapi = _service(source, "fastapi")
        shared_worker_environment = source.split(
            "x-durable-worker-environment: &durable-worker-environment\n", 1
        )[1].split("\nservices:\n", 1)[0]
        for mode in (
            "OPERATIONS_WORKER_MODE: external",
            "GENERATION_WORKER_MODE: external",
            "RESEARCH_WORKER_MODE: external",
            "FILE_PROCESSING_WORKER_MODE: external",
            "ACCOUNT_LIFECYCLE_WORKER_MODE: external",
            "MAINTENANCE_WORKER_MODE: external",
            "RENDERING_WORKER_MODE: external",
            "MEDIA_WORKER_MODE: external",
            "CONNECTOR_WORKER_MODE: external",
            "AUDIT_EVENT_WORKER_MODE: external",
        ):
            assert mode in fastapi
            assert mode in shared_worker_environment
        for quota_key in (
            "OPERATIONS_IMPORT_MAX_BYTES",
            "OPERATIONS_IMPORT_STAGING_GLOBAL_MAX_BYTES",
            "OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_BYTES",
            "OPERATIONS_IMPORT_STAGING_GLOBAL_MAX_SLOTS",
            "OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_SLOTS",
        ):
            assert f"{quota_key}:" in fastapi
            assert f"{quota_key}:" in shared_worker_environment
        assert 'BACKUP_SCHEDULER_ENABLED: "false"' in fastapi

        for service_name, module in WORKERS.items():
            worker = _service(source, service_name)
            assert f"python -m {module} run" in worker
            assert f'python", "-m", "{module}", "healthcheck' in worker
            assert "restart: unless-stopped" in worker
            assert "profiles:" not in worker

        automation_worker = _service(source, "automation_worker")
        for mode in (
            "RESEARCH_WORKER_MODE: external",
            "FILE_PROCESSING_WORKER_MODE: external",
            "RENDERING_WORKER_MODE: external",
            "MEDIA_WORKER_MODE: external",
            "CONNECTOR_WORKER_MODE: external",
            "AUDIT_EVENT_WORKER_MODE: external",
        ):
            assert mode in automation_worker

        gateway = _service(source, "realtime_gateway")
        assert "uvicorn app.realtime.gateway:app" in gateway
        assert "localhost:8001/ready" in gateway
        assert "restart: unless-stopped" in gateway
        assert "profiles:" not in gateway


def test_api_starts_audit_delivery_worker_in_inline_mode():
    source = (REPO_ROOT / "backend/app/main.py").read_text(encoding="utf-8")

    assert "audit_events_are_external = external_audit_event_enabled()" in source
    assert "if not audit_events_are_external:" in source
    assert "build_audit_event_worker()" in source
    assert '"inline-audit-event-worker"' in source


def test_worker_overlays_keep_build_network_and_observability_parity():
    source_build = (REPO_ROOT / "docker-compose.source-build.yml").read_text(
        encoding="utf-8"
    )
    launcher_network = (
        REPO_ROOT / "docker-compose.launcher-services.yml"
    ).read_text(encoding="utf-8")
    observability = (REPO_ROOT / "docker-compose.observability.yml").read_text(
        encoding="utf-8"
    )
    for service_name in WORKERS:
        assert f"  {service_name}:\n    build:\n      context: ./backend" in source_build
        assert f"  {service_name}:\n    networks:" in launcher_network
        assert f"  {service_name}:\n    labels:" in observability
    for source, marker in (
        (source_build, "  realtime_gateway:\n    build:\n      context: ./backend"),
        (launcher_network, "  realtime_gateway:\n    networks:"),
        (observability, "  realtime_gateway:\n    labels:"),
    ):
        assert marker in source


def test_launcher_cli_and_offline_restore_share_the_worker_service_set():
    electron = (REPO_ROOT / "electron/server-manager.js").read_text(encoding="utf-8")
    cli = (REPO_ROOT / "cmd/omlorix-server-cli/operations.go").read_text(
        encoding="utf-8"
    )
    restore_script = (REPO_ROOT / "script/coordinated-backup-restore.sh").read_text(
        encoding="utf-8"
    )
    for service_name in WORKERS:
        assert f"'{service_name}'" in electron
        assert f'"{service_name}"' in cli
        assert service_name in restore_script
    for source in (electron, cli, restore_script):
        assert "realtime_gateway" in source


def test_file_worker_image_includes_ocr_runtime_and_schema_migration():
    dockerfile = (REPO_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    migration = (
        REPO_ROOT / "backend/alembic_main/versions/worker_architecture_20260829.py"
    ).read_text(encoding="utf-8")
    assert "tesseract-ocr" in dockerfile
    assert '"durable_worker_jobs"' in migration
    assert '"file_processing_artifacts"' in migration


def test_import_staging_quota_migration_is_operational_only():
    migration = (
        REPO_ROOT
        / "backend/alembic_main/versions/import_staging_quota_20260830.py"
    ).read_text(encoding="utf-8")
    assert '"import_staging_reservations"' in migration
    assert "durable_worker_jobs.id" in migration
    assert 'ondelete="SET NULL"' in migration
    assert '"size_bytes >= 0"' in migration


def test_extended_worker_migration_and_realtime_proxy_boundary_exist():
    migration = (
        REPO_ROOT / "backend/alembic_main/versions/extended_workers_20260829.py"
    ).read_text(encoding="utf-8")
    erasure_migration = (
        REPO_ROOT
        / "backend/alembic_main/versions/audit_event_erasure_20260830.py"
    ).read_text(encoding="utf-8")
    audit_gate_migration = (
        REPO_ROOT
        / "backend/alembic_audit/versions/audit_subject_fence_20260830.py"
    ).read_text(encoding="utf-8")
    nginx = (
        REPO_ROOT / "nginx/default.http.conf.template/default.conf"
    ).read_text(encoding="utf-8")
    gateway = (REPO_ROOT / "backend/app/realtime/gateway.py").read_text(
        encoding="utf-8"
    )
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert '"audit_event_outbox"' in migration
    assert '"audit_event_subject_states"' in erasure_migration
    assert '"audit_event_subject_references"' in erasure_migration
    assert '"audit_event_erasure_guards"' in erasure_migration
    assert '"audit_erasure_reconciliation_checkpoints"' in erasure_migration
    assert '"subjects_indexed"' in erasure_migration
    assert "_cancel_legacy_audit_events" in erasure_migration
    assert "ck_audit_event_outbox_unindexed_safe" in erasure_migration
    assert "ck_logs_subject_fenced" in audit_gate_migration
    assert "location ^~ /api/v1/realtime/" in nginx
    assert "realtime_gateway:8001" in nginx
    assert "_REALTIME_PREFIX" in gateway
    assert "down --remove-orphans" in makefile
    migrate_target = makefile.split("\nmigrate:\n", 1)[1].split(
        "\nsource-probe:\n", 1
    )[0]
    assert "down --remove-orphans" in migrate_target
    assert "run --rm $(COMPOSE_BUILD_FLAG) migrate" in migrate_target
