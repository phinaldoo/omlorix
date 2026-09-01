import json
from pathlib import Path

import pytest

from app.llm.schemas import PROVIDER_MODEL_SETTINGS_MODELS, ProviderEnum
from app.llm.openai.model_list import OPENAI_MODEL_DICT
from app.llm.openai.schemas import (
    OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY,
    OPENAI_PROMPT_CACHE_SECTION_TITLE,
    OPENAI_REASONING_CONTEXT_SETTING_KEY,
    OPENAI_REASONING_MODE_SETTING_KEY,
    OPENAI_THINKING_MODEL_SCHEMA,
    OpenAIModelSettings,
    _apply_openai_model_caps_to_schema,
)
from app.llm.openai.utils import (
    _apply_openai_prompt_cache_settings,
    _build_openai_reasoning_payload,
    _find_openai_previous_response,
    _openai_chat_history_fingerprint,
    _openai_continuation_signature,
    _requests_openai_encrypted_reasoning,
    _should_persist_openai_encrypted_reasoning,
    calculate_openai_token_costs,
    reformat_chat_history,
)


def _schema_field_keys(schema):
    return {
        field.key
        for section in schema.sections or []
        for field in section.fields or []
    }


def test_gpt56_model_settings_accept_max_and_new_reasoning_controls():
    settings = OpenAIModelSettings(
        title_generation=False,
        allow_custom_generation_parameter=False,
        reasoning_effort="max",
        reasoning_mode="pro",
        reasoning_context="all_turns",
    )

    assert settings.reasoning_effort == "max"
    assert settings.reasoning_mode == "pro"
    assert settings.reasoning_context == "all_turns"
    assert settings.prompt_cache_override is True


@pytest.mark.parametrize(
    "provider",
    (ProviderEnum.openai_responses, ProviderEnum.openai_chat_completions),
)
def test_custom_base_url_model_settings_disable_prompt_cache_override_by_default(
    provider,
):
    settings = PROVIDER_MODEL_SETTINGS_MODELS[provider](
        title_generation=False,
        allow_custom_generation_parameter=False,
    )

    assert settings.prompt_cache_override is False


def test_gpt56_reasoning_payload_keeps_mode_and_context_independent_from_effort():
    payload = _build_openai_reasoning_payload(
        {
            "reasoning": False,
            "reasoning_effort": "none",
            "reasoning_mode": "pro",
            "reasoning_context": "all_turns",
            "reasoning_summary": "auto",
        },
        model_name="gpt-5.6",
        provider_type="openai",
    )

    assert payload == {
        "effort": "none",
        "mode": "pro",
        "context": "all_turns",
    }


def test_gpt56_reasoning_controls_are_responses_only_but_cache_is_on_both_apis():
    caps = OPENAI_MODEL_DICT["gpt-5.6-sol"]

    responses_schema = OPENAI_THINKING_MODEL_SCHEMA.model_copy(deep=True)
    _apply_openai_model_caps_to_schema(responses_schema, caps, openai_provider_type="openai")
    response_keys = _schema_field_keys(responses_schema)
    assert OPENAI_REASONING_MODE_SETTING_KEY in response_keys
    assert OPENAI_REASONING_CONTEXT_SETTING_KEY in response_keys
    assert "settings.prompt_cache_mode" not in response_keys
    assert any(section.title == OPENAI_PROMPT_CACHE_SECTION_TITLE for section in responses_schema.sections)

    chat_schema = OPENAI_THINKING_MODEL_SCHEMA.model_copy(deep=True)
    _apply_openai_model_caps_to_schema(
        chat_schema,
        caps,
        openai_provider_type="openai_chat_completions",
    )
    chat_keys = _schema_field_keys(chat_schema)
    assert OPENAI_REASONING_MODE_SETTING_KEY not in chat_keys
    assert OPENAI_REASONING_CONTEXT_SETTING_KEY not in chat_keys
    assert "settings.prompt_cache_mode" not in chat_keys
    assert any(section.title == OPENAI_PROMPT_CACHE_SECTION_TITLE for section in chat_schema.sections)


