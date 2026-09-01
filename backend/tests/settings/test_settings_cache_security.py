from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from app.settings import utils as settings_utils


class _WeakReferenceableSession:
    """Minimal session-like object accepted by the settings cache registry."""

    def __init__(self, bind=None):
        self.bind = bind


class _FakeRedis:
    """Small Redis test double supporting the settings-cache operations."""

    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, *, nx=False, **kwargs):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True


class _BrokenRedis:
    """Redis test double that fails every generation lookup."""

    def get(self, key):
        raise ConnectionError("redis unavailable")


def test_local_settings_cache_changes_with_shared_generation(monkeypatch):
    """A Redis generation bump must make every process reload from the database."""
    session = _WeakReferenceableSession()
    redis = _FakeRedis()
    current_value = {"public_url": []}

    monkeypatch.setattr(settings_utils, "get_redis_client", lambda: redis)
    monkeypatch.setattr(
        settings_utils,
        "_load_setting_value",
        lambda db, page_name, key_name: current_value[key_name],
    )
    settings_utils._cached_get_value.cache_clear()

    assert (
        settings_utils.get_value_by_page_and_key("general", "public_url", session) == []
    )

    # Model another process committing a settings update and publishing a new
    # shared generation. This process intentionally keeps its old local LRU.
    current_value["public_url"] = ["https://chat.example"]
    db_identity = settings_utils._engine_fingerprint(session.bind)
    namespace = settings_utils._settings_cache_namespace(db_identity)
    redis.set(
        settings_utils._settings_cache_version_key(namespace),
        "other-process-generation",
    )

    assert settings_utils.get_value_by_page_and_key(
        "general", "public_url", session
    ) == ["https://chat.example"]

    settings_utils._cached_get_value.cache_clear()
    settings_utils._SESSION_REGISTRY.clear()


def test_settings_read_uses_database_when_redis_generation_fails(monkeypatch):
    """Partial Redis failures must fail open to the authoritative database."""
    session = _WeakReferenceableSession()

    monkeypatch.setattr(settings_utils, "get_redis_client", lambda: _BrokenRedis())
    monkeypatch.setattr(
        settings_utils,
        "_cached_get_value",
        lambda *args, **kwargs: pytest.fail(
            "local cache must be bypassed without a reliable generation"
        ),
    )
    monkeypatch.setattr(
        settings_utils,
        "_load_setting_value",
        lambda db, page_name, key_name: ["https://database.example"],
    )

    assert settings_utils.get_value_by_page_and_key(
        "general", "public_url", session
    ) == ["https://database.example"]


def test_settings_cache_namespace_isolates_databases_and_deployments(monkeypatch):
    """Shared Redis instances must not mix settings from different databases."""
    monkeypatch.delenv("SETTINGS_CACHE_NAMESPACE", raising=False)
    first_database = settings_utils._settings_cache_namespace(
        "postgresql://postgres:***@postgres:5432/omlorix"
    )
    second_database = settings_utils._settings_cache_namespace(
        "postgresql://postgres:***@postgres:5432/omlorix-db"
    )

    assert first_database != second_database

    monkeypatch.setenv("SETTINGS_CACHE_NAMESPACE", "blue-deployment")
    isolated_deployment = settings_utils._settings_cache_namespace(
        "postgresql://postgres:***@postgres:5432/omlorix-db"
    )

    assert isolated_deployment != second_database


def test_public_url_startup_validation_bypasses_caches(monkeypatch):
    """A stale cache must never cause valid startup configuration to exit."""
    database_values = {
        ("general", "public_url"): ["https://chat.example"],
        ("login_general", "enable_password_reset"): False,
    }

    monkeypatch.setattr(
        settings_utils,
        "get_value_by_page_and_key",
        lambda *args, **kwargs: pytest.fail("startup validation must bypass caches"),
    )
    monkeypatch.setattr(
        settings_utils,
        "_load_setting_value",
        lambda db, page_name, key_name: database_values[(page_name, key_name)],
    )
    monkeypatch.setattr(
        settings_utils,
        "get_settings_page_data",
        lambda db, page_name: (
            {
                "enable_apple_login": True,
            }
            if page_name == "login_social"
            else {
                "enable_saml": False,
                "enable_oidc": False,
            }
        ),
    )

    settings_utils.validate_public_url_requirements(object())


def test_get_value_by_page_and_key_skips_cache_for_sensitive_keys(monkeypatch):
    calls = []

    monkeypatch.setattr(
        settings_utils,
        "get_redis_client",
        lambda: pytest.fail("redis should be bypassed"),
    )
    monkeypatch.setattr(
        settings_utils,
        "_cached_get_value",
        lambda *args, **kwargs: pytest.fail("in-process cache should be bypassed"),
    )
    monkeypatch.setattr(
        settings_utils,
        "get_settings_page",
        lambda db, page_name: SimpleNamespace(
            page_name=page_name, data={"smtp_password": "enc:v1:ciphertext"}
        ),
    )

    def get_settings_value_from_page_data(page_name, page_data, key_name):
        calls.append((page_name, key_name, page_data[key_name]))
        return "smtp-secret"

    monkeypatch.setattr(
        settings_utils,
        "get_settings_value_from_page_data",
        get_settings_value_from_page_data,
    )

    value = settings_utils.get_value_by_page_and_key(
        "login_general",
        "smtp_password",
        SimpleNamespace(bind=None),
    )

    assert value == "smtp-secret"
    assert calls == [("login_general", "smtp_password", "enc:v1:ciphertext")]


def test_get_value_by_page_and_key_skips_cache_for_forbidden_pages(monkeypatch):
    calls = []

    monkeypatch.setattr(
        settings_utils,
        "get_redis_client",
        lambda: pytest.fail("redis should be bypassed"),
    )
    monkeypatch.setattr(
        settings_utils,
        "_cached_get_value",
        lambda *args, **kwargs: pytest.fail("in-process cache should be bypassed"),
    )
    monkeypatch.setattr(
        settings_utils,
        "get_settings_page",
        lambda db, page_name: SimpleNamespace(
            page_name=page_name,
            data={"passkey_padding_secret": "enc:v1:padding-ciphertext"},
        ),
    )

    def get_settings_value_from_page_data(page_name, page_data, key_name):
        calls.append((page_name, key_name, page_data[key_name]))
        return "padding-secret"

    monkeypatch.setattr(
        settings_utils,
        "get_settings_value_from_page_data",
        get_settings_value_from_page_data,
    )

    value = settings_utils.get_value_by_page_and_key(
        "secret",
        "passkey_padding_secret",
        SimpleNamespace(bind=None),
    )

    assert value == "padding-secret"
    assert calls == [("secret", "passkey_padding_secret", "enc:v1:padding-ciphertext")]
