import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.token_usage import add_cached_input_token_meta, read_cached_input_tokens


def test_add_cached_input_token_meta_uses_stats_canonical_key():
    """Saved message metadata should use the key recognized by llmstats."""
    meta = {}

    add_cached_input_token_meta(meta, 128)

    assert meta == {"input_token_cached": 128}


def test_add_cached_input_token_meta_can_preserve_provider_aliases():
    """Provider aliases can stay present without losing the canonical key."""
    meta = {"input_tokens": 4096}

    add_cached_input_token_meta(meta, "256", aliases=("input_tokens_cached",))

    assert meta["input_token_cached"] == 256
    assert meta["input_tokens_cached"] == 256


def test_add_cached_input_token_meta_omits_empty_values():
    """Zero cached tokens should not add noisy metadata to message blocks."""
    meta = {}

    add_cached_input_token_meta(meta, 0)

    assert meta == {}


def test_read_cached_input_tokens_accepts_existing_spellings():
    """Cost/stat code should continue reading older provider spellings."""
    assert read_cached_input_tokens({"input_token_cached": 11}) == 11
    assert read_cached_input_tokens({"cached_input_tokens": 12}) == 12
    assert read_cached_input_tokens({"input_tokens_cached": 13}) == 13
