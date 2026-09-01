from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import database
from app.auth import models as auth_models
from app.ip_analytics import service
from app.middleware.rate_limiter import RedisRateLimiterMiddleware, _RateLimitRule
from app.ip_analytics.schemas import AdminIPAddressStatisticsOverview


def _session():
    engine = create_engine("sqlite:///:memory:")
    database.Base.metadata.create_all(
        bind=engine,
        tables=[
            auth_models.BlockedIP.__table__,
            auth_models.IPAddressSecurityStatistic.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def test_repeated_request_denials_are_aggregated_in_five_minute_bucket(monkeypatch):
    db = _session()
    monkeypatch.setattr(auth_models, "is_ip_address_statistics_enabled", lambda _db: True)
    event_time = datetime(2026, 7, 20, 12, 2, tzinfo=timezone.utc)
    try:
        first = auth_models.record_ip_address_security_event(
            db,
            "2001:0db8::1",
            "request_denied",
            event_source="ip_policy",
            reason_code="country_blocklist",
            route_category="api",
            request_count=2,
            created_at=event_time,
        )
        second = auth_models.record_ip_address_security_event(
            db,
            "2001:db8::1",
            "request_denied",
            event_source="ip_policy",
            reason_code="country_blocklist",
            route_category="api",
            request_count=3,
            created_at=event_time + timedelta(minutes=1),
        )

        assert first.id == second.id
        assert second.request_count == 5
        assert db.query(auth_models.IPAddressSecurityStatistic).count() == 1
        assert second.bucket_start.minute == 0
    finally:
        db.close()


def test_aggregate_increment_uses_database_value_when_session_state_is_stale(
    monkeypatch,
    tmp_path,
):
    """Concurrent workers must add to, rather than overwrite, the saved count."""

    engine = create_engine(f"sqlite:///{tmp_path / 'ip-analytics.db'}")
    database.Base.metadata.create_all(
        bind=engine,
        tables=[auth_models.IPAddressSecurityStatistic.__table__],
    )
    sessions = sessionmaker(bind=engine)
    setup_db = sessions()
    first_worker = sessions()
    second_worker = sessions()
    monkeypatch.setattr(auth_models, "is_ip_address_statistics_enabled", lambda _db: True)
    event_time = datetime(2026, 7, 20, 12, 2, tzinfo=timezone.utc)
    try:
        auth_models.record_ip_address_security_event(
            setup_db,
            "198.51.100.40",
            "request_denied",
            event_source="ip_policy",
            reason_code="ip_blocklist",
            request_count=1,
            created_at=event_time,
        )

        # Load the same old value into two independent identity maps. The
        # previous Python read-modify-write implementation would commit 3 and
        # then overwrite it with 5, losing the first worker's increment.
        assert first_worker.query(auth_models.IPAddressSecurityStatistic).one().request_count == 1
        assert second_worker.query(auth_models.IPAddressSecurityStatistic).one().request_count == 1

        auth_models.record_ip_address_security_event(
            first_worker,
            "198.51.100.40",
            "request_denied",
            event_source="ip_policy",
            reason_code="ip_blocklist",
            request_count=2,
            created_at=event_time + timedelta(seconds=10),
        )
        auth_models.record_ip_address_security_event(
            second_worker,
            "198.51.100.40",
            "request_denied",
            event_source="ip_policy",
            reason_code="ip_blocklist",
            request_count=4,
            created_at=event_time + timedelta(seconds=20),
        )

        verification_db = sessions()
        try:
            event = verification_db.query(auth_models.IPAddressSecurityStatistic).one()
            assert event.request_count == 7
            assert event.last_seen_at == (event_time + timedelta(seconds=20)).replace(
                tzinfo=None
            )
        finally:
            verification_db.close()
    finally:
        setup_db.close()
        first_worker.close()
        second_worker.close()


def test_overview_separates_denials_rate_limits_and_manual_auto_bans(monkeypatch):
    db = _session()
    monkeypatch.setattr(auth_models, "is_ip_address_statistics_enabled", lambda _db: True)
    monkeypatch.setattr(service, "get_ip_address_statistics_retention_days", lambda _db: 90)
    monkeypatch.setattr(
        service,
        "get_ip_address_statistics_settings",
        lambda _db: {"enabled": True, "regulatory_confirmed": True},
    )
    monkeypatch.setattr(
        service,
        "provider_status",
        lambda _db, **_kwargs: {
            "configured": False,
            "provider": None,
            "status": "missing",
            "sends_ip_to_external_provider": False,
        },
    )
    now = datetime.now(timezone.utc)
    try:
        db.add_all(
            [
                auth_models.BlockedIP(
                    ip_address="203.0.113.1",
                    blocked_at=now,
                    expires_at=now + timedelta(days=1),
                    reason="selected",
                ),
                auth_models.BlockedIP(
                    ip_address="203.0.113.99",
                    blocked_at=now,
                    expires_at=now + timedelta(days=1),
                    reason="other",
                ),
            ]
        )
        db.commit()
        auth_models.record_ip_address_security_event(
            db, "203.0.113.1", "request_denied", country_code="DE",
            event_source="ip_policy", reason_code="ip_blocklist", request_count=4,
            created_at=now,
        )
        auth_models.record_ip_address_security_event(
            db, "203.0.113.2", "rate_limited", country_code="DE",
            event_source="redis_rate_limiter", reason_code="rate_limit_auth",
            request_count=3, created_at=now,
        )
        auth_models.record_ip_address_security_event(
            db, "203.0.113.3", "ban_created", country_code="US",
            event_source="admin_manual", reason_code="manual",
            is_automatic=False, created_at=now,
        )
        auth_models.record_ip_address_security_event(
            db, "203.0.113.4", "ban_created", country_code="US",
            event_source="automated_policy", reason_code="automated_policy",
            is_automatic=True, created_at=now,
        )

        overview = service.build_overview(db, days=30)
        AdminIPAddressStatisticsOverview.model_validate(overview)
        assert overview["summary"]["denied_requests"] == 4
        assert overview["summary"]["rate_limited_requests"] == 3
        assert overview["summary"]["manual_bans_created"] == 1
        assert overview["summary"]["automatic_bans_created"] == 1
        assert overview["summary"]["active_bans"] == 2
        assert overview["summary"]["top_country_code"] == "DE"
        assert overview["summary"]["top_country_distinct_ips"] == 1
        assert overview["countries"][0]["distinct_ips"] == 2
        filtered = service.build_overview(db, days=30, ip_address="203.0.113.1")
        assert filtered["summary"]["active_bans"] == 1
    finally:
        db.close()


def test_export_import_roundtrip_preserves_events_without_credentials(monkeypatch):
    source_db = _session()
    target_db = _session()
    monkeypatch.setattr(auth_models, "is_ip_address_statistics_enabled", lambda _db: True)
    monkeypatch.setattr(
        service,
        "get_ip_address_statistics_settings",
        lambda _db: {"enabled": False, "regulatory_confirmed": False, "retention_days": 30},
    )
    try:
        auth_models.record_ip_address_security_event(
            source_db,
            "198.51.100.4",
            "ban_removed",
            event_source="admin_manual",
            reason_code="manual",
        )
        payload = service.export_payload(source_db)
        assert "api_key" not in str(payload).lower()
        streamed_payload = json.loads("".join(service.iter_export_json(source_db)))
        assert streamed_payload["events"] == payload["events"]

        # The focused target schema intentionally omits the settings table, so
        # exercise the event portion of the versioned backup.
        payload.pop("settings", None)
        result = service.import_payload(target_db, payload)
        assert result["imported_rows"] == 1
        assert target_db.query(auth_models.IPAddressSecurityStatistic).one().event_type == "ban_removed"
    finally:
        source_db.close()
        target_db.close()


def test_import_skips_duplicate_ids_and_aggregation_keys_within_payload():
    """Only the first valid occurrence of each unique import key is accepted."""
    db = _session()
    created_at = "2026-07-20T12:00:00+00:00"
    payload = {
        "format": "omlorix-ip-analytics",
        "export_version": 1.0,
        "events": [
            {
                "id": "event-one",
                "ip_address": "198.51.100.10",
                "event_type": "request_denied",
                "aggregation_key": "bucket-one",
                "created_at": created_at,
            },
            {
                "id": "event-one",
                "ip_address": "198.51.100.11",
                "event_type": "request_denied",
                "aggregation_key": "bucket-two",
                "created_at": created_at,
            },
            {
                "id": "event-two",
                "ip_address": "198.51.100.12",
                "event_type": "request_denied",
                "aggregation_key": "bucket-one",
                "created_at": created_at,
            },
        ],
    }
    try:
        result = service.import_payload(db, payload)

        assert result["imported_rows"] == 1
        assert result["skipped_rows"] == 2
        assert db.query(auth_models.IPAddressSecurityStatistic).count() == 1
    finally:
        db.close()


@pytest.mark.parametrize(
    "version_fields",
    [
        {},
        {"version": 1.0},
        {"export_version": True},
        {"export_version": 0.9},
        {"export_version": 2.0},
        {"export_version": "1.0"},
    ],
)
def test_import_rejects_removed_or_non_current_version_shapes(version_fields):
    """Only the numeric export_version 1.0 IP analytics contract is accepted."""
    db = _session()
    payload = {
        "format": "omlorix-ip-analytics",
        "events": [],
        **version_fields,
    }
    try:
        with pytest.raises(ValueError, match="Unsupported IP analytics import format or version"):
            service.import_payload(db, payload)
    finally:
        db.close()


def test_import_accepts_browser_normalized_integer_version():
    """JSON tools may serialize the current 1.0 version as integer 1."""

    db = _session()
    try:
        result = service.import_payload(
            db,
            {
                "format": "omlorix-ip-analytics",
                "export_version": 1,
                "events": [],
            },
        )
        assert result["status"] == "success"
        assert result["imported_rows"] == 0
    finally:
        db.close()


def test_import_rolls_back_and_reports_commit_integrity_errors(monkeypatch):
    """A late uniqueness race becomes the route's clean validation error."""
    db = _session()
    rollback_calls = 0
    original_rollback = db.rollback

    def fail_commit():
        raise IntegrityError("insert", {}, Exception("duplicate"))

    def track_rollback():
        nonlocal rollback_calls
        rollback_calls += 1
        original_rollback()

    monkeypatch.setattr(db, "commit", fail_commit)
    monkeypatch.setattr(db, "rollback", track_rollback)
    payload = {
        "format": "omlorix-ip-analytics",
        "export_version": 1.0,
        "events": [
            {
                "id": "event-one",
                "ip_address": "198.51.100.10",
                "event_type": "ban_removed",
                "created_at": "2026-07-20T12:00:00+00:00",
            }
        ],
    }
    try:
        with pytest.raises(ValueError, match="contains conflicting rows"):
            service.import_payload(db, payload)

        assert rollback_calls == 1
    finally:
        db.close()


def test_rate_limiter_records_a_separate_aggregated_event(monkeypatch):
    recorded: list[dict] = []
    opened_sessions = 0

    class FakeDb:
        def __init__(self):
            nonlocal opened_sessions
            opened_sessions += 1

        def rollback(self):
            return None

        def begin_nested(self):
            return nullcontext()

        def commit(self):
            return None

        def close(self):
            return None

    middleware = RedisRateLimiterMiddleware(app=SimpleNamespace())
    # Prevent the real daemon from racing the deterministic manual drain below.
    middleware._analytics_worker = SimpleNamespace()
    monkeypatch.setattr(middleware, "_resolve_trusted_proxies", lambda _request: [])
    monkeypatch.setattr(
        auth_models,
        "is_ip_address_statistics_enabled",
        lambda _db: True,
    )
    monkeypatch.setattr(
        auth_models,
        "record_ip_address_security_event",
        lambda _db, ip_address, event_type, **kwargs: recorded.append(
            {"ip_address": ip_address, "event_type": event_type, **kwargs}
        ),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(db=FakeDb)),
        client=SimpleNamespace(host="198.51.100.20"),
        headers={},
    )

    middleware._record_rate_limit_event(
        request,
        _RateLimitRule(name="auth", limit=40, window_seconds=60),
    )
    middleware._record_rate_limit_event(
        request,
        _RateLimitRule(name="auth", limit=40, window_seconds=60),
    )

    # Rejected requests only touch the bounded memory buffer.
    assert opened_sessions == 0
    pending, db_factory = middleware._drain_rate_limit_analytics()
    middleware._flush_rate_limit_analytics(pending, db_factory)

    assert recorded == [
        {
            "ip_address": "198.51.100.20",
            "event_type": "rate_limited",
            "event_source": "redis_rate_limiter",
            "reason_code": "rate_limit_auth",
            "route_category": "auth",
            "reason": "Request rejected by application rate limit",
            "request_count": 2,
            "aggregate": True,
            "commit": False,
            "statistics_enabled": True,
        }
    ]
    assert opened_sessions == 1


def test_request_time_ban_expiry_records_removal_event(monkeypatch):
    db = _session()
    monkeypatch.setattr(auth_models, "is_ip_address_statistics_enabled", lambda _db: True)
    monkeypatch.setattr(
        auth_models,
        "ip_restrictions_disabled_by_environment",
        lambda: False,
    )
    try:
        db.add(
            auth_models.BlockedIP(
                ip_address="198.51.100.30",
                blocked_at=datetime.now(timezone.utc) - timedelta(days=1),
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                reason="temporary",
            )
        )
        db.commit()

        assert auth_models.check_blocked_ip_address("198.51.100.30", db) is False
        assert db.query(auth_models.BlockedIP).count() == 0
        event = db.query(auth_models.IPAddressSecurityStatistic).one()
        assert event.event_type == "ban_removed"
        assert event.event_source == "system_expiry"
        assert event.reason_code == "expired"
    finally:
        db.close()
