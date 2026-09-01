from __future__ import annotations

import asyncio
import hashlib
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from webauthn.helpers import bytes_to_base64url

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

from app.auth import passkeys
from app.auth import models as auth_models
from app.ip_analytics.schemas import AdminIPAddressStatisticsSettingsUpdate, BlockIP
from app.admin.settings.schema_categories.security import SecuritySettings
from app.chats import io as chat_io
from app import database
from app.logging import models as logging_models
from app.middleware import ip_restriction
from app.middleware import rate_limiter
from app.network import policy as outbound_policy
from app.settings import utils as settings_utils
from app.settings.defaults import DEFAULT_SETTINGS
from app.tools.custom.utils import execute_custom_python_tool_source, inspect_custom_python_tool_source
from app.tools.code_execution import utils as code_execution_utils
from app.tools.slide_presentation.rendering import utils as slide_rendering_utils
from app.utils import origin
from app.utils import sqlalchemy_encryption


REPO_ROOT = Path(__file__).resolve().parents[3]


def _basic_tool_source(body: str) -> str:
    return f"""
TOOL_DEFINITION = {{
    "name": "security_probe",
    "description": "security probe",
    "parameters": {{"type": "object", "properties": {{}}}},
}}

def run_tool(arguments, context):
{body}
"""


def test_custom_python_tool_allows_trusted_import_time_file_access():
    source = """
TOOL_DEFINITION = {
    "name": "trusted_file_probe",
    "description": "trusted file probe",
    "parameters": {"type": "object", "properties": {}},
}
LEAK = open("__AGENTS_PATH__").read()
""".replace("__AGENTS_PATH__", str(REPO_ROOT / "AGENTS.md"))

    definition = inspect_custom_python_tool_source(source)

    assert definition["name"] == "trusted_file_probe"

def test_custom_python_tool_allows_trusted_low_level_file_access():
    source = _basic_tool_source(
        """
    import os
    fd = os.open("__AGENTS_PATH__", os.O_RDONLY)
    try:
        return {"content": os.read(fd, 6).decode("utf-8", "ignore")}
    finally:
        os.close(fd)
""".replace("__AGENTS_PATH__", str(REPO_ROOT / "AGENTS.md"))
    )

    output = execute_custom_python_tool_source(source_code=source)

    assert output["content"] == "# AGEN"

def test_custom_python_tool_allows_trusted_socket_access():
    source = _basic_tool_source(
        """
    import socket
    sock = socket.socket()
    try:
        return {"content": "opened"}
    finally:
        sock.close()
"""
    )

    output = execute_custom_python_tool_source(source_code=source)

    assert output["content"] == "opened"


def test_enforce_same_origin_rejects_local_origin_by_default(monkeypatch):
    def fake_setting(page, key, db):
        values = {
            ("general", "public_url"): "https://chat.example.com",
        }
        return values.get((page, key))

    monkeypatch.delenv(origin.ALLOW_LOCAL_OR_PRIVATE_ORIGINS_ENV, raising=False)
    monkeypatch.setattr(origin, "get_value_by_page_and_key", fake_setting)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("chat.example.com", 443),
            "path": "/api/v1/auth/refresh",
            "headers": [(b"origin", b"http://127.0.0.1:3000")],
        }
    )

    with pytest.raises(HTTPException) as exc:
        origin.enforce_same_origin(request, db=object())

    assert exc.value.status_code == 403


def test_enforce_same_origin_allows_local_origin_when_env_enabled(monkeypatch):
    def fake_setting(page, key, db):
        values = {
            ("general", "public_url"): "https://chat.example.com",
        }
        return values.get((page, key))

    monkeypatch.setenv(origin.ALLOW_LOCAL_OR_PRIVATE_ORIGINS_ENV, "true")
    monkeypatch.setattr(origin, "get_value_by_page_and_key", fake_setting)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("chat.example.com", 443),
            "path": "/api/v1/auth/refresh",
            "headers": [(b"origin", b"http://127.0.0.1:3000")],
        }
    )

    origin.enforce_same_origin(request, db=object())


def test_ip_restrictions_apply_allowlist_to_private_and_loopback_ips(monkeypatch):
    def fake_setting(page, key, db):
        values = {
            ("security", "enable_ip_restrictions"): True,
            ("security", "only_allow_specific_ip"): True,
            ("security", "allow_specific_ip"): ["203.0.113.10"],
        }
        return values.get((page, key))

    monkeypatch.setattr(ip_restriction, "get_value_by_page_and_key", fake_setting)

    assert asyncio.run(ip_restriction.is_ip_allowed("10.0.0.25", db=object())) is False
    assert asyncio.run(ip_restriction.is_ip_allowed("127.0.0.1", db=object())) is False
    assert asyncio.run(ip_restriction.is_ip_allowed("203.0.113.10", db=object())) is True


def test_ip_restrictions_default_to_disabled_for_fresh_deploys():
    assert DEFAULT_SETTINGS["security"]["enable_ip_restrictions"] is False
    assert SecuritySettings().enable_ip_restrictions is False


