from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

from cryptography.fernet import Fernet
from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

if "opentelemetry" not in sys.modules:
    class _NoopMetric:
        def add(self, *args, **kwargs):
            return None

        def record(self, *args, **kwargs):
            return None

    class _NoopMeter:
        def create_counter(self, *args, **kwargs):
            return _NoopMetric()

        def create_histogram(self, *args, **kwargs):
            return _NoopMetric()

        def create_up_down_counter(self, *args, **kwargs):
            return _NoopMetric()

    class _NoopSpan:
        def is_recording(self):
            return False

        def set_attribute(self, *args, **kwargs):
            return None

        def record_exception(self, *args, **kwargs):
            return None

        def set_status(self, *args, **kwargs):
            return None

    class _NoopSpanContextManager:
        def __enter__(self):
            return _NoopSpan()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _NoopTracer:
        def start_as_current_span(self, *args, **kwargs):
            return _NoopSpanContextManager()

    class _NoopProvider:
        def __init__(self, *args, **kwargs):
            pass

        def add_span_processor(self, *args, **kwargs):
            return None

    class _NoopResource:
        @staticmethod
        def create(attributes):
            return attributes

    class _NoopInstrumentor:
        @classmethod
        def instrument_app(cls, *args, **kwargs):
            return None

        @classmethod
        def uninstrument_app(cls, *args, **kwargs):
            return None

        def instrument(self, *args, **kwargs):
            return None

        def uninstrument(self, *args, **kwargs):
            return None

    class _Status:
        def __init__(self, *args, **kwargs):
            pass

    class _StatusCode:
        OK = "ok"
        ERROR = "error"

    class _SpanKind:
        CLIENT = "client"
        INTERNAL = "internal"

    opentelemetry_stub = ModuleType("opentelemetry")
    trace_module = ModuleType("opentelemetry.trace")
    trace_module.Status = _Status
    trace_module.StatusCode = _StatusCode
    trace_module.Span = _NoopSpan
    trace_module.SpanKind = _SpanKind
    trace_module.Tracer = _NoopTracer
    trace_module.get_current_span = lambda: _NoopSpan()
    trace_module.get_tracer = lambda *args, **kwargs: _NoopTracer()
    trace_module.set_tracer_provider = lambda *args, **kwargs: None

    metrics_module = ModuleType("opentelemetry.metrics")
    metrics_module.Meter = _NoopMeter
    metrics_module.get_meter = lambda *args, **kwargs: _NoopMeter()
    metrics_module.get_meter_provider = lambda: None
    metrics_module.set_meter_provider = lambda *args, **kwargs: None

    opentelemetry_stub.trace = trace_module
    opentelemetry_stub.metrics = metrics_module

    sdk_trace_module = ModuleType("opentelemetry.sdk.trace")
    sdk_trace_module.TracerProvider = _NoopProvider
    sdk_trace_module.SpanProcessor = _NoopProvider

    sdk_trace_export_module = ModuleType("opentelemetry.sdk.trace.export")
    sdk_trace_export_module.BatchSpanProcessor = _NoopProvider
    sdk_trace_export_module.ConsoleSpanExporter = _NoopProvider

    sdk_trace_sampling_module = ModuleType("opentelemetry.sdk.trace.sampling")
    sdk_trace_sampling_module.TraceIdRatioBased = lambda *args, **kwargs: None
    sdk_trace_sampling_module.ParentBasedTraceIdRatio = lambda *args, **kwargs: None
    sdk_trace_sampling_module.ALWAYS_ON = object()
    sdk_trace_sampling_module.ALWAYS_OFF = object()

    sdk_metrics_module = ModuleType("opentelemetry.sdk.metrics")
    sdk_metrics_module.MeterProvider = _NoopProvider

    sdk_metrics_export_module = ModuleType("opentelemetry.sdk.metrics.export")
    sdk_metrics_export_module.PeriodicExportingMetricReader = _NoopProvider
    sdk_metrics_export_module.ConsoleMetricExporter = _NoopProvider

    sdk_resources_module = ModuleType("opentelemetry.sdk.resources")
    sdk_resources_module.Resource = _NoopResource
    sdk_resources_module.SERVICE_NAME = "service.name"
    sdk_resources_module.SERVICE_VERSION = "service.version"
    sdk_resources_module.DEPLOYMENT_ENVIRONMENT = "deployment.environment"

    otlp_trace_module = ModuleType("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    otlp_trace_module.OTLPSpanExporter = _NoopProvider

    otlp_metric_module = ModuleType("opentelemetry.exporter.otlp.proto.grpc.metric_exporter")
    otlp_metric_module.OTLPMetricExporter = _NoopProvider

    prometheus_module = ModuleType("opentelemetry.exporter.prometheus")
    prometheus_module.PrometheusMetricReader = _NoopProvider

    propagate_module = ModuleType("opentelemetry.propagate")
    propagate_module.set_global_textmap = lambda *args, **kwargs: None

    b3_module = ModuleType("opentelemetry.propagators.b3")
    b3_module.B3MultiFormat = _NoopProvider

    tracecontext_module = ModuleType("opentelemetry.trace.propagation.tracecontext")
    tracecontext_module.TraceContextTextMapPropagator = _NoopProvider

    baggage_module = ModuleType("opentelemetry.baggage.propagation")
    baggage_module.W3CBaggagePropagator = _NoopProvider

    composite_module = ModuleType("opentelemetry.propagators.composite")
    composite_module.CompositePropagator = _NoopProvider

    fastapi_instrumentor_module = ModuleType("opentelemetry.instrumentation.fastapi")
    fastapi_instrumentor_module.FastAPIInstrumentor = _NoopInstrumentor

    sqlalchemy_instrumentor_module = ModuleType("opentelemetry.instrumentation.sqlalchemy")
    sqlalchemy_instrumentor_module.SQLAlchemyInstrumentor = _NoopInstrumentor

    httpx_instrumentor_module = ModuleType("opentelemetry.instrumentation.httpx")
    httpx_instrumentor_module.HTTPXClientInstrumentor = _NoopInstrumentor

    aiohttp_instrumentor_module = ModuleType("opentelemetry.instrumentation.aiohttp_client")
    aiohttp_instrumentor_module.AioHttpClientInstrumentor = _NoopInstrumentor

    requests_instrumentor_module = ModuleType("opentelemetry.instrumentation.requests")
    requests_instrumentor_module.RequestsInstrumentor = _NoopInstrumentor

    logging_instrumentor_module = ModuleType("opentelemetry.instrumentation.logging")
    logging_instrumentor_module.LoggingInstrumentor = _NoopInstrumentor

    psycopg2_instrumentor_module = ModuleType("opentelemetry.instrumentation.psycopg2")
    psycopg2_instrumentor_module.Psycopg2Instrumentor = _NoopInstrumentor

    sys.modules["opentelemetry"] = opentelemetry_stub
    sys.modules["opentelemetry.trace"] = trace_module
    sys.modules["opentelemetry.metrics"] = metrics_module
    sys.modules["opentelemetry.sdk.trace"] = sdk_trace_module
    sys.modules["opentelemetry.sdk.trace.export"] = sdk_trace_export_module
    sys.modules["opentelemetry.sdk.trace.sampling"] = sdk_trace_sampling_module
    sys.modules["opentelemetry.sdk.metrics"] = sdk_metrics_module
    sys.modules["opentelemetry.sdk.metrics.export"] = sdk_metrics_export_module
    sys.modules["opentelemetry.sdk.resources"] = sdk_resources_module
    sys.modules["opentelemetry.exporter.otlp.proto.grpc.trace_exporter"] = otlp_trace_module
    sys.modules["opentelemetry.exporter.otlp.proto.grpc.metric_exporter"] = otlp_metric_module
    sys.modules["opentelemetry.exporter.prometheus"] = prometheus_module
    sys.modules["opentelemetry.propagate"] = propagate_module
    sys.modules["opentelemetry.propagators.b3"] = b3_module
    sys.modules["opentelemetry.trace.propagation.tracecontext"] = tracecontext_module
    sys.modules["opentelemetry.baggage.propagation"] = baggage_module
    sys.modules["opentelemetry.propagators.composite"] = composite_module
    sys.modules["opentelemetry.instrumentation.fastapi"] = fastapi_instrumentor_module
    sys.modules["opentelemetry.instrumentation.sqlalchemy"] = sqlalchemy_instrumentor_module
    sys.modules["opentelemetry.instrumentation.httpx"] = httpx_instrumentor_module
    sys.modules["opentelemetry.instrumentation.aiohttp_client"] = aiohttp_instrumentor_module
    sys.modules["opentelemetry.instrumentation.requests"] = requests_instrumentor_module
    sys.modules["opentelemetry.instrumentation.logging"] = logging_instrumentor_module
    sys.modules["opentelemetry.instrumentation.psycopg2"] = psycopg2_instrumentor_module

if "prometheus_client" not in sys.modules:
    prometheus_client_stub = ModuleType("prometheus_client")
    prometheus_client_stub.CONTENT_TYPE_LATEST = "text/plain"
    prometheus_client_stub.REGISTRY = object()
    prometheus_client_stub.generate_latest = lambda *args, **kwargs: b""
    sys.modules["prometheus_client"] = prometheus_client_stub

from app.auth.models import (
    Authentication,
    NativeAuthGrant,
    PasswordResetToken,
    PendingAuthAction,
    WebAuthnChallenge,
)
from app.email.models import (
    EMAIL_CHANGE_CANCELLED,
    EMAIL_CHANGE_PENDING,
    OUTBOX_CANCELLED,
    EmailOutbox,
    PendingEmailChange,
    enqueue_email,
)
from app.database import Base
from app.users import utils as user_utils
from app.users.models import User, create_user
from app.utils import encryption as encryption_utils


def _session_with_authentication_tables():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Authentication.__table__,
            PasswordResetToken.__table__,
            PendingAuthAction.__table__,
            NativeAuthGrant.__table__,
            WebAuthnChallenge.__table__,
            EmailOutbox.__table__,
            PendingEmailChange.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)
    return session_factory()