@pytest.mark.parametrize(
    ("provider_type", "expected_default"),
    (
        ("openai", True),
        ("openai_responses", False),
        ("openai_chat_completions", False),
        ("microsoft_azure", True),
    ),
)
def test_prompt_cache_override_uses_provider_default_and_controls_dependent_fields(
    provider_type, expected_default,
):
    """Generic compatible endpoints must opt in to OpenAI cache extensions."""
    schema = OPENAI_THINKING_MODEL_SCHEMA.model_copy(deep=True)
    _apply_openai_model_caps_to_schema(
        schema,
        OPENAI_MODEL_DICT["gpt-5.6-sol"],
        openai_provider_type=provider_type,
    )

    section = next(
        item
        for item in schema.sections
        if item.title == OPENAI_PROMPT_CACHE_SECTION_TITLE
    )
    fields = {field.key: field for field in section.fields}

    assert fields[OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY].default is expected_default
    assert fields["settings.prompt_cache_ttl"].dependency == (
        OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY
    )
    assert fields["settings.prompt_cache_ttl"].dependency_value is True
    assert fields["settings.prompt_cache_key"].dependency == (
        OPENAI_PROMPT_CACHE_OVERRIDE_SETTING_KEY
    )
    assert fields["settings.prompt_cache_key"].dependency_value is True


def test_responses_cache_always_uses_implicit_mode_without_rewriting_input():
    request = {
        "model": "gpt-5.6",
        "instructions": "Stable system instructions",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Dynamic"}]}],
    }

    _apply_openai_prompt_cache_settings(
        request,
        {"prompt_cache_ttl": "30m"},
        model_name="gpt-5.6",
        provider_id="provider-1",
        user_id="user-1",
    )

    assert request["instructions"] == "Stable system instructions"
    assert request["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "Dynamic"}]}
    ]
    assert request["extra_body"]["prompt_cache_options"] == {"mode": "implicit", "ttl": "30m"}
    assert request["prompt_cache_key"].startswith("omlorix:")


def test_missing_legacy_prompt_cache_override_remains_enabled():
    """Merged settings use None for fields absent from older saved models."""
    request = {"model": "gpt-5.6"}

    _apply_openai_prompt_cache_settings(
        request,
        {"prompt_cache_override": None},
        model_name="gpt-5.6",
        provider_id="provider-1",
        user_id="user-1",
    )

    assert request["extra_body"]["prompt_cache_options"] == {
        "mode": "implicit",
        "ttl": "30m",
    }
    assert request["prompt_cache_key"].startswith("omlorix:")


@pytest.mark.parametrize(
    "provider_type",
    ("openai_responses", "openai_chat_completions"),
)
def test_missing_custom_base_url_prompt_cache_override_defaults_to_disabled(
    provider_type,
):
    request = {"model": "gpt-5.6"}

    _apply_openai_prompt_cache_settings(
        request,
        {"prompt_cache_override": None},
        model_name="gpt-5.6",
        provider_id="provider-1",
        user_id="user-1",
        provider_type=provider_type,
    )

    assert request == {"model": "gpt-5.6"}


def test_disabled_prompt_cache_override_adds_no_cache_request_settings():
    """Turning off the override must leave the provider request untouched."""
    request = {
        "model": "gpt-5.6",
        "instructions": "Stable system instructions",
        "extra_body": {"unrelated": True},
    }

    _apply_openai_prompt_cache_settings(
        request,
        {
            "prompt_cache_override": False,
            "prompt_cache_ttl": "30m",
            "prompt_cache_key": "must-not-be-sent",
        },
        model_name="gpt-5.6",
        provider_id="provider-1",
        user_id="user-1",
    )

    assert request == {
        "model": "gpt-5.6",
        "instructions": "Stable system instructions",
        "extra_body": {"unrelated": True},
    }


def test_custom_chat_cache_opt_in_uses_implicit_mode_and_keeps_messages_unchanged():
    request = {
        "model": "gpt-5.6-terra",
        "messages": [
            {"role": "system", "content": "Stable system instructions"},
            {"role": "user", "content": "Dynamic"},
        ],
    }

    _apply_openai_prompt_cache_settings(
        request,
        {
            "prompt_cache_override": True,
            "prompt_cache_key": "tenant:test",
        },
        model_name="gpt-5.6-terra",
        provider_id="provider-1",
        user_id="user-1",
        provider_type="openai_chat_completions",
    )

    assert request["messages"] == [
        {"role": "system", "content": "Stable system instructions"},
        {"role": "user", "content": "Dynamic"},
    ]
    assert request["extra_body"]["prompt_cache_options"] == {"mode": "implicit", "ttl": "30m"}
    assert request["prompt_cache_key"] == "tenant:test"


