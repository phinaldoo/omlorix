from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.backups.state import get_write_freeze_details, is_write_freeze_active


_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_HEALTH_PREFIXES = (
    "/health",
    "/healthz",
    "/ready",
    "/metrics",
)
_BACKUP_ADMIN_PREFIXES = (
    "/api/v1/admin/backups",
)


class WriteFreezeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if is_write_freeze_active():
            details = get_write_freeze_details()
            reason = str(details.get("reason") or "").strip().lower()
            is_restore = reason.startswith("restore")
            is_health_request = request.url.path.startswith(_HEALTH_PREFIXES)
            is_allowed_backup_request = request.url.path.startswith(_BACKUP_ADMIN_PREFIXES)
            is_mutation = request.method.upper() in _MUTATING_METHODS

            # A PostgreSQL full restore replaces schemas and needs exclusive
            # relation locks. Block reads as well as writes before dependency
            # injection opens an authentication/database session. Health
            # probes remain available so orchestration can distinguish
            # maintenance from a crashed process.
            should_block = (
                is_restore and not is_health_request
            ) or (
                not is_restore
                and is_mutation
                and not is_health_request
                and not is_allowed_backup_request
            )
            if should_block:
                detail = (
                    "Reads and writes are temporarily disabled while a restore operation is running."
                    if is_restore
                    else "Writes are temporarily disabled while a backup or restore operation is running."
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": detail,
                        "maintenance": details,
                    },
                    headers={"Retry-After": "30"},
                )

        return await call_next(request)
