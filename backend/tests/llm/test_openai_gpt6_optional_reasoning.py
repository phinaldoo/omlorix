"""GPT-6 Sol/Luna pricing and endpoint constraints."""

import pytest

from app.admin.settings.schema_categories.realtime import RealtimeSettings
from app.llm.openai.live import LIVE_BACKEND_MODELS
from app.llm.openai.model_list import OPENAI_COMPLETION_MODELS, OPENAI_MODEL_DICT
from app.llm.openai.request_policy import (
    OpenAIRequestPolicyError,
    apply_openai_request_policy,
)
from app.llm.openai.utils import (
    _build_openai_reasoning_payload,
    calculate_openai_token_costs,
)
from app.llm.openai_chat_completions.utils import (
    _apply_openai_chat_completions_reasoning_effort,
)


@pytest.mark.parametrize("model,scale", [("gpt-6-sol", 1), ("gpt-6-luna", 0.05)])
def test_gpt6_catalog_costs_and_live_settings(model, scale):
    assert model in OPENAI_COMPLETION_MODELS
    assert model in LIVE_BACKEND_MODELS
    settings = RealtimeSettings(realtime_live_backend_model=model)
    assert RealtimeSettings.model_validate_json(settings.model_dump_json()) == settings
    caps = OPENAI_MODEL_DICT[model]
    assert (caps["input_token_limit"], caps["output_token_limit"]) == (922000, 128000)
    assert caps["thinking"]["thinking_effort"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert "none" not in OPENAI_MODEL_DICT["gpt-6-astra"]["thinking"]["thinking_effort"]
    # 272k input plus output still uses short-context rates. Crossing by one
    # input token reprices the entire request, including cache reads/writes.
    for input_tokens, expected in [(272000, 0.469), (272001, 0.888004)]:
        for tier, multiplier in [("standard", 1), ("flex", 0.5), ("fast", 2)]:
            costs = calculate_openai_token_costs(
                model_name=model,
                service_tier=tier,
                input_tokens=input_tokens,
                cached_input_tokens=100000,
                cache_write_tokens=10000,
                output_tokens=10000,
                reasoning_tokens=2000,
                native_websearch_tool_calls_count=0,
            )
            assert costs["total_costs"] == pytest.approx(expected * scale * multiplier)


@pytest.mark.parametrize("model", ["gpt-6-sol", "gpt-6-luna"])
@pytest.mark.parametrize("protocol", ["openai", "openai_chat_completions"])
@pytest.mark.parametrize(
    "effort,expected",
    [(None, "medium"), ("none", "none"), ("minimal", "low"), ("max", "max")],
)
def test_optional_reasoning_controls_sampling(model, protocol, effort, expected):
    request = {
        "model": model,
        "temperature": 0.7,
        "top_p": 0.8,
        "logprobs": True,
        "include": ["message.output_text.logprobs"],
        "extra_body": {"top_logprobs": 3},
    }
    if protocol == "openai":
        request["reasoning"] = {"effort": effort}
    else:
        request["reasoning_effort"] = effort
    apply_openai_request_policy(request, provider_type=protocol)
    actual = (
        request["reasoning"]["effort"]
        if protocol == "openai"
        else request["reasoning_effort"]
    )
    assert actual == expected
    assert ("temperature" in request) == (expected == "none")
    assert ("top_p" in request) == (expected == "none")
    assert ("logprobs" in request) == (expected == "none")
    assert ("top_logprobs" in request["extra_body"]) == (expected == "none")
    assert bool(request["include"]) == (expected == "none")


@pytest.mark.parametrize("model", ["gpt-6-sol", "gpt-6-luna"])
def test_disabling_reasoning_overrides_stale_effort_for_both_apis(model):
    settings = {"reasoning": False, "reasoning_effort": "high"}
    payload = _build_openai_reasoning_payload(
        settings, model_name=model, provider_type="openai"
    )
    assert payload["effort"] == "none"
    request = {"model": model}
    _apply_openai_chat_completions_reasoning_effort(request, settings)
    assert request["reasoning_effort"] == "none"


@pytest.mark.parametrize("model", ["gpt-6-sol", "gpt-6-luna"])
@pytest.mark.parametrize(
    "payload",
    [
        {"tools": [{"type": "function", "function": {"name": "notes"}}]},
        {"messages": [{"role": "tool", "content": "result", "tool_call_id": "call-1"}]},
    ],
)
def test_chat_tools_follow_effective_reasoning_override(model, payload):
    request = {
        "model": model,
        "reasoning_effort": "high",
        "temperature": 0.7,
        "extra_body": {"reasoning_effort": "none", **payload},
    }
    apply_openai_request_policy(request, provider_type="openai_chat_completions")
    assert request["temperature"] == 0.7
    request["reasoning_effort"] = "none"
    request["extra_body"]["reasoning_effort"] = "high"
    with pytest.raises(OpenAIRequestPolicyError):
        apply_openai_request_policy(request, provider_type="openai_chat_completions")
    # Responses supports reasoning together with tools.
    request = {"model": model, "reasoning": {"effort": "high"}, **payload}
    apply_openai_request_policy(request, provider_type="openai")
    assert request["reasoning"]["effort"] == "high"
