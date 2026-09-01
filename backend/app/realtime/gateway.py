"""ASGI boundary for independently scaled realtime connections.

The gateway deliberately reuses Omlorix's complete authentication, proxy trust,
rate-limit, audit, and provider implementation from ``app.main``.  This small
outer boundary makes every non-realtime application route unreachable even if
the service is accidentally exposed inside a cluster.
"""

from __future__ import annotations

from app.main import app as _application


_ALLOWED_EXACT_PATHS = frozenset({"/health", "/healthz", "/ready", "/metrics"})
_REALTIME_PREFIX = "/api/v1/realtime"


class RealtimeGateway:
    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await _application(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        allowed = path in _ALLOWED_EXACT_PATHS or path == _REALTIME_PREFIX or path.startswith(
            f"{_REALTIME_PREFIX}/"
        )
        if allowed:
            await _application(scope, receive, send)
            return

        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "Not found"})
            return
        if scope_type == "http":
            body = b'{"detail":"Not Found"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await _application(scope, receive, send)


app = RealtimeGateway()
