import pytest

from app.llm.openai.request_policy import apply_openai_request_policy


@pytest.mark.parametrize("tier", ["priority", "fast", "flex", "default"])
@pytest.mark.parametrize(
    "provider", ["openai", None, "openai_responses", "openai_chat_completions", "xai", "microsoft_azure", "lmstudio"]
)
def test_fast_mode_wire_value_preserves_other_tiers_and_providers(tier, provider):
    request = {
        "model": "custom-model",
        "service_tier": tier,
        "extra_body": {"service_tier": tier},
    }
    apply_openai_request_policy(request, provider_type=provider)
    expected = "fast" if provider in {"openai", None} and tier == "priority" else tier
    assert request["service_tier"] == expected
    assert request["extra_body"]["service_tier"] == expected
