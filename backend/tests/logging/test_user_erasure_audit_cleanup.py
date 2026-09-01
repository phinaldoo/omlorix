from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database import AuditBase, Base
from app.logging.models import (
    AdminNotifications,
    AuthenticationLogs,
    Logs,
    delete_authentication_logs_older_than,
    delete_admin_notifications_for_user,
    delete_audit_logs_for_user,
    prune_authentication_logs_to_max_count,
    scrub_share_capability_references_in_audit_logs,
)
from app.workers.models import (
    AuditEventErasureGuard,
    AuditEventOutbox,
    AuditEventSubjectReference,
    AuditEventSubjectState,
    DurableWorkerJob,
    JOB_CANCELLED,
    JOB_PENDING,
    QUEUE_EVENTS,
    audit_event_subject_fingerprint,
)


def _audit_session():
    engine = create_engine("sqlite:///:memory:")
    AuditBase.metadata.create_all(bind=engine, tables=[Logs.__table__, AdminNotifications.__table__])
    return sessionmaker(bind=engine)()


def _auth_log_session():
    engine = create_engine("sqlite:///:memory:")
    AuditBase.metadata.create_all(bind=engine, tables=[AuthenticationLogs.__table__])
    return sessionmaker(bind=engine)()


def _audit_event_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            AuditEventOutbox.__table__,
            AuditEventErasureGuard.__table__,
            AuditEventSubjectState.__table__,
            AuditEventSubjectReference.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def test_delete_audit_logs_for_user_scrubs_target_user_references():
    db = _audit_session()
    main_db = _audit_event_session()
    target_user_id = "user-erase"
    timestamp = datetime(2026, 5, 25, tzinfo=timezone.utc)

    db.add_all(
        [
            Logs(
                id="log-self",
                user_id=target_user_id,
                action="SELF_ACTION",
                category="admin",
                details={"user_id": target_user_id},
                timestamp=timestamp,
            ),
            Logs(
                id="log-admin",
                user_id="admin-1",
                action="ADMIN_ACTION",
                category="admin",
                details={
                    "user_id": target_user_id,
                    "nested": {"target_user": target_user_id},
                    "user_ids": [target_user_id, "other-user"],
                    "note": "Affected user user-erase was scheduled for deletion.",
                },
                timestamp=datetime(2026, 5, 26, tzinfo=timezone.utc),
            ),
            Logs(
                id="log-other",
                user_id="admin-2",
                action="UNRELATED_ACTION",
                category="admin",
                details={"user_id": "someone-else"},
                timestamp=datetime(2026, 5, 27, tzinfo=timezone.utc),
            ),
        ]
    )
    db.commit()

    main_db.add(
        AuditEventOutbox(
            id="queued-target-event",
            user_id=target_user_id,
            action="QUEUED_ACTION",
            details={"private": True},
            status=JOB_PENDING,
        )
    )
    main_db.add(
        DurableWorkerJob(
            id="queued-target-job",
            queue=QUEUE_EVENTS,
            kind="audit_log",
            user_id=target_user_id,
            payload={"event_id": "queued-target-event"},
            status=JOB_PENDING,
            idempotency_key="audit:queued-target-event",
        )
    )
    main_db.commit()

    deleted = delete_audit_logs_for_user(db, target_user_id, main_db=main_db)

    assert deleted == 1

    remaining_logs = {row.id: row for row in db.query(Logs).all()}
    assert set(remaining_logs) == {"log-admin", "log-other"}

    admin_log = remaining_logs["log-admin"]
    replacement = admin_log.details["user_id"]
    assert replacement.startswith("deleted-user:")
    assert replacement != target_user_id
    assert admin_log.details["nested"]["target_user"] == replacement
    assert admin_log.details["user_ids"] == [replacement, "other-user"]
    assert admin_log.details["note"] == "Affected user user-erase was scheduled for deletion."
    assert remaining_logs["log-other"].details == {"user_id": "someone-else"}
    queued_event = main_db.query(AuditEventOutbox).one()
    queued_job = main_db.query(DurableWorkerJob).one()
    assert queued_event.status == JOB_CANCELLED
    assert queued_event.user_id == ""
    assert queued_event.details is None
    assert queued_job.status == JOB_CANCELLED
    assert queued_job.user_id is None
    assert queued_job.payload is None
    subject_state = main_db.query(AuditEventSubjectState).filter_by(
        subject_fingerprint=audit_event_subject_fingerprint(target_user_id)
    ).one()
    assert subject_state.erased_at is not None
    main_db.close()


