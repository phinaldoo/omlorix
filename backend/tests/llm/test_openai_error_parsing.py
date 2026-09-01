"""Regression tests for OpenAI-compatible provider error payloads."""

from app.llm.openai.utils import _parse_openai_exception


class _Response:
    """Provide the response attributes consumed by the exception parser."""

    def __init__(self, payload: dict, status_code: int = 400):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        """Return the configured provider response body."""
        return self._payload


def test_parse_openai_exception_accepts_string_error_from_compatible_provider():
    """A flat xAI error must remain visible instead of causing AttributeError."""
    message = "This model does not support `reasoning_effort` value `none`."
    exc = RuntimeError("SDK fallback text")
    exc.response = _Response({"code": "invalid-argument", "error": message})

    assert _parse_openai_exception(exc) == (
        400,
        message,
        None,
        "invalid-argument",
    )


def test_parse_openai_exception_preserves_standard_nested_error():
    """Support for the standard OpenAI error envelope must remain unchanged."""
    exc = RuntimeError("SDK fallback text")
    exc.response = _Response(
        {
            "error": {
                "message": "Invalid request",
                "type": "invalid_request_error",
                "code": "unsupported_value",
            }
        },
        status_code=422,
    )

    assert _parse_openai_exception(exc) == (
        422,
        "Invalid request",
        "invalid_request_error",
        "unsupported_value",
    )
