"""
Auto-instrumentation for FastAPI, SQLAlchemy, and HTTP clients.

Provides automatic span creation for common operations without manual code changes.
"""

import logging
import os
import hashlib
from typing import TYPE_CHECKING, Optional

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.engine import Engine


logger = logging.getLogger(__name__)

# Track instrumentation state
_fastapi_instrumented = False
_sqlalchemy_instrumented = False
_sqlalchemy_instrumented_engines: set[int] = set()
_psycopg2_instrumented = False
_http_instrumented = False
_logging_instrumented = False

_LOG_CORRELATION_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] "
    "[trace_id=%(otelTraceID)s span_id=%(otelSpanID)s "
    "service.name=%(otelServiceName)s trace_sampled=%(otelTraceSampled)s] "
    "- %(message)s"
)


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or str(default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}


_CAPTURE_HTTP_ROUTE = _env_bool("OTEL_CAPTURE_HTTP_ROUTE", False)
_CAPTURE_HTTP_USER_AGENT = _env_bool("OTEL_CAPTURE_HTTP_USER_AGENT", False)
_HASH_HTTP_USER_AGENT = _env_bool("OTEL_HASH_HTTP_USER_AGENT", True)


def _request_hook(span, scope):
    """Hook to add custom attributes to request spans."""
    if span and span.is_recording():
        # Add request-specific attributes
        if _CAPTURE_HTTP_ROUTE and "path" in scope:
            span.set_attribute("http.route", scope.get("path", ""))
        if _CAPTURE_HTTP_USER_AGENT and "headers" in scope:
            headers = dict(scope.get("headers", []))
            # Add user agent if present
            user_agent = headers.get(b"user-agent", b"").decode("utf-8", errors="ignore")
            if user_agent:
                if _HASH_HTTP_USER_AGENT:
                    user_agent_value = hashlib.sha256(user_agent.encode("utf-8")).hexdigest()
                else:
                    user_agent_value = user_agent[:256]
                span.set_attribute("http.user_agent", user_agent_value)


def _response_hook(span, status_code, response_headers):
    """Hook to add custom attributes to response spans."""
    if span and span.is_recording():
        # Mark errors based on status code
        if status_code >= 400:
            span.set_attribute("error", True)
            if status_code >= 500:
                span.set_attribute("error.type", "server_error")
            else:
                span.set_attribute("error.type", "client_error")


def instrument_app(app: "FastAPI", excluded_urls: Optional[str] = None) -> bool:
    """
    Instrument a FastAPI application with OpenTelemetry.
    
    Args:
        app: FastAPI application instance
        excluded_urls: Comma-separated list of URL patterns to exclude from tracing
        
    Returns:
        True if instrumentation was successful
    """
    global _fastapi_instrumented
    
    if _fastapi_instrumented:
        logger.debug("FastAPI already instrumented")
        return True
    
    try:
        # Default exclusions for health checks and static files
        default_exclusions = "/health,/healthz,/ready,/metrics,/favicon.ico"
        exclusions = excluded_urls or default_exclusions
        
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls=exclusions,
            server_request_hook=_request_hook,
            client_response_hook=_response_hook,
        )
        
        _fastapi_instrumented = True
        logger.info("FastAPI instrumentation enabled (excluded: %s)", exclusions)
        return True
        
    except Exception as e:
        logger.warning("Failed to instrument FastAPI: %s", e)
        return False


def uninstrument_app(app: "FastAPI") -> bool:
    """Remove instrumentation from a FastAPI application."""
    global _fastapi_instrumented
    
    try:
        FastAPIInstrumentor.uninstrument_app(app)
        _fastapi_instrumented = False
        logger.info("FastAPI instrumentation removed")
        return True
    except Exception as e:
        logger.warning("Failed to uninstrument FastAPI: %s", e)
        return False


def instrument_sqlalchemy(engine: "Engine", enable_commenter: bool = False) -> bool:
    """
    Instrument SQLAlchemy engine for database tracing.
    
    Args:
        engine: SQLAlchemy engine instance
        enable_commenter: Add SQL comments with trace info
        
    Returns:
        True if instrumentation was successful
    """
    global _psycopg2_instrumented, _sqlalchemy_instrumented
    
    engine_id = id(engine)
    if engine_id in _sqlalchemy_instrumented_engines:
        logger.debug("SQLAlchemy engine already instrumented")
        return True
    
    try:
        SQLAlchemyInstrumentor().instrument(
            engine=engine,
            enable_commenter=enable_commenter,
            commenter_options={
                "db_framework": True,
                "opentelemetry_values": True,
            },
        )
        
        # Also instrument psycopg2 for lower-level PostgreSQL tracing
        if not _psycopg2_instrumented:
            try:
                Psycopg2Instrumentor().instrument(
                    enable_commenter=enable_commenter,
                    skip_dep_check=True,
                )
                _psycopg2_instrumented = True
                logger.debug("Psycopg2 instrumentation enabled")
            except Exception as e:
                logger.debug("Psycopg2 instrumentation skipped: %s", e)
        
        _sqlalchemy_instrumented_engines.add(engine_id)
        _sqlalchemy_instrumented = True
        logger.info("SQLAlchemy instrumentation enabled")
        return True
        
    except Exception as e:
        logger.warning("Failed to instrument SQLAlchemy: %s", e)
        return False


def instrument_http_clients() -> bool:
    """
    Instrument HTTP client libraries (httpx, aiohttp, requests).
    
    Returns:
        True if at least one client was instrumented
    """
    global _http_instrumented
    
    if _http_instrumented:
        logger.debug("HTTP clients already instrumented")
        return True
    
    success = False
    
    # Instrument httpx (async HTTP client)
    try:
        HTTPXClientInstrumentor().instrument()
        logger.debug("HTTPX instrumentation enabled")
        success = True
    except Exception as e:
        logger.debug("HTTPX instrumentation failed: %s", e)
    
    # Instrument aiohttp (async HTTP client)
    try:
        AioHttpClientInstrumentor().instrument()
        logger.debug("aiohttp instrumentation enabled")
        success = True
    except Exception as e:
        logger.debug("aiohttp instrumentation failed: %s", e)
    
    # Instrument requests (sync HTTP client)
    try:
        RequestsInstrumentor().instrument()
        logger.debug("Requests instrumentation enabled")
        success = True
    except Exception as e:
        logger.debug("Requests instrumentation failed: %s", e)
    
    if success:
        _http_instrumented = True
        logger.info("HTTP client instrumentation enabled")
    
    return success


def instrument_logging() -> bool:
    """
    Instrument Python logging to include trace context.
    
    Returns:
        True if instrumentation was successful
    """
    global _logging_instrumented
    
    if _logging_instrumented:
        logger.debug("Logging already instrumented")
        return True
    
    try:
        LoggingInstrumentor().instrument(
            set_logging_format=True,
            logging_format=_LOG_CORRELATION_FORMAT,
            log_level=logging.INFO,
        )
        _logging_instrumented = True
        logger.info("Logging instrumentation enabled with trace correlation")
        return True
    except Exception as e:
        logger.warning("Failed to instrument logging: %s", e)
        return False
