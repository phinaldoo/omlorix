from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import random
import signal
import socket
import sys
import threading
import time
import uuid

from sqlalchemy import func

from app.auth.email_delivery import (
    EmailDeliveryConfigurationError,
    is_email_delivery_config_ready,
    load_login_email_delivery_config,
)
from app.auth.models import delete_expired_pending_auth_actions
from app.database import SessionLocal
from app.email.delivery import EmailDeliverySendError, SMTPDeliveryClient
from app.email.metrics import get_email_delivery_metrics
from app.email.models import (
    EmailOutbox,
    OUTBOX_PENDING,
    OUTBOX_PROCESSING,
    OUTBOX_RETRY,
    claim_email_batch,
    expire_email_rows,
    mark_email_cancelled,
    mark_email_failed,
    mark_email_sent,
    lock_email_for_delivery,
    purge_stale_email_security_state,
    purge_terminal_email_rows,
    renew_email_lease,
)
from app.email.templates import render_outbox_message
from app.email.validation import validate_outbox_row


logger = logging.getLogger(__name__)
HEARTBEAT_PATH = Path(
    os.getenv("EMAIL_WORKER_HEARTBEAT_PATH", "/tmp/omlorix-email-worker-heartbeat")
)
_stop_event = threading.Event()
_last_heartbeat_monotonic = 0.0


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"[:96]