def test_ip_restrictions_enabled_without_policy_does_not_lock_out_requests(monkeypatch):
    def fake_setting(page, key, db):
        values = {
            ("security", "enable_ip_restrictions"): True,
            ("security", "only_allow_specific_ip"): True,
            ("security", "allow_specific_ip"): [],
            ("security", "block_specific_ip"): [],
            ("security", "only_allow_ip_from_specific_countries"): True,
            ("security", "allow_country_ip"): [],
            ("security", "block_country_ip"): [],
        }
        return values.get((page, key))

    async def fail_country_lookup(ip, db):
        raise AssertionError("empty policies should not perform geo-IP lookups")

    monkeypatch.setattr(ip_restriction, "get_value_by_page_and_key", fake_setting)
    monkeypatch.setattr(ip_restriction, "get_country_by_ip", fail_country_lookup)
    monkeypatch.setattr(ip_restriction, "_NO_POLICY_WARNING_EMITTED", False)

    assert asyncio.run(ip_restriction.is_ip_allowed("198.51.100.10", db=object())) is True


def test_ip_restrictions_empty_specific_allowlist_can_still_apply_blocklist(monkeypatch):
    def fake_setting(page, key, db):
        values = {
            ("security", "enable_ip_restrictions"): True,
            ("security", "only_allow_specific_ip"): True,
            ("security", "allow_specific_ip"): [],
            ("security", "block_specific_ip"): ["198.51.100.10"],
            ("security", "only_allow_ip_from_specific_countries"): False,
            ("security", "allow_country_ip"): [],
            ("security", "block_country_ip"): [],
        }
        return values.get((page, key))

    monkeypatch.setattr(ip_restriction, "get_value_by_page_and_key", fake_setting)

    assert asyncio.run(ip_restriction.is_ip_allowed("198.51.100.10", db=object())) is False
    assert asyncio.run(ip_restriction.is_ip_allowed("198.51.100.11", db=object())) is True


def test_admin_blocked_ip_table_is_enforced_by_global_middleware(monkeypatch):
    """Admin-managed temporary IP blocks should protect every routed request."""
    events: list[tuple[str, str]] = []

    class FakeDb:
        def rollback(self):
            events.append(("rollback", ""))

        def close(self):
            events.append(("close", ""))

    fake_db = FakeDb()
    app = FastAPI()
    app.state.db = lambda: fake_db

    @app.get("/")
    async def blocked_route():
        raise AssertionError("blocked requests must not reach downstream handlers")

    app.add_middleware(ip_restriction.IPRestrictionMiddleware)

    monkeypatch.setattr(ip_restriction, "get_client_ip", lambda _request, _db: "198.51.100.10")
    monkeypatch.setattr(ip_restriction, "check_blocked_ip_address", lambda _ip, _db: 120)
    monkeypatch.setattr(
        ip_restriction,
        "record_ip_address_security_event",
        lambda _db, ip, event_type, **_kwargs: events.append((event_type, ip)),
    )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 403
    assert ("request_denied", "198.51.100.10") in events
    assert ("close", "") in events


def test_ip_restriction_middleware_closes_db_before_downstream_handler(monkeypatch):
    """Allowed requests must not hold the middleware DB session while routes run."""
    events: list[str] = []

    class FakeDb:
        closed = False

        def close(self):
            self.closed = True
            events.append("close")

    fake_db = FakeDb()
    app = FastAPI()
    app.state.db = lambda: fake_db

    @app.get("/api/v1/auth/access-status")
    async def allowed_route():
        events.append("route")
        assert fake_db.closed is True
        return {"status": "ok"}

    app.add_middleware(ip_restriction.IPRestrictionMiddleware)

    async def allow_request(_ip, _db):
        return True, None, None

    monkeypatch.setattr(ip_restriction, "get_client_ip", lambda _request, _db: "198.51.100.10")
    monkeypatch.setattr(ip_restriction, "check_blocked_ip_address", lambda _ip, _db: False)
    monkeypatch.setattr(
        ip_restriction,
        "_load_ip_policy_settings_snapshot",
        lambda _db: ip_restriction._IPPolicySettingsSnapshot({}),
    )
    monkeypatch.setattr(ip_restriction, "evaluate_ip_policy", allow_request)

    with TestClient(app) as client:
        response = client.get("/api/v1/auth/access-status")

    assert response.status_code == 200
    assert events == ["close", "route"]


def test_admin_block_ip_payload_normalizes_ipv6_and_rejects_invalid_ip():
    payload = BlockIP(ip_address="2001:0db8:0000:0000:0000:ff00:0042:8329")

    assert payload.ip_address == "2001:db8::ff00:42:8329"

    with pytest.raises(ValueError, match="valid IPv4 or IPv6"):
        BlockIP(ip_address="not-an-ip")


