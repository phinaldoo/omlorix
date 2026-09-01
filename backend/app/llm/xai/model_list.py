"""Authoritative xAI language-model catalog used by Omlorix.

The xAI ``/models`` endpoint remains the source of truth for which models a
particular API key can access.  This static catalog complements that live list
with the capability and pricing information needed to build safe model forms
and to estimate costs when an API response does not expose its billed amount.
"""

from datetime import datetime
from copy import deepcopy
from typing import Any


# Keep the verification date and source URLs next to the data.  xAI changes
# aliases and rates independently of model identifiers, so reviewers can
# quickly tell whether a future catalog refresh is needed.
XAI_CATALOG_LAST_VERIFIED = "2026-08-12"
XAI_MODELS_DOCS_URL = "https://docs.x.ai/developers/models"
XAI_PRICING_DOCS_URL = "https://docs.x.ai/developers/pricing"
XAI_COST_TRACKING_DOCS_URL = "https://docs.x.ai/developers/cost-tracking"
XAI_MODEL_RETIREMENT_DOCS_URL = (
    "https://docs.x.ai/developers/migration/may-15-retirement"
)

# xAI keeps these language-model slugs callable as redirects.  Recording the
# mapping explicitly makes lifecycle behavior inspectable without incorrectly
# filtering still-functional aliases from live discovery.
XAI_RETIRED_MODEL_REDIRECTS = {
    "grok-4-1-fast-reasoning": "grok-4.3",
    "grok-4-1-fast-non-reasoning": "grok-4.3",
    "grok-4-fast-reasoning": "grok-4.3",
    "grok-4-fast-non-reasoning": "grok-4.3",
    "grok-4-0709": "grok-4.3",
    "grok-code-fast-1": "grok-build-0.1",
    "grok-3": "grok-4.3",
}
XAI_DEPRECATED_MODELS = sorted(XAI_RETIRED_MODEL_REDIRECTS)
XAI_RETIRED_REASONING_MODELS = {
    "grok-4-1-fast-reasoning",
    "grok-4-fast-reasoning",
    "grok-4-0709",
}


def _text_pricing(
    *,
    input_price: float,
    cached_input_price: float,
    output_price: float,
    long_input_price: float,
    long_cached_input_price: float,
    long_output_price: float,
) -> dict[str, Any]:
    """Build xAI's standard, priority, and long-context pricing structure.

    xAI priority processing is exactly twice the standard token rate.  Keeping
    both tiers explicit lets the shared Responses cost calculator use the tier
    confirmed by the API response, while the high-context block preserves the
    provider's all-tokens-at-200K billing rule.
    """

    standard = {
        "input": input_price,
        "cached_input": cached_input_price,
        "output": output_price,
    }
    priority = {key: value * 2 for key, value in standard.items()}
    long_standard = {
        "input": long_input_price,
        "cached_input": long_cached_input_price,
        "output": long_output_price,
    }
    long_priority = {key: value * 2 for key, value in long_standard.items()}
    return {
        "standard": standard,
        "priority": priority,
        "high_context_pricing": {
            "mark": 200_000,
            "inclusive": True,
            "standard": long_standard,
            "priority": long_priority,
        },
        # Web Search, X Search, and Code Execution currently share this public
        # $5/1K-call rate.  Omlorix's existing native-search counter uses the
        # web-search key; exact provider usage remains authoritative when tools
        # other than web search are involved.
        "native_web_search_tool_call": 0.005,
    }


def _language_model(
    *,
    name: str,
    description: str,
    ids: list[str],
    context_window: int,
    pricing: dict[str, Any],
    thinking_effort: list[str],
    default_thinking_effort: str | None,
    knowledge_cutoff: datetime | None = None,
    reasoning_toggle_supported: bool = False,
    thinking_supported: bool | None = None,
) -> dict[str, Any]:
    """Build the common metadata shared by current Grok language models."""

    thinking: dict[str, Any] = {
        "thinking": (
            bool(thinking_effort or default_thinking_effort)
            if thinking_supported is None
            else thinking_supported
        ),
        "thinking_effort": list(thinking_effort),
        "reasoning_toggle_supported": reasoning_toggle_supported,
    }
    if default_thinking_effort:
        thinking["default_thinking_effort"] = default_thinking_effort

    model: dict[str, Any] = {
        "name": name,
        "description": description,
        "ids": ids,
        "input_formats": ["text", "image", "pdf", "text_document"],
        "output_formats": ["text"],
        "input_token_limit": context_window,
        "thinking": thinking,
        "prompt_caching": {
            # xAI supports a routing key but does not expose OpenAI's explicit
            # cache-retention TTL control.  An empty list intentionally keeps
            # Omlorix from serializing ``prompt_cache_options`` to xAI.
            "ttl": [],
        },
        "supported_service_tier": ["standard", "priority"],
        "supports_native_websearch": True,
        "temperature": {"temperature": True},
        "top_p": {"top_p": True},
        "frequency_penalty": {"frequency_penalty": False},
        "presence_penalty": {"presence_penalty": False},
        "pricing": pricing,
    }
    if knowledge_cutoff is not None:
        model["knowledge_cutoff"] = knowledge_cutoff
    return model