def test_delete_admin_notifications_for_user_scrubs_target_user_references():
    db = _audit_session()
    target_user_id = "user-erase"

    db.add_all(
        [
            AdminNotifications(
                id="notif-self",
                user_id=target_user_id,
                category="users",
                type="info",
                message="Notification for deleted user",
                details={"user_id": target_user_id},
                timestamp=datetime(2026, 5, 25, tzinfo=timezone.utc),
            ),
            AdminNotifications(
                id="notif-admin",
                user_id="admin-1",
                category="users",
                type="warning",
                message="Admin notification about target user",
                details={
                    "user_id": target_user_id,
                    "target_user": target_user_id,
                    "members": [target_user_id, "other-user"],
                },
                timestamp=datetime(2026, 5, 26, tzinfo=timezone.utc),
            ),
        ]
    )
    db.commit()

    deleted = delete_admin_notifications_for_user(db, target_user_id)

    assert deleted == 1

    remaining_notifications = db.query(AdminNotifications).all()
    assert len(remaining_notifications) == 1

    notification = remaining_notifications[0]
    replacement = notification.details["user_id"]
    assert replacement.startswith("deleted-user:")
    assert replacement != target_user_id
    assert notification.details["target_user"] == replacement
    assert notification.details["members"] == [replacement, "other-user"]


def test_delete_authentication_logs_older_than_removes_expired_rows():
    db = _auth_log_session()
    old_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc)
    fresh_timestamp = datetime.now(timezone.utc)

    db.add_all(
        [
            AuthenticationLogs(
                id="old-log",
                auth_type="password",
                status="success",
                user_id="user-1",
                timestamp=old_timestamp,
            ),
            AuthenticationLogs(
                id="fresh-log",
                auth_type="password",
                status="success",
                user_id="user-2",
                timestamp=fresh_timestamp,
            ),
        ]
    )
    db.commit()

    deleted = delete_authentication_logs_older_than(db, max_age_days=90)

    assert deleted == 1
    assert [row.id for row in db.query(AuthenticationLogs).all()] == ["fresh-log"]


def test_prune_authentication_logs_to_max_count_keeps_newest_rows():
    db = _auth_log_session()
    timestamps = [
        datetime(2026, 5, 25, hour, tzinfo=timezone.utc)
        for hour in range(4)
    ]
    db.add_all(
        [
            AuthenticationLogs(
                id=f"log-{index}",
                auth_type="password",
                status="success",
                user_id=f"user-{index}",
                timestamp=timestamp,
            )
            for index, timestamp in enumerate(timestamps)
        ]
    )
    db.commit()

    deleted = prune_authentication_logs_to_max_count(db, max_count=2)

    remaining_ids = [
        row.id
        for row in db.query(AuthenticationLogs)
        .order_by(AuthenticationLogs.timestamp.asc())
        .all()
    ]
    assert deleted == 2
    assert remaining_ids == ["log-2", "log-3"]


def test_scrub_share_capability_references_in_audit_logs_fingerprints_existing_rows():
    db = _audit_session()
    timestamp = datetime(2026, 5, 28, tzinfo=timezone.utc)

    db.add_all(
        [
            Logs(
                id="log-share",
                user_id="user-1",
                action="PROMPT_SHARED",
                category="prompts",
                details={
                    "share_id": "prompt-share-token",
                    "share_url": "https://chat.example/prompts/shared/prompt-share-token",
                    "nested": {"live_share_id": "live-share-token"},
                    "ip_address": "ip_existinghash",
                },
                timestamp=timestamp,
            ),
            Logs(
                id="log-clean",
                user_id="user-2",
                action="PROMPT_UPDATED",
                category="prompts",
                details={"prompt_id": "prompt-2"},
                timestamp=datetime(2026, 5, 29, tzinfo=timezone.utc),
            ),
        ]
    )
    db.commit()

    updated = scrub_share_capability_references_in_audit_logs(db)

    assert updated == 1

    scrubbed_log = db.query(Logs).filter(Logs.id == "log-share").one()
    assert scrubbed_log.details["share_id"].startswith("share_fp_")
    assert scrubbed_log.details["share_url"].startswith("share_url_fp_")
    assert scrubbed_log.details["nested"]["live_share_id"].startswith("share_fp_")
    assert scrubbed_log.details["ip_address"] == "ip_existinghash"

    clean_log = db.query(Logs).filter(Logs.id == "log-clean").one()
    assert clean_log.details == {"prompt_id": "prompt-2"}
    assert scrub_share_capability_references_in_audit_logs(db) == 0


def test_scrub_share_capability_references_in_audit_logs_can_limit_batches(monkeypatch):
    monkeypatch.setattr("app.logging.models._AUDIT_SHARE_SCRUB_BATCH_SIZE", 2)
    db = _audit_session()
    timestamp = datetime(2026, 5, 30, tzinfo=timezone.utc)

    db.add_all(
        [
            Logs(
                id=f"log-share-{index}",
                user_id="user-1",
                action="PROMPT_SHARED",
                category="prompts",
                details={"share_id": f"prompt-share-token-{index}"},
                timestamp=timestamp,
            )
            for index in range(3)
        ]
    )
    db.commit()

    assert scrub_share_capability_references_in_audit_logs(db, max_batches=1) == 2
    assert db.query(Logs).filter(Logs.share_refs_scrubbed.is_(False)).count() == 1