def test_security_ip_restriction_settings_normalize_network_policy_values():
    settings = SecuritySettings(
        allow_specific_ip=[" localhost ", "2001:0db8::1", "2001:db8::1"],
        block_specific_ip=["198.51.100.10"],
        allow_country_ip=["de", "US", "de"],
        block_country_ip=["cn"],
        trusted_proxies=["10.0.0.10", "172.31.250.0/24"],
    )

    assert settings.allow_specific_ip == ["127.0.0.1", "2001:db8::1"]
    assert settings.block_specific_ip == ["198.51.100.10"]
    assert settings.allow_country_ip == ["DE", "US"]
    assert settings.block_country_ip == ["CN"]
    assert settings.trusted_proxies == ["10.0.0.10/32", "172.31.250.0/24"]


@pytest.mark.parametrize("invalid_country_code", ["Germany", "ZZ"])
def test_security_ip_restriction_settings_reject_invalid_policy_values(invalid_country_code):
    with pytest.raises(ValueError, match="valid IPv4 or IPv6"):
        SecuritySettings(allow_specific_ip=["not-an-ip"])

    with pytest.raises(ValueError, match="Invalid country code"):
        SecuritySettings(allow_country_ip=[invalid_country_code])

    with pytest.raises(ValueError, match="valid IP addresses or CIDR ranges"):
        SecuritySettings(trusted_proxies=["not-a-proxy"])


def test_ip_restrictions_match_canonicalized_ipv6_policy_values(monkeypatch):
    def fake_setting(page, key, db):
        values = {
            ("security", "enable_ip_restrictions"): True,
            ("security", "only_allow_specific_ip"): True,
            ("security", "allow_specific_ip"): ["2001:0db8:0000:0000:0000:ff00:0042:8329"],
            ("security", "block_specific_ip"): [],
            ("security", "only_allow_ip_from_specific_countries"): False,
            ("security", "allow_country_ip"): [],
            ("security", "block_country_ip"): [],
        }
        return values.get((page, key))

    monkeypatch.setattr(ip_restriction, "get_value_by_page_and_key", fake_setting)

    assert asyncio.run(ip_restriction.is_ip_allowed("2001:db8::ff00:42:8329", db=object())) is True


def test_admin_block_route_rejects_current_request_ip_static_guard():
    source = (REPO_ROOT / "backend/app/ip_analytics/router.py").read_text(encoding="utf-8")

    assert "normalized_target_ip == normalized_admin_ip" in source
    assert "You cannot block the IP address used by your current admin session." in source


@pytest.mark.parametrize(
    "loopback_address",
    [
        "localhost",
        "127.0.0.2",
        "::1",
        "::ffff:127.0.0.1",
    ],
)
def test_loopback_bans_cannot_be_created_or_enforced(loopback_address):
    """Protect both persistence and middleware checks from loopback ban rows."""

    engine = create_engine("sqlite:///:memory:")
    database.Base.metadata.create_all(bind=engine, tables=[auth_models.BlockedIP.__table__])
    session = sessionmaker(bind=engine)()
    try:
        now = datetime.now(timezone.utc)
        create_result = auth_models.block_ip_address(
            loopback_address,
            now + timedelta(days=1),
            "unsafe loopback ban",
            session,
        )

        assert create_result == {
            "status": "error",
            "message": "Cannot block localhost IP addresses",
        }
        assert session.query(auth_models.BlockedIP).count() == 0

        # Simulate a dangerous row left by an older vulnerable version or a
        # manual database change. Runtime enforcement must fail safely so the
        # administrator can still reach Omlorix and remove the row.
        normalized_ip = auth_models.normalize_ip_address_for_storage(loopback_address)
        session.add(
            auth_models.BlockedIP(
                ip_address=normalized_ip,
                blocked_at=now,
                expires_at=now + timedelta(days=1),
                reason="legacy unsafe row",
            )
        )
        session.commit()

        assert auth_models.check_blocked_ip_address(normalized_ip, session) is False
        assert auth_models.deblock_ip_address(normalized_ip, session) == {"status": "success"}
        assert session.query(auth_models.BlockedIP).count() == 0
    finally:
        session.close()


def test_expired_blocked_ip_rows_are_deleted_from_block_table():
    engine = create_engine("sqlite:///:memory:")
    database.Base.metadata.create_all(bind=engine, tables=[auth_models.BlockedIP.__table__])
    session = sessionmaker(bind=engine)()
    try:
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                auth_models.BlockedIP(
                    ip_address="198.51.100.10",
                    blocked_at=now - timedelta(days=2),
                    expires_at=now - timedelta(minutes=1),
                    reason="expired",
                ),
                auth_models.BlockedIP(
                    ip_address="198.51.100.11",
                    blocked_at=now,
                    expires_at=now + timedelta(days=1),
                    reason="active",
                ),
            ]
        )
        session.commit()

        deleted = auth_models.delete_expired_blocked_ip_addresses(session, now=now)

        assert deleted == 1
        assert session.query(auth_models.BlockedIP).count() == 1
        assert session.query(auth_models.BlockedIP).first().ip_address == "198.51.100.11"
    finally:
        session.close()


