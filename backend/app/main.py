from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import secrets
import threading

# Disable ONNX Runtime's non-Windows telemetry before third-party imports can initialize it.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

from app.telemetry.bootstrap import bootstrap_telemetry

_telemetry_bootstrap = bootstrap_telemetry(instrument_database=False)

from app.paths import DATA_DIR

# OpenTelemetry is initialized before imports that might make HTTP calls.
from app.telemetry import (
    shutdown_telemetry,
    instrument_app,
    instrument_sqlalchemy,
    is_prometheus_metrics_enabled,
    collect_prometheus_metrics,
)

_telemetry_config = _telemetry_bootstrap.config
_telemetry_initialized = _telemetry_bootstrap.initialized

DATA_SUBDIRECTORIES = (
    "oauth_profilepicture",
    "chats",
    "logo",
    "profilepicture",
    "skills",
    "userFiles",
)

_AUDIT_SHARE_SCRUB_BATCHES_PER_PASS = 4
_AUDIT_SHARE_SCRUB_ACTIVE_SLEEP_SECONDS = 1.0
_AUDIT_SHARE_SCRUB_IDLE_SLEEP_SECONDS = 30.0
_AUDIT_SHARE_SCRUB_JOIN_TIMEOUT_SECONDS = 5.0


from app.database import engine, SessionLocal, audit_engine, AuditSessionLocal
from app.redis_client import close_async_redis_client, get_redis_client, redis_enabled
from app.version import APP_VERSION

# Instrument SQLAlchemy engines for database tracing
if _telemetry_initialized and _telemetry_config.instrument_sqlalchemy:
    instrument_sqlalchemy(engine, enable_commenter=_telemetry_config.sql_commenter_enabled)
    instrument_sqlalchemy(audit_engine, enable_commenter=_telemetry_config.sql_commenter_enabled)
from app.admin.router import admin_router
from app.admin.concurrency.models import (
    initialize_concurrency_metrics,
    start_concurrency_metrics_maintenance_worker,
    stop_concurrency_metrics_maintenance_worker,
)
from app.agents.router import agents_router
from app.auth.router import auth_router
from app.auth.jwt_material import reconcile_jwt_signing_key
from app.chats.router import chats_router
from app.connections.router import connections_router
from app.backups.router import backups_router
from app.backups.service import ensure_backup_directories
from app.chats.worker import (
    start_auto_delete_chats_worker,
    stop_auto_delete_chats_worker,
)
from app.chats.read_aloud import (
    start_read_aloud_cleanup_worker,
    stop_read_aloud_cleanup_worker,
)
from app.file_folders.router import file_folders_router
from app.files.router import files_router
from app.files.storage import get_user_file_storage_config
from app.files.worker import (
    start_artifact_share_cleanup_worker,
    stop_artifact_share_cleanup_worker,
    start_temp_file_cleanup_worker,
    stop_temp_file_cleanup_worker,
)
from app.groups.management_router import group_management_router
from app.groups.init import initialize_groups
from app.llm.router import llm_router
from app.feedback.router import feedback_router
from app.memories.router import memories_router
from app.notes.router import notes_router
from app.prompts.router import prompts_router
from app.llm.ollama.router import ollama_router
from app.llm.lmstudio.router import lmstudio_router
from app.llm.worker import start_llm_provider_worker, stop_llm_provider_worker
from app.llmstats.worker import (
    start_byok_stats_retention_worker,
    stop_byok_stats_retention_worker,
)
from app.middleware.cors import DynamicCORSMiddleware, _load_cors_allowed_origins
from app.middleware.ip_restriction import IPRestrictionMiddleware
from app.middleware.rate_limiter import RedisRateLimiterMiddleware
from app.middleware.request_body_limit import RequestBodyLimitMiddleware
from app.middleware.trusted_host import LocalOrPrivateTrustedHostMiddleware
from app.middleware.write_freeze import WriteFreezeMiddleware
from app.utils.cache_headers import apply_no_store_headers
from app.utils.origin import allow_local_or_private_origins_from_env
from app.utils.trusted_hosts import load_trusted_hosts
from app.logging.models import (
    create_audit_log,
    scrub_share_capability_references_in_audit_logs,
    validate_ip_hash_salt_configuration,
)
from app.logging.worker import (
    prepare_logging_partitions,
    start_auth_log_retention_worker,
    stop_auth_log_retention_worker,
)
from app.projects.router import projects_router
from app.realtime.router import realtime_router
from app.realtime.worker import (
    start_realtime_enforcement_worker,
    stop_realtime_enforcement_worker,
)
from app.scim.router import ScimException, scim_exception_handler, scim_router
from app.settings.models import initialize_settings
from app.service_connections.router import service_connections_router
from app.settings.utils import (
    coerce_bool,
    get_value_by_page_and_key,
    validate_ldap_sync_requirements,
    validate_ip_restriction_requirements,
    validate_public_url_requirements,
    validate_ip_address_statistics_requirements,
)
from app.settings.router import settings_router
from app.skills.router import skills_router
from app.todos.router import todo_router
from app.userNotifications.router import user_notifications_router
from app.automations.router import automations_router
from app.automations.worker import (
    start_automation_scheduler_worker,
    stop_automation_scheduler_worker,
)

