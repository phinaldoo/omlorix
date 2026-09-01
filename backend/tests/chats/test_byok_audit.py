import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.chats import router as chat_router


def _byok_payload():
    return {
        "provider": "openai_chat_completions",
        "provider_id": "byok_provider_123456789",
        "model_name": "gpt-4.1-mini",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-secret-value",
    }


def test_byok_audit_details_include_destination_without_secrets():
    details = chat_router._build_byok_audit_details(
        {
            "provider": "openai_chat_completions",
            "provider_id": "byok_provider_123456789",
            "provider_name": "Personal OpenAI",
            "model_name": "gpt-4.1-mini",
            "api_key": "sk-secret-value",
            "base_url": "https://user:pass@Api.Example.com:8443/v1/chat?api_key=sk-secret-value",
            "custom_headers": {"X-Api-Key": "header-secret"},
            "settings": {"temperature": 0.2},
        }
    )

    assert details["byok_provider_type"] == "openai_chat_completions"
    assert details["byok_model_name"] == "gpt-4.1-mini"
    assert details["byok_base_url_host"] == "api.example.com:8443"
    assert details["byok_provider_instance_hash"].startswith("byok_provider_hash_")

    serialized = str(details)
    assert "byok_provider_123456789" not in serialized
    assert "sk-secret-value" not in serialized
    assert "header-secret" not in serialized
    assert "user:pass" not in serialized
    assert "/v1/chat" not in serialized


def test_byok_audit_details_fall_back_to_provider_settings_base_url():
    details = chat_router._build_byok_audit_details(
        {
            "provider": "ollama",
            "provider_id": "local-provider",
            "model_name": "llama3.2",
            "provider_settings": {"base_url": "localhost:11434/api"},
        }
    )

    assert details["byok_base_url_host"] == "localhost:11434"


def test_send_route_audits_byok_destination_metadata():
    user = SimpleNamespace(id="user-1", group_id="group-1", role="user")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})

    with patch.object(chat_router, "_ensure_byok_allowed_for_user"), patch.object(
        chat_router,
        "can_send_message_in_chat",
        return_value=True,
    ), patch.object(
        chat_router,
        "_log_chat_event",
    ) as audit_event, patch.object(
        chat_router.background_task_executor,
        "submit",
        return_value=SimpleNamespace(done=lambda: False),
    ):
        chat_router.send(
            payload=chat_router.SendChatRequest(model_id="", message="hello", chat_id="chat-1"),
            request=request,
            custom_settings={},
            db=MagicMock(),
            db_log=MagicMock(),
            user=user,
            byok=_byok_payload(),
        )

    details = audit_event.call_args.args[4]
    assert details["model_id"] == ""
    assert details["byok"] is True
    assert details["byok_provider_type"] == "openai_chat_completions"
    assert details["byok_model_name"] == "gpt-4.1-mini"
    assert details["byok_base_url_host"] == "api.example.com"
    assert "byok_provider_123456789" not in str(details)
    assert "sk-secret-value" not in str(details)


def test_regenerate_route_audits_byok_destination_metadata():
    user = SimpleNamespace(id="user-1", group_id="group-1", role="user")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"user-agent": "pytest"})

    with patch.object(chat_router, "_ensure_byok_allowed_for_user"), patch.object(
        chat_router,
        "_log_chat_event",
    ) as audit_event, patch.object(
        chat_router.background_task_executor,
        "submit",
        return_value=SimpleNamespace(done=lambda: False),
    ):
        chat_router.regenerate(
            payload=chat_router.RegenerateMessageRequest(
                model_id="",
                chat_id="chat-1",
                user_message_id="message-1",
            ),
            request=request,
            custom_settings={},
            db=MagicMock(),
            db_log=MagicMock(),
            user=user,
            byok=_byok_payload(),
        )

    details = audit_event.call_args.args[4]
    assert details["model_id"] == ""
    assert details["byok"] is True
    assert details["byok_provider_type"] == "openai_chat_completions"
    assert details["byok_model_name"] == "gpt-4.1-mini"
    assert details["byok_base_url_host"] == "api.example.com"
    assert "byok_provider_123456789" not in str(details)
    assert "sk-secret-value" not in str(details)