def test_expired_block_cleanup_keeps_deletions_when_later_analytics_event_fails(monkeypatch):
    """A failed optional event must not roll back an earlier staged ban deletion."""
    engine = create_engine("sqlite:///:memory:")
    database.Base.metadata.create_all(bind=engine, tables=[auth_models.BlockedIP.__table__])
    session = sessionmaker(bind=engine)()
    event_calls = 0

    def record_event(*_args, **_kwargs):
        nonlocal event_calls
        event_calls += 1
        if event_calls == 2:
            raise RuntimeError("analytics unavailable")

    monkeypatch.setattr(auth_models, "record_ip_address_security_event", record_event)
    try:
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                auth_models.BlockedIP(
                    ip_address="198.51.100.20",
                    blocked_at=now - timedelta(days=2),
                    expires_at=now - timedelta(minutes=2),
                    reason="expired",
                ),
                auth_models.BlockedIP(
                    ip_address="198.51.100.21",
                    blocked_at=now - timedelta(days=2),
                    expires_at=now - timedelta(minutes=1),
                    reason="expired",
                ),
            ]
        )
        session.commit()

        deleted = auth_models.delete_expired_blocked_ip_addresses(session, now=now)

        assert deleted == 2
        assert event_calls == 2
        assert session.query(auth_models.BlockedIP).count() == 0
    finally:
        session.close()


def test_ip_statistics_settings_update_accepts_retention_days():
    payload = AdminIPAddressStatisticsSettingsUpdate(retention_days=365)

    assert payload.retention_days == 365

    with pytest.raises(ValueError):
        AdminIPAddressStatisticsSettingsUpdate(retention_days=0)


def test_ip_restriction_startup_validation_warns_for_enabled_empty_policy(monkeypatch, caplog):
    monkeypatch.setattr(
        settings_utils,
        "get_settings_page_data",
        lambda db, page: {
            "enable_ip_restrictions": True,
            "only_allow_specific_ip": True,
            "allow_specific_ip": [],
            "block_specific_ip": [],
            "only_allow_ip_from_specific_countries": True,
            "allow_country_ip": [],
            "block_country_ip": [],
        },
    )

    with caplog.at_level("WARNING", logger=settings_utils.logger.name):
        settings_utils.validate_ip_restriction_requirements(db=object())

    assert "security.enable_ip_restrictions is true" in caplog.text
    assert "security.only_allow_specific_ip is true" in caplog.text
    assert "security.only_allow_ip_from_specific_countries is true" in caplog.text


def test_audit_ip_hash_without_configured_salt_uses_random_non_default_salt(monkeypatch):
    monkeypatch.setattr(logging_models, "_IP_HASH_SALT", None)
    monkeypatch.setattr(logging_models, "_GENERATED_IP_HASH_SALT", None)
    monkeypatch.setattr(logging_models, "_GENERATED_IP_HASH_SALT_WARNED", False)

    hashed = logging_models._sanitize_ip("203.0.113.10")
    repeated = logging_models._sanitize_ip("203.0.113.10")
    predictable = hashlib.sha256("ip:omlorix-log-ip-salt:203.0.113.10".encode("utf-8")).hexdigest()

    assert hashed == repeated
    assert hashed != f"ip_{predictable[:12]}"


def test_authentication_log_sanitizers_hash_sensitive_metadata(monkeypatch):
    monkeypatch.setattr(logging_models, "_IP_HASH_SALT", "test-auth-log-salt")

    message = logging_models._sanitize_log_message("User with email Victim@example.com already exists.")
    device = logging_models._sanitize_device_info("PoC-UA/1.0 (SecretDeviceFingerprint)")
    ip_address = logging_models._sanitize_ip("203.0.113.77")

    assert message == "User with email email_5d26d86338eb already exists."
    assert "Victim@example.com" not in message
    assert "example.com" not in message
    assert device == "device_a9bda7069162"
    assert "SecretDeviceFingerprint" not in device
    assert ip_address == "ip_b94bfc650a69"
    assert "203.0.113.77" not in ip_address


def test_imported_chat_meta_strips_code_execution_container_binding():
    sanitized = chat_io._sanitize_import_chat_meta(
        {
            "code_execution": {"container_id": "container-from-another-user"},
            "import_source_chat_id": "old-chat",
        }
    )

    assert "code_execution" not in sanitized
    assert sanitized["import_source_chat_id"] == "old-chat"


