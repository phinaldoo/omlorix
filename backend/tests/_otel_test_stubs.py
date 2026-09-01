from __future__ import annotations

import sys
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace


class _NoopInstrument:
    def add(self, *_args, **_kwargs):
        return None

    def record(self, *_args, **_kwargs):
        return None


class _NoopMeter:
    def create_counter(self, **_kwargs):
        return _NoopInstrument()

    def create_histogram(self, **_kwargs):
        return _NoopInstrument()

    def create_up_down_counter(self, **_kwargs):
        return _NoopInstrument()


class _NoopSpan:
    def is_recording(self):
        return False

    def set_attribute(self, *_args, **_kwargs):
        return None

    def record_exception(self, *_args, **_kwargs):
        return None

    def set_status(self, *_args, **_kwargs):
        return None


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, *_args, **_kwargs):
        yield _NoopSpan()


def _install_module(name: str) -> ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = ModuleType(name)
        sys.modules[name] = module
    return module


def install_otel_stubs() -> None:
    if "opentelemetry" in sys.modules:
        return

    otel = _install_module("opentelemetry")

    trace_mod = _install_module("opentelemetry.trace")
    trace_mod.get_tracer = lambda *_args, **_kwargs: _NoopTracer()
    trace_mod.get_tracer_provider = lambda: SimpleNamespace()
    trace_mod.set_tracer_provider = lambda *_args, **_kwargs: None
    trace_mod.get_current_span = lambda: _NoopSpan()
    trace_mod.Status = lambda code=None, description=None: SimpleNamespace(code=code, description=description)
    trace_mod.StatusCode = SimpleNamespace(OK="OK", ERROR="ERROR")
    trace_mod.Span = _NoopSpan
    trace_mod.Tracer = _NoopTracer
    trace_mod.SpanKind = SimpleNamespace(CLIENT="CLIENT", INTERNAL="INTERNAL")
    otel.trace = trace_mod

    metrics_mod = _install_module("opentelemetry.metrics")
    metrics_mod.get_meter = lambda *_args, **_kwargs: _NoopMeter()
    metrics_mod.get_meter_provider = lambda: SimpleNamespace()
    metrics_mod.set_meter_provider = lambda *_args, **_kwargs: None
    metrics_mod.Meter = _NoopMeter
    otel.metrics = metrics_mod

    sdk_trace = _install_module("opentelemetry.sdk.trace")
    sdk_trace.TracerProvider = type(
        "TracerProvider",
        (),
        {
            "__init__": lambda self, *args, **kwargs: None,
            "add_span_processor": lambda self, *_args, **_kwargs: None,
            "shutdown": lambda self: None,
            "force_flush": lambda self: True,
            "get_tracer": lambda self, *_args, **_kwargs: _NoopTracer(),
        },
    )
    sdk_trace.SpanProcessor = object

    sdk_trace_export = _install_module("opentelemetry.sdk.trace.export")
    sdk_trace_export.BatchSpanProcessor = type("BatchSpanProcessor", (), {"__init__": lambda self, *args, **kwargs: None})
    sdk_trace_export.ConsoleSpanExporter = type("ConsoleSpanExporter", (), {"__init__": lambda self, *args, **kwargs: None})

    sdk_trace_sampling = _install_module("opentelemetry.sdk.trace.sampling")
    sdk_trace_sampling.TraceIdRatioBased = lambda ratio: SimpleNamespace(ratio=ratio)
    sdk_trace_sampling.ParentBasedTraceIdRatio = lambda ratio: SimpleNamespace(ratio=ratio)
    sdk_trace_sampling.ALWAYS_ON = "ALWAYS_ON"
    sdk_trace_sampling.ALWAYS_OFF = "ALWAYS_OFF"

    sdk_metrics = _install_module("opentelemetry.sdk.metrics")
    sdk_metrics.MeterProvider = type(
        "MeterProvider",
        (),
        {
            "__init__": lambda self, *args, **kwargs: None,
            "get_meter": lambda self, *_args, **_kwargs: _NoopMeter(),
            "shutdown": lambda self: None,
            "force_flush": lambda self: True,
        },
    )

    sdk_metrics_export = _install_module("opentelemetry.sdk.metrics.export")
    sdk_metrics_export.PeriodicExportingMetricReader = type(
        "PeriodicExportingMetricReader",
        (),
        {"__init__": lambda self, *args, **kwargs: None},
    )
    sdk_metrics_export.ConsoleMetricExporter = type(
        "ConsoleMetricExporter",
        (),
        {"__init__": lambda self, *args, **kwargs: None},
    )

    sdk_resources = _install_module("opentelemetry.sdk.resources")
    sdk_resources.Resource = type(
        "Resource",
        (),
        {
            "create": staticmethod(lambda attributes: SimpleNamespace(attributes=attributes)),
        },
    )
    sdk_resources.SERVICE_NAME = "service.name"
    sdk_resources.SERVICE_VERSION = "service.version"
    sdk_resources.DEPLOYMENT_ENVIRONMENT = "deployment.environment"

    _install_module("opentelemetry.exporter")
    _install_module("opentelemetry.exporter.otlp")
    _install_module("opentelemetry.exporter.otlp.proto")
    _install_module("opentelemetry.exporter.otlp.proto.grpc")
    otlp_trace = _install_module("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    otlp_trace.OTLPSpanExporter = type("OTLPSpanExporter", (), {"__init__": lambda self, *args, **kwargs: None})
    otlp_metric = _install_module("opentelemetry.exporter.otlp.proto.grpc.metric_exporter")
    otlp_metric.OTLPMetricExporter = type("OTLPMetricExporter", (), {"__init__": lambda self, *args, **kwargs: None})
    exporter_prom = _install_module("opentelemetry.exporter.prometheus")
    exporter_prom.PrometheusMetricReader = type("PrometheusMetricReader", (), {"__init__": lambda self, *args, **kwargs: None})

    propagate = _install_module("opentelemetry.propagate")
    propagate.set_global_textmap = lambda *_args, **_kwargs: None

    _install_module("opentelemetry.propagators")
    propagators_b3 = _install_module("opentelemetry.propagators.b3")
    propagators_b3.B3MultiFormat = type("B3MultiFormat", (), {"__init__": lambda self, *args, **kwargs: None})
    propagators_composite = _install_module("opentelemetry.propagators.composite")
    propagators_composite.CompositePropagator = type(
        "CompositePropagator",
        (),
        {"__init__": lambda self, *args, **kwargs: None},
    )

    trace_propagation = _install_module("opentelemetry.trace.propagation")
    tracecontext = _install_module("opentelemetry.trace.propagation.tracecontext")
    tracecontext.TraceContextTextMapPropagator = type(
        "TraceContextTextMapPropagator",
        (),
        {"__init__": lambda self, *args, **kwargs: None},
    )
    trace_propagation.tracecontext = tracecontext

    _install_module("opentelemetry.baggage")
    baggage_propagation = _install_module("opentelemetry.baggage.propagation")
    baggage_propagation.W3CBaggagePropagator = type(
        "W3CBaggagePropagator",
        (),
        {"__init__": lambda self, *args, **kwargs: None},
    )

    _install_module("opentelemetry.instrumentation")
    for module_name, class_name in [
        ("opentelemetry.instrumentation.fastapi", "FastAPIInstrumentor"),
        ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
        ("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
        ("opentelemetry.instrumentation.aiohttp_client", "AioHttpClientInstrumentor"),
        ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
        ("opentelemetry.instrumentation.logging", "LoggingInstrumentor"),
        ("opentelemetry.instrumentation.psycopg2", "Psycopg2Instrumentor"),
    ]:
        module = _install_module(module_name)
        instrumentor_cls = type(
            class_name,
            (),
            {
                "__init__": lambda self, *args, **kwargs: None,
                "instrument": lambda self, *args, **kwargs: None,
                "instrument_app": staticmethod(lambda *args, **kwargs: None),
                "uninstrument_app": staticmethod(lambda *args, **kwargs: None),
            },
        )
        setattr(module, class_name, instrumentor_cls)

    if "prometheus_client" not in sys.modules:
        prometheus_client = ModuleType("prometheus_client")
        prometheus_client.CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"
        prometheus_client.REGISTRY = SimpleNamespace()
        prometheus_client.generate_latest = lambda *_args, **_kwargs: b""
        sys.modules["prometheus_client"] = prometheus_client
