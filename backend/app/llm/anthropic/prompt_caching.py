"""Anthropic automatic prompt-caching request support.

Omlorix deliberately exposes only Anthropic's automatic five-minute cache. It
provides the useful multi-turn behavior without introducing TTL ordering,
manual breakpoint placement, or endpoint-specific cache strategies into chat
or generation orchestration.
"""

from typing import Any


ANTHROPIC_AUTOMATIC_CACHE_CONTROL = {"type": "ephemeral"}


def apply_anthropic_prompt_cache(
    request_kwargs: dict[str, Any],
    settings: dict[str, Any] | None,
) -> None:
    """Add Anthropic's automatic five-minute cache marker when opted in.

    The setting is accepted for first-party and Anthropic-compatible clients.
    Compatible providers that do not implement ``cache_control`` remain safe
    by default because the model setting is disabled unless an administrator
    explicitly enables it.
    """
    if not isinstance(settings, dict):
        return

    # Use an exact boolean check so malformed strings such as ``"false"`` can
    # never accidentally enable a billable provider feature.
    if settings.get("prompt_cache_enabled") is not True:
        return

    # Copy the constant so later request mutation cannot change process-wide
    # defaults or leak state between concurrent generations.
    request_kwargs["cache_control"] = dict(ANTHROPIC_AUTOMATIC_CACHE_CONTROL)
