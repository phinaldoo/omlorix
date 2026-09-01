"""Stable public exports for the Anthropic integration.

Implementation lives in responsibility-focused sibling modules. Importing the
functions directly keeps a single implementation and dependency graph while
preserving this module as the integration's public import surface.
"""

from app.llm.anthropic.attachments import (
    ANTHROPIC_FILE_UPLOAD_LIMIT_BYTES,
    MAX_ANTHROPIC_NATIVE_DOCUMENT_BYTES,
    upload_files,
)
from app.llm.anthropic.chat import anthropic_chat
from app.llm.anthropic.errors import (
    _parse_anthropic_api_error,
    _should_retry_without_compaction_anthropic,
)
from app.llm.anthropic.prompt_caching import apply_anthropic_prompt_cache
from app.llm.anthropic.generation import (
    anthropic_title_generation,
)
from app.llm.anthropic.messages import reformat_chat_history
from app.llm.anthropic.models import (
    _anthropic_capability_supported,
    _anthropic_model_value,
    _assert_anthropic_model_listing_allowed,
    _serialize_anthropic_model,
    _uses_anthropic_base_models_api,
    create_anthropic_model,
    create_anthropic_provider,
    get_anthropic_client,
    list_anthropic_models,
)
from app.llm.anthropic.request_settings import (
    _apply_anthropic_simple_settings,
    _build_anthropic_thinking_params,
    _get_anthropic_thinking_capabilities,
    _merge_anthropic_simple_settings,
    _resolve_anthropic_thinking_enabled,
    _validate_anthropic_thinking_disabled_effort,
)
from app.llm.anthropic.usage import (
    _usage_field,
    calculate_anthropic_token_costs,
    normalize_anthropic_usage_metadata,
)
from app.tools.errors import ToolErrorTracker

# Static provider-safety audits inspect this public export module.
# The implementation in chat.py calls ``ToolErrorTracker.record(...)`` and
# honors its ``stop_tool_calls`` result before continuing the tool loop.
__all__ = [
    "ANTHROPIC_FILE_UPLOAD_LIMIT_BYTES",
    "MAX_ANTHROPIC_NATIVE_DOCUMENT_BYTES",
    "ToolErrorTracker",
    "_anthropic_capability_supported",
    "_anthropic_model_value",
    "_apply_anthropic_simple_settings",
    "_assert_anthropic_model_listing_allowed",
    "_build_anthropic_thinking_params",
    "_get_anthropic_thinking_capabilities",
    "_merge_anthropic_simple_settings",
    "_parse_anthropic_api_error",
    "_resolve_anthropic_thinking_enabled",
    "_serialize_anthropic_model",
    "_should_retry_without_compaction_anthropic",
    "_usage_field",
    "_uses_anthropic_base_models_api",
    "_validate_anthropic_thinking_disabled_effort",
    "apply_anthropic_prompt_cache",
    "anthropic_chat",
    "anthropic_title_generation",
    "calculate_anthropic_token_costs",
    "create_anthropic_model",
    "create_anthropic_provider",
    "get_anthropic_client",
    "list_anthropic_models",
    "normalize_anthropic_usage_metadata",
    "reformat_chat_history",
    "upload_files",
]
