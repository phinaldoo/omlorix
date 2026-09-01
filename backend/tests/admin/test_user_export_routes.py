from types import SimpleNamespace

from app.admin import router as admin_root_router
from app.admin.user_exports import router as admin_router_module
from app.dependencies import get_db, get_db_log, verified_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_removed_user_data_transfer_routes_are_not_registered():
    """The canonical ZIP job flow is the only administrator user export surface."""
    removed_routes = {
        ("/api/v1/admin/users/skills/export", "GET"),
        ("/api/v1/admin/users/skills/import", "POST"),
        ("/api/v1/admin/users/todos/export", "GET"),
        ("/api/v1/admin/users/todos/import", "POST"),
        ("/api/v1/admin/users/notes/export", "GET"),
        ("/api/v1/admin/users/notes/import", "POST"),
    }

    registered_operations = {
        (route.path, method)
        for route in admin_root_router.admin_router.routes
        for method in getattr(route, "methods", set())
    }

    assert removed_routes.isdisjoint(registered_operations)


def test_user_export_download_registers_only_the_authenticated_get_route():
    """The frontend downloads directly and does not perform a preparatory HEAD."""
    download_path = "/api/v1/admin/users/export/jobs/{job_id}/download"
    download_routes = [
        route
        for route in admin_root_router.admin_router.routes
        if getattr(route, "path", None) == download_path
    ]

    assert {method for route in download_routes for method in route.methods} == {"GET"}
    for route in download_routes:
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert verified_admin in dependency_calls


def test_user_export_download_get_succeeds_with_safe_headers(tmp_path, monkeypatch):
    """Exercise the frontend's direct GET download through the real HTTP router."""
    artifact = tmp_path / "admin-users.zip"
    artifact.write_bytes(b"PK\x05\x06" + (b"\x00" * 18))
    monkeypatch.setattr(
        admin_router_module,
        "materialize_admin_user_export_job",
        lambda db, job_id: (artifact, "admin-users.zip"),
    )
    monkeypatch.setattr(
        admin_router_module,
        "get_audit_request_ip",
        lambda request, db: "127.0.0.1",
    )
    audit_calls = []
    monkeypatch.setattr(
        admin_router_module,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    app = FastAPI()
    app.include_router(admin_root_router.admin_router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_db_log] = lambda: object()
    app.dependency_overrides[verified_admin] = lambda: SimpleNamespace(id="admin-1")
    client = TestClient(app)
    download_url = "/api/v1/admin/users/export/jobs/job-1/download"

    get = client.get(download_url, headers={"user-agent": "pytest"})

    assert get.status_code == 200
    assert get.headers["content-disposition"] == 'attachment; filename="admin-users.zip"'
    assert get.headers["cache-control"] == "private, no-store"
    assert get.headers["pragma"] == "no-cache"
    assert get.headers["x-content-type-options"] == "nosniff"
    assert get.headers["content-length"] == str(artifact.stat().st_size)
    assert "x-export-size" not in get.headers
    assert get.content == artifact.read_bytes()
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "EXPORT_USERS_ADMIN_JOB_DOWNLOAD"