def test_code_execution_cached_container_requires_runtime_owner_stamp():
    class FakeQuery:
        def __init__(self, chat):
            self.chat = chat

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return self.chat

    class FakeDb:
        def __init__(self, chat):
            self.chat = chat

        def query(self, _model):
            return FakeQuery(self.chat)

    valid_chat = SimpleNamespace(
        meta={
            "code_execution": {
                "container_id": "container-owned",
                "user_id": "user-1",
                "chat_id": "chat-1",
                "base_url": "http://executor:8000",
            }
        }
    )
    imported_chat = SimpleNamespace(
        meta={
            "code_execution": {
                "container_id": "container-attacker",
                "base_url": "http://executor:8000",
            }
        }
    )

    assert code_execution_utils._get_chat_container_id(FakeDb(valid_chat), "user-1", "chat-1", "http://executor:8000") == "container-owned"
    assert code_execution_utils._get_chat_container_id(FakeDb(imported_chat), "user-1", "chat-1", "http://executor:8000") is None
    assert code_execution_utils._get_chat_container_id(FakeDb(valid_chat), "user-2", "chat-1", "http://executor:8000") is None
    assert code_execution_utils._get_chat_container_id(FakeDb(valid_chat), "user-1", "chat-1", "http://other-executor:8000") is None


def test_code_execution_public_output_does_not_include_container_id():
    output = code_execution_utils.format_code_execution_tool_output(
        {
            "result": {
                "container_id": "container-secret",
                "language": "python",
                "stdout": "ok",
            },
            "saved_files": [],
        }
    )

    assert "container-secret" not in output
    assert "container_id" not in output
    assert "stdout:\nok" in output


def test_workspace_notifications_do_not_template_untrusted_attributes():
    source = (REPO_ROOT / "frontend/js/chat/workspaceNotifications.js").read_text(encoding="utf-8")

    assert 'data-share-id="${' not in source
    assert 'data-item-type="${' not in source
    assert 'data-share-type="${' not in source
    assert 'data-category="${' not in source
    assert 'data-type="${notification.type}' not in source
    assert "function normalizeNotificationType" in source
    assert "category.textContent = translatedCategory" in source
    assert "typeBadge.textContent = translatedType" in source
    assert "actions.setAttribute('data-share-id', shareId)" in source


def test_project_share_invitations_use_disclosed_join_flow():
    source = (REPO_ROOT / "frontend/js/chat/workspaceNotifications.js").read_text(encoding="utf-8")

    assert 'endpoint = `/api/v1/projects/shared/${encodeURIComponent(shareId)}/join`' not in source
    assert 'const shareUrl = details.share_url || `/projects/join/${encodeURIComponent(shareId)}`' in source


def test_audio_preview_uses_dom_properties_for_untrusted_metadata():
    source = (REPO_ROOT / "frontend/js/chat/files.js").read_text(encoding="utf-8")

    assert 'title="${Utils.escapeHtml(fileName)}"' not in source
    assert '<span class="files-preview-audio-format">${extension}</span>' not in source
    assert '<source src="${objectUrl}"' not in source
    assert "titleEl.textContent = fileName" in source
    assert "titleEl.title = fileName" in source
    assert "formatEl.textContent = extension" in source
    assert "sourceEl.src = objectUrl" in source


def test_file_previews_cap_large_content_before_materializing():
    files_source = (REPO_ROOT / "frontend/js/chat/files.js").read_text(encoding="utf-8")
    share_source = (REPO_ROOT / "frontend/js/chat-share.js").read_text(encoding="utf-8")

    assert "textPreviewMaxBytes: 1024 * 1024" in files_source
    assert "binaryPreviewMaxBytes: 25 * 1024 * 1024" in files_source
    assert "requestHeaders.Range = `bytes=0-${FilesPreview.textPreviewMaxBytes - 1}`" in files_source
    assert "FilesPreview.readTextPreviewContent(response)" in files_source
    assert "FilesPreview.createPreviewTooLargeElement" in files_source
    assert "const textContent = await response.text();" not in files_source

    assert "const TEXT_PREVIEW_MAX_BYTES = 1024 * 1024" in share_source
    assert "const BINARY_PREVIEW_MAX_BYTES = 25 * 1024 * 1024" in share_source
    assert "Range: `bytes=0-${TEXT_PREVIEW_MAX_BYTES - 1}`" in share_source
    assert "readTextPreviewContent(response)" in share_source
    assert "preview.createPreviewTooLarge(file)" in share_source
    assert "const text = await response.text();" not in share_source


def test_slide_preview_documents_include_csp_and_sandboxed_iframes():
    source = (REPO_ROOT / "frontend/js/chat/slide-presentation-widget.js").read_text(encoding="utf-8")

    assert "const _SLIDE_PREVIEW_CSP" in source
    assert '<meta http-equiv="Content-Security-Policy" content="${_SLIDE_PREVIEW_CSP}">' in source
    assert "iframe.setAttribute('sandbox', '')" in source
    assert "ssIframe.setAttribute('sandbox', '')" in source
    assert "document.write" not in source
    assert "doc.write(html)" not in source