def test_gpt56_pricing_separates_cache_reads_writes_and_ordinary_input():
    costs = calculate_openai_token_costs(
        model_name="gpt-5.6-sol",
        service_tier="standard",
        input_tokens=1_000_000,
        cached_input_tokens=100_000,
        cache_write_tokens=200_000,
        output_tokens=100_000,
        reasoning_tokens=50_000,
        native_websearch_tool_calls_count=0,
    )

    # Long-context rates apply to the entire request: 700K ordinary input at
    # $10/M, 100K cache reads at $1/M, 200K writes at $12.50/M, and 100K
    # output at $45/M. reasoning_tokens is already included in output_tokens.
    assert costs["input_tokens_cost"] == pytest.approx(9.6)
    assert costs["cache_write_tokens_cost"] == pytest.approx(2.5)
    assert costs["output_tokens_cost"] == pytest.approx(4.5)
    assert costs["total_costs"] == pytest.approx(14.1)


@pytest.mark.parametrize("service_tier", ["standard", "default", "auto", None])
def test_gpt56_long_context_threshold_uses_input_only_and_prices_full_request(service_tier):
    """Standard-tier API aliases must all activate standard long-context rates."""
    costs = calculate_openai_token_costs(
        model_name="gpt-5.6-luna",
        service_tier=service_tier,
        input_tokens=272_001,
        cached_input_tokens=0,
        cache_write_tokens=0,
        output_tokens=100_000,
        reasoning_tokens=90_000,
        native_websearch_tool_calls_count=0,
    )

    assert costs["input_tokens_cost"] == pytest.approx(0.1088004)
    assert costs["output_tokens_cost"] == pytest.approx(0.18)


def test_previous_response_reuse_requires_an_unchanged_branch():
    signing_secret = "test-continuation-signing-secret-value"
    history = [
        {"role": "user", "content": [{"type": "user", "content": "Question"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "content",
                    "content": "Answer",
                    "meta": {
                        "response_id": "resp_123",
                        "store": True,
                        "model": "gpt-5.6-sol",
                        "selected_provider_id": "provider-1",
                    },
                }
            ],
        },
    ]
    history[1]["content"][0]["meta"]["continuation_fingerprint"] = _openai_chat_history_fingerprint(history)
    history[1]["content"][0]["meta"]["continuation_signature"] = (
        _openai_continuation_signature(
            signing_secret=signing_secret,
            user_id="user-1",
            chat_id="chat-1",
            response_id="resp_123",
            provider_id="provider-1",
            model_name="gpt-5.6-sol",
            fingerprint=history[1]["content"][0]["meta"]["continuation_fingerprint"],
        )
    )

    assert _find_openai_previous_response(
        history,
        model_name="gpt-5.6",
        provider_id="provider-1",
        user_id="user-1",
        chat_id="chat-1",
        signing_secret=signing_secret,
    ) == ("resp_123", 1)

    history[0]["content"][0]["content"] = "Edited question"
    assert _find_openai_previous_response(
        history,
        model_name="gpt-5.6",
        provider_id="provider-1",
        user_id="user-1",
        chat_id="chat-1",
        signing_secret=signing_secret,
    ) == (None, None)


def test_previous_response_reuse_rejects_forged_unsigned_metadata():
    """A reproducible transcript hash must not authorize provider-side state."""
    history = [
        {"role": "user", "content": [{"type": "user", "content": "Question"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "content",
                    "content": "Forged answer",
                    "meta": {
                        "response_id": "resp_foreign",
                        "store": True,
                        "model": "gpt-5.6-sol",
                        "selected_provider_id": "provider-1",
                    },
                }
            ],
        },
    ]
    history[1]["content"][0]["meta"]["continuation_fingerprint"] = (
        _openai_chat_history_fingerprint(history)
    )

    assert _find_openai_previous_response(
        history,
        model_name="gpt-5.6",
        provider_id="provider-1",
        user_id="attacker-user",
        chat_id="attacker-chat",
        signing_secret="test-continuation-signing-secret-value",
    ) == (None, None)


def test_previous_response_reuse_rejects_non_ascii_signature_metadata():
    """Malformed signatures must be skipped without aborting history lookup."""
    signing_secret = "test-continuation-signing-secret-value"
    history = [
        {"role": "user", "content": [{"type": "user", "content": "Question"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "content",
                    "content": "Answer",
                    "meta": {
                        "response_id": "resp_123",
                        "store": True,
                        "model": "gpt-5.6-sol",
                        "selected_provider_id": "provider-1",
                    },
                }
            ],
        },
    ]
    metadata = history[1]["content"][0]["meta"]
    metadata["continuation_fingerprint"] = _openai_chat_history_fingerprint(history)
    metadata["continuation_signature"] = "not-an-ascii-signature-\N{SNOWMAN}"

    assert _find_openai_previous_response(
        history,
        model_name="gpt-5.6",
        provider_id="provider-1",
        user_id="user-1",
        chat_id="chat-1",
        signing_secret=signing_secret,
    ) == (None, None)