from app.tools.websearch.router import websearch_router
from app.tools.deep_research.router import deep_research_router
from app.tools.slide_presentation.router import presentations_router
from app.tools.custom.router import custom_python_tools_router
from app.users.router import users_router
from app.llmstats.router import llmstats_router
from app.llmstats.realtime_router import realtime_stats_router
from app.utils.utils import (
    start_internet_connectivity_checker_worker,
    stop_internet_connectivity_checker_worker,
)
from app.workers.maintenance import external_maintenance_enabled
from app.workers.events import (
    build_worker as build_audit_event_worker,
    external_audit_event_enabled,
)
from app.workers.lifecycle import (
    build_worker as build_account_lifecycle_worker,
    external_account_lifecycle_enabled,
)
from app.workers.operations import (
    build_worker as build_operations_worker,
    external_operations_enabled,
)

def _load_trusted_hosts() -> list[str]:
    """Load trusted Host header values from env and configured public URL."""

    configured_candidates = []
    public_url_settings_loaded = False
    db = None
    try:
        db = SessionLocal()
        from app.settings.public_urls import normalize_public_urls

        configured_candidates.extend(
            normalize_public_urls(
                get_value_by_page_and_key("general", "public_url", db),
                allow_empty=True,
            )
        )
        public_url_settings_loaded = True
    except HTTPException as exc:
        if exc.status_code == 404:
            # A missing settings page is a valid fresh-install state.
            public_url_settings_loaded = True
        else:
            logger.warning("Unable to load public_url from settings for host validation", exc_info=True)
    except Exception:
        logger.warning("Unable to load public_url from settings for host validation", exc_info=True)
    finally:
        if db:
            db.close()

    trusted_hosts = load_trusted_hosts(
        public_url_candidates=configured_candidates,
        mode=os.getenv("MODE", "production"),
        allow_any_if_unconfigured=public_url_settings_loaded,
    )
    if trusted_hosts:
        logger.info("Trusted host validation enabled for: %s", ", ".join(trusted_hosts))
    return trusted_hosts


from app.utils.router import utils_router


