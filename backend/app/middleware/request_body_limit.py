from __future__ import annotations

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


_MIB = 1024 * 1024

# Keep authentication payloads small enough to cover federated-login callbacks
# while bounding public JSON parsing well below the general API allowance.
AUTH_REQUEST_BODY_LIMIT_BYTES = 1 * _MIB
DEFAULT_REQUEST_BODY_LIMIT_BYTES = 16 * _MIB
LARGE_REQUEST_BODY_LIMIT_BYTES = 512 * _MIB


_LARGE_POST_BODY_PATH = re.compile(
    r"^/api/v1/(?:"
    r"files/(?:upload|canvas/(?:save|spreadsheet/save|markdown/pdf|latex/render))"
    r"|chats/(?:import/chatgpt|meetings/transcribe)"
    r"|llm/transcribe"
    r"|users/import/self"
    r"|admin/(?:users/import|ip-address/statistics/import|import/openwebui/chats(?:/bulk)?)"
    r"|agents/[^/]+/assets"
    r"|skills/(?:import-markdown-files|admin/(?:import-files|[^/]+/files/[^/]+)|(?!admin/)[^/]+/files/[^/]+)"
    r")$"
)
_LARGE_PUT_BODY_PATH = re.compile(
    r"^/api/v1/presentations/[^/]+/editor$"
)


class _RequestBodyTooLarge(Exception):
    pass


def _contains_only_body_limit_errors(error: BaseException) -> bool:
    """Return whether an exception group contains only body-limit signals."""

    if isinstance(error, _RequestBodyTooLarge):
        return True
    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(
            _contains_only_body_limit_errors(nested) for nested in error.exceptions
        )
    return False


def _normalize_path(path: object) -> str:
    normalized = str(path or "/")
    if normalized != "/":
        normalized = normalized.rstrip("/")
    return normalized or "/"


def is_explicit_large_body_route(method: object, path: object) -> bool:
    """Return whether one known workflow legitimately accepts a large body."""

    normalized_method = str(method or "").upper()
    normalized_path = _normalize_path(path)
    if normalized_method == "POST":
        return _LARGE_POST_BODY_PATH.fullmatch(normalized_path) is not None
    if normalized_method == "PUT":
        return _LARGE_PUT_BODY_PATH.fullmatch(normalized_path) is not None
    return False


def resolve_request_body_limit_bytes(method: object, path: object) -> int:
    """Resolve the hard pre-parser request-body ceiling for one HTTP request."""

    normalized_path = _normalize_path(path)
    if normalized_path.startswith("/api/v1/auth/"):
        return AUTH_REQUEST_BODY_LIMIT_BYTES
    if is_explicit_large_body_route(method, normalized_path):
        return LARGE_REQUEST_BODY_LIMIT_BYTES
    return DEFAULT_REQUEST_BODY_LIMIT_BYTES


def _declared_content_length(scope: Scope) -> int | None:
    """Return the largest valid declared length, or None for an invalid header."""

    declared_lengths: list[int] = []
    for name, value in scope.get("headers") or []:
        if name.lower() != b"content-length":
            continue
        for raw_value in value.split(b","):
            normalized = raw_value.strip()
            if not normalized.isdigit():
                return None
            declared_lengths.append(int(normalized))
    return max(declared_lengths) if declared_lengths else None


class RequestBodyLimitMiddleware:
    """Reject oversized declared and streamed HTTP bodies before route parsing."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        auth_limit_bytes: int = AUTH_REQUEST_BODY_LIMIT_BYTES,
        default_limit_bytes: int = DEFAULT_REQUEST_BODY_LIMIT_BYTES,
        large_limit_bytes: int = LARGE_REQUEST_BODY_LIMIT_BYTES,
    ) -> None:
        limits = (auth_limit_bytes, default_limit_bytes, large_limit_bytes)
        if any(int(limit) <= 0 for limit in limits):
            raise ValueError("Request body limits must be positive")
        self.app = app
        self.auth_limit_bytes = int(auth_limit_bytes)
        self.default_limit_bytes = int(default_limit_bytes)
        self.large_limit_bytes = int(large_limit_bytes)

    def _resolve_limit(self, scope: Scope) -> int:
        path = _normalize_path(scope.get("path"))
        if path.startswith("/api/v1/auth/"):
            return self.auth_limit_bytes
        if is_explicit_large_body_route(scope.get("method"), path):
            return self.large_limit_bytes
        return self.default_limit_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit_bytes = self._resolve_limit(scope)
        declared_length = _declared_content_length(scope)
        if declared_length is not None and declared_length > limit_bytes:
            await self._send_too_large(scope, receive, send)
            return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > limit_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _RequestBodyTooLarge:
            # Body-bound FastAPI routes read before starting a response. If a
            # custom streaming handler ever starts first, terminate it instead
            # of attempting to emit a second response status.
            if response_started:
                raise
            await self._send_too_large(scope, receive, send)
        except BaseExceptionGroup as error:
            # Starlette's BaseHTTPMiddleware runs its downstream application
            # in a task group, which wraps receive errors in an exception
            # group. Preserve unrelated grouped failures while translating the
            # body-limit signal into the same 413 response as direct handlers.
            if not _contains_only_body_limit_errors(error) or response_started:
                raise
            await self._send_too_large(scope, receive, send)

    @staticmethod
    async def _send_too_large(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "Request body exceeds the allowed size."},
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)
