from types import SimpleNamespace

from app.llm import audio_generation_pricing, capabilities, speech, transcription_errors
from app.llm.schemas import ProviderEnum


def test_determine_model_capabilities_for_openai_modalities_and_tools():
    result = capabilities.determine_model_capabilities(
        ProviderEnum.openai_chat_completions,
        {
            "input_formats": ["text", "image", "pdf"],
            "output_formats": ["text", "audio"],
            "reasoning": True,
        },
        tools=[{"function": "web_search"}],
    )

    assert result == ["completion", "vision", "audio", "documents", "thinking", "tools"]


def test_openrouter_capabilities_include_audio_and_both_document_inputs():
    result = capabilities.determine_model_capabilities(
        ProviderEnum.openrouter,
        {
            "input_formats": [
                "text",
                "image",
                "audio",
                "video",
                "pdf",
                "text_document",
            ],
            "supported_parameters": ["tools"],
            "reasoning_enabled": True,
        },
        tools=[],
    )

    assert result == [
        "completion",
        "vision",
        "audio",
        "video",
        "documents",
        "tools",
        "thinking",
    ]


def test_ollama_capabilities_drop_unintegrated_modalities_and_add_documents():
    result = capabilities.determine_model_capabilities(
        ProviderEnum.ollama,
        {"input_formats": ["text", "image", "pdf", "text_document"]},
        tools=[],
        existing_capabilities=[
            "completion",
            "vision",
            "audio",
            "video",
            "tools",
            "thinking",
        ],
    )

    assert result == ["completion", "vision", "documents", "tools", "thinking"]


def test_determine_model_capabilities_falls_back_for_unknown_provider_result():
    assert capabilities.determine_model_capabilities(
        ProviderEnum.elevenlabs,
        {},
        tools=[],
        existing_capabilities=["vision", "vision", "tools"],
    ) == ["vision", "tools"]


def test_has_configured_tools_understands_common_payload_shapes():
    assert capabilities.has_configured_tools({"web_search": False, "code": []}) is False
    assert capabilities.has_configured_tools({"web_search": True}) is True
    assert capabilities.has_configured_tools([{}, "", None]) is False
    assert capabilities.has_configured_tools([{"name": "web_search"}]) is True


def test_model_has_capability_requires_an_explicit_truthy_flag():
    """Capability checks must not infer tool support from adjacent metadata."""

    assert capabilities.model_has_capability(["completion", "tools"], "tools")
    assert capabilities.model_has_capability({"tools": True}, "tools")
    assert not capabilities.model_has_capability(["completion", "thinking"], "tools")
    assert not capabilities.model_has_capability({"tools": False}, "tools")
    assert not capabilities.model_has_capability(None, "tools")


def test_audio_generation_pricing_normalizes_models_and_formats_labels():
    label, metadata = audio_generation_pricing.build_audio_generation_model_option("openai", "tts-1", label="TTS")
    responses_label, responses_metadata = audio_generation_pricing.build_audio_generation_model_option(
        "openai_responses",
        "tts-1",
        label="TTS",
    )

    assert label == "TTS ($15.00 / 1M chars)"
    assert metadata["billing_unit"] == "characters"
    assert responses_label == "TTS ($15.00 / 1M chars)"
    assert responses_metadata["billing_unit"] == "characters"


def test_openai_api_variants_support_tts_runtime_and_capabilities():
    for provider_type in (
        ProviderEnum.openai_responses.value,
        ProviderEnum.openai_chat_completions.value,
    ):
        provider = SimpleNamespace(provider=provider_type, api_key="test-key", settings={})

        model_ids = speech.get_tts_model_ids_for_provider(provider)
        capabilities_payload = speech.get_tts_model_capabilities_for_provider(
            "tts-1",
            provider_type=provider_type,
            provider_row=provider,
        )

        assert provider_type in speech.TTS_PROVIDER_TYPES
        assert speech.PROVIDER_AUDIO_GENERATORS[provider_type] is speech.PROVIDER_AUDIO_GENERATORS["openai"]
        assert "tts-1" in model_ids
        assert "alloy" in capabilities_payload["voices"]
        assert capabilities_payload["pricing_label"] == "$15.00 / 1M chars"


def test_calculate_audio_generation_cost_for_character_and_token_models():
    chars = audio_generation_pricing.calculate_audio_generation_cost(
        "elevenlabs",
        "eleven_flash_v2",
        input_text="abcd",
    )
    tokens = audio_generation_pricing.calculate_audio_generation_cost(
        "openai",
        "gpt-4o-mini-tts",
        input_text_tokens=1_000,
        output_audio_tokens=2_000,
    )

    assert chars["cost"] == 0.0002
    assert chars["input_character_count"] == 4
    assert tokens["input_cost"] == 0.0006
    assert tokens["output_cost"] == 0.024
    assert tokens["cost"] == 0.0246


def test_transcription_error_context_redacts_sensitive_fallbacks():
    exc = RuntimeError(
        "failed for user@example.com token=secret-value from 192.0.2.10 "
        "https://example.com/path?api_key=secret"
    )

    context = transcription_errors.extract_transcription_error_context(exc)

    assert "<redacted-email>" in context["message"]
    assert "token=<redacted>" in context["message"]
    assert "<redacted-ip>" in context["message"]
    assert "api_key=secret" not in context["message"]


def test_transcription_error_detail_hides_provider_context_for_non_admins():
    exc = SimpleNamespace(
        __str__=lambda self: "ignored",
        detail={"message": "provider says no", "status": "bad_request"},
        status_code=400,
    )

    assert transcription_errors.build_transcription_error_detail(
        exc,
        is_admin=False,
        fallback_message="Transcription failed",
    ) == {"message": "Transcription failed"}

    admin_detail = transcription_errors.build_transcription_error_detail(
        exc,
        is_admin=True,
        fallback_message="Transcription failed",
    )
    assert admin_detail["message"] == "provider says no"
    assert admin_detail["status"] == "bad_request"
    assert admin_detail["status_code"] == 400
