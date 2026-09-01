"""
OpenTelemetry instrumentation module for Omlorix.

This module provides comprehensive observability through:
- Distributed tracing (traces and spans)
- Metrics collection (counters, histograms, gauges)
- Structured logging with trace correlation

Configuration is controlled via environment variables:
- OTEL_ENABLED: Enable/disable OpenTelemetry (default: false)
- OTEL_SERVICE_NAME: Service name for telemetry (default: omlorix-backend)
- OTEL_EXPORTER_OTLP_ENDPOINT: OTLP exporter endpoint (default: https://otel-collector:4317)
- OTEL_EXPORTER_OTLP_INSECURE: Allow insecure OTLP transport (default: false)
- OTEL_TRACES_SAMPLER: Sampling strategy (default: parentbased_traceidratio)
- OTEL_TRACES_SAMPLER_ARG: Sampling ratio (default: 1.0)
- OTEL_METRICS_ENABLED: Enable metrics collection (default: true)
- OTEL_LOGS_ENABLED: Enable log correlation (default: true)
"""

from app.telemetry.config import (
    init_telemetry,
    shutdown_telemetry,
    get_tracer,
    get_meter,
    is_telemetry_enabled,
    TelemetryConfig,
    is_prometheus_metrics_enabled,
    collect_prometheus_metrics,
)
from app.telemetry.instrumentor import (
    instrument_app,
    instrument_sqlalchemy,
    instrument_http_clients,
    instrument_logging,
)
from app.telemetry.bootstrap import TelemetryBootstrap, bootstrap_telemetry
from app.telemetry.metrics import (
    ChatMetrics,
    LLMMetrics,
    AuthMetrics,
    SystemMetrics,
    record_auth_ip_block_metric,
    record_auth_login_attempt_metric,
    record_auth_logout_metric,
    record_background_task_metric,
    record_chat_created_metric,
    record_chat_deleted_metric,
    record_chat_message_metric,
    record_file_upload_metric,
    record_llm_request_metric,
)
from app.telemetry.spans import (
    trace_llm_request,
    trace_db_operation,
    trace_external_api,
    trace_background_task,
    add_span_attributes,
    record_exception,
)

__all__ = [
    # Configuration
    "init_telemetry",
    "shutdown_telemetry",
    "get_tracer",
    "get_meter",
    "is_telemetry_enabled",
    "TelemetryConfig",
    "is_prometheus_metrics_enabled",
    "collect_prometheus_metrics",
    # Instrumentation
    "instrument_app",
    "instrument_sqlalchemy",
    "instrument_http_clients",
    "instrument_logging",
    "TelemetryBootstrap",
    "bootstrap_telemetry",
    # Metrics
    "ChatMetrics",
    "LLMMetrics",
    "AuthMetrics",
    "SystemMetrics",
    "record_auth_ip_block_metric",
    "record_auth_login_attempt_metric",
    "record_auth_logout_metric",
    "record_background_task_metric",
    "record_chat_created_metric",
    "record_chat_deleted_metric",
    "record_chat_message_metric",
    "record_file_upload_metric",
    "record_llm_request_metric",
    # Spans
    "trace_llm_request",
    "trace_db_operation",
    "trace_external_api",
    "trace_background_task",
    "add_span_attributes",
    "record_exception",
]
