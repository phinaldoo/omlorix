"""Anthropic metadata that is not exposed by the Models API."""

import re


ANTHROPIC_CATALOG_LAST_VERIFIED = "2026-09-01"
ANTHROPIC_MODELS_DOCS_URL = "https://platform.claude.com/docs/en/models/overview"
ANTHROPIC_PRICING_DOCS_URL = "https://platform.claude.com/docs/en/about-claude/pricing"


# USD per million tokens. Model names, limits, and capabilities come from the
# provider; only pricing remains local because Anthropic does not return it.
ANTHROPIC_MODEL_PRICING = {
    "claude-fable-5-1": (10, 50),
    "claude-fable-5": (10, 50),
    "claude-mythos-5-1": (10, 50),
    "claude-mythos-5": (10, 50),
    "claude-mythos-preview": (10, 50),
    "claude-sonnet-5": (2, 10),
    "claude-opus-5": (5, 25),
    "claude-haiku-4-5": (1, 5),
    "claude-sonnet-4-5": (3, 15),
    "claude-sonnet-4-6": (3, 15),
    "claude-opus-4-1": (15, 75),
    "claude-opus-4-5": (5, 25),
    "claude-opus-4-6": (5, 25),
    "claude-opus-4-7": (5, 25),
    "claude-opus-4-8": (5, 25),
}

# Anthropic's Models API does not expose knowledge cutoffs, so these remain
# local and are also used for dated model IDs after their date suffix is removed.
ANTHROPIC_KNOWLEDGE_CUTOFFS = {
    "claude-fable-5-1": "2026-06-01",
    "claude-fable-5": "2026-01-01",
    "claude-mythos-5-1": "2026-06-01",
    "claude-mythos-5": "2026-01-01",
    "claude-mythos-preview": "2026-01-01",
    "claude-sonnet-5": "2026-01-01",
    "claude-opus-5": "2026-05-01",
    "claude-haiku-4-5": "2025-02-01",
    "claude-sonnet-4-5": "2025-01-01",
    "claude-sonnet-4-6": "2025-08-01",
    "claude-opus-4-1": "2025-03-01",
    "claude-opus-4-5": "2025-05-01",
    "claude-opus-4-6": "2025-05-01",
    "claude-opus-4-7": "2026-01-01",
    "claude-opus-4-8": "2026-01-01",
}

# The Models API does not report whether thinking can be disabled or whether
# particular effort levels require it. Keep those documented exceptions here
# with the other non-discoverable model metadata.
ANTHROPIC_THINKING_OVERRIDES = {
    "claude-fable-5-1": {
        "thinking": True,
        "thinking_disabled_allowed": False,
    },
    "claude-fable-5": {
        "thinking": True,
        "thinking_disabled_allowed": False,
    },
    "claude-mythos-5": {
        "thinking": True,
        "thinking_disabled_allowed": False,
    },
    "claude-mythos-5-1": {
        "thinking": True,
        "thinking_disabled_allowed": False,
    },
    "claude-mythos-preview": {
        "thinking": True,
        "thinking_disabled_allowed": False,
    },
    "claude-opus-5": {
        "thinking": True,
        "thinking_disabled_allowed": True,
        "thinking_disabled_forbidden_efforts": ["xhigh", "max"],
    },
}

# The Models API does not expose server-tool compatibility. Keep basic native
# web search conservative so an unknown or legacy model is never sent a tool
# definition that the Messages API may reject.
ANTHROPIC_NATIVE_WEBSEARCH_MODELS = frozenset(
    {
        "claude-fable-5-1",
        "claude-fable-5",
        "claude-haiku-4-5",
        "claude-opus-4-1",
        "claude-opus-4-5",
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-mythos-5-1",
        "claude-mythos-5",
        "claude-mythos-preview",
        "claude-sonnet-4-5",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
    }
)


def get_anthropic_pricing(model_id: str) -> dict | None:
    """Return pricing for an alias or dated Anthropic model ID."""
    normalized_model_id = re.sub(r"-\d{8}$", "", model_id)
    rates = ANTHROPIC_MODEL_PRICING.get(normalized_model_id)
    if not rates:
        return None
    pricing = {"input": rates[0], "output": rates[1], "native_web_search_tool_call": 0.01}
    if normalized_model_id in {"claude-fable-5-1", "claude-mythos-5-1"}:
        pricing["cache_read_input_multiplier"] = 0.025
    return pricing


def get_anthropic_knowledge_cutoff(model_id: str) -> str | None:
    """Return the documented cutoff for an alias or dated model ID."""
    return ANTHROPIC_KNOWLEDGE_CUTOFFS.get(re.sub(r"-\d{8}$", "", model_id))


def get_anthropic_thinking_override(model_id: str) -> dict:
    """Return request constraints missing from the Models API."""
    return dict(ANTHROPIC_THINKING_OVERRIDES.get(re.sub(r"-\d{8}$", "", model_id), {}))


def supports_anthropic_native_websearch(model_id: str) -> bool:
    """Return whether Anthropic documents basic native web search support."""
    return re.sub(r"-\d{8}$", "", model_id) in ANTHROPIC_NATIVE_WEBSEARCH_MODELS
