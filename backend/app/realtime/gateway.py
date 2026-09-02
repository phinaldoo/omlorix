"""Minimal, independently deployable ASGI application for realtime traffic."""

# ruff: noqa: E402 -- telemetry must initialize before application imports

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import secrets

# Disable ONNX Runtime telemetry before any optional provider dependency can
# initialize it. The realtime gateway does not import ONNX itself.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import text

from app.telemetry.bootstrap import bootstrap_telemetry


_telemetry_bootstrap = bootstrap_telemetry(instrument_database=False)

from app.auth.jwt_material import reconcile_jwt_signing_key
from app.database import AuditSessionLocal, SessionLocal, audit_engine, engine
from app.files.storage import get_user_file_storage_config
from app.groups.init import initialize_groups
from app.logging.models import create_audit_log, validate_ip_hash_salt_configuration
from app.logging.worker import prepare_logging_partitions
from app.middleware.cors import DynamicCORSMiddleware, _load_cors_allowed_origins
from app.middleware.ip_restriction import IPRestrictionMiddleware
from app.middleware.rate_limiter import RedisRateLimiterMiddleware
from app.middleware.request_body_limit import RequestBodyLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.trusted_host import LocalOrPrivateTrustedHostMiddleware
from app.middleware.trusted_host_config import load_application_trusted_hosts
from app.middleware.write_freeze import WriteFreezeMiddleware
from app.paths import DATA_DIR
from app.realtime.router import realtime_router
from app.redis_client import close_async_redis_client, get_redis_client, redis_enabled
from app.settings.models import initialize_settings
from app.settings.utils import (
    validate_ip_address_statistics_requirements,
    validate_ip_restriction_requirements,
    validate_public_url_requirements,
)
from app.telemetry import (
    collect_prometheus_metrics,
    instrument_app,
    instrument_sqlalchemy,
    is_prometheus_metrics_enabled,
    shutdown_telemetry,
)
from app.utils.origin import allow_local_or_private_origins_from_env
from app.version import APP_VERSION


logger = logging.getLogger(__name__)
MODE = os.getenv("MODE", "production")
_ALLOWED_EXACT_PATHS = frozenset({"/health", "/healthz", "/ready", "/metrics"})
_REALTIME_PREFIX = "/api/v1/realtime"

_telemetry_config = _telemetry_bootstrap.config
_telemetry_initialized = _telemetry_bootstrap.initialized
if _telemetry_initialized and _telemetry_config.instrument_sqlalchemy:
    instrument_sqlalchemy(engine, enable_commenter=_telemetry_config.sql_commenter_enabled)
    instrument_sqlalchemy(audit_engine, enable_commenter=_telemetry_config.sql_commenter_enabled)


def _log_gateway_event(action: str, details: dict | None = None) -> None:
    session = AuditSessionLocal()
    try:
        create_audit_log(
            db_log=session,
            user_id="system",
            action=action,
            details=details,
            category="system",
        )
    except Exception:
        logger.exception("Failed to write %s audit log", action)
    finally:
        session.close()


def _initialize_gateway_runtime() -> None:
    """Initialize only state required by realtime authentication and requests."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    prepare_logging_partitions()
    db = SessionLocal()
    try:
        initialize_settings(db)
        validate_ip_hash_salt_configuration()
        signing_key_changed = reconcile_jwt_signing_key(db)
        validate_public_url_requirements(db)
        validate_ip_restriction_requirements(db)
        validate_ip_address_statistics_requirements(db)
        initialize_groups(db)
    finally:
        db.close()

    get_user_file_storage_config()
    if signing_key_changed:
        _log_gateway_event(
            "AUTH_SIGNING_KEY_CHANGED",
            {"sessions_revoked": True, "source": "environment"},
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _initialize_gateway_runtime()
    _log_gateway_event("REALTIME_GATEWAY_START", {"mode": MODE})
    try:
        yield
    finally:
        try:
            await close_async_redis_client()
        finally:
            _log_gateway_event("REALTIME_GATEWAY_STOP", {"mode": MODE})
            shutdown_telemetry()


app = FastAPI(
    title="Omlorix Realtime Gateway",
    description="Dedicated realtime API boundary",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.state.db = SessionLocal

if _telemetry_initialized and _telemetry_config.instrument_fastapi:
    instrument_app(app, excluded_urls="/health,/healthz,/ready,/metrics,/favicon.ico")

# Match the primary API's middleware order and security policy without loading
# any of its unrelated routers or background-worker implementations.
app.add_middleware(WriteFreezeMiddleware)
app.add_middleware(RedisRateLimiterMiddleware)
if MODE.strip().lower() != "dev":
    app.add_middleware(IPRestrictionMiddleware)
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(
    DynamicCORSMiddleware,
    allow_origin_resolver=_load_cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Accept-Language",
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Omlorix-User-Access-Token",
        "X-Omlorix-User-Authorization",
        "X-Requested-With",
    ],
)
app.add_middleware(
    LocalOrPrivateTrustedHostMiddleware,
    allowed_hosts=load_application_trusted_hosts(),
    allow_local_or_private_hosts=allow_local_or_private_origins_from_env(),
    www_redirect=False,
)
app.add_middleware(SecurityHeadersMiddleware)


@app.get("/health", tags=["health"])
@app.get("/healthz", tags=["health"])
def root_health_check():
    """Cheap liveness check for the realtime process."""

    from app.telemetry import is_telemetry_enabled

    return {
        "status": "ok",
        "version": APP_VERSION,
        "telemetry": is_telemetry_enabled(),
    }


@app.get("/ready", tags=["health"])
def root_readiness_check():
    """Verify the database and configured Redis dependency."""

    checks: dict[str, str] = {}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"unavailable: {exc.__class__.__name__}"

    if redis_enabled():
        try:
            client = get_redis_client()
            if client is None:
                raise RuntimeError("redis client unavailable")
            client.ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"unavailable: {exc.__class__.__name__}"
    else:
        checks["redis"] = "disabled"

    if any(
        not status.startswith(("ok", "disabled")) for status in checks.values()
    ):
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "checks": checks},
        )
    return {"status": "ready", "checks": checks}


def _load_prometheus_metrics_token() -> str:
    token = str(os.getenv("PROMETHEUS_METRICS_TOKEN", "") or "").strip()
    if token:
        return token
    token_file = str(os.getenv("PROMETHEUS_METRICS_TOKEN_FILE", "") or "").strip()
    if not token_file:
        return ""
    try:
        return Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("Unable to read Prometheus metrics token file %s: %s", token_file, exc)
        return ""


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request):
    if not is_prometheus_metrics_enabled():
        raise HTTPException(status_code=503, detail="Prometheus exporter disabled")

    metrics_token = _load_prometheus_metrics_token()
    if metrics_token:
        auth_header = str(request.headers.get("authorization") or "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            token.strip(), metrics_token
        ):
            raise HTTPException(status_code=403, detail="Metrics endpoint is restricted")
    elif str(os.getenv("PROMETHEUS_METRICS_PUBLIC", "")).strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise HTTPException(status_code=403, detail="Metrics endpoint is restricted")

    payload, content_type = collect_prometheus_metrics()
    return Response(content=payload, media_type=content_type)


app.include_router(realtime_router)