MODE = os.getenv("MODE", "production")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def ensure_data_directories() -> None:
    """Create the application data directory and its required children."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in DATA_SUBDIRECTORIES:
        target = DATA_DIR / name
        target.mkdir(parents=True, exist_ok=True)


def log_startup_status() -> None:
    """Record concise operational startup information."""
    logger.info("Omlorix Backend Server Starting...")
    logger.info("Initializing database and background workers...")
    
    # Log OpenTelemetry status
    if _telemetry_initialized:
        logger.info("OpenTelemetry: ENABLED (service=%s, endpoint=%s)", 
                   _telemetry_config.service_name, _telemetry_config.otlp_endpoint)
    else:
        logger.info("OpenTelemetry: DISABLED")


def _log_application_event(action: str, details: dict | None = None):
    """Log application event to the audit schema."""
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_startup_status()

    # Ensure shared data folders exist even if the repo doesn't ship them
    ensure_data_directories()
    ensure_backup_directories()

    # Audit partitions must be durable before any startup action can emit an
    # audit row.  Let preparation failures abort lifespan startup so the
    # service cannot report healthy after silently dropping security events.
    prepare_logging_partitions()

    _log_application_event("APPLICATION_START", {"mode": MODE})

    db = SessionLocal()
    try:
        # Database schema migrations are intentionally owned by the dedicated
        # deployment migration job (the Compose ``migrate`` service). Running
        # them in an API process would duplicate orchestration and allow every
        # FastAPI replica to attempt schema changes during startup.
        initialize_settings(db)
        validate_ip_hash_salt_configuration()
        # Reconcile the operator-owned environment key before accepting any
        # request so a rotation cannot leave old database sessions usable.
        if reconcile_jwt_signing_key(db):
            _log_application_event(
                "AUTH_SIGNING_KEY_CHANGED",
                {"sessions_revoked": True, "source": "environment"},
            )
        # Establish durable tracking provenance before request traffic begins.
        # This also repairs compact aggregates for SQLite metadata bootstraps.
        try:
            initialize_concurrency_metrics(db)
        except (SQLAlchemyError, RuntimeError):
            logger.exception("Failed to initialize optional concurrency metrics")
            # A failed statement can leave the shared startup session unusable
            # until its transaction is explicitly rolled back.
            try:
                db.rollback()
            except SQLAlchemyError:
                logger.exception("Failed to roll back concurrency metrics initialization")
        validate_public_url_requirements(db)
        validate_ldap_sync_requirements(db)
        validate_ip_restriction_requirements(db)
        validate_ip_address_statistics_requirements(db)
        initialize_groups(db)
        get_user_file_storage_config()
        # Read connectivity settings before closing the session
        offline_mode_enabled = False
        connectivity_check_enabled = True
        try:
            offline_mode_enabled = coerce_bool(get_value_by_page_and_key("general", "offline_mode", db), default=False)
        except Exception:
            logger.warning("Failed to read offline_mode setting, using default: False")
        try:
            connectivity_check_enabled = coerce_bool(
                get_value_by_page_and_key("general", "internet_connectivity_check_enabled", db),
                default=True,
            )
        except Exception:
            logger.warning("Failed to read internet_connectivity_check_enabled setting, using default: True")
    except RuntimeError as exc:
        message = str(exc).strip()
        if message:
            logger.critical("%s", message)
        else:
            logger.error("Critical startup error encountered; shutting down.")
        db.close()
        os._exit(1)
    finally:
        db.close()

    maintenance_is_external = external_maintenance_enabled()
    audit_events_are_external = external_audit_event_enabled()
    operations_are_external = external_operations_enabled()
    lifecycle_is_external = external_account_lifecycle_enabled()
    inline_durable_workers: list[tuple[object, threading.Thread]] = []
    audit_scrub_stop_event = threading.Event()
    audit_scrub_thread: threading.Thread | None = None

    def _scrub_existing_share_capability_references(stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            audit_scrub_db = AuditSessionLocal()
            scrubbed_rows = 0
            try:
                scrubbed_rows = scrub_share_capability_references_in_audit_logs(
                    audit_scrub_db,
                    max_batches=_AUDIT_SHARE_SCRUB_BATCHES_PER_PASS,
                )
                if scrubbed_rows:
                    logger.info("Scrubbed share capability references from %s audit log rows", scrubbed_rows)
            except Exception:
                logger.exception("Failed to scrub share capability references from existing audit logs")
            finally:
                audit_scrub_db.close()

            sleep_seconds = (
                _AUDIT_SHARE_SCRUB_ACTIVE_SLEEP_SECONDS
                if scrubbed_rows
                else _AUDIT_SHARE_SCRUB_IDLE_SLEEP_SECONDS
            )
            stop_event.wait(sleep_seconds)

    if not maintenance_is_external:
        audit_scrub_thread = threading.Thread(
            target=_scrub_existing_share_capability_references,
            args=(audit_scrub_stop_event,),
            name="audit-share-capability-scrub",
            daemon=True,
        )
        audit_scrub_thread.start()

        # Local development compatibility. Production deployments set
        # MAINTENANCE_WORKER_MODE=external and run these only in maintenance_worker.
        start_llm_provider_worker()
        start_realtime_enforcement_worker()
        start_auto_delete_chats_worker()
        if not offline_mode_enabled and connectivity_check_enabled:
            start_internet_connectivity_checker_worker()
        start_temp_file_cleanup_worker()
        start_artifact_share_cleanup_worker()
        start_auth_log_retention_worker()
        start_byok_stats_retention_worker()
        start_read_aloud_cleanup_worker()
        start_concurrency_metrics_maintenance_worker()
    # Startup: start automation scheduler worker
    start_automation_scheduler_worker()

    def _start_inline_durable_worker(worker, name: str) -> None:
        thread = threading.Thread(
            target=worker.run_forever,
            name=name,
            daemon=True,
        )
        thread.start()
        inline_durable_workers.append((worker, thread))

    if not operations_are_external:
        _start_inline_durable_worker(
            build_operations_worker(),
            "inline-operations-worker",
        )
    if not audit_events_are_external:
        _start_inline_durable_worker(
            build_audit_event_worker(),
            "inline-audit-event-worker",
        )
    if not lifecycle_is_external:
        _start_inline_durable_worker(
            build_account_lifecycle_worker(),
            "inline-account-lifecycle-worker",
        )
    yield
    # Shutdown: signal background workers to stop and surface failures
    shutdown_errors: list[str] = []

    def _run_with_guard(func, description: str) -> None:
        try:
            func()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to stop %s", description)
            shutdown_errors.append(f"{description}: {exc!s}")

    if not maintenance_is_external:
        _run_with_guard(stop_internet_connectivity_checker_worker, "internet connectivity checker worker")
        _run_with_guard(stop_auto_delete_chats_worker, "chats auto delete worker")
        _run_with_guard(stop_artifact_share_cleanup_worker, "canvas share cleanup worker")
        _run_with_guard(stop_temp_file_cleanup_worker, "temp file cleanup worker")
        _run_with_guard(stop_llm_provider_worker, "LLM provider monitor worker")
        _run_with_guard(stop_realtime_enforcement_worker, "realtime deadline enforcement worker")
        _run_with_guard(stop_auth_log_retention_worker, "auth log retention worker")
        _run_with_guard(stop_byok_stats_retention_worker, "BYOK stats retention worker")
        _run_with_guard(stop_read_aloud_cleanup_worker, "read aloud cleanup worker")
        _run_with_guard(
            stop_concurrency_metrics_maintenance_worker,
            "concurrency metrics maintenance worker",
        )
    _run_with_guard(stop_automation_scheduler_worker, "automation scheduler worker")
    for worker, _thread in inline_durable_workers:
        _run_with_guard(worker.request_stop, f"inline {worker.queue} worker")
    for worker, thread in inline_durable_workers:
        thread.join(timeout=15)
        if thread.is_alive():
            shutdown_errors.append(f"inline {worker.queue} worker: shutdown timed out")
    if audit_scrub_thread is not None:
        audit_scrub_stop_event.set()
        audit_scrub_thread.join(timeout=_AUDIT_SHARE_SCRUB_JOIN_TIMEOUT_SECONDS)
    try:
        await close_async_redis_client()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to close async Redis client")
        shutdown_errors.append(f"async Redis client: {exc!s}")
    if shutdown_errors:
        raise RuntimeError(
            "One or more shutdown tasks failed: " + "; ".join(shutdown_errors)
        )

    # Shutdown OpenTelemetry and flush pending data
    try:
        shutdown_telemetry()
    except Exception:
        logger.exception("Failed to shutdown OpenTelemetry")

    _log_application_event("APPLICATION_STOP", {"mode": MODE})

if MODE == "dev":
    logger.info("Development mode enabled")
    app = FastAPI(
        title="Omlorix (Development)",
        description="Development environment",
        lifespan=lifespan
    )
else:
    app = FastAPI(
        title="Omlorix",
        description="Production deployment",
        docs_url=None,          # Disable Swagger UI
        redoc_url=None,         # Disable ReDoc
        openapi_url=None,       # Disable OpenAPI schema
        lifespan=lifespan
    )

app.add_exception_handler(ScimException, scim_exception_handler)

# Instrument FastAPI application with OpenTelemetry
if _telemetry_initialized and _telemetry_config.instrument_fastapi:
    instrument_app(app, excluded_urls="/health,/healthz,/ready,/metrics,/favicon.ico")
    logger.info("OpenTelemetry FastAPI instrumentation enabled")

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
        if MODE != "dev":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

# Write-freeze middleware blocks mutating requests while backup/restore jobs run.
app.add_middleware(WriteFreezeMiddleware)



# Add distributed Redis rate limiter middleware before IP checks.
app.add_middleware(RedisRateLimiterMiddleware)


# Add IP restriction middleware (runs *after* CORS) except in dev mode so
# local Docker/NAT setups are usable without proxy bootstrapping.
if MODE.strip().lower() != "dev":
    app.add_middleware(IPRestrictionMiddleware)


# Reject oversized declared and streamed bodies before FastAPI parses route
# payloads. CORS, trusted-host validation, and security headers are registered
# after this middleware so their response protections remain outside it.
app.add_middleware(RequestBodyLimitMiddleware)


# CORS middleware must be registered last because Starlette inserts each added
# middleware at the front of the user stack. Adding CORS last keeps it
# outermost so it can decorate responses produced by deeper middleware too.
# Note: For development purposes, the cors middleware is hard-coded. # TODO
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
_allow_local_or_private_hosts = allow_local_or_private_origins_from_env()
if _allow_local_or_private_hosts:
    logger.info(
        "Trusted host validation also accepts localhost and literal private/loopback IP addresses because "
        "ALLOW_LOCAL_OR_PRIVATE_ORIGINS is enabled."
    )

app.add_middleware(
    LocalOrPrivateTrustedHostMiddleware,
    allowed_hosts=_load_trusted_hosts(),
    # Keep Host-header validation aligned with the same explicit opt-in used
    # by sensitive authentication endpoints for private browser origins.
    allow_local_or_private_hosts=_allow_local_or_private_hosts,
    www_redirect=False,
)
app.add_middleware(SecurityHeadersMiddleware)



# Root-level health endpoints for container orchestration (bypasses /api/v1 prefix)
@app.get("/health", tags=["health"])
@app.get("/healthz", tags=["health"])
def root_health_check():
    """Cheap liveness check for container and load-balancer health probes."""
    from app.telemetry import is_telemetry_enabled
    return {"status": "ok", "version": APP_VERSION, "telemetry": is_telemetry_enabled()}


@app.get("/ready", tags=["health"])
def root_readiness_check():
    """Readiness check that verifies required runtime dependencies."""
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

    failed = {
        name: status
        for name, status in checks.items()
        if not status.startswith(("ok", "disabled"))
    }
    if failed:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})

    return {"status": "ready", "checks": checks}


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request):
    """Expose Prometheus metrics collected via the embedded reader."""
    if not is_prometheus_metrics_enabled():
        raise HTTPException(status_code=503, detail="Prometheus exporter disabled")

    metrics_token = _load_prometheus_metrics_token()
    if metrics_token:
        auth_header = str(request.headers.get("authorization") or "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(token.strip(), metrics_token):
            raise HTTPException(status_code=403, detail="Metrics endpoint is restricted")
        payload, content_type = collect_prometheus_metrics()
        return Response(content=payload, media_type=content_type)

    allow_public_metrics = str(os.getenv("PROMETHEUS_METRICS_PUBLIC", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not allow_public_metrics:
        raise HTTPException(status_code=403, detail="Metrics endpoint is restricted")

    payload, content_type = collect_prometheus_metrics()
    return Response(content=payload, media_type=content_type)


def _load_prometheus_metrics_token() -> str:
    metrics_token = str(os.getenv("PROMETHEUS_METRICS_TOKEN", "") or "").strip()
    if metrics_token:
        return metrics_token

    token_file = str(os.getenv("PROMETHEUS_METRICS_TOKEN_FILE", "") or "").strip()
    if not token_file:
        return ""

    try:
        return Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("Unable to read Prometheus metrics token file %s: %s", token_file, exc)
        return ""


# Register routers
app.include_router(admin_router)
app.include_router(agents_router)
app.include_router(backups_router)
app.include_router(auth_router)
app.include_router(chats_router)
app.include_router(connections_router)
app.include_router(file_folders_router)
app.include_router(files_router)
app.include_router(group_management_router)
app.include_router(feedback_router)
app.include_router(memories_router)
app.include_router(notes_router)
app.include_router(prompts_router)
app.include_router(llm_router)
app.include_router(ollama_router)
app.include_router(lmstudio_router)
app.include_router(projects_router)
app.include_router(realtime_router)
app.include_router(scim_router)
app.include_router(settings_router)
app.include_router(service_connections_router)
app.include_router(skills_router)
app.include_router(users_router)
app.include_router(websearch_router)
app.include_router(deep_research_router)
app.include_router(custom_python_tools_router)
app.include_router(utils_router)
app.include_router(llmstats_router)
app.include_router(realtime_stats_router)
app.include_router(todo_router)
app.include_router(user_notifications_router)
app.include_router(automations_router)
app.include_router(presentations_router)



# Add database to app state for middleware access
app.state.db = SessionLocal
