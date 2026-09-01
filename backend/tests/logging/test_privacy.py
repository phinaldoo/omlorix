from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.logging import privacy as logging_privacy
from app.logging.models import (
    _normalized_audit_log_payload,
    _sanitize_audit_details,
    _sanitize_log_message,
)


def test_stream_line_metadata_redacts_payload_content():
    metadata = logging_privacy.stream_line_metadata(
        '{"t": "c", "seq": 7, "d": "secret prompt text from a user"}'
    )

    assert metadata["event_type"] == "c"
    assert metadata["seq"] == 7
    assert metadata["payload_kind"] == "str"
    assert metadata["payload_length"] == 30
    assert "secret prompt text" not in repr(metadata)


def test_object_event_metadata_redacts_provider_chunk_content():
    event = SimpleNamespace(
        type="response.output_text.delta",
        delta="secret provider chunk",
        response=SimpleNamespace(id="resp_123", model="gpt-test"),
    )

    metadata = logging_privacy.object_event_metadata(event)

    assert metadata == {
        "event_type": "response.output_text.delta",
        "response_id": "resp_123",
        "model": "gpt-test",
        "delta_length": 21,
    }
    assert "secret provider chunk" not in repr(metadata)


def test_redacted_debug_logging_requires_explicit_flag(monkeypatch):
    monkeypatch.delenv("OMLORIX_LOG_REDACTED_DEBUG", raising=False)
    assert logging_privacy.redacted_debug_logging_enabled() is False

    monkeypatch.setenv("OMLORIX_LOG_REDACTED_DEBUG", "true")
    assert logging_privacy.redacted_debug_logging_enabled() is True


def test_exception_metadata_redacts_exception_detail_text():
    exc = ValueError("secret failure text")
    exc.detail = "top secret detail"

    metadata = logging_privacy.exception_metadata(exc)

    assert metadata == {
        "exc_type": "ValueError",
        "detail_type": "str",
        "detail_length": 17,
    }
    assert "secret failure text" not in repr(metadata)
    assert "top secret detail" not in repr(metadata)


def test_audit_details_redact_sensitive_nested_containers():
    details = {
        "provider": "openai",
        "api_key": ["sk-live-secret", {"nested": "still-secret"}],
        "config": {
            "headers": {
                "Authorization": {"bearer": "token-secret"},
            },
            "metadata": ["visible"],
        },
    }

    sanitized = _sanitize_audit_details(details)

    assert sanitized == {
        "provider": "openai",
        "api_key": "<redacted>",
        "config": {
            "headers": {
                "Authorization": "<redacted>",
            },
            "metadata": ["visible"],
        },
    }
    assert "sk-live-secret" not in repr(sanitized)
    assert "token-secret" not in repr(sanitized)


def test_audit_details_preserve_parent_key_for_list_items():
    sanitized = _sanitize_audit_details(
        {
            "ip_address": ["203.0.113.10"],
            "devices": ["test@example.com"],
        }
    )

    assert sanitized["ip_address"][0].startswith("ip_")
    assert "203.0.113.10" not in repr(sanitized)
    assert sanitized["devices"][0].startswith("device_")
    assert "test@example.com" not in repr(sanitized)


def test_audit_details_fingerprint_share_capability_fields():
    sanitized = _sanitize_audit_details(
        {
            "share_id": "prompt-share-token",
            "clone_share_id": ["clone-token-1", "clone-token-2"],
            "share_url": "https://chat.example/prompts/shared/prompt-share-token",
            "nested": {
                "live_share_id": "live-token",
                "collaborate_share_id": "collab-token",
            },
        }
    )

    assert sanitized["share_id"].startswith("share_fp_")
    assert sanitized["share_id"] != "prompt-share-token"
    assert all(item.startswith("share_fp_") for item in sanitized["clone_share_id"])
    assert sanitized["share_url"].startswith("share_url_fp_")
    assert sanitized["nested"]["live_share_id"].startswith("share_fp_")
    assert sanitized["nested"]["collaborate_share_id"].startswith("share_fp_")
    assert "prompt-share-token" not in repr(sanitized)
    assert "clone-token-1" not in repr(sanitized)
    assert "live-token" not in repr(sanitized)


def test_audit_text_is_normalized_to_one_physical_line():
    sanitized = _sanitize_log_message(
        "reviewed\r\nforged-entry\tcontinued\u0085next\u2028last\u2029done"
    )

    assert sanitized == "reviewed forged-entry continued next last done"
    assert sanitized is not None
    assert sanitized.splitlines() == [sanitized]


def test_nested_audit_details_cannot_inject_text_log_lines():
    sanitized = _sanitize_audit_details(
        {"reason_context": "first line\nsecond line", "items": ["a\rb"]}
    )

    assert sanitized == {
        "reason_context": "first line second line",
        "items": ["a b"],
    }


def test_all_text_audit_payload_fields_are_single_line():
    payload = _normalized_audit_log_payload(
        user_id="user\nforged",
        action="ACTION\rFORGED",
        reason="reason\tcontinued",
        details=None,
        ip_address=None,
        user_agent=None,
        category="security\u2028forged",
    )

    assert payload["user_id"] == "user forged"
    assert payload["action"] == "ACTION FORGED"
    assert payload["reason"] == "reason continued"
    assert payload["category"] == "security forged"


def test_safe_audit_metadata_survives_sensitive_key_scrubbing():
    payload = _normalized_audit_log_payload(
        user_id="user-1",
        action="AUDIT_EVENT",
        reason=None,
        details={
            "login_method": "password",
            "realtime_record_count": 3,
            "auth_token": "must-not-survive",
            "session_id": "must-not-survive",
        },
        ip_address=None,
        user_agent=None,
        category="security",
    )

    assert payload["details"] == {
        "login_method": "password",
        "realtime_record_count": 3,
        "auth_token": "<redacted>",
        "session_id": "<redacted>",
    }


def test_safe_login_revocation_metadata_survives_sensitive_key_scrubbing():
    sanitized = _sanitize_audit_details(
        {
            "revocation_scope": "single_session",
            "target_count": 1,
            "current_login_revoked": True,
            "requested_login_id": "login-1",
            "target_login": {
                "login_id": "login-1",
                "login_fingerprint": "login_fp_0123456789ab",
                "device_info": "Firefox on macOS",
                "ip_address": "198.51.100.0/24",
                "current": True,
            },
        }
    )

    assert sanitized["revocation_scope"] == "single_session"
    assert sanitized["target_count"] == 1
    assert sanitized["current_login_revoked"] is True
    assert sanitized["requested_login_id"] == "login-1"
    assert sanitized["target_login"]["login_id"] == "login-1"
    assert sanitized["target_login"]["login_fingerprint"] == "login_fp_0123456789ab"
    assert sanitized["target_login"]["device_info"].startswith("device_")
    assert sanitized["target_login"]["ip_address"].startswith("ip_")
