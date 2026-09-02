"""Shared response security headers for Omlorix ASGI applications."""

from __future__ import annotations

import os

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.cache_headers import apply_no_store_headers


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "form-action 'self'; "
    "img-src 'self' data: blob: https:; "
    "media-src 'self' data: blob: https:; "
    "font-src 'self' data:; "
    "frame-src 'self' blob: data: https://www.youtube-nocookie.com https://docs.google.com https://drive.google.com https://accounts.google.com; "
    "child-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' https://apis.google.com; "
    "connect-src 'self' https://api.openai.com https://generativelanguage.googleapis.com wss://generativelanguage.googleapis.com; "
    "worker-src 'self' blob:; "
    "manifest-src 'self'"
)

PERMISSIONS_POLICY = (
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
    "serial=(), bluetooth=(), browsing-topics=()"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        request_path = request.url.path
        if (
            request_path.startswith("/api/v1/chats/shared")
            or request_path.startswith("/api/v1/files/canvas/shared")
        ):
            apply_no_store_headers(response)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if os.getenv("MODE", "production").strip().lower() != "dev":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response
