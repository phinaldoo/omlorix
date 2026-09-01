from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.google_aistudio.realtime import (  # noqa: E402
    build_google_aistudio_live_connect_config,
    build_google_aistudio_live_client_setup,
    build_google_aistudio_live_session_config,
    get_google_aistudio_live_models,
    get_google_aistudio_live_default_voice,
    mint_google_aistudio_live_ephemeral_token,
)
from app.realtime import proxy as realtime_proxy  # noqa: E402


def _session_config(*, session_handle: str | None = None) -> dict:
    """Build the provider-side config snapshot bound into the ephemeral token."""
    return build_google_aistudio_live_session_config(
        instructions="Be helpful.",
        model_name="gemini-3.1-flash-live-preview",
        voice="Kore",
        settings={
            "enable_session_resumption": True,
            "enable_context_window_compression": True,
        },
        session_handle=session_handle,
    )


def test_google_live_browser_setup_contains_only_the_constrained_model():
    """Privileged provider settings must stay in the constrained token."""
    setup = build_google_aistudio_live_client_setup(
        model_name="gemini-3.1-flash-live-preview",
    )

    assert setup == {"model": "models/gemini-3.1-flash-live-preview"}


def test_google_live_proxy_rejects_browser_owned_setup_messages():
    """A modified browser cannot replace the server-bound Gemini setup."""
    client = SimpleNamespace(
        receive=AsyncMock(
            return_value={
                "type": "websocket.receive",
                "text": '{"setup":{"model":"models/attacker-model"}}',
            }
        )
    )
    upstream = SimpleNamespace(send=AsyncMock())

    with pytest.raises(ValueError, match="setup is server-owned"):
        asyncio.run(realtime_proxy._forward_browser_to_google(client, upstream))

    upstream.send.assert_not_awaited()

    client.receive = AsyncMock(
        return_value={
            "type": "websocket.receive",
            "bytes": b'{"setup":{"model":"models/attacker-model"}}',
        }
    )
    with pytest.raises(ValueError, match="setup is server-owned"):
        asyncio.run(realtime_proxy._forward_browser_to_google(client, upstream))

    upstream.send.assert_not_awaited()


def test_initial_google_live_config_requests_session_resumption():
    """New sessions must opt in before Google will emit a resumable handle."""
    config = _session_config()

    assert "sessionResumption" in config
    assert not config["sessionResumption"].get("handle")


def test_resumed_google_live_config_includes_latest_handle():
    """Reconnect setup must bind the new socket to the previous session."""
    config = _session_config(session_handle="resume-handle")

    assert config["sessionResumption"]["handle"] == "resume-handle"


def test_google_live_legacy_or_unknown_voice_uses_google_default():
    """Provider switches must not send an OpenAI-only voice to Gemini Live."""
    assert get_google_aistudio_live_default_voice("alloy") == "Kore"
    assert get_google_aistudio_live_default_voice("unknown-voice") == "Kore"
    assert get_google_aistudio_live_default_voice("Puck") == "Puck"
    assert get_google_aistudio_live_default_voice("pUcK") == "Puck"


def test_google_31_ignores_unsupported_native_audio_dialog_settings():
    """Persisted legacy settings must not make Gemini 3.1 setup invalid."""
    config = build_google_aistudio_live_session_config(
        instructions="Be helpful.",
        model_name="gemini-3.1-flash-live-preview",
        voice="Kore",
        settings={
            "enable_affective_dialog": True,
            "enable_proactive_audio": True,
        },
    )

    assert "enableAffectiveDialog" not in config
    assert "proactivity" not in config


def test_google_25_retains_supported_native_audio_dialog_settings():
    """Compatible 2.5 native-audio models keep their advanced controls."""
    config = build_google_aistudio_live_session_config(
        instructions="Be helpful.",
        model_name="gemini-2.5-flash-native-audio-preview-12-2025",
        voice="Kore",
        settings={
            "enable_affective_dialog": True,
            "enable_proactive_audio": True,
        },
    )

    assert config["enableAffectiveDialog"] is True
    assert config["proactivity"]["proactiveAudio"] is True


def test_google_ephemeral_token_globally_locks_privileged_setup():
    """The single-use token must own every effective setup field."""
    create_token = MagicMock(return_value=SimpleNamespace(name="auth-token"))
    fake_client = SimpleNamespace(
        auth_tokens=SimpleNamespace(create=create_token),
    )
    provider = SimpleNamespace(
        settings={},
        api_key="provider-secret",
    )
    provider_config = build_google_aistudio_live_connect_config(
        instructions="admin-only instruction",
        model_name="gemini-3.1-flash-live-preview",
        voice="Kore",
        settings={},
    )

    with patch(
        "app.llm.google_aistudio.realtime.get_llm_provider",
        return_value=provider,
    ), patch(
        "app.llm.google_aistudio.realtime.genai.Client",
        return_value=fake_client,
    ):
        token_name = mint_google_aistudio_live_ephemeral_token(
            db=MagicMock(),
            provider_id="google-provider",
            model_name="gemini-3.1-flash-live-preview",
            session_config=provider_config,
        )

    token_config = create_token.call_args.kwargs["config"]
    assert token_name == "auth-token"
    assert token_config.uses == 1
    assert token_config.lock_additional_fields is None
    assert token_config.live_connect_constraints.model == "gemini-3.1-flash-live-preview"
    assert token_config.live_connect_constraints.config is provider_config


def test_generic_google_live_assistant_excludes_translation_only_models():
    """The translation endpoint has a different, incompatible protocol."""
    provider = SimpleNamespace(settings={})
    advertised_models = [
        {
            "id": "gemini-3.1-flash-live-preview",
            "supported_actions": ["bidiGenerateContent"],
        },
        {
            "id": "gemini-3.5-live-translate-preview",
            "supported_actions": ["bidiGenerateContent"],
        },
    ]

    with patch(
        "app.llm.google_aistudio.realtime.get_llm_provider",
        return_value=provider,
    ), patch(
        "app.llm.google_aistudio.realtime.list_models_google_aistudio",
        return_value=advertised_models,
    ):
        models = get_google_aistudio_live_models(
            db=MagicMock(),
            google_provider_id="google-provider",
        )

    assert "gemini-3.1-flash-live-preview" in models
    assert "gemini-3.5-live-translate-preview" not in models
