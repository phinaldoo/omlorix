"""
OpenTelemetry configuration and initialization.

Handles setup of tracers, meters, and exporters based on environment configuration.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.sampling import (
    TraceIdRatioBased,
    ParentBasedTraceIdRatio,
    ALWAYS_ON,
    ALWAYS_OFF,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION, DEPLOYMENT_ENVIRONMENT
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.b3 import B3MultiFormat
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagators.composite import CompositePropagator
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest

from app.version import APP_VERSION


logger = logging.getLogger(__name__)

# Global state
_tracer_provider: Optional[TracerProvider] = None
_meter_provider: Optional[MeterProvider] = None
_prometheus_reader: Optional[PrometheusMetricReader] = None
_initialized: bool = False


@dataclass
class TelemetryConfig:
    """Configuration for OpenTelemetry instrumentation."""
    
    enabled: bool = False
    service_name: str = "omlorix-backend"
    service_version: str = APP_VERSION
    environment: str = "production"
    
    # OTLP Exporter settings
    # Secure-by-default. The bundled observability compose overlay explicitly
    # overrides these values for its local plaintext collector.
    otlp_endpoint: str = "https://otel-collector:4317"
    otlp_insecure: bool = False
    otlp_timeout: int = 30
    
    # Tracing settings
    traces_enabled: bool = True
    traces_sampler: str = "parentbased_traceidratio"
    traces_sampler_ratio: float = 1.0
    traces_console_export: bool = False
    
    # Metrics settings
    metrics_enabled: bool = True
    metrics_export_interval_ms: int = 60000
    metrics_console_export: bool = False
    prometheus_export_enabled: bool = True
    
    # Logging settings
    logs_enabled: bool = True

    # Instrumentation scope
    instrument_fastapi: bool = True
    instrument_sqlalchemy: bool = True
    instrument_http_clients: bool = True
    sql_commenter_enabled: bool = False

    # Privacy-related capture controls
    capture_http_route: bool = False
    capture_http_user_agent: bool = False
    hash_http_user_agent: bool = True
    
    # Additional attributes
    extra_attributes: dict = field(default_factory=dict)
    
    @classmethod
    def from_env(cls) -> "TelemetryConfig":
        """Create configuration from environment variables."""
        def _bool(key: str, default: bool) -> bool:
            val = os.getenv(key, str(default)).lower()
            return val in ("true", "1", "yes", "on")
        
        def _float(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, str(default)))
            except ValueError:
                return default
        
        def _int(key: str, default: int) -> int:
            try:
                return int(os.getenv(key, str(default)))
            except ValueError:
                return default
        
        return cls(
            enabled=_bool("OTEL_ENABLED", False),
            service_name=os.getenv("OTEL_SERVICE_NAME", "omlorix-backend"),
            service_version=APP_VERSION,
            environment=os.getenv("MODE", "production"),
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel-collector:4317"),
            otlp_insecure=_bool("OTEL_EXPORTER_OTLP_INSECURE", False),
            otlp_timeout=_int("OTEL_EXPORTER_OTLP_TIMEOUT", 30),
            traces_enabled=_bool("OTEL_TRACES_ENABLED", True),
            traces_sampler=os.getenv("OTEL_TRACES_SAMPLER", "parentbased_traceidratio"),
            traces_sampler_ratio=_float("OTEL_TRACES_SAMPLER_ARG", 1.0),
            traces_console_export=_bool("OTEL_TRACES_CONSOLE_EXPORT", False),
            metrics_enabled=_bool("OTEL_METRICS_ENABLED", True),
            metrics_export_interval_ms=_int("OTEL_METRICS_EXPORT_INTERVAL_MS", 60000),
            metrics_console_export=_bool("OTEL_METRICS_CONSOLE_EXPORT", False),
            prometheus_export_enabled=_bool("OTEL_PROMETHEUS_EXPORTER_ENABLED", True),
            logs_enabled=_bool("OTEL_LOGS_ENABLED", True),
            instrument_fastapi=_bool("OTEL_INSTRUMENT_FASTAPI", True),
            instrument_sqlalchemy=_bool("OTEL_INSTRUMENT_SQLALCHEMY", True),
            instrument_http_clients=_bool("OTEL_INSTRUMENT_HTTP_CLIENTS", True),
            sql_commenter_enabled=_bool("OTEL_SQL_COMMENTER_ENABLED", False),
            capture_http_route=_bool("OTEL_CAPTURE_HTTP_ROUTE", False),
            capture_http_user_agent=_bool("OTEL_CAPTURE_HTTP_USER_AGENT", False),
            hash_http_user_agent=_bool("OTEL_HASH_HTTP_USER_AGENT", True),
        )


def _create_resource(config: TelemetryConfig) -> Resource:
    """Create OpenTelemetry resource with service information."""
    attributes = {
        SERVICE_NAME: config.service_name,
        SERVICE_VERSION: config.service_version,
        DEPLOYMENT_ENVIRONMENT: config.environment,
        "service.namespace": "omlorix",
        "service.instance.id": os.getenv("HOSTNAME", "unknown"),
    }
    attributes.update(config.extra_attributes)
    return Resource.create(attributes)


def _create_sampler(config: TelemetryConfig):
    """Create trace sampler based on configuration."""
    sampler_name = config.traces_sampler.lower()
    ratio = config.traces_sampler_ratio
    
    if sampler_name == "always_on":
        return ALWAYS_ON
    elif sampler_name == "always_off":
        return ALWAYS_OFF
    elif sampler_name == "traceidratio":
        return TraceIdRatioBased(ratio)
    elif sampler_name in ("parentbased_traceidratio", "parentbased"):
        return ParentBasedTraceIdRatio(ratio)
    else:
        logger.warning("Unknown sampler '%s', defaulting to parentbased_traceidratio", sampler_name)
        return ParentBasedTraceIdRatio(ratio)


def _setup_tracing(config: TelemetryConfig, resource: Resource) -> TracerProvider:
    """Initialize tracing with appropriate exporters."""
    sampler = _create_sampler(config)
    provider = TracerProvider(resource=resource, sampler=sampler)
    
    # Add OTLP exporter
    try:
        otlp_exporter = OTLPSpanExporter(
            endpoint=config.otlp_endpoint,
            insecure=config.otlp_insecure,
            timeout=config.otlp_timeout,
        )
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        logger.info("OTLP trace exporter configured: %s", config.otlp_endpoint)
    except Exception as e:
        logger.warning("Failed to configure OTLP trace exporter: %s", e)
    
    # Optionally add console exporter for debugging
    if config.traces_console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("Console trace exporter enabled")
    
    return provider


def _setup_metrics(config: TelemetryConfig, resource: Resource) -> MeterProvider:
    """Initialize metrics with appropriate exporters."""
    global _prometheus_reader
    readers = []
    
    # Add OTLP metric exporter
    try:
        otlp_exporter = OTLPMetricExporter(
            endpoint=config.otlp_endpoint,
            insecure=config.otlp_insecure,
            timeout=config.otlp_timeout,
        )
        readers.append(PeriodicExportingMetricReader(
            otlp_exporter,
            export_interval_millis=config.metrics_export_interval_ms,
        ))
        logger.info("OTLP metric exporter configured: %s", config.otlp_endpoint)
    except Exception as e:
        logger.warning("Failed to configure OTLP metric exporter: %s", e)
    
    # Optionally add console exporter for debugging
    if config.metrics_console_export:
        readers.append(PeriodicExportingMetricReader(
            ConsoleMetricExporter(),
            export_interval_millis=config.metrics_export_interval_ms,
        ))
        logger.info("Console metric exporter enabled")
    
    # Add Prometheus reader for direct scraping
    if config.prometheus_export_enabled:
        _prometheus_reader = PrometheusMetricReader()
        readers.append(_prometheus_reader)
        logger.info("Prometheus metric exporter enabled (exposed via /metrics)")
    else:
        _prometheus_reader = None
    
    return MeterProvider(resource=resource, metric_readers=readers)


def _setup_propagation():
    """Configure context propagation for distributed tracing."""
    propagator = CompositePropagator([
        TraceContextTextMapPropagator(),
        W3CBaggagePropagator(),
        B3MultiFormat(),
    ])
    set_global_textmap(propagator)
    logger.info("Trace propagation configured (W3C TraceContext, W3C Baggage, B3)")


def init_telemetry(config: Optional[TelemetryConfig] = None) -> bool:
    """
    Initialize OpenTelemetry instrumentation.
    
    Args:
        config: Optional configuration. If not provided, loads from environment.
        
    Returns:
        True if initialization was successful, False otherwise.
    """
    global _tracer_provider, _meter_provider, _initialized
    
    if _initialized:
        logger.debug("Telemetry already initialized, skipping")
        return True
    
    if config is None:
        config = TelemetryConfig.from_env()
    
    if not config.enabled:
        logger.info("OpenTelemetry disabled via configuration")
        _initialized = True
        return False
    
    try:
        logger.info("Initializing OpenTelemetry for service: %s", config.service_name)
        
        # Create shared resource
        resource = _create_resource(config)
        
        # Setup propagation
        _setup_propagation()
        
        # Setup tracing
        if config.traces_enabled:
            _tracer_provider = _setup_tracing(config, resource)
            trace.set_tracer_provider(_tracer_provider)
            logger.info("Tracing initialized (sampler: %s, ratio: %.2f)", 
                       config.traces_sampler, config.traces_sampler_ratio)
        
        # Setup metrics
        if config.metrics_enabled:
            _meter_provider = _setup_metrics(config, resource)
            metrics.set_meter_provider(_meter_provider)
            logger.info("Metrics initialized (export interval: %dms)", 
                       config.metrics_export_interval_ms)

        # Wire log records to the active trace context when log correlation is enabled.
        if config.logs_enabled:
            from app.telemetry.instrumentor import instrument_logging

            instrument_logging()
        
        _initialized = True
        logger.info("OpenTelemetry initialization complete")
        return True
        
    except Exception as e:
        logger.exception("Failed to initialize OpenTelemetry: %s", e)
        _initialized = True  # Mark as initialized to prevent retry loops
        return False


def shutdown_telemetry():
    """Gracefully shutdown telemetry providers and flush pending data."""
    global _tracer_provider, _meter_provider, _prometheus_reader, _initialized
    
    if not _initialized:
        return
    
    logger.info("Shutting down OpenTelemetry...")
    
    try:
        if _tracer_provider:
            _tracer_provider.shutdown()
            logger.debug("Tracer provider shut down")
    except Exception as e:
        logger.warning("Error shutting down tracer provider: %s", e)
    
    try:
        if _meter_provider:
            _meter_provider.shutdown()
            logger.debug("Meter provider shut down")
    except Exception as e:
        logger.warning("Error shutting down meter provider: %s", e)
    
    _tracer_provider = None
    _meter_provider = None
    _prometheus_reader = None
    _initialized = False
    logger.info("OpenTelemetry shutdown complete")


def get_tracer(name: str = __name__, version: str | None = None) -> trace.Tracer:
    """
    Get a tracer instance for creating spans.
    
    Args:
        name: Name of the tracer (typically module name)
        version: Version of the instrumenting library. Defaults to the app version.
        
    Returns:
        Tracer instance
    """
    return trace.get_tracer(name, version or APP_VERSION)


def get_meter(name: str = __name__, version: str | None = None) -> metrics.Meter:
    """
    Get a meter instance for creating metrics.
    
    Args:
        name: Name of the meter (typically module name)
        version: Version of the instrumenting library. Defaults to the app version.
        
    Returns:
        Meter instance
    """
    return metrics.get_meter(name, version or APP_VERSION)


def is_telemetry_enabled() -> bool:
    """Check if telemetry is enabled and initialized."""
    config = TelemetryConfig.from_env()
    return config.enabled and _initialized


def is_prometheus_metrics_enabled() -> bool:
    """Return True when Prometheus scraping is configured."""
    return _prometheus_reader is not None


def collect_prometheus_metrics() -> tuple[bytes, str]:
    """Collect current Prometheus metrics payload and content type."""
    if _prometheus_reader is None:
        raise RuntimeError("Prometheus exporter not enabled")
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