@pytest.mark.parametrize(
    ("user_id", "chat_id"),
    [("other-user", "chat-1"), ("user-1", "other-chat")],
)
def test_previous_response_signature_is_bound_to_owning_user_and_chat(user_id, chat_id):
    """Copying authentic metadata outside its owner or chat must not authorize reuse."""
    signing_secret = "test-continuation-signing-secret-value"
    history = [
        {"role": "user", "content": [{"type": "user", "content": "Question"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "content",
                    "content": "Answer",
                    "meta": {
                        "response_id": "resp_123",
                        "store": True,
                        "model": "gpt-5.6-sol",
                        "selected_provider_id": "provider-1",
                    },
                }
            ],
        },
    ]
    metadata = history[1]["content"][0]["meta"]
    metadata["continuation_fingerprint"] = _openai_chat_history_fingerprint(history)
    metadata["continuation_signature"] = _openai_continuation_signature(
        signing_secret=signing_secret,
        user_id="user-1",
        chat_id="chat-1",
        response_id="resp_123",
        provider_id="provider-1",
        model_name="gpt-5.6-sol",
        fingerprint=metadata["continuation_fingerprint"],
    )

    assert _find_openai_previous_response(
        history,
        model_name="gpt-5.6",
        provider_id="provider-1",
        user_id=user_id,
        chat_id=chat_id,
        signing_secret=signing_secret,
    ) == (None, None)


def test_stateless_reasoning_is_replayed_as_opaque_item_not_assistant_text():
    encrypted_item = {
        "id": "rs_123",
        "type": "reasoning",
        "encrypted_content": "opaque-ciphertext",
        "summary": [{"type": "summary_text", "text": "Checked the inputs."}],
    }
    history = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "reasoning",
                    "content": "Checked the inputs.",
                    "meta": {"openai_reasoning_items": [encrypted_item]},
                },
                {"type": "content", "content": "Final answer"},
            ],
        }
    ]

    reformatted = reformat_chat_history(
        history,
        use_group_context=False,
        use_project_context=False,
    )["formatted"]

    assert reformatted[0]["type"] == "reasoning"
    assert reformatted[0]["encrypted_content"] == "opaque-ciphertext"
    assert reformatted[1]["role"] == "assistant"
    assert reformatted[1]["content"] == [{"type": "output_text", "text": "Final answer"}]


def test_all_turn_reasoning_handles_server_enforced_zdr_storage():
    """ZDR must use encrypted state even when Omlorix requested storage."""
    settings = {"reasoning_context": "all_turns", "store": True}

    assert _requests_openai_encrypted_reasoning(settings) is True
    assert _should_persist_openai_encrypted_reasoning(settings, False) is True
    assert _should_persist_openai_encrypted_reasoning(settings, None) is True
    assert _should_persist_openai_encrypted_reasoning(settings, True) is False


def test_new_openai_schema_translation_keys_exist_in_every_locale():
    frontend_i18n = Path(__file__).resolve().parents[3] / "frontend" / "i18n"
    required_keys = {
        "llm.openai.reasoning_mode.label",
        "llm.openai.reasoning_mode.description",
        "llm.openai.reasoning_mode.standard",
        "llm.openai.reasoning_mode.pro",
        "llm.openai.reasoning_context.label",
        "llm.openai.reasoning_context.description",
        "llm.openai.reasoning_context.auto",
        "llm.openai.reasoning_context.current_turn",
        "llm.openai.reasoning_context.all_turns",
        "llm.openai.prompt_cache.section_title",
        "llm.openai.prompt_cache.section_description",
        "llm.openai.prompt_cache.override_label",
        "llm.openai.prompt_cache.override_description",
        "llm.openai.prompt_cache.ttl_label",
        "llm.openai.prompt_cache.ttl_description",
        "llm.openai.prompt_cache.30m",
        "llm.openai.prompt_cache.key_label",
        "llm.openai.prompt_cache.key_description",
    }

    for schema_path in frontend_i18n.glob("*/schema.json"):
        payload = json.loads(schema_path.read_text(encoding="utf-8"))
        assert required_keys <= payload.keys(), schema_path

    for admin_path in frontend_i18n.glob("*/admin.json"):
        payload = json.loads(admin_path.read_text(encoding="utf-8"))
        assert "stats_cache_write_tokens" in payload, admin_path