def _retry_delay(attempt_count: int) -> int:
    base = min(6 * 60 * 60, 30 * (2 ** max(0, min(int(attempt_count) - 1, 10))))
    return base + random.SystemRandom().randint(0, max(1, base // 5))


def _write_heartbeat(*, force: bool = False) -> None:
    global _last_heartbeat_monotonic
    monotonic_now = time.monotonic()
    if not force and monotonic_now - _last_heartbeat_monotonic < 5:
        return
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = HEARTBEAT_PATH.with_name(f"{HEARTBEAT_PATH.name}.{os.getpid()}.tmp")
    temporary.write_text(str(time.time()), encoding="ascii")
    os.replace(temporary, HEARTBEAT_PATH)
    _last_heartbeat_monotonic = monotonic_now


def healthcheck() -> bool:
    try:
        age = time.time() - HEARTBEAT_PATH.stat().st_mtime
    except OSError:
        return False
    maximum_age = _bounded_env_int("EMAIL_WORKER_HEALTH_MAX_AGE_SECONDS", 90, 15, 3600)
    return 0 <= age <= maximum_age


def _queue_snapshot(metrics) -> None:
    session = SessionLocal()
    try:
        current = datetime.now(timezone.utc)
        depth, oldest = (
            session.query(func.count(EmailOutbox.id), func.min(EmailOutbox.created_at))
            .filter(
                EmailOutbox.status.in_((OUTBOX_PENDING, OUTBOX_RETRY, OUTBOX_PROCESSING))
            )
            .one()
        )
        if oldest is None:
            age = 0.0
        else:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            age = max(0.0, (current - oldest).total_seconds())
        metrics.queue_snapshot(int(depth or 0), age)
    except Exception:
        session.rollback()
        logger.exception("Email worker queue telemetry failed")
    finally:
        session.close()


def _load_delivery_config():
    session = SessionLocal()
    try:
        return load_login_email_delivery_config(session, include_secrets=True)
    finally:
        session.close()


def _record_delivery_metric(
    metrics,
    template_type: str,
    outcome: str,
    duration_ms: float | None = None,
) -> None:
    """Keep telemetry failures outside the durable delivery state machine."""

    try:
        metrics.delivery(template_type, outcome, duration_ms)
    except Exception:
        logger.exception(
            "Email delivery telemetry failed template=%s outcome=%s",
            template_type,
            outcome,
        )


def _process_batch(worker_id: str, metrics) -> int:
    try:
        config = _load_delivery_config()
    except Exception:
        logger.exception("Email worker could not load delivery configuration")
        return 0
    # Health represents a worker that can still reach and read its delivery
    # authority, not merely a Python process spinning during a database outage.
    _write_heartbeat()
    # A missing configuration is an operator state, not a message failure. Do
    # not lease or burn attempts; queued work starts automatically once SMTP is
    # configured.
    if not is_email_delivery_config_ready(config):
        return 0

    session = SessionLocal()
    batch_size = _bounded_env_int("EMAIL_WORKER_BATCH_SIZE", 20, 1, 200)
    lease_seconds = _bounded_env_int("EMAIL_WORKER_LEASE_SECONDS", 600, 60, 3600)
    try:
        rows = claim_email_batch(
            session,
            worker_id=worker_id,
            batch_size=batch_size,
            lease_seconds=lease_seconds,
        )
        if not rows:
            return 0

        client = None
        processed = 0
        for row in rows:
            row_id = row.id
            started = time.monotonic()
            template_type = row.template_type
            if not renew_email_lease(
                session,
                row,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            ):
                _record_delivery_metric(metrics, template_type, "lease_lost")
                processed += 1
                _write_heartbeat()
                continue
            row = lock_email_for_delivery(
                session,
                row,
                worker_id=worker_id,
            )
            if row is None:
                session.rollback()
                _record_delivery_metric(metrics, template_type, "lease_lost")
                processed += 1
                _write_heartbeat()
                continue
            valid, reason = validate_outbox_row(session, row)
            if not valid:
                mark_email_cancelled(session, row, reason=reason)
                _record_delivery_metric(metrics, row.template_type, "cancelled")
                processed += 1
                _write_heartbeat()
                continue

            try:
                message = render_outbox_message(row, config)
                if client is None:
                    client = SMTPDeliveryClient(config)
                    client.__enter__()
                client.send_message(message)
                duration_ms = (time.monotonic() - started) * 1000
                template_type = row.template_type
                mark_email_sent(session, row)
                _record_delivery_metric(metrics, template_type, "sent", duration_ms)
            except EmailDeliverySendError as exc:
                duration_ms = (time.monotonic() - started) * 1000
                status = mark_email_failed(
                    session,
                    row,
                    error_type=exc.error_type,
                    retryable=exc.retryable,
                    retry_delay_seconds=_retry_delay(row.attempt_count),
                )
                _record_delivery_metric(
                    metrics,
                    row.template_type,
                    status,
                    duration_ms,
                )
                logger.warning(
                    "Email delivery failed template=%s category=%s retryable=%s attempt=%s",
                    row.template_type,
                    exc.error_type,
                    exc.retryable,
                    row.attempt_count,
                )
                if client is not None and (
                    exc.retryable or getattr(client, "server", None) is None
                ):
                    client.__exit__(None, None, None)
                    client = None
            except (EmailDeliveryConfigurationError, ValueError) as exc:
                category = "configuration" if isinstance(exc, EmailDeliveryConfigurationError) else "rendering"
                status = mark_email_failed(
                    session,
                    row,
                    error_type=category,
                    retryable=isinstance(exc, EmailDeliveryConfigurationError),
                    retry_delay_seconds=_retry_delay(row.attempt_count),
                )
                _record_delivery_metric(metrics, row.template_type, status)
                logger.error("Email job failed template=%s category=%s", row.template_type, category)
            except Exception:
                session.rollback()
                row = (
                    session.query(EmailOutbox)
                    .filter(
                        EmailOutbox.id == row_id,
                        EmailOutbox.status == OUTBOX_PROCESSING,
                        EmailOutbox.lease_owner == worker_id,
                    )
                    .with_for_update()
                    .first()
                )
                if row is not None:
                    status = mark_email_failed(
                        session,
                        row,
                        error_type="internal",
                        retryable=True,
                        retry_delay_seconds=_retry_delay(row.attempt_count),
                    )
                    _record_delivery_metric(metrics, row.template_type, status)
                else:
                    # A commit may have completed before an unrelated callback
                    # raised. Never revive a sent, cancelled, or newly claimed
                    # row from terminal/foreign state.
                    _record_delivery_metric(metrics, template_type, "state_changed")
                logger.exception("Unexpected email worker failure")
            processed += 1
            _write_heartbeat()

        if client is not None:
            client.__exit__(None, None, None)
        return processed
    except Exception:
        session.rollback()
        logger.exception("Email worker batch failed")
        return 0
    finally:
        session.close()


def _maintenance() -> None:
    session = SessionLocal()
    try:
        expired = 0
        purged = 0
        security_state = 0
        pending_auth_actions = 0
        batch_size = _bounded_env_int(
            "EMAIL_WORKER_MAINTENANCE_BATCH_SIZE", 1000, 100, 5000
        )
        max_batches = _bounded_env_int(
            "EMAIL_WORKER_MAINTENANCE_MAX_BATCHES", 10, 1, 100
        )
        for _ in range(max_batches):
            count = expire_email_rows(session, batch_size=batch_size)
            expired += count
            _write_heartbeat()
            if count < batch_size:
                break
        for _ in range(max_batches):
            count = purge_terminal_email_rows(
                session,
                retention_days=_bounded_env_int(
                    "EMAIL_OUTBOX_RETENTION_DAYS", 7, 1, 90
                ),
                batch_size=batch_size,
            )
            purged += count
            _write_heartbeat()
            if count < batch_size:
                break
        security_state = purge_stale_email_security_state(
            session,
            batch_size=batch_size,
        )
        for _ in range(max_batches):
            count = delete_expired_pending_auth_actions(
                session,
                batch_size=batch_size,
            )
            pending_auth_actions += count
            _write_heartbeat()
            if count < batch_size:
                break
        if expired or purged or security_state or pending_auth_actions:
            logger.info(
                "Email outbox maintenance expired=%s purged=%s security_state=%s "
                "pending_auth_actions=%s",
                expired,
                purged,
                security_state,
                pending_auth_actions,
            )
    except Exception:
        session.rollback()
        logger.exception("Email outbox maintenance failed")
    finally:
        session.close()


def run_forever() -> None:
    worker_id = _worker_id()
    metrics = get_email_delivery_metrics()
    poll_seconds = _bounded_env_int("EMAIL_WORKER_POLL_SECONDS", 2, 1, 60)
    next_maintenance = 0.0
    next_snapshot = 0.0
    logger.info("Durable email worker started worker=%s", worker_id)
    while not _stop_event.is_set():
        monotonic_now = time.monotonic()
        if monotonic_now >= next_snapshot:
            _queue_snapshot(metrics)
            next_snapshot = monotonic_now + 30
        if monotonic_now >= next_maintenance:
            _maintenance()
            next_maintenance = monotonic_now + 300

        processed = _process_batch(worker_id, metrics)
        if processed == 0:
            _stop_event.wait(poll_seconds)

    _write_heartbeat(force=True)
    logger.info("Durable email worker stopped worker=%s", worker_id)


def _request_stop(_signum, _frame) -> None:
    _stop_event.set()


def main(argv: list[str] | None = None) -> int:
    command = (argv or sys.argv[1:] or ["run"])[0]
    if command == "healthcheck":
        return 0 if healthcheck() else 1
    if command != "run":
        print("Usage: python -m app.email.worker [run|healthcheck]", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    from app.telemetry.bootstrap import bootstrap_telemetry

    bootstrap_telemetry()
    try:
        run_forever()
    finally:
        from app.telemetry import shutdown_telemetry

        shutdown_telemetry()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
