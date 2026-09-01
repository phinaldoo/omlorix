import importlib.util
import base64
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import close_all_sessions


# Application encryption is initialized while test modules are collected. Set a
# valid process-wide test key before any application module can cache the value.
if not os.environ.get("ENCRYPTION_KEY"):
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _install_module(name: str, module: ModuleType) -> None:
    sys.modules[name] = module


def _ensure_opentelemetry_stubs() -> None:
    if importlib.util.find_spec("opentelemetry") is not None:
        return

    class _FakeMetricInstrument:
        def add(self, *args, **kwargs):
            return None

        def record(self, *args, **kwargs):
            return None

    class _FakeMeter:
        def create_counter(self, *args, **kwargs):
            return _FakeMetricInstrument()

        def create_histogram(self, *args, **kwargs):
            return _FakeMetricInstrument()

        def create_up_down_counter(self, *args, **kwargs):
            return _FakeMetricInstrument()

    class _FakeSpan:
        def is_recording(self):
            return False

        def set_attribute(self, *args, **kwargs):
            return None

        def record_exception(self, *args, **kwargs):
            return None

        def set_status(self, *args, **kwargs):
            return None

    class _FakeTracer:
        @contextmanager
        def start_as_current_span(self, *args, **kwargs):
            yield _FakeSpan()

    class _FakeTracerProvider:
        def __init__(self, *args, **kwargs):
            pass

        def add_span_processor(self, *args, **kwargs):
            return None

        def shutdown(self):
            return None

    class _FakeMeterProvider:
        def __init__(self, *args, **kwargs):
            pass

        def shutdown(self):
            return None

        def force_flush(self):
            return None

        def get_meter(self, *args, **kwargs):
            return _FakeMeter()

    trace_api = ModuleType("opentelemetry.trace")
    trace_api.Status = lambda *args, **kwargs: SimpleNamespace()
    trace_api.StatusCode = SimpleNamespace(OK="ok", ERROR="error")
    trace_api.Span = _FakeSpan
    trace_api.Tracer = _FakeTracer
    trace_api.SpanKind = SimpleNamespace(CLIENT="client", INTERNAL="internal")
    trace_api.get_current_span = lambda: _FakeSpan()
    trace_api.get_tracer = lambda *args, **kwargs: _FakeTracer()
    trace_api.get_tracer_provider = lambda: _FakeTracerProvider()
    trace_api.set_tracer_provider = lambda provider: None

    metrics_api = ModuleType("opentelemetry.metrics")
    metrics_api.get_meter = lambda *args, **kwargs: _FakeMeter()
    metrics_api.get_meter_provider = lambda: _FakeMeterProvider()
    metrics_api.set_meter_provider = lambda provider: None
    metrics_api.Meter = _FakeMeter

    root = ModuleType("opentelemetry")
    root.trace = trace_api
    root.metrics = metrics_api

    sdk_trace = ModuleType("opentelemetry.sdk.trace")
    sdk_trace.TracerProvider = _FakeTracerProvider
    sdk_trace.SpanProcessor = type("SpanProcessor", (), {})

    sdk_trace_export = ModuleType("opentelemetry.sdk.trace.export")
    sdk_trace_export.BatchSpanProcessor = lambda *args, **kwargs: SimpleNamespace()
    sdk_trace_export.ConsoleSpanExporter = lambda *args, **kwargs: SimpleNamespace()

    sdk_trace_sampling = ModuleType("opentelemetry.sdk.trace.sampling")
    sdk_trace_sampling.TraceIdRatioBased = lambda *args, **kwargs: SimpleNamespace()
    sdk_trace_sampling.ParentBasedTraceIdRatio = lambda *args, **kwargs: SimpleNamespace()
    sdk_trace_sampling.ALWAYS_ON = SimpleNamespace()
    sdk_trace_sampling.ALWAYS_OFF = SimpleNamespace()

    sdk_metrics = ModuleType("opentelemetry.sdk.metrics")
    sdk_metrics.MeterProvider = _FakeMeterProvider

    sdk_metrics_export = ModuleType("opentelemetry.sdk.metrics.export")
    sdk_metrics_export.PeriodicExportingMetricReader = lambda *args, **kwargs: SimpleNamespace()
    sdk_metrics_export.ConsoleMetricExporter = lambda *args, **kwargs: SimpleNamespace()

    sdk_resources = ModuleType("opentelemetry.sdk.resources")

    class _FakeResource:
        @staticmethod
        def create(*args, **kwargs):
            return SimpleNamespace()

    sdk_resources.Resource = _FakeResource
    sdk_resources.SERVICE_NAME = "service.name"
    sdk_resources.SERVICE_VERSION = "service.version"
    sdk_resources.DEPLOYMENT_ENVIRONMENT = "deployment.environment"

    otlp_trace_exporter = ModuleType("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    otlp_trace_exporter.OTLPSpanExporter = lambda *args, **kwargs: SimpleNamespace()

    otlp_metric_exporter = ModuleType("opentelemetry.exporter.otlp.proto.grpc.metric_exporter")
    otlp_metric_exporter.OTLPMetricExporter = lambda *args, **kwargs: SimpleNamespace()

    prometheus_exporter = ModuleType("opentelemetry.exporter.prometheus")
    prometheus_exporter.PrometheusMetricReader = lambda *args, **kwargs: SimpleNamespace()

    propagate = ModuleType("opentelemetry.propagate")
    propagate.set_global_textmap = lambda *args, **kwargs: None

    propagators_b3 = ModuleType("opentelemetry.propagators.b3")
    propagators_b3.B3MultiFormat = lambda *args, **kwargs: SimpleNamespace()

    tracecontext = ModuleType("opentelemetry.trace.propagation.tracecontext")
    tracecontext.TraceContextTextMapPropagator = lambda *args, **kwargs: SimpleNamespace()

    baggage = ModuleType("opentelemetry.baggage.propagation")
    baggage.W3CBaggagePropagator = lambda *args, **kwargs: SimpleNamespace()

    composite = ModuleType("opentelemetry.propagators.composite")
    composite.CompositePropagator = lambda *args, **kwargs: SimpleNamespace()

    class _FakeInstrumentor:
        def instrument(self, *args, **kwargs):
            return None

        def uninstrument(self, *args, **kwargs):
            return None

    instrumentation_fastapi = ModuleType("opentelemetry.instrumentation.fastapi")
    instrumentation_fastapi.FastAPIInstrumentor = type(
        "FastAPIInstrumentor",
        (),
        {
            "instrument_app": staticmethod(lambda *args, **kwargs: None),
            "uninstrument_app": staticmethod(lambda *args, **kwargs: None),
        },
    )

    instrumentation_sqlalchemy = ModuleType("opentelemetry.instrumentation.sqlalchemy")
    instrumentation_sqlalchemy.SQLAlchemyInstrumentor = _FakeInstrumentor

    instrumentation_httpx = ModuleType("opentelemetry.instrumentation.httpx")
    instrumentation_httpx.HTTPXClientInstrumentor = _FakeInstrumentor

    instrumentation_aiohttp = ModuleType("opentelemetry.instrumentation.aiohttp_client")
    instrumentation_aiohttp.AioHttpClientInstrumentor = _FakeInstrumentor

    instrumentation_requests = ModuleType("opentelemetry.instrumentation.requests")
    instrumentation_requests.RequestsInstrumentor = _FakeInstrumentor

    instrumentation_logging = ModuleType("opentelemetry.instrumentation.logging")
    instrumentation_logging.LoggingInstrumentor = _FakeInstrumentor

    instrumentation_psycopg2 = ModuleType("opentelemetry.instrumentation.psycopg2")
    instrumentation_psycopg2.Psycopg2Instrumentor = _FakeInstrumentor

    _install_module("opentelemetry", root)
    _install_module("opentelemetry.trace", trace_api)
    _install_module("opentelemetry.metrics", metrics_api)
    _install_module("opentelemetry.sdk.trace", sdk_trace)
    _install_module("opentelemetry.sdk.trace.export", sdk_trace_export)
    _install_module("opentelemetry.sdk.trace.sampling", sdk_trace_sampling)
    _install_module("opentelemetry.sdk.metrics", sdk_metrics)
    _install_module("opentelemetry.sdk.metrics.export", sdk_metrics_export)
    _install_module("opentelemetry.sdk.resources", sdk_resources)
    _install_module("opentelemetry.exporter.otlp.proto.grpc.trace_exporter", otlp_trace_exporter)
    _install_module("opentelemetry.exporter.otlp.proto.grpc.metric_exporter", otlp_metric_exporter)
    _install_module("opentelemetry.exporter.prometheus", prometheus_exporter)
    _install_module("opentelemetry.propagate", propagate)
    _install_module("opentelemetry.propagators.b3", propagators_b3)
    _install_module("opentelemetry.trace.propagation.tracecontext", tracecontext)
    _install_module("opentelemetry.baggage.propagation", baggage)
    _install_module("opentelemetry.propagators.composite", composite)
    _install_module("opentelemetry.instrumentation.fastapi", instrumentation_fastapi)
    _install_module("opentelemetry.instrumentation.sqlalchemy", instrumentation_sqlalchemy)
    _install_module("opentelemetry.instrumentation.httpx", instrumentation_httpx)
    _install_module("opentelemetry.instrumentation.aiohttp_client", instrumentation_aiohttp)
    _install_module("opentelemetry.instrumentation.requests", instrumentation_requests)
    _install_module("opentelemetry.instrumentation.logging", instrumentation_logging)
    _install_module("opentelemetry.instrumentation.psycopg2", instrumentation_psycopg2)


_ensure_opentelemetry_stubs()


def _ensure_prometheus_client_stub() -> None:
    if importlib.util.find_spec("prometheus_client") is not None:
        return

    prometheus_client = ModuleType("prometheus_client")
    prometheus_client.CONTENT_TYPE_LATEST = "text/plain"
    prometheus_client.REGISTRY = SimpleNamespace()
    prometheus_client.generate_latest = lambda *args, **kwargs: b""
    _install_module("prometheus_client", prometheus_client)


_ensure_prometheus_client_stub()


def _ensure_webauthn_stub() -> None:
    if importlib.util.find_spec("webauthn") is not None:
        return

    def _bytes_to_base64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(bytes(value)).rstrip(b"=").decode("ascii")

    def _base64url_to_bytes(value: str) -> bytes:
        normalized = str(value)
        return base64.urlsafe_b64decode(normalized + "=" * (-len(normalized) % 4))

    class _Struct:
        def __init__(self, *args, **kwargs):
            self.args = args
            for key, item in kwargs.items():
                setattr(self, key, item)

    class _PublicKeyCredentialDescriptor:
        def __init__(self, id, **kwargs):
            self.id = id
            for key, item in kwargs.items():
                setattr(self, key, item)

    class _UserVerificationRequirement:
        REQUIRED = "required"
        PREFERRED = "preferred"
        DISCOURAGED = "discouraged"

    webauthn = ModuleType("webauthn")
    webauthn.generate_authentication_options = lambda **kwargs: kwargs
    webauthn.generate_registration_options = lambda **kwargs: kwargs
    webauthn.options_to_json = lambda _value: "{}"
    webauthn.verify_authentication_response = lambda **_kwargs: None
    webauthn.verify_registration_response = lambda **_kwargs: None

    helpers = ModuleType("webauthn.helpers")
    helpers.base64url_to_bytes = _base64url_to_bytes
    helpers.bytes_to_base64url = _bytes_to_base64url

    structs = ModuleType("webauthn.helpers.structs")
    structs.AuthenticatorSelectionCriteria = _Struct
    structs.PublicKeyCredentialDescriptor = _PublicKeyCredentialDescriptor
    structs.UserVerificationRequirement = _UserVerificationRequirement

    _install_module("webauthn", webauthn)
    _install_module("webauthn.helpers", helpers)
    _install_module("webauthn.helpers.structs", structs)


_ensure_webauthn_stub()


@pytest.fixture(scope="session")
def _used_test_engines():
    """Keep test-used engines alive until their database resources are disposed."""

    engines: set[Engine] = set()

    def _track_engine(connection) -> None:
        engines.add(connection.engine)

    event.listen(Engine, "engine_connect", _track_engine)
    try:
        yield engines
    finally:
        for engine in engines:
            engine.dispose()
        event.remove(Engine, "engine_connect", _track_engine)


@pytest.fixture(autouse=True)
def _dispose_test_engines(_used_test_engines: set[Engine]):
    """Close SQLite resources left behind by lightweight test databases."""

    yield

    # Test functions frequently create one-off sessions without closing them.
    # Return their checked-out connections to the pool, then dispose every
    # engine used by the completed test.
    close_all_sessions()
    for engine in _used_test_engines:
        engine.dispose()
    _used_test_engines.clear()