_GROK_43_PRICING = _text_pricing(
    input_price=1.25,
    cached_input_price=0.20,
    output_price=2.50,
    long_input_price=2.50,
    long_cached_input_price=0.40,
    long_output_price=5.00,
)


XAI_MODEL_DICT: dict[str, dict[str, Any]] = {
    "grok-4.6": _language_model(
        name="Grok 4.6",
        description=(
            "xAI's flagship model for coding, long-running agents, and knowledge work."
        ),
        ids=["grok-4.6"],
        context_window=500_000,
        knowledge_cutoff=datetime(2026, 2, 1),
        thinking_effort=["low", "medium", "high", "xhigh"],
        default_thinking_effort="high",
        pricing=_text_pricing(
            input_price=2.00,
            cached_input_price=0.50,
            output_price=6.00,
            long_input_price=4.00,
            long_cached_input_price=1.00,
            long_output_price=12.00,
        ),
    ),
    "grok-4.5": _language_model(
        name="Grok 4.5",
        description="xAI's frontier model for coding, agents, and knowledge work.",
        ids=["grok-4.5", "grok-4.5-latest", "grok-build-latest"],
        context_window=500_000,
        knowledge_cutoff=datetime(2026, 2, 1),
        thinking_effort=["low", "medium", "high"],
        default_thinking_effort="high",
        pricing=_text_pricing(
            input_price=2.00,
            cached_input_price=0.30,
            output_price=6.00,
            long_input_price=4.00,
            long_cached_input_price=0.60,
            long_output_price=12.00,
        ),
    ),
    "grok-build-0.1": _language_model(
        name="Grok Build 0.1",
        description="xAI's fast coding model for agentic engineering workflows.",
        ids=[
            "grok-build-0.1",
            "grok-code-fast",
            "grok-code-fast-1",
            "grok-code-fast-1-0825",
        ],
        context_window=256_000,
        # The model page documents reasoning support, but not a configurable
        # effort enum.  Do not expose unverified request values in the form.
        thinking_effort=[],
        default_thinking_effort=None,
        thinking_supported=True,
        pricing=_text_pricing(
            input_price=1.00,
            cached_input_price=0.20,
            output_price=2.00,
            long_input_price=2.00,
            long_cached_input_price=0.40,
            long_output_price=4.00,
        ),
    ),
    "grok-4.3": _language_model(
        name="Grok 4.3",
        description="Fast, reliable Grok model with strong agentic tool calling.",
        ids=[
            "grok-4.3",
            "grok-4.3-latest",
            "grok-latest",
            # xAI keeps these retired slugs callable by redirecting them to
            # Grok 4.3.  Associating them with the replacement gives users the
            # current capabilities and rates instead of stale legacy prices.
            "grok-4-1-fast-reasoning",
            "grok-4-1-fast-non-reasoning",
            "grok-4-fast-reasoning",
            "grok-4-fast-non-reasoning",
            "grok-4-0709",
            "grok-3",
        ],
        context_window=1_000_000,
        knowledge_cutoff=datetime(2024, 11, 1),
        thinking_effort=["none", "low", "medium", "high"],
        default_thinking_effort="none",
        pricing=_GROK_43_PRICING,
    ),
    "grok-4.20-multi-agent-0309": _language_model(
        name="Grok 4.20 Multi-Agent Beta",
        description="Parallel Grok agents for deep research tasks.",
        ids=[
            "grok-4.20-multi-agent-0309",
            "grok-4.20-multi-agent",
            "grok-4.20-multi-agent-latest",
            "grok-4.20-multi-agent-beta-latest",
            "grok-4.20-multi-agent-experimental-beta-0304",
            "grok-4.20-multi-agent-experimental-beta-latest",
            "grok-4.20-multi-agent-beta-0309",
        ],
        context_window=1_000_000,
        knowledge_cutoff=datetime(2024, 11, 1),
        thinking_effort=["low", "medium", "high", "xhigh"],
        default_thinking_effort="high",
        pricing=_GROK_43_PRICING,
    ),
    "grok-4.20-0309-reasoning": _language_model(
        name="Grok 4.20",
        description="High-performance Grok 4.20 with reasoning enabled.",
        ids=[
            "grok-4.20-0309-reasoning",
            "grok-4.20-reasoning-latest",
            "grok-4.20",
            "grok-4.20-reasoning",
            "grok-4.20-0309",
            "grok-4.20-beta-0309-reasoning",
            "grok-4.20-beta",
            "grok-4.20-beta-0309",
            "grok-4.20-beta-latest",
            "grok-4.20-beta-latest-reasoning",
            "grok-4.20-beta-reasoning",
            "grok-4.20-experimental-beta-0304-reasoning",
            "grok-4.20-experimental-beta-0304",
            "grok-4.20-experimental-beta-reasoning-latest",
            "grok-4.20-experimental-beta-latest",
            "grok-4.20-reasoning-gv2",
        ],
        context_window=1_000_000,
        knowledge_cutoff=datetime(2024, 11, 1),
        # The reasoning/non-reasoning choice is encoded in this model slug;
        # xAI does not document a per-request effort selector for this variant.
        thinking_effort=[],
        default_thinking_effort=None,
        thinking_supported=True,
        pricing=_GROK_43_PRICING,
    ),
    "grok-4.20-0309-non-reasoning": {
        **_language_model(
            name="Grok 4.20 (Non-Reasoning)",
            description="High-performance Grok 4.20 with reasoning disabled.",
            ids=[
                "grok-4.20-0309-non-reasoning",
                "grok-4.20-non-reasoning",
                "grok-4.20-non-reasoning-latest",
                "grok-4.20-beta-non-reasoning",
                "grok-4.20-beta-latest-non-reasoning",
                "grok-4.20-experimental-beta-0304-non-reasoning",
                "grok-4.20-experimental-beta-non-reasoning-latest",
                "grok-4.20-beta-0309-non-reasoning",
                "grok-4.20-non-reasoning-gv2",
            ],
            context_window=1_000_000,
            knowledge_cutoff=datetime(2024, 11, 1),
            thinking_effort=[],
            default_thinking_effort=None,
            pricing=_GROK_43_PRICING,
        ),
        "thinking": {
            "thinking": False,
            "thinking_effort": [],
            "reasoning_toggle_supported": False,
        },
        # The fixed non-reasoning endpoint can use ordinary sampling controls.
        "frequency_penalty": {"frequency_penalty": True},
        "presence_penalty": {"presence_penalty": True},
    },
}


