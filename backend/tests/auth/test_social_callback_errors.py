import sys
from pathlib import Path
from types import ModuleType

from fastapi import FastAPI
from starlette.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "onelogin.saml2.auth" not in sys.modules:
    fake_onelogin = ModuleType("onelogin")
    fake_saml2 = ModuleType("onelogin.saml2")
    fake_saml2_auth = ModuleType("onelogin.saml2.auth")
    fake_saml2_auth.OneLogin_Saml2_Auth = object
    sys.modules["onelogin"] = fake_onelogin
    sys.modules["onelogin.saml2"] = fake_saml2
    sys.modules["onelogin.saml2.auth"] = fake_saml2_auth

if "zstandard" not in sys.modules:
    sys.modules["zstandard"] = ModuleType("zstandard")

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

from app.auth import router as auth_router_module


def _build_client():
    app = FastAPI()
    app.include_router(auth_router_module.auth_router)
    app.dependency_overrides[auth_router_module.get_db] = lambda: object()
    app.dependency_overrides[auth_router_module.get_db_log] = lambda: object()
    return TestClient(app)


def test_social_oauth_query_error_redirects_and_clears_cookies():
    client = _build_client()
    client.cookies.set("social_state", "state-cookie")
    client.cookies.set("social_nonce", "nonce-cookie")

    response = client.get(
        "/api/v1/auth/social/google/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=social_login_failed"
    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any(header.startswith("social_state=") for header in set_cookie_headers)
    assert any(header.startswith("social_nonce=") for header in set_cookie_headers)


def test_social_oauth_form_post_error_redirects_and_clears_cookies():
    client = _build_client()
    client.cookies.set("social_state", "state-cookie")
    client.cookies.set("social_nonce", "nonce-cookie")

    response = client.post(
        "/api/v1/auth/social/apple/callback",
        data={"error": "access_denied"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=social_login_failed"
    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any(header.startswith("social_state=") for header in set_cookie_headers)
    assert any(header.startswith("social_nonce=") for header in set_cookie_headers)
