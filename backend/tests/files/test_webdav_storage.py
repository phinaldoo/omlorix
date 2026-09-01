from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from webdav3.client import Urn as RealUrn

from app.files.storage.webdav import WebDAVUserFileStorageAdapter


def test_webdav_tls_verification_does_not_disable_existence_checks(monkeypatch):
    """Self-signed TLS mode must still let migration detect missing objects."""
    captured = {}

    class FakeClient:
        def __init__(self, options):
            captured["options"] = options
            self.session = SimpleNamespace(verify=None)
            self.verify = None

        def execute_request(self, *, action, path):
            assert action == "check"
            assert path.endswith("/file.txt")
            return SimpleNamespace(status_code=200, close=lambda: None)

    webdav_module = ModuleType("webdav3")
    webdav_client_module = ModuleType("webdav3.client")
    webdav_client_module.Client = FakeClient
    webdav_client_module.Urn = RealUrn
    webdav_module.client = webdav_client_module
    monkeypatch.setitem(sys.modules, "webdav3", webdav_module)
    monkeypatch.setitem(sys.modules, "webdav3.client", webdav_client_module)

    adapter = WebDAVUserFileStorageAdapter(
        {
            "url": "https://storage.example.invalid",
            "username": "user",
            "password": "password",
            "verify_ssl": False,
        }
    )

    assert captured["options"]["disable_check"] is True
    assert captured["options"]["verify"] is False
    assert adapter.client.verify is False
    assert adapter.client.session.verify is False
    assert adapter.exists("user-1/file.txt") is True
