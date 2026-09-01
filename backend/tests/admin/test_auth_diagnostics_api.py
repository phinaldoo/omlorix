"""Administrator OIDC diagnostic endpoint contract tests."""

import asyncio
from types import SimpleNamespace

from app.admin.auth_diagnostics import router
from app.admin.auth_diagnostics.schemas import OIDCConfigurationTestRequest


def test_oidc_configuration_test_is_audited_without_secrets(monkeypatch):
    audit_calls: list[dict] = []

    async def fake_test(_db, _request):
        return {
            "status": "failed",
            "reference": "AUTH-123",
            "callback_url": "https://chat.example/api/v1/auth/sso/oidc/callback",
            "checks": [
                {
                    "code": "oidc_issuer_match",
                    "status": "failed",
                    "details": {
                        "configured_issuer": "http://localhost:9000/application/o/omlorix/",
                        "metadata_issuer": "http://host.docker.internal:9000/application/o/omlorix/",
                    },
                }
            ],
        }

    monkeypatch.setattr(router, "test_oidc_configuration", fake_test)
    monkeypatch.setattr(router, "create_audit_log", lambda **kwargs: audit_calls.append(kwargs))
    monkeypatch.setattr(router, "get_audit_request_ip", lambda *_args: "ip-hash")

    result = asyncio.run(
        router.run_oidc_configuration_test(
            payload=OIDCConfigurationTestRequest(),
            request=SimpleNamespace(headers={"user-agent": "pytest"}),
            db=object(),
            db_log=object(),
            admin_user=SimpleNamespace(id="admin-1"),
        )
    )

    assert result["checks"][0]["code"] == "oidc_issuer_match"
    assert audit_calls[0]["action"] == "TEST_OIDC_CONFIGURATION"
    assert audit_calls[0]["details"] == {
        "status": "failed",
        "reference": "AUTH-123",
    }
    assert "secret" not in repr(audit_calls).lower()