def _insert_authentication_row(db, *, auth_id: str, user_id: str) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.execute(
        text(
            """
            INSERT INTO authentication (
                id,
                user_id,
                device_info,
                ip_address,
                access_token,
                refresh_token,
                access_token_hash,
                refresh_token_hash,
                created_at,
                last_active_at
            )
            VALUES (
                :id,
                :user_id,
                :device_info,
                :ip_address,
                :access_token,
                :refresh_token,
                :access_token_hash,
                :refresh_token_hash,
                :created_at,
                :last_active_at
            )
            """
        ),
        {
            "id": auth_id,
            "user_id": user_id,
            "device_info": "Desktop browser",
            "ip_address": "203.0.113.10",
            "access_token": "stale-access-token",
            "refresh_token": "stale-refresh-token",
            "access_token_hash": f"{auth_id}:access",
            "refresh_token_hash": f"{auth_id}:refresh",
            "created_at": now,
            "last_active_at": now,
        },
    )
    db.commit()


def test_admin_profile_password_change_revokes_existing_sessions(monkeypatch):
    """The admin Profile password field must invalidate every existing login."""
    db = _session_with_authentication_tables()
    revoked_user_ids: list[str] = []

    monkeypatch.setattr(user_utils, "_assert_password_policy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.auth.utils.hash_password", lambda password: f"hashed::{password}")
    monkeypatch.setattr(user_utils, "revoke_user_sessions", lambda user_id: revoked_user_ids.append(user_id))
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)

    try:
        user = create_user(
            db,
            email="profile-user@example.com",
            hashed_password="old-hash",
            first_name="Profile",
            last_name="User",
            role="user",
            group_id="group-1",
            user_id="profile-user-1",
        )
        _insert_authentication_row(db, auth_id="profile-auth-1", user_id=user.id)
        now = datetime.now(timezone.utc)
        reset_token = PasswordResetToken(
            id="profile-reset-1",
            user_id=user.id,
            token_hash="profile-reset-hash",
            requested_ip="203.0.113.0/24",
            requested_user_agent="Desktop browser",
            created_at=now,
            expires_at=now.replace(microsecond=0) + timedelta(minutes=30),
        )
        db.add(reset_token)
        pending_change = PendingEmailChange(
            id="profile-email-change-1",
            user_id=user.id,
            new_email="attacker@example.com",
            old_email=user.email,
            verify_token_hash="v" * 64,
            cancel_token_hash="c" * 64,
            status=EMAIL_CHANGE_PENDING,
            created_at=now,
            expires_at=now + timedelta(hours=24),
        )
        db.add(pending_change)
        pending_job = enqueue_email(
            db,
            user_id=user.id,
            recipient="attacker@example.com",
            template_type="email_change",
            idempotency_key="email-change:verify:profile-email-change-1",
            payload={"kind": "verify", "request_id": pending_change.id},
        )
        db.commit()

        response = user_utils.admin_update_user_profile(
            SimpleNamespace(
                user_id=user.id,
                email=None,
                first_name=None,
                last_name=None,
                group_id=None,
                password="NewPassword123!",
                wrong_sign_in_attempts=None,
                lock=None,
            ),
            db,
        )

        db.refresh(user)

        assert response["updated_fields"] == ["password"]
        assert response["changes"] == [
            {"field": "password", "changed": True, "sessions_revoked": True}
        ]
        assert user.hashed_password == "hashed::NewPassword123!"
        assert db.query(Authentication).filter(Authentication.user_id == user.id).count() == 0
        db.refresh(reset_token)
        assert reset_token.consumed_at is not None
        assert reset_token.requested_ip is None
        assert reset_token.requested_user_agent is None
        db.refresh(pending_change)
        db.refresh(pending_job)
        assert pending_change.status == EMAIL_CHANGE_CANCELLED
        assert pending_job.status == OUTBOX_CANCELLED
        assert pending_job.recipient is None
        assert pending_job.payload is None
        assert revoked_user_ids == [user.id]
        notice = (
            db.query(EmailOutbox)
            .filter(EmailOutbox.template_type == "security_event")
            .one()
        )
        assert notice.recipient == user.email
        assert notice.payload["event_type"] == "admin_password_reset"
    finally:
        db.close()