# The language-model endpoint may surface capability-specific products for an
# account.  Keep those out of Omlorix's chat-model picker; their native adapters
# expose them in the appropriate Image, Video, Voice, and transcription forms.
XAI_NON_CHAT_MODELS = {
    "grok-imagine-image",
    "grok-imagine-image-quality",
    "grok-imagine-image-pro",
    "grok-imagine-video",
    "grok-imagine-video-1.5",
    "grok-voice-think-fast-1.0",
    "grok-voice-think-fast-2.0",
}

XAI_COMPLETION_MODELS = sorted(
    {
        identifier
        for model in XAI_MODEL_DICT.values()
        for identifier in model.get("ids", [])
        if isinstance(identifier, str) and identifier
    }
)
XAI_UNSUPPORTED_MODELS = sorted(XAI_NON_CHAT_MODELS)


def get_xai_model_capabilities(model_name: str | None) -> dict[str, Any] | None:
    """Resolve a canonical xAI model name or any documented alias."""

    identifier = str(model_name or "").strip()
    if not identifier:
        return None
    for group_name, capabilities in XAI_MODEL_DICT.items():
        if identifier == group_name or identifier in (capabilities.get("ids") or []):
            if identifier in XAI_RETIRED_REASONING_MODELS:
                # xAI preserves these retired slugs by routing them to Grok
                # 4.3 with low reasoning. Return an identifier-specific copy so
                # the admin form does not override that behavior with the
                # canonical Grok 4.3 default of none.
                resolved = deepcopy(capabilities)
                resolved.setdefault("thinking", {})[
                    "default_thinking_effort"
                ] = "low"
                return resolved
            return capabilities
    return None
