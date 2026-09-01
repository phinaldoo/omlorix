from datetime import datetime, timezone
from types import SimpleNamespace

import fakeredis
from rq import Queue
from rq.job import validate_job_id

from app.automations import queue as automation_queue


class _FakeDb:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _rq_queue(redis_client):
    return Queue(name=automation_queue.AUTOMATION_QUEUE_NAME, connection=redis_client)


def test_webhook_delivery_enqueues_with_rq_safe_job_id(monkeypatch):
    redis_client = fakeredis.FakeRedis()
    fake_db = _FakeDb()

    monkeypatch.setattr(automation_queue, "get_redis_client", lambda: redis_client)
    monkeypatch.setattr(automation_queue, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        automation_queue,
        "reserve_automation_execution",
        lambda *args, **kwargs: (SimpleNamespace(id="execution-1"), "queued"),
    )

    slot = "webhook:trigger-1:idempotency:delivery-1"
    result = automation_queue.enqueue_automation_execution(
        "automation-1",
        "user-1",
        slot,
        {"type": "webhook", "delivery_id": "delivery-1"},
    )

    assert result.status == "queued"
    assert fake_db.closed is True
    job_ids = _rq_queue(redis_client).get_job_ids()
    assert len(job_ids) == 1
    validate_job_id(job_ids[0])
    assert ":" not in job_ids[0]
    assert _rq_queue(redis_client).fetch_job(job_ids[0]).args[2] == slot


def test_scheduled_delivery_enqueues_and_deduplicates_with_rq_safe_job_id(monkeypatch):
    redis_client = fakeredis.FakeRedis()
    monkeypatch.setattr(automation_queue, "get_redis_client", lambda: redis_client)

    scheduled_for = datetime(2026, 8, 22, 10, 30, tzinfo=timezone.utc)
    first = automation_queue.enqueue_scheduled_automation_execution(
        "automation-1",
        "user-1",
        scheduled_for,
        "20260822:1030",
    )
    second = automation_queue.enqueue_scheduled_automation_execution(
        "automation-1",
        "user-1",
        scheduled_for,
        "20260822:1030",
    )

    assert first.status == "queued"
    assert second.status == "duplicate"
    job_ids = _rq_queue(redis_client).get_job_ids()
    assert len(job_ids) == 1
    validate_job_id(job_ids[0])
    assert ":" not in job_ids[0]


def test_rq_job_id_keeps_distinct_normalized_slots_distinct():
    colon_slot_id = automation_queue._automation_job_id(
        "automation-1", "webhook:key:a:b"
    )
    dash_slot_id = automation_queue._automation_job_id(
        "automation-1", "webhook:key:a-b"
    )

    validate_job_id(colon_slot_id)
    validate_job_id(dash_slot_id)
    assert colon_slot_id != dash_slot_id