def test_admin_profile_password_reset_rejects_current_password(monkeypatch):
    db = _session_with_authentication_tables()
    revoked_user_ids: list[str] = []
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)
    monkeypatch.setattr(
        user_utils,
        "revoke_user_sessions",
        lambda user_id: revoked_user_ids.append(user_id),
    )
    monkeypatch.setattr(
        "app.auth.utils.verify_password",
        lambda password, password_hash: (
            password == "CurrentPassword123!" and password_hash == "current-hash"
        ),
    )
    monkeypatch.setattr(
        user_utils,
        "_assert_password_policy",
        lambda *_args, **_kwargs: pytest.fail(
            "password policy and mutation must not run for password reuse"
        ),
    )

    try:
        user = create_user(
            db,
            email="admin-reset-reuse@example.com",
            hashed_password="current-hash",
            first_name="Admin reset",
            last_name="Reuse",
            role="user",
            group_id="group-1",
            user_id="admin-reset-reuse-1",
        )
        _insert_authentication_row(
            db,
            auth_id="admin-reset-reuse-auth-1",
            user_id=user.id,
        )

        with pytest.raises(HTTPException) as exc_info:
            user_utils.admin_update_user_profile(
                SimpleNamespace(
                    user_id=user.id,
                    email=None,
                    first_name=None,
                    last_name=None,
                    group_id=None,
                    password="CurrentPassword123!",
                    wrong_sign_in_attempts=None,
                    lock=None,
                ),
                db,
            )

        assert exc_info.value.status_code == 400
        assert (
            exc_info.value.detail
            == "New password must be different from the current password."
        )
        db.rollback()
        db.refresh(user)
        assert user.hashed_password == "current-hash"
        assert (
            db.query(Authentication)
            .filter(Authentication.user_id == user.id)
            .count()
            == 1
        )
        assert revoked_user_ids == []
        assert db.query(EmailOutbox).count() == 0
    finally:
        db.close()


