"""Helpers for normalizing provider token-usage metadata."""

from typing import Any


CACHED_INPUT_TOKEN_META_KEY = "input_token_cached"
CACHED_INPUT_TOKEN_META_ALIASES = (
    CACHED_INPUT_TOKEN_META_KEY,
    "cached_input_tokens",
    "input_tokens_cached",
)
CACHE_WRITE_TOKEN_META_KEY = "cache_write_tokens"
CACHE_WRITE_TOKEN_META_ALIASES = (
    CACHE_WRITE_TOKEN_META_KEY,
    "input_token_cache_write",
    "cache_creation_input_tokens",
)


def coerce_token_count(value: Any) -> int:
    """Return a non-negative integer token count for loose provider values."""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def read_cached_input_tokens(meta: dict | None) -> int:
    """Read cached input tokens from any known Omlorix/provider metadata key."""
    if not isinstance(meta, dict):
        return 0
    for key in CACHED_INPUT_TOKEN_META_ALIASES:
        if key in meta:
            return coerce_token_count(meta.get(key))
    return 0


def add_cached_input_token_meta(
    meta: dict,
    cached_input_tokens: Any,
    *,
    aliases: tuple[str, ...] = (),
) -> None:
    """Add canonical cached-token metadata and optional compatibility aliases."""
    tokens = coerce_token_count(cached_input_tokens)
    if tokens <= 0:
        return
    meta[CACHED_INPUT_TOKEN_META_KEY] = tokens
    for alias in aliases:
        if alias and alias != CACHED_INPUT_TOKEN_META_KEY:
            meta[alias] = tokens


def read_cache_write_tokens(meta: dict | None) -> int:
    """Read cache-write input tokens from any known provider metadata key."""
    if not isinstance(meta, dict):
        return 0
    for key in CACHE_WRITE_TOKEN_META_ALIASES:
        if key in meta:
            return coerce_token_count(meta.get(key))
    return 0


def add_cache_write_token_meta(meta: dict, cache_write_tokens: Any) -> None:
    """Add canonical cache-write token metadata when the count is non-zero."""
    tokens = coerce_token_count(cache_write_tokens)
    if tokens > 0:
        meta[CACHE_WRITE_TOKEN_META_KEY] = tokens
