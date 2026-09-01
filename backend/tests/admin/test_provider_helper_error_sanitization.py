from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

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

from app.admin.settings import router as admin_router
from app.llm.schemas import ProviderEnum


class _QueryResult:
    def __init__(self, provider: SimpleNamespace) -> None:
        self._provider = provider

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._provider


class _DB:
    def __init__(self, provider: SimpleNamespace) -> None:
        self._provider = provider

    def query(self, _model):
        return _QueryResult(self._provider)


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(host="198.51.100.10"),
        headers={"user-agent": "pytest"},
    )


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id="admin-1")


def test_admin_list_audio_generation_models_sanitizes_upstream_exception(monkeypatch, caplog):
    provider = SimpleNamespace(
        id="provider-1",
        provider=ProviderEnum.openai.value,
        api_key="sk-live-do-not-return",
        settings={},
    )
    fake_module = ModuleType("app.llm.openai.text_to_speech")

    def _raise_audio_error(_db, _provider_id):
        raise RuntimeError("provider rejected key sk-live-do-not-return")

    fake_module.get_audio_generation_schema_part_1 = _raise_audio_error
    monkeypatch.setitem(sys.modules, "app.llm.openai.text_to_speech", fake_module)

    with caplog.at_level(logging.ERROR, logger=admin_router.logger.name):
        with pytest.raises(HTTPException) as exc_info:
            admin_router.admin_list_audio_generation_models(
                provider_id="provider-1",
                request=_request(),
                db=_DB(provider),
                db_log=object(),
                admin_user=_admin(),
            )

    assert exc_info.value.status_code == 500
    assert "sk-live-do-not-return" not in exc_info.value.detail
    assert re.fullmatch(
        r"Failed to list audio generation models\. Correlation ID: [0-9a-f-]{36}\.",
        exc_info.value.detail,
    )
    assert "provider rejected key sk-live-do-not-return" not in caplog.text
    assert "sk-live-do-not-return" not in caplog.text
    assert "LIST_AUDIO_GENERATION_MODELS" in caplog.text
    assert "RuntimeError" in caplog.text


def test_admin_get_image_generation_model_settings_sanitizes_upstream_exception(monkeypatch, caplog):
    provider = SimpleNamespace(
        id="provider-2",
        provider=ProviderEnum.openai.value,
        api_key="sk-image-secret",
        settings={},
    )
    fake_module = ModuleType("app.llm.openai.image_generation")

    def _raise_image_error(_model_name):
        raise RuntimeError("Authorization header Bearer sk-image-secret failed")

    fake_module.get_image_generation_schema_part_2 = _raise_image_error
    monkeypatch.setitem(sys.modules, "app.llm.openai.image_generation", fake_module)

    with caplog.at_level(logging.ERROR, logger=admin_router.logger.name):
        with pytest.raises(HTTPException) as exc_info:
            admin_router.admin_get_image_generation_model_settings(
                provider_id="provider-2",
                model_name="gpt-image-1",
                request=_request(),
                db=_DB(provider),
                db_log=object(),
                admin_user=_admin(),
            )

    assert exc_info.value.status_code == 500
    assert "sk-image-secret" not in exc_info.value.detail
    assert re.fullmatch(
        r"Failed to get image generation model settings\. Correlation ID: [0-9a-f-]{36}\.",
        exc_info.value.detail,
    )
    assert "Authorization header Bearer sk-image-secret failed" not in caplog.text
    assert "sk-image-secret" not in caplog.text
    assert "GET_IMAGE_GENERATION_MODEL_SETTINGS" in caplog.text
    assert "RuntimeError" in caplog.text


def test_admin_provider_helper_http_exception_keeps_status_and_detail(monkeypatch):
    """Deliberate provider errors must bypass generic sanitization."""
    provider = SimpleNamespace(
        id="provider-1",
        provider=ProviderEnum.openai.value,
        api_key="",
        settings={},
    )
    fake_module = ModuleType("app.llm.openai.text_to_speech")

    def _raise_actionable_error(_db, _provider_id):
        raise HTTPException(status_code=400, detail="Configure an API key first")

    fake_module.get_audio_generation_schema_part_1 = _raise_actionable_error
    monkeypatch.setitem(sys.modules, "app.llm.openai.text_to_speech", fake_module)

    with pytest.raises(HTTPException) as exc_info:
        admin_router.admin_list_audio_generation_models(
            provider_id="provider-1",
            request=_request(),
            db=_DB(provider),
            db_log=object(),
            admin_user=_admin(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Configure an API key first"


def test_image_generation_rejects_provider_without_type():
    """A missing provider discriminator must not dispatch to OpenAI."""
    provider = SimpleNamespace(
        id="provider-2",
        provider=None,
        api_key="credential-for-an-unknown-backend",
        settings={},
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_router.admin_list_image_generation_models(
            provider_id="provider-2",
            request=_request(),
            db=_DB(provider),
            db_log=object(),
            admin_user=_admin(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unsupported provider type for image generation"
