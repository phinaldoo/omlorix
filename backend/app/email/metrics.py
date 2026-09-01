from __future__ import annotations

from app.telemetry.config import get_meter


_TEMPLATES = {"password_reset", "twofa_otp", "security_event", "email_change"}


def _template(value: object) -> str:
    normalized = str(value or "unknown")
    return normalized if normalized in _TEMPLATES else "other"


class EmailDeliveryMetrics:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        meter = get_meter("omlorix.email")
        self._delivery = meter.create_counter(
            "omlorix.email.delivery.total",
            description="Durable system-email delivery outcomes",
            unit="1",
        )
        self._duration = meter.create_histogram(
            "omlorix.email.delivery.duration",
            description="SMTP delivery duration",
            unit="ms",
        )
        self._queue_depth = meter.create_histogram(
            "omlorix.email.queue.depth",
            description="Periodic snapshots of deliverable email queue depth",
            unit="1",
        )
        self._oldest_age = meter.create_histogram(
            "omlorix.email.queue.oldest_age",
            description="Periodic snapshots of oldest deliverable email age",
            unit="s",
        )
        self._initialized = True

    def delivery(self, template_type: str, outcome: str, duration_ms: float | None = None):
        attrs = {"template": _template(template_type), "outcome": str(outcome or "unknown")[:24]}
        self._delivery.add(1, attrs)
        if duration_ms is not None:
            self._duration.record(max(0.0, float(duration_ms)), attrs)

    def queue_snapshot(self, depth: int, oldest_age_seconds: float):
        self._queue_depth.record(max(0, int(depth)))
        self._oldest_age.record(max(0.0, float(oldest_age_seconds)))


def get_email_delivery_metrics() -> EmailDeliveryMetrics:
    return EmailDeliveryMetrics()
