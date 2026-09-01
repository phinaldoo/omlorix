from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _service(source: str, name: str, next_name: str) -> str:
    return source.split(f"  {name}:\n", 1)[1].split(f"  {next_name}:\n", 1)[0]


def test_pgbouncer_routes_runtime_services_but_not_database_bootstrap():
    source = (REPO_ROOT / "docker-compose.server.yml").read_text(encoding="utf-8")
    migrate = _service(source, "migrate", "fastapi")
    fastapi = _service(source, "fastapi", "automation_scheduler")
    scheduler = _service(source, "automation_scheduler", "automation_worker")
    worker = _service(source, "automation_worker", "email_worker")
    email_worker = _service(source, "email_worker", "pgbouncer")

    assert "DATABASE_HOST: ${DATABASE_MIGRATION_HOST_OVERRIDE:-postgres}" in migrate
    assert "DATABASE_PORT: ${DATABASE_MIGRATION_PORT_OVERRIDE:-5432}" in migrate
    assert "OMLORIX_AUTO_CREATE_DATABASES: ${OMLORIX_AUTO_CREATE_DATABASES:-true}" in migrate

    for service in (fastapi, scheduler, worker, email_worker):
        assert "DATABASE_URL: ${DATABASE_URL-}" in service
        assert "DATABASE_HOST: ${DATABASE_HOST_OVERRIDE:-postgres}" in service
        assert "DATABASE_PORT: ${DATABASE_PORT_OVERRIDE:-5432}" in service
        assert 'OMLORIX_AUTO_CREATE_DATABASES: "false"' in service

    dependencies = source.split("x-backend-depends: &backend-depends\n", 1)[1].split(
        "services:\n", 1
    )[0]
    assert "pgbouncer:\n      condition: service_healthy" in dependencies


def test_pgbouncer_health_proves_scram_authenticated_database_query():
    source = (REPO_ROOT / "docker-compose.server.yml").read_text(encoding="utf-8")
    pgbouncer = _service(source, "pgbouncer", "minio")

    assert "AUTH_TYPE: scram-sha-256" in pgbouncer
    assert "PGPASSWORD=" in pgbouncer
    assert "psql -w -h 127.0.0.1 -p 5432" in pgbouncer
    assert "-tAc 'SELECT 1' | grep -qx 1" in pgbouncer
    assert "healthcheck:" in pgbouncer


def test_statement_pooling_is_not_advertised_for_transactional_application():
    launcher = (REPO_ROOT / "electron/renderer/launcher.html").read_text(encoding="utf-8")
    features = (REPO_ROOT / "features.md").read_text(encoding="utf-8")

    assert '<option value="transaction">' in launcher
    assert '<option value="session">' in launcher
    assert '<option value="statement">' not in launcher
    assert "transaction/session/statement pooling" not in features
