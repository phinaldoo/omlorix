import logging
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.service_connections import utils as service
from app.service_connections.models import ServiceConnection
from app.service_connections.utils import (
    SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES,
    SERVICE_PURPOSE_CODE_EXECUTION,
    SERVICE_PURPOSE_LATEX_PDF,
    SERVICE_PURPOSE_SLIDE_RENDERER,
    create_service_connection,
    has_configured_service_connection,
    get_service_connection_candidates,
    has_healthy_service_connection_capability,
    refresh_service_connection_status,
    record_service_connection_runtime_status,
)


SERVICE_CONNECTIONS_ROOT = (
    Path(__file__).resolve().parents[2] / "app" / "service_connections"
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[ServiceConnection.__table__])
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _set_test_encryption_key(monkeypatch):
    """Install a valid Fernet key for tests that persist service API keys."""
    from app.utils import encryption as encryption_utils

    # The encryption module caches its Fernet instance globally. Reset both the
    # key and cached suite so each test can run independently of process state.
    monkeypatch.setattr(
        encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8")
    )
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)


def test_service_connections_use_the_standard_feature_structure():
    """Keep schemas, persistence, business logic, and HTTP wiring separated."""

    for module_name in ("models.py", "schemas.py", "utils.py", "router.py"):
        assert (SERVICE_CONNECTIONS_ROOT / module_name).is_file()
    assert not (SERVICE_CONNECTIONS_ROOT / "service.py").exists()


def test_service_connections_use_dedicated_rows_and_encrypt_api_keys(monkeypatch):
    """Persist each connection independently without exposing its key at rest."""

    db = _session()
    try:
        _set_test_encryption_key(monkeypatch)
        connection = create_service_connection(
            db,
            {
                "name": "Encrypted",
                "base_url": "https://encrypted.example.test",
                "api_key": "super-secret",
                "enabled_for_code_execution": True,
            },
        )

        raw_api_key = db.execute(
            text("SELECT api_key FROM service_connections WHERE id = :id"),
            {"id": connection["id"]},
        ).scalar_one()

        assert raw_api_key
        assert raw_api_key != "super-secret"
        assert db.query(ServiceConnection).one().api_key == "super-secret"
    finally:
        db.close()


def test_weighted_service_connection_selection_uses_weights(monkeypatch):
    db = _session()
    try:
        first = create_service_connection(
            db,
            {
                "name": "Primary",
                "base_url": "http://primary.example.test",
                "enabled_for_code_execution": True,
                "weight": 5,
            },
        )
        second = create_service_connection(
            db,
            {
                "name": "Secondary",
                "base_url": "http://secondary.example.test",
                "enabled_for_code_execution": True,
                "weight": 1,
            },
        )

        monkeypatch.setattr(service.random, "uniform", lambda _low, _high: 5.5)

        candidates = get_service_connection_candidates(
            db, SERVICE_PURPOSE_CODE_EXECUTION
        )

        assert candidates[0]["id"] == second["id"]
        assert {candidate["id"] for candidate in candidates} == {
            first["id"],
            second["id"],
        }
    finally:
        db.close()


def test_refresh_status_does_not_decrypt_probe_plaintext_prefixed_api_key(
    monkeypatch, caplog
):
    db = _session()
    api_key = "enc:v1:not-a-fernet-token"

    class DummyClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, endpoint, headers):
            assert endpoint == "http://prefixed.example.test/health"
            assert headers["Authorization"] == f"Bearer {api_key}"
            return service.httpx.Response(401, content=b"unauthorized")

    try:
        _set_test_encryption_key(monkeypatch)
        monkeypatch.setattr(service, "assert_url_allowed", lambda *args, **kwargs: None)
        monkeypatch.setattr(service.httpx, "Client", DummyClient)

        connection = create_service_connection(
            db,
            {
                "name": "Prefixed",
                "base_url": "http://prefixed.example.test",
                "api_key": api_key,
                "enabled_for_code_execution": True,
            },
        )

        caplog.clear()
        caplog.set_level(logging.ERROR, logger="app.utils.encryption")

        refreshed = refresh_service_connection_status(
            db, connection["id"], purpose=SERVICE_PURPOSE_CODE_EXECUTION
        )

        assert refreshed["api_key"] == api_key
        assert refreshed["status"]["code_execution_auth"] == "invalid"
        assert "Failed to decrypt value" not in caplog.text
    finally:
        db.close()


