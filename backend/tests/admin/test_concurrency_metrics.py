from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.admin.concurrency import models as concurrency_metrics
from app.admin.concurrency.models import (
    ConcurrencyBucketMetric,
    ConcurrencyMetricsState,
    UserActivityPresence,
    cleanup_expired_concurrency_metrics,
    get_peak_concurrent_users_last_week,
    initialize_concurrency_metrics,
    record_user_activity_presence,
)
from app.database import Base


@pytest.fixture
def db(monkeypatch):
    """Provide an isolated SQLite session with only concurrency-metrics tables."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            UserActivityPresence.__table__,
            ConcurrencyBucketMetric.__table__,
            ConcurrencyMetricsState.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(
        concurrency_metrics,
        "get_jwt_material",
        lambda: ("s" * 64, "HS512"),
    )
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_presence_deduplication_updates_compact_bucket_once(db):
    now = datetime(2026, 8, 9, 12, 3, tzinfo=timezone.utc)
    user = SimpleNamespace(id="user-1", last_active_at=None)

    assert record_user_activity_presence(db, user, now=now) is True
    # Force the database conflict path instead of the object-level fast path.
    assert record_user_activity_presence(db, user, now=now) is False
    db.commit()

    assert db.query(UserActivityPresence).count() == 1
    aggregate = db.get(ConcurrencyBucketMetric, now.replace(minute=0, second=0, microsecond=0))
    assert aggregate.unique_users == 1


def test_peak_query_reads_compact_aggregates_and_has_stable_contract(db):
    started_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    initialize_concurrency_metrics(db, now=started_at)
    peak_bucket = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    for user_id in ("user-1", "user-2", "user-3"):
        assert record_user_activity_presence(
            db,
            SimpleNamespace(id=user_id, last_active_at=None),
            now=peak_bucket + timedelta(minutes=1),
        )
    assert record_user_activity_presence(
        db,
        SimpleNamespace(id="user-1", last_active_at=peak_bucket),
        now=peak_bucket + timedelta(minutes=6),
    )
    db.commit()

    result = get_peak_concurrent_users_last_week(db, now=peak_bucket + timedelta(hours=1))

    assert result == {
        "max_concurrent_users_last_week": 3,
        "tracking_started_at": started_at.isoformat(),
        "is_partial_window": False,
        "window_minutes": 5,
    }


def test_empty_peak_result_keeps_the_same_response_shape(db):
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    result = get_peak_concurrent_users_last_week(db, now=now)

    assert result == {
        "max_concurrent_users_last_week": 0,
        "tracking_started_at": None,
        "is_partial_window": True,
        "window_minutes": 5,
    }


def test_metric_failure_rolls_back_savepoint_without_poisoning_caller_transaction(db, monkeypatch):
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    def fail_increment(*_args, **_kwargs):
        raise RuntimeError("simulated aggregate failure")

    monkeypatch.setattr(concurrency_metrics, "_increment_bucket_count", fail_increment)

    assert record_user_activity_presence(
        db,
        SimpleNamespace(id="user-1", last_active_at=None),
        now=now,
    ) is False

    # A successful statement and commit prove that the surrounding session was
    # restored after the nested metrics failure.
    assert db.execute(text("SELECT 1")).scalar() == 1
    db.commit()
    assert db.query(UserActivityPresence).count() == 0


def test_cleanup_prunes_detail_and_aggregate_but_preserves_tracking_provenance(db):
    started_at = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    initialize_concurrency_metrics(db, now=started_at)

    old_time = now - timedelta(days=9)
    recent_time = now - timedelta(days=1)
    for user_id, seen_at in (("old-user", old_time), ("recent-user", recent_time)):
        assert record_user_activity_presence(
            db,
            SimpleNamespace(id=user_id, last_active_at=None),
            now=seen_at,
        )
    db.commit()

    assert cleanup_expired_concurrency_metrics(db, now=now) == (1, 1)
    db.commit()

    assert db.query(UserActivityPresence).count() == 1
    assert db.query(ConcurrencyBucketMetric).count() == 1
    state = db.get(ConcurrencyMetricsState, 1)
    assert concurrency_metrics.normalize_utc_datetime(state.tracking_started_at) == started_at
    assert get_peak_concurrent_users_last_week(db, now=now)["is_partial_window"] is False


def test_initialize_backfills_compact_aggregates_from_existing_presence(db):
    bucket = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    db.add_all(
        [
            UserActivityPresence(
                bucket_start=bucket,
                user_fingerprint=f"fingerprint-{index}",
                first_seen_at=bucket + timedelta(seconds=index),
            )
            for index in range(2)
        ]
    )
    db.commit()

    initialize_concurrency_metrics(db, now=bucket + timedelta(hours=1))

    aggregate = db.get(ConcurrencyBucketMetric, bucket)
    assert aggregate.unique_users == 2
    state = db.get(ConcurrencyMetricsState, 1)
    assert concurrency_metrics.normalize_utc_datetime(state.tracking_started_at) == bucket