def test_inline_visualization_previews_default_to_static_proxy_with_no_http_csp():
    """Protect static visualizer previews, disabled evaluation, and no-network CSP."""
    source = "".join(
        (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for relative_path in (
            "frontend/js/chat/rendering/visualization-renderers.js",
            "frontend/js/chat/rendering/preview-modals.js",
        )
    )
    visualizer_csp_block = source.split("function buildVisualizerPreviewContentSecurityPolicy", 1)[1].split("function buildVisualizerPreviewDocument", 1)[0]

    assert "const ALLOWED_PREVIEW_ACTIONS = Object.freeze(['expand', 'run-interactive']);" in source
    assert "data-preview-action=\"run-interactive\"" in source
    assert "view-source" not in source
    assert "openVisualizerSourceModal" not in source
    assert "function isVisualizerLanguage" not in source
    assert "openVisualizerPreviewModal" not in source
    assert "previewKind === 'visualizer'" not in source
    assert "window.OmlorixCanvasHtmlPreview" in source
    assert "proxyRuntime.render(iframe, previewDocument" in source
    assert "allowEval: false," in source
    assert "relayVisualizationMessages: true," in source
    assert "iframe.srcdoc = buildVisualizerPreviewDocument" not in source
    assert "allowScripts: false," in source
    assert "connect-src 'none';" in visualizer_csp_block
    assert "http:" not in visualizer_csp_block


def test_byok_private_network_targets_require_explicit_allowlist(monkeypatch):
    def fake_settings(_db, page):
        assert page == "general"
        return SimpleNamespace(
            data={
                "offline_mode": False,
                "external_requests_mode": "allow_all",
                "external_requests_allowlist": [],
            }
        )

    monkeypatch.setattr(outbound_policy, "get_settings_page", fake_settings)

    with pytest.raises(outbound_policy.OutboundRequestBlockedError):
        outbound_policy.assert_llm_config_allowed(
            object(),
            provider_type="ollama",
            settings={"base_url": "http://127.0.0.1:11434"},
            feature="BYOK test",
            require_private_allowlist=True,
        )

    monkeypatch.setattr(
        outbound_policy,
        "get_settings_page",
        lambda _db, _page: SimpleNamespace(
            data={
                "offline_mode": False,
                "external_requests_mode": "allow_all",
                "external_requests_allowlist": ["127.0.0.1"],
            }
        ),
    )

    outbound_policy.assert_llm_config_allowed(
        object(),
        provider_type="ollama",
        settings={"base_url": "http://127.0.0.1:11434"},
        feature="BYOK test",
        require_private_allowlist=True,
    )


def test_code_execution_service_url_uses_outbound_policy(monkeypatch):
    class FakeDb:
        def close(self):
            pass

    def deny_url(*_args, **_kwargs):
        raise outbound_policy.OutboundRequestBlockedError(
            target="http://127.0.0.1:8001",
            feature="Code execution service",
            policy_mode=outbound_policy.OutboundAccessMode.deny_all,
            reason="all outbound network access is disabled",
        )

    monkeypatch.setattr(
        code_execution_utils,
        "_get_code_execution_runtime_config",
        lambda: {
            "max_output_length": 10000,
        },
    )
    monkeypatch.setattr(
        code_execution_utils,
        "get_service_connection_candidates",
        lambda *_args, **_kwargs: [
            {
                "id": "svc-code",
                "name": "Code",
                "base_url": "http://127.0.0.1:8001",
                "api_key": "",
            }
        ],
    )
    monkeypatch.setattr(code_execution_utils, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(code_execution_utils, "assert_url_allowed", deny_url)

    with pytest.raises(RuntimeError, match="Code execution service blocked"):
        code_execution_utils.execute_code("print('ok')", user_id="user-1")


def test_slide_renderer_service_url_uses_outbound_policy(monkeypatch, tmp_path):
    def deny_url(*_args, **_kwargs):
        raise outbound_policy.OutboundRequestBlockedError(
            target="http://127.0.0.1:8002",
            feature="Slide renderer service",
            policy_mode=outbound_policy.OutboundAccessMode.deny_all,
            reason="all outbound network access is disabled",
        )

    monkeypatch.setattr(
        slide_rendering_utils,
        "get_service_connection_candidates",
        lambda *_args, **_kwargs: [
            {
                "id": "svc-slides",
                "name": "Slides",
                "base_url": "http://127.0.0.1:8002",
                "api_key": "",
            }
        ],
    )
    monkeypatch.setattr(slide_rendering_utils, "_build_input_files_payload", lambda **_kwargs: [])
    monkeypatch.setattr(slide_rendering_utils, "assert_url_allowed", deny_url)

    with pytest.raises(RuntimeError, match="Slide renderer service blocked"):
        slide_rendering_utils.render_slide_presentation(
            "<html></html>",
            "user-1",
            "deck.pptx",
            presentation_dir=tmp_path,
            db=object(),
        )


def test_chat_sidebar_title_is_not_interpolated_into_inner_html():
    source = (REPO_ROOT / "frontend/js/chat/chatsHelper.js").read_text(encoding="utf-8")

    assert "<p>${row.dataset.chatTitle}</p>" not in source
    assert "querySelector('a.sidebar-element-button > p').textContent = row.dataset.chatTitle" in source


def test_default_compose_backend_commands_drop_privileges():
    recursive_runtime_ownership = (
        "chown -R appuser:appuser /app/app/logs /app/app/data /app/backups"
    )
    root_runtime_ownership = (
        "chown appuser:appuser /app/app/logs /app/app/data /app/backups"
    )
    for compose_name in (
        "docker-compose.server.yml",
        "docker-compose.managed-cloud.yml",
    ):
        source = (REPO_ROOT / compose_name).read_text(encoding="utf-8")

        assert recursive_runtime_ownership not in source
        assert root_runtime_ownership in source
        assert "exec gosu appuser ./migrate.sh" in source
        assert "exec gosu appuser uvicorn app.main:app" in source
        assert "exec gosu appuser python -m app.automations.scheduler" in source
        assert "exec gosu appuser rq worker" in source
        assert "--worker-class app.automations.rq_worker.TelemetryWorker" in source

    dockerfile = (REPO_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    assert recursive_runtime_ownership not in dockerfile
    assert root_runtime_ownership in dockerfile
    # Image-layer ownership is bounded by the image contents and remains valid.
    assert "chown -R appuser:appuser /app" in dockerfile


def test_default_compose_redis_requires_authentication():
    source = (REPO_ROOT / "docker-compose.server.yml").read_text(encoding="utf-8")

    assert "REDIS_PASSWORD: ${REDIS_PASSWORD:-}" in source
    assert "--requirepass" in source
    # setup.sh percent-encodes reserved password characters when it writes
    # REDIS_URL. Rebuilding the URI from the raw password here would turn `#`,
    # `@`, and similar characters back into URI syntax and break authentication.
    # API/migration and the shared worker environments all receive the same
    # already-encoded URL; dedicated workers must not reconstruct credentials.
    assert source.count("REDIS_URL: ${REDIS_URL:-redis://redis:6379/0}") == 6
    assert "redis://:${REDIS_PASSWORD" not in source


def test_observability_compose_configures_authenticated_backend_metrics():
    compose_source = (REPO_ROOT / "docker-compose.observability.yml").read_text(encoding="utf-8")
    prometheus_source = (REPO_ROOT / "otel/prometheus.yml").read_text(encoding="utf-8")

    assert "metrics_token:" in compose_source
    assert "head -c 32 /dev/urandom | base64 > /metrics/token" in compose_source
    assert 'PROMETHEUS_METRICS_TOKEN: ""' in compose_source
    assert "PROMETHEUS_METRICS_TOKEN_FILE: /run/omlorix-metrics/token" in compose_source
    assert "PROMETHEUS_METRICS_PUBLIC" not in compose_source
    assert "metrics_token:/run/omlorix-metrics:ro" in compose_source
    assert "credentials_file: /run/omlorix-metrics/token" in prometheus_source


def test_cors_localhost_bootstrap_is_disabled_in_production(monkeypatch):
    from app.middleware import cors as cors_middleware

    class FakeDb:
        def close(self):
            pass

    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.setenv("MODE", "production")
    monkeypatch.setattr(cors_middleware, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(cors_middleware, "get_value_by_page_and_key", lambda *_args, **_kwargs: None)

    assert cors_middleware._load_cors_allowed_origins() == []


def test_startup_enforces_ip_address_statistics_requirements():
    source = (REPO_ROOT / "backend/app/main.py").read_text(encoding="utf-8")

    active_call = "        validate_ip_address_statistics_requirements(db)"
    commented_call = "        # validate_ip_address_statistics_requirements(db)"

    assert active_call in source
    assert commented_call not in source


def test_cors_credentials_use_explicit_methods_and_headers():
    source = (REPO_ROOT / "backend/app/main.py").read_text(encoding="utf-8")

    assert 'allow_methods=["*"]' not in source
    assert 'allow_headers=["*"]' not in source
    assert "allow_origins=_load_cors_allowed_origins()" not in source
    assert "allow_origin_resolver=_load_cors_allowed_origins" in source
    assert '"Authorization"' in source
    assert '"X-Omlorix-User-Authorization"' in source


def test_nginx_does_not_reflect_preflight_origins_with_credentials():
    config_paths = (
        REPO_ROOT / "nginx/default.http.conf.template/default.conf",
    )

    for path in config_paths:
        source = path.read_text(encoding="utf-8")
        assert "Access-Control-Allow-Origin $http_origin" not in source
        assert 'Access-Control-Allow-Credentials "true"' not in source


def test_metrics_endpoint_requires_public_flag_or_bearer_token(monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main, "is_prometheus_metrics_enabled", lambda: True)
    monkeypatch.setattr(app_main, "collect_prometheus_metrics", lambda: (b"ok", "text/plain"))
    monkeypatch.delenv("PROMETHEUS_METRICS_PUBLIC", raising=False)
    monkeypatch.delenv("PROMETHEUS_METRICS_TOKEN", raising=False)

    local_request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 12345),
            "path": "/metrics",
            "headers": [],
        }
    )
    with pytest.raises(HTTPException) as exc:
        app_main.prometheus_metrics(local_request)
    assert exc.value.status_code == 403

    monkeypatch.setenv("PROMETHEUS_METRICS_TOKEN", "metrics-secret")
    token_request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": ("203.0.113.10", 12345),
            "path": "/metrics",
            "headers": [(b"authorization", b"Bearer metrics-secret")],
        }
    )
    response = app_main.prometheus_metrics(token_request)
    assert response.body == b"ok"