def test_admin_profile_update_without_password_preserves_existing_sessions(monkeypatch):
    """Ordinary profile edits must not sign the user out."""
    db = _session_with_authentication_tables()
    revoked_user_ids: list[str] = []

    monkeypatch.setattr(user_utils, "revoke_user_sessions", lambda user_id: revoked_user_ids.append(user_id))
    monkeypatch.setattr(encryption_utils, "_ENCRYPTION_KEY", Fernet.generate_key())
    monkeypatch.setattr(encryption_utils, "_CIPHER_SUITE", None)

    try:
        user = create_user(
            db,
            email="ordinary-profile-user@example.com",
            hashed_password="old-hash",
            first_name="Before",
            last_name="User",
            role="user",
            group_id="group-1",
            user_id="ordinary-profile-user-1",
        )
        _insert_authentication_row(db, auth_id="ordinary-profile-auth-1", user_id=user.id)

        response = user_utils.admin_update_user_profile(
            SimpleNamespace(
                user_id=user.id,
                email=None,
                first_name="After",
                last_name=None,
                group_id=None,
                password="",
                wrong_sign_in_attempts=None,
                lock=None,
            ),
            db,
        )

        db.refresh(user)

        assert response["updated_fields"] == ["first_name"]
        assert user.first_name == "After"
        assert db.query(Authentication).filter(Authentication.user_id == user.id).count() == 1
        assert revoked_user_ids == []
    finally:
        db.close()
