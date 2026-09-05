"""Recognize upstream safety stops without retaining sensitive error bodies."""

MISALIGNMENT_CODE = "misalignment_policy_violation"


class OpenAISafetyStop(RuntimeError):
    code = MISALIGNMENT_CODE
    i18n_key = "chat_openai_safety_stop"

    def __init__(self, *, request_id=None, response_id=None):
        super().__init__(
            "OpenAI stopped this conversation for safety review. Review the actions "
            "already taken with the responsible operator. This workflow cannot be retried."
        )
        self.request_id = request_id
        self.response_id = response_id

    def metadata(self):
        return {
            "error_code": self.code,
            "error_message": str(self),
            "i18n_key": self.i18n_key,
            "retryable": False,
            **({"request_id": self.request_id} if self.request_id else {}),
            **({"response_id": self.response_id} if self.response_id else {}),
        }

    def event(self):
        return {"t": "e", "d": str(self), "code": self.code, **self.metadata()}


def _read(value, key):
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def get_openai_safety_stop(value):
    """Accept SDK exceptions, SSE failures/errors, and non-streaming responses."""
    if isinstance(value, OpenAISafetyStop):
        return value
    response = _read(value, "response")
    body = _read(value, "body")
    candidates = (
        value,
        body,
        _read(body, "error"),
        _read(value, "error"),
        _read(response, "error"),
    )
    if not any(_read(item, "code") == MISALIGNMENT_CODE for item in candidates):
        return None
    return OpenAISafetyStop(
        request_id=_read(value, "request_id") or _read(value, "_request_id"),
        response_id=_read(value, "response_id")
        or _read(response, "id")
        or _read(value, "id"),
    )


def get_conversation_safety_stop(history):
    """Retain stops across retries/continuations using existing message metadata."""
    for message in history or []:
        if _read(message, "role") != "assistant":
            continue
        content = _read(message, "content")
        if not isinstance(content, list):
            continue
        for block in content:
            meta = _read(block, "meta")
            if _read(meta, "error_code") == MISALIGNMENT_CODE:
                return OpenAISafetyStop(
                    request_id=_read(meta, "request_id"),
                    response_id=_read(meta, "response_id"),
                )
    return None