def test_down_service_connection_is_skipped_for_runtime_selection():
    db = _session()
    try:
        down = create_service_connection(
            db,
            {
                "name": "Down",
                "base_url": "http://down.example.test",
                "enabled_for_code_execution": True,
                "weight": 10,
            },
        )
        healthy = create_service_connection(
            db,
            {
                "name": "Healthy",
                "base_url": "http://healthy.example.test",
                "enabled_for_code_execution": True,
                "weight": 1,
            },
        )
        record_service_connection_runtime_status(
            db,
            down,
            SERVICE_PURPOSE_CODE_EXECUTION,
            available=False,
            message="health check failed",
            failure_scope="service",
        )

        candidates = get_service_connection_candidates(
            db, SERVICE_PURPOSE_CODE_EXECUTION
        )

        assert [candidate["id"] for candidate in candidates] == [healthy["id"]]
    finally:
        db.close()


def test_down_service_connection_is_retained_when_it_is_the_only_runtime_option():
    db = _session()
    try:
        down = create_service_connection(
            db,
            {
                "name": "Down",
                "base_url": "http://down.example.test",
                "enabled_for_code_execution": True,
                "weight": 10,
            },
        )
        record_service_connection_runtime_status(
            db,
            down,
            SERVICE_PURPOSE_CODE_EXECUTION,
            available=False,
            message="health check failed",
            failure_scope="service",
        )

        candidates = get_service_connection_candidates(
            db, SERVICE_PURPOSE_CODE_EXECUTION
        )

        assert [candidate["id"] for candidate in candidates] == [down["id"]]
    finally:
        db.close()


def test_down_service_connection_still_counts_as_configured():
    db = _session()
    try:
        down = create_service_connection(
            db,
            {
                "name": "Down",
                "base_url": "http://down.example.test",
                "enabled_for_code_execution": True,
            },
        )
        record_service_connection_runtime_status(
            db,
            down,
            SERVICE_PURPOSE_CODE_EXECUTION,
            available=False,
            message="health check failed",
            failure_scope="service",
        )

        assert (
            has_configured_service_connection(db, SERVICE_PURPOSE_CODE_EXECUTION)
            is True
        )
    finally:
        db.close()


def test_request_failure_does_not_persist_global_down_status():
    """A user-request failure must not overwrite shared service health."""
    db = _session()
    try:
        connection = create_service_connection(
            db,
            {
                "name": "Shared",
                "base_url": "http://shared.example.test",
                "enabled_for_code_execution": True,
            },
        )
        record_service_connection_runtime_status(
            db,
            connection,
            SERVICE_PURPOSE_CODE_EXECUTION,
            available=True,
            message="Available",
        )
        healthy_status = list(service.list_service_connections(db))[0]["status"]

        # The 429 is produced by a particular execution request. It says
        # nothing authoritative about whether the shared service is healthy
        # for other users, so it must not poison the persisted global status.
        record_service_connection_runtime_status(
            db,
            connection,
            SERVICE_PURPOSE_CODE_EXECUTION,
            available=False,
            message="Code execution service returned status 429",
        )

        stored = list(service.list_service_connections(db))[0]
        assert stored["status"] == healthy_status
        assert [
            candidate["id"]
            for candidate in get_service_connection_candidates(
                db,
                SERVICE_PURPOSE_CODE_EXECUTION,
            )
        ] == [connection["id"]]
    finally:
        db.close()


def test_code_execution_candidates_require_service_connection_rows():
    db = _session()
    try:
        candidates = get_service_connection_candidates(
            db, SERVICE_PURPOSE_CODE_EXECUTION
        )

        assert candidates == []
        assert (
            has_configured_service_connection(db, SERVICE_PURPOSE_CODE_EXECUTION)
            is False
        )
    finally:
        db.close()


def test_latex_pdf_selection_uses_dedicated_toggle_not_code_execution():
    db = _session()
    try:
        code_only = create_service_connection(
            db,
            {
                "name": "Code only",
                "base_url": "http://code-only.example.test",
                "enabled_for_code_execution": True,
                "weight": 1,
            },
        )
        latex_only = create_service_connection(
            db,
            {
                "name": "LaTeX only",
                "base_url": "http://latex-only.example.test",
                "enabled_for_latex_pdf": True,
                "weight": 1,
            },
        )

        latex_candidates = get_service_connection_candidates(
            db, SERVICE_PURPOSE_LATEX_PDF
        )
        code_candidates = get_service_connection_candidates(
            db, SERVICE_PURPOSE_CODE_EXECUTION
        )

        assert [candidate["id"] for candidate in latex_candidates] == [latex_only["id"]]
        assert [candidate["id"] for candidate in code_candidates] == [code_only["id"]]
    finally:
        db.close()


