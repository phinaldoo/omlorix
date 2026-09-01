from __future__ import annotations

from math import isfinite
from typing import Any


def build_websearch_usage_event(
    *,
    provider: str | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Keep only a valid cost reported by Exa."""
    if str(provider or "").strip().lower() != "exa" or not isinstance(metadata, dict):
        return {}

    try:
        cost = float(metadata["cost"])
    except (KeyError, TypeError, ValueError):
        return {}

    return {"cost": cost} if cost >= 0 and isfinite(cost) else {}


def build_websearch_tool_meta(
    *,
    base_meta: dict[str, Any] | None = None,
    usage_events: list[dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Save the total Exa-reported cost in the standard tool metadata field."""
    meta = dict(base_meta) if isinstance(base_meta, dict) else {}
    reported_costs = [event["cost"] for event in usage_events or [] if "cost" in event]
    if reported_costs:
        meta["cost"] = round(sum(reported_costs), 6)
    return meta