def test_metrics_endpoint_accepts_bearer_token_from_file(monkeypatch, tmp_path):
    from app import main as app_main

    token_file = tmp_path / "metrics-token"
    token_file.write_text("file-secret\n", encoding="utf-8")

    monkeypatch.setattr(app_main, "is_prometheus_metrics_enabled", lambda: True)
    monkeypatch.setattr(app_main, "collect_prometheus_metrics", lambda: (b"ok", "text/plain"))
    monkeypatch.delenv("PROMETHEUS_METRICS_PUBLIC", raising=False)
    monkeypatch.delenv("PROMETHEUS_METRICS_TOKEN", raising=False)
    monkeypatch.setenv("PROMETHEUS_METRICS_TOKEN_FILE", str(token_file))

    token_request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": ("203.0.113.10", 12345),
            "path": "/metrics",
            "headers": [(b"authorization", b"Bearer file-secret")],
        }
    )

    response = app_main.prometheus_metrics(token_request)
    assert response.body == b"ok"


def test_rate_limiter_uses_local_counter_when_redis_is_unavailable():
    limiter = rate_limiter.RedisRateLimiterMiddleware(lambda scope, receive, send: None)
    rule = rate_limiter._RateLimitRule("auth", limit=1, window_seconds=60)
    window_start = int(time.time() // rule.window_seconds) * rule.window_seconds

    first_count, _first_ttl = limiter._record_local_attempt("test-key", rule, window_start)
    second_count, _second_ttl = limiter._record_local_attempt("test-key", rule, window_start)

    assert first_count == 1
    assert second_count == 2
    assert second_count > rule.limit


def test_widget_frame_creation_uses_dedicated_rate_limit(monkeypatch):
    """Large frame creation must not inherit the permissive default API rule."""

    monkeypatch.setenv("RATE_LIMIT_WIDGET_FRAME_RPM", "7")
    limiter = rate_limiter.RedisRateLimiterMiddleware(lambda scope, receive, send: None)

    create_rule = limiter._resolve_rule("/api/v1/llm/widgets/frame")
    load_rule = limiter._resolve_rule("/api/v1/llm/widgets/frame/example-frame-id")

    assert create_rule.name == "widget_frames"
    assert create_rule.limit == 7
    assert load_rule.name == "default"


def test_default_widget_frame_rate_limit_allows_full_transcript_hydration(monkeypatch):
    """A normal transcript may contain substantially more than ten widgets."""

    monkeypatch.delenv("RATE_LIMIT_WIDGET_FRAME_RPM", raising=False)
    limiter = rate_limiter.RedisRateLimiterMiddleware(lambda scope, receive, send: None)

    assert limiter._resolve_rule("/api/v1/llm/widgets/frame").limit >= 120


def test_sqlite_fallback_fails_in_explicit_production(monkeypatch, tmp_path):
    monkeypatch.setenv("MODE", "production")
    monkeypatch.delenv("OMLORIX_ALLOW_SQLITE_FALLBACK", raising=False)
    for suffix in ("URL", "USER", "PASSWORD", "HOST", "PORT", "NAME"):
        monkeypatch.delenv(f"TEST_DATABASE_{suffix}", raising=False)

    with pytest.raises(RuntimeError, match="Database credentials missing"):
        database._resolve_database_configuration("TEST_DATABASE", tmp_path / "app.db")


def test_encrypted_orm_value_fails_closed_on_decrypt_error(monkeypatch):
    def fail_decrypt(_value):
        raise ValueError("bad key")

    monkeypatch.setattr(sqlalchemy_encryption, "decrypt_value", fail_decrypt)

    with pytest.raises(ValueError, match="Failed to decrypt value"):
        sqlalchemy_encryption._decrypt_with_fallback("ciphertext", field="EncryptedString")


def test_passkey_begin_descriptors_are_padded_without_user_id_or_count_oracle(monkeypatch):
    monkeypatch.setattr(passkeys, "get_value_by_page_and_key", lambda page, key, db: "x" * 32)
    credential = SimpleNamespace(credential_id=bytes_to_base64url(b"real-credential-id"))

    empty_descriptors = passkeys._padded_authentication_descriptors(
        object(),
        normalized_identifier="missing@example.com",
        credentials=[],
    )
    real_descriptors = passkeys._padded_authentication_descriptors(
        object(),
        normalized_identifier="user@example.com",
        credentials=[credential],
    )

    assert len(empty_descriptors) == passkeys._AUTH_ALLOW_CREDENTIALS_SIZE
    assert len(real_descriptors) == passkeys._AUTH_ALLOW_CREDENTIALS_SIZE
    assert any(item.id == b"real-credential-id" for item in real_descriptors)
