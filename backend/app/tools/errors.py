"""Safe, provider-independent error transport for model tool calls.

Tool implementations often raise detailed exceptions that are valuable in logs
but unsafe to expose to a model or end user.  Only exceptions that explicitly
subclass :class:`SafeToolExecutionError` cross that boundary with a stable code
and a deliberately written public message.  Everything else keeps the existing
generic response.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

GENERIC_TOOL_ERROR_MESSAGE = "An error occurred during tool execution."


class SafeToolExecutionError(ValueError):
    """An expected tool validation error whose public details are safe to show.

    ``detail`` is retained for internal statistics and logs. ``safe_message`` is
    the only exception-authored text sent back to the model.  Callers must never
    put user content, secrets, filesystem paths, or provider responses in the
    public message.
    """

    def __init__(
        self,
        *,
        code: str,
        safe_message: str,
        detail: str | None = None,
        allow_same_response_retry: bool = True,
    ) -> None:
        normalized_code = str(code or "").strip()
        normalized_message = str(safe_message or "").strip()
        if not normalized_code:
            raise ValueError("Safe tool errors require a stable code.")
        if not normalized_message:
            raise ValueError("Safe tool errors require a public message.")

        self.code = normalized_code
        self.safe_message = normalized_message
        # Most safe tool errors describe a payload that the model can correct
        # once. Transient infrastructure conditions, such as exhausted sandbox
        # capacity, cannot be corrected by regenerating the same tool call and
        # must instead end tool use for the current assistant response.
        self.allow_same_response_retry = bool(allow_same_response_retry)
        super().__init__(str(detail or normalized_message))


class ToolExecutionDiagnosticError(RuntimeError):
    """Internal tool failure carrying non-sensitive statistics context.

    The detailed message remains internal, while ``tool_statistic_meta`` lets
    orchestrated tools identify the component that actually failed (for
    example, a nested presentation model rather than the calling chat model).
    This is deliberately not a ``SafeToolExecutionError`` and therefore never
    exposes provider diagnostics to the assistant or end user.
    """

    def __init__(self, detail: str, *, statistic_meta: dict[str, Any] | None = None) -> None:
        self.tool_statistic_meta = (
            dict(statistic_meta) if isinstance(statistic_meta, dict) else {}
        )
        super().__init__(str(detail or "Tool execution failed."))


@dataclass(frozen=True)
class ToolErrorResponse:
    """Normalized internal and model-facing representations of a tool error."""

    internal_message: str
    public_message: str
    model_output: str
    error_code: str | None
    retry_allowed: bool
    stop_tool_calls: bool
    diagnostic_meta: dict[str, object] | None = None

    @property
    def result_payload(self) -> dict[str, object]:
        """Return the structured result persisted beside a failed tool call."""

        payload: dict[str, object] = {"error": self.public_message}
        if self.error_code:
            payload["error_code"] = self.error_code
            payload["retry_allowed"] = self.retry_allowed
        return payload

    @property
    def statistic_meta(self) -> dict[str, object] | None:
        """Return non-sensitive metadata suitable for tool-call statistics."""

        meta = dict(self.diagnostic_meta or {})
        if self.error_code:
            meta.update({
                "error_code": self.error_code,
                "retry_allowed": self.retry_allowed,
            })
        return meta or None


class ToolErrorTracker:
    """Track repeated safe errors for one assistant generation.

    A first safe validation failure receives one correction opportunity.  When
    the same tool returns the same stable code again, provider adapters suppress
    tools on the next model round so the assistant must explain the limitation
    instead of repeatedly regenerating large payloads.
    """

    def __init__(self, *, max_identical_safe_errors: int = 2) -> None:
        if max_identical_safe_errors < 1:
            raise ValueError("max_identical_safe_errors must be at least one")
        self.max_identical_safe_errors = max_identical_safe_errors
        self._counts: dict[tuple[str, str], int] = {}

    def record(self, tool_name: str | None, exc: Exception) -> ToolErrorResponse:
        """Classify ``exc`` and return safe transport plus retry policy."""

        internal_message = str(exc) or exc.__class__.__name__
        raw_diagnostic_meta = getattr(exc, "tool_statistic_meta", None)
        diagnostic_meta = (
            dict(raw_diagnostic_meta) if isinstance(raw_diagnostic_meta, dict) else None
        )
        if not isinstance(exc, SafeToolExecutionError):
            return ToolErrorResponse(
                internal_message=internal_message,
                public_message=GENERIC_TOOL_ERROR_MESSAGE,
                model_output=GENERIC_TOOL_ERROR_MESSAGE,
                error_code=None,
                retry_allowed=False,
                stop_tool_calls=False,
                diagnostic_meta=diagnostic_meta,
            )

        key = (str(tool_name or "unknown").strip().lower(), exc.code)
        occurrence = self._counts.get(key, 0) + 1
        self._counts[key] = occurrence
        retry_allowed = (
            exc.allow_same_response_retry
            and occurrence < self.max_identical_safe_errors
        )

        public_message = exc.safe_message
        if exc.allow_same_response_retry and not retry_allowed:
            public_message = (
                f"{public_message} The same validation error occurred again, "
                "so do not call tools again in this response. Explain the limitation "
                "to the user."
            )

        payload = {
            "error": public_message,
            "error_code": exc.code,
            "retry_allowed": retry_allowed,
        }
        return ToolErrorResponse(
            internal_message=internal_message,
            public_message=public_message,
            model_output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            error_code=exc.code,
            retry_allowed=retry_allowed,
            stop_tool_calls=not retry_allowed,
            diagnostic_meta=diagnostic_meta,
        )


def build_tool_error_stream_event(
    tool_name: str | None,
    tool_call_id: str | None,
    exc: Exception,
) -> str:
    """Build a safe, user-visible terminal event for one failed tool call."""
    descriptor: dict[str, object] = {
        "name": str(tool_name or "").strip(),
        "error": (
            exc.safe_message
            if isinstance(exc, SafeToolExecutionError)
            else GENERIC_TOOL_ERROR_MESSAGE
        ),
    }
    normalized_call_id = str(tool_call_id or "").strip()
    if normalized_call_id:
        descriptor["id"] = normalized_call_id
    if isinstance(exc, SafeToolExecutionError):
        descriptor["error_code"] = exc.code
    return json.dumps(
        {"t": "t_e", "d": descriptor},
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
