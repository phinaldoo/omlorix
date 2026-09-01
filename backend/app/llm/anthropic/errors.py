"""Anthropic API error parsing and retry classification."""

from anthropic import APIStatusError


def _parse_anthropic_api_error(api_exc: APIStatusError) -> tuple[int, str, str]:
    """Parse Anthropic API error."""
    status_code = getattr(api_exc, "status_code", 0) or 0
    error_type = api_exc.__class__.__name__
    message = str(api_exc)
    try:
        body = getattr(api_exc, "body", None) or {}
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            error_type = error.get("type") or error_type
            message = error.get("message") or message
    except Exception:
        pass
    return status_code, str(message), str(error_type)


def _should_retry_without_compaction_anthropic(api_exc: APIStatusError) -> bool:
    """Check if should retry without compaction for Anthropic."""
    status_code, message, error_type = _parse_anthropic_api_error(api_exc)
    if status_code not in (0, 400, 404, 422):
        return False
    text = f"{message} {error_type}".lower()
    return any(
        token in text
        for token in (
            "context_management",
            "compact_20260112",
            "compaction",
            "compact-2026-01-12",
        )
    )
