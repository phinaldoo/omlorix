from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.llm.models import export_llm_providers
from app.llm.openai.custom_headers import (
    CUSTOM_HEADER_SECRET_PLACEHOLDER,
    custom_headers_to_dict,
    normalize_custom_header_entries,
    preserve_redacted_custom_headers_in_settings,
    redact_custom_header_entries,
    redact_custom_headers_for_display_settings,
    redact_custom_headers_in_settings,
)
from app.llm.openai.schemas import OpenaiSettings


@pytest.mark.parametrize(
    "header_name",
    [
        "Authorization",
        "Cookie",
        "Host",
        "Forwarded",
        "X-Forwarded-For",
        "Proxy-Authorization",
        "Proxy-Connection",
        "Connection",
        "Transfer-Encoding",
        "TE",
        "Content-Length",
        "X-Real-IP",
    ],
)
def test_custom_headers_reject_sensitive_and_hop_by_hop_names(header_name):
    with pytest.raises(ValueError, match="not allowed"):
        normalize_custom_header_entries([f"{header_name}: unsafe"])


def test_openai_settings_validation_rejects_sensitive_custom_headers():
    with pytest.raises(ValidationError):
        OpenaiSettings.model_validate({"custom_headers": ["Authorization: unsafe"]})


def test_custom_headers_allow_regular_extension_headers():
    assert custom_headers_to_dict({"X-API-Key": "provider-secret", "X-Request-ID": "req-1"}) == {
        "X-API-Key": "provider-secret",
        "X-Request-ID": "req-1",
    }


def test_custom_header_redaction_keeps_names_and_hides_values():
    assert redact_custom_header_entries(["X-API-Key: provider-secret", "X-Trace-ID: trace-1"]) == [
        f"X-API-Key: {CUSTOM_HEADER_SECRET_PLACEHOLDER}",
        f"X-Trace-ID: {CUSTOM_HEADER_SECRET_PLACEHOLDER}",
    ]


def test_custom_header_settings_export_uses_separate_redacted_secret_field():
    settings = redact_custom_headers_in_settings(
        {
            "timeout": 30,
            "custom_headers": ["X-API-Key: provider-secret", "X-Trace-ID: trace-1"],
        }
    )

    assert settings == {
        "timeout": 30,
        "custom_headers_redacted": [
            f"X-API-Key: {CUSTOM_HEADER_SECRET_PLACEHOLDER}",
            f"X-Trace-ID: {CUSTOM_HEADER_SECRET_PLACEHOLDER}",
        ],
    }
    assert "provider-secret" not in str(settings)
    assert "trace-1" not in str(settings)


def test_custom_header_display_settings_keep_editable_key_with_redacted_values():
    settings = redact_custom_headers_for_display_settings(
        {
            "timeout": 30,
            "custom_headers": ["X-API-Key: provider-secret", "X-Trace-ID: trace-1"],
        }
    )

    assert settings == {
        "timeout": 30,
        "custom_headers": [
            f"X-API-Key: {CUSTOM_HEADER_SECRET_PLACEHOLDER}",
            f"X-Trace-ID: {CUSTOM_HEADER_SECRET_PLACEHOLDER}",
        ],
    }
    assert "provider-secret" not in str(settings)
    assert "trace-1" not in str(settings)


def test_custom_header_update_preserves_redacted_existing_values():
    settings = preserve_redacted_custom_headers_in_settings(
        {
            "custom_headers": ["X-API-Key: provider-secret", "X-Trace-ID: trace-1"],
        },
        {
            "timeout": 60,
            "custom_headers": [
                f"X-API-Key: {CUSTOM_HEADER_SECRET_PLACEHOLDER}",
                "X-New-Header: fresh",
            ],
        },
    )

    assert settings == {
        "timeout": 60,
        "custom_headers": ["X-API-Key: provider-secret", "X-New-Header: fresh"],
    }


def test_llm_provider_export_redacts_custom_header_values():
    provider = SimpleNamespace(
        id="provider-1",
        provider="openai",
        name="OpenAI",
        icon="openai",
        api_key="sk-provider",
        settings={
            "timeout": 30,
            "custom_headers": ["X-API-Key: provider-secret"],
        },
        status={"available": "unknown"},
    )

    class Db:
        def query(self, _model):
            return self

        def all(self):
            return [provider]

    payload = export_llm_providers(Db())
    exported_settings = payload["data"]["providers"][0]["settings"]

    assert exported_settings["custom_headers_redacted"] == [
        f"X-API-Key: {CUSTOM_HEADER_SECRET_PLACEHOLDER}"
    ]
    assert "timeout" not in exported_settings
    assert "custom_headers" not in exported_settings
    assert "provider-secret" not in str(payload)