def test_latex_pdf_candidates_require_service_connection_rows():
    db = _session()
    try:
        candidates = get_service_connection_candidates(db, SERVICE_PURPOSE_LATEX_PDF)

        assert candidates == []
        assert has_configured_service_connection(db, SERVICE_PURPOSE_LATEX_PDF) is False
    finally:
        db.close()


def test_refresh_service_connection_status_marks_api_key_valid_on_success(monkeypatch):
    db = _session()

    class DummyClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, endpoint, headers):
            return service.httpx.Response(200, content=b"ok")

    try:
        _set_test_encryption_key(monkeypatch)
        connection = create_service_connection(
            db,
            {
                "name": "Protected",
                "base_url": "http://protected.example.test",
                "api_key": "secret",
                "enabled_for_code_execution": True,
            },
        )

        monkeypatch.setattr(service, "assert_url_allowed", lambda *args, **kwargs: None)
        monkeypatch.setattr(service.httpx, "Client", DummyClient)

        refreshed = refresh_service_connection_status(
            db, connection["id"], purpose=SERVICE_PURPOSE_CODE_EXECUTION
        )

        assert refreshed["status"]["code_execution"] == "up"
        assert refreshed["status"]["code_execution_auth"] == "valid"
        assert refreshed["status"]["available"] == "up"
    finally:
        db.close()


def test_refresh_status_persists_code_execution_capabilities(monkeypatch):
    db = _session()

    class DummyClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, endpoint, headers):
            return service.httpx.Response(
                200,
                json={
                    "status": "healthy",
                    "capabilities": {
                        SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES: True,
                    },
                },
            )

    try:
        _set_test_encryption_key(monkeypatch)
        connection = create_service_connection(
            db,
            {
                "name": "Pip capable",
                "base_url": "http://pip-capable.example.test",
                "enabled_for_code_execution": True,
            },
        )

        monkeypatch.setattr(service, "assert_url_allowed", lambda *args, **kwargs: None)
        monkeypatch.setattr(service.httpx, "Client", DummyClient)

        refreshed = refresh_service_connection_status(
            db,
            connection["id"],
            purpose=SERVICE_PURPOSE_CODE_EXECUTION,
        )

        assert refreshed["status"]["code_execution_capabilities"] == {
            SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES: True,
        }
        assert has_healthy_service_connection_capability(
            db,
            SERVICE_PURPOSE_CODE_EXECUTION,
            SERVICE_CAPABILITY_EXTERNAL_PIP_PACKAGES,
        )
    finally:
        db.close()


def test_slide_renderer_status_probe_uses_renderer_api_key_contract(monkeypatch):
    db = _session()

    class DummyClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, endpoint, headers):
            assert endpoint == "http://renderer.example.test/health"
            assert headers["X-API-Key"] == "renderer-secret"
            assert headers["Authorization"] == "Bearer renderer-secret"
            return service.httpx.Response(200, json={"status": "healthy"})

    try:
        _set_test_encryption_key(monkeypatch)
        connection = create_service_connection(
            db,
            {
                "name": "Renderer",
                "base_url": "http://renderer.example.test/api/v1/render",
                "api_key": "renderer-secret",
                "enabled_for_slide_renderer": True,
            },
        )

        monkeypatch.setattr(service, "assert_url_allowed", lambda *args, **kwargs: None)
        monkeypatch.setattr(service.httpx, "Client", DummyClient)

        refreshed = refresh_service_connection_status(
            db,
            connection["id"],
            purpose=SERVICE_PURPOSE_SLIDE_RENDERER,
        )

        assert refreshed["status"]["slide_renderer"] == "up"
        assert refreshed["status"]["slide_renderer_auth"] == "valid"
        assert refreshed["status"]["available"] == "up"
    finally:
        db.close()


def test_refresh_service_connection_status_marks_api_key_invalid_on_401(monkeypatch):
    db = _session()

    class DummyClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, endpoint, headers):
            return service.httpx.Response(401, content=b"unauthorized")

    try:
        _set_test_encryption_key(monkeypatch)
        connection = create_service_connection(
            db,
            {
                "name": "Protected",
                "base_url": "http://protected.example.test",
                "api_key": "secret",
                "enabled_for_code_execution": True,
            },
        )

        monkeypatch.setattr(service, "assert_url_allowed", lambda *args, **kwargs: None)
        monkeypatch.setattr(service.httpx, "Client", DummyClient)

        refreshed = refresh_service_connection_status(
            db, connection["id"], purpose=SERVICE_PURPOSE_CODE_EXECUTION
        )

        assert refreshed["status"]["code_execution"] == "down"
        assert refreshed["status"]["code_execution_auth"] == "invalid"
        assert refreshed["status"]["available"] == "down"
        assert "API key" in refreshed["status"]["message"]
    finally:
        db.close()
