from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import io
from types import SimpleNamespace

import anyio
import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers

from app.database import AuditBase, Base
from app.logging import models as logging_models
from app.logging.models import (
    AdminNotifications,
    AuditLogDeletionQueue,
    Logs,
    create_audit_log,
)
from app.users.models import User
from app.workers import events, media, rendering, tool_jobs
from app.workers.models import (
    AuditEventOutbox,
    AuditEventErasureGuard,
    AuditEventSubjectReference,
    AuditEventSubjectState,
    DurableWorkerJob,
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_PROCESSING,
    JOB_SUCCEEDED,
    QUEUE_EVENTS,
    QUEUE_MEDIA,
    QUEUE_RENDERING,
    WorkerJobSnapshot,
    audit_event_subject_fingerprint,
    cancel_user_worker_jobs,
    erase_user_audit_event_state,
    restore_user_audit_event_subject,
)


class _NeverCancelled:
    def raise_if_cancelled(self) -> None:
        return None


def test_audit_outbox_is_encrypted_and_delivered_idempotently(monkeypatch):
    main_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=main_engine,
        tables=[
            AuditEventOutbox.__table__,
            AuditEventSubjectState.__table__,
            AuditEventSubjectReference.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    audit_engine = create_engine("sqlite:///:memory:")
    AuditBase.metadata.create_all(bind=audit_engine, tables=[Logs.__table__])
    main_factory = sessionmaker(bind=main_engine)
    audit_factory = sessionmaker(bind=audit_engine)
    monkeypatch.setattr(events, "SessionLocal", main_factory)
    monkeypatch.setattr(events, "AuditSessionLocal", audit_factory)

    occurred_at = datetime.now(timezone.utc)
    outbox = events.enqueue_audit_event(
        payload={
            "user_id": "user-1",
            "action": "TEST_EVENT",
            "reason": "private reason",
            "details": {"safe": "private detail"},
            "ip_address": "ip_hash",
            "user_agent": "device_hash",
            "category": "test",
        },
        occurred_at=occurred_at,
    )

    with main_engine.connect() as connection:
        raw = connection.execute(
            text("SELECT reason, details FROM audit_event_outbox WHERE id = :id"),
            {"id": outbox.id},
        ).one()
    assert "private reason" not in str(raw)
    assert "private detail" not in str(raw)

    db = main_factory()
    try:
        job = db.query(DurableWorkerJob).one()
        snapshot = WorkerJobSnapshot.from_row(job)
    finally:
        db.close()

    assert events._handle_audit_log(snapshot, _NeverCancelled()) == {
        "event_id": outbox.id
    }
    # Re-delivery after a primary-DB acknowledgement ambiguity is harmless.
    assert events._handle_audit_log(snapshot, _NeverCancelled()) == {
        "event_id": outbox.id
    }

    db = main_factory()
    audit_db = audit_factory()
    try:
        delivered = db.query(AuditEventOutbox).one()
        assert delivered.status == "delivered"
        assert delivered.details is None
        assert audit_db.query(Logs).count() == 1
        assert audit_db.query(Logs).one().action == "TEST_EVENT"
    finally:
        db.close()
        audit_db.close()


def test_audit_outbox_can_share_the_callers_transaction():
    main_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=main_engine,
        tables=[
            AuditEventOutbox.__table__,
            AuditEventSubjectState.__table__,
            AuditEventSubjectReference.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    main_factory = sessionmaker(bind=main_engine)
    occurred_at = datetime.now(timezone.utc)

    db = main_factory()
    staged = events.stage_audit_event(
        db,
        payload={
            "user_id": "user-atomic",
            "action": "ATOMIC_EVENT",
            "details": {
                "owner_id": "user-atomic",
                "to_user_id": "user-target",
            },
            "category": "test",
        },
        occurred_at=occurred_at,
    )
    assert db.query(AuditEventOutbox).filter_by(id=staged.id).one()
    assert db.query(DurableWorkerJob).count() == 1
    assert {
        row.subject_fingerprint
        for row in db.query(AuditEventSubjectReference).all()
    } == {
        audit_event_subject_fingerprint("user-atomic"),
        audit_event_subject_fingerprint("user-target"),
    }
    db.rollback()
    db.close()

    verify = main_factory()
    try:
        assert verify.query(AuditEventOutbox).count() == 0
        assert verify.query(AuditEventSubjectReference).count() == 0
        assert verify.query(AuditEventSubjectState).count() == 0
        assert verify.query(DurableWorkerJob).count() == 0
    finally:
        verify.close()


def test_durable_audit_erasure_handoff_finishes_cross_database_cleanup(monkeypatch):
    main_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=main_engine,
        tables=[
            User.__table__,
            AuditEventOutbox.__table__,
            AuditEventErasureGuard.__table__,
            AuditEventSubjectState.__table__,
            AuditEventSubjectReference.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    audit_engine = create_engine("sqlite:///:memory:")
    AuditBase.metadata.create_all(
        bind=audit_engine,
        tables=[
            Logs.__table__,
            AdminNotifications.__table__,
            AuditLogDeletionQueue.__table__,
        ],
    )
    main_factory = sessionmaker(bind=main_engine)
    audit_factory = sessionmaker(bind=audit_engine)
    monkeypatch.setattr(events, "SessionLocal", main_factory)
    monkeypatch.setattr(events, "AuditSessionLocal", audit_factory)
    monkeypatch.setattr(logging_models, "SessionLocal", main_factory)

    now = datetime.now(timezone.utc)
    main_db = main_factory()
    try:
        main_db.add(
            User(
                id="user-erased",
                email="erased@example.test",
                group_id="group-1",
                hashed_password="hash",
                first_name="Erased",
                last_name="User",
                role="user",
                settings={},
                is_active=False,
                deleted_at=now,
                created_at=now,
                last_active_at=now,
            )
        )
        job = events.enqueue_audit_erasure(
            main_db,
            user_id="user-erased",
            boundary_id="delete-boundary-1",
            commit=True,
        )
        snapshot = WorkerJobSnapshot.from_row(job)
    finally:
        main_db.close()

    audit_db = audit_factory()
    try:
        audit_db.add(
            Logs(
                id="audit-user-erased",
                user_id="user-erased",
                action="PRIVATE_ACTION",
                category="user",
                timestamp=now,
            )
        )
        audit_db.add(
            AdminNotifications(
                id="notification-user-erased",
                user_id="user-erased",
                category="users",
                type="info",
                message="private",
                timestamp=now,
            )
        )
        audit_db.commit()
    finally:
        audit_db.close()

    result = events._handle_audit_erasure(snapshot, _NeverCancelled())
    assert result["completed"] is True
    assert result["audit_logs_deleted"] == 1
    assert result["notifications_deleted"] == 1

    audit_db = audit_factory()
    try:
        assert audit_db.query(Logs).count() == 0
        assert audit_db.query(AdminNotifications).count() == 0
    finally:
        audit_db.close()

    main_db = main_factory()
    try:
        state = main_db.query(AuditEventSubjectState).one()
        assert state.erased_at is not None
        persisted_job = main_db.query(DurableWorkerJob).one()
        assert persisted_job.kind == "audit_erasure"
        assert persisted_job.payload == {"user_id": "user-erased"}
    finally:
        main_db.close()


def test_audit_retention_erasure_cancels_and_redacts_outbox_and_event_jobs():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            AuditEventOutbox.__table__,
            AuditEventSubjectState.__table__,
            AuditEventSubjectReference.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    try:
        db.add_all(
            [
                AuditEventOutbox(
                    id="target-pending",
                    user_id="user-1",
                    action="PRIVATE_EVENT",
                    reason="private reason",
                    details={"private": True},
                    ip_address="private ip",
                    user_agent="private agent",
                    status=JOB_PENDING,
                ),
                AuditEventOutbox(
                    id="target-delivered",
                    user_id="user-1",
                    action="OLD_EVENT",
                    status="delivered",
                ),
                AuditEventOutbox(
                    id="other-pending",
                    user_id="user-2",
                    action="OTHER_EVENT",
                    details={"keep": True},
                    status=JOB_PENDING,
                ),
                DurableWorkerJob(
                    id="target-active-job",
                    queue=QUEUE_EVENTS,
                    kind="audit_log",
                    user_id="user-1",
                    payload={"event_id": "target-pending"},
                    status=JOB_PROCESSING,
                    idempotency_key="audit:target-pending",
                ),
                DurableWorkerJob(
                    id="target-terminal-job",
                    queue=QUEUE_EVENTS,
                    kind="audit_log",
                    user_id="user-1",
                    result={"event_id": "target-delivered"},
                    status=JOB_SUCCEEDED,
                    idempotency_key="audit:target-delivered",
                ),
                DurableWorkerJob(
                    id="other-job",
                    queue=QUEUE_EVENTS,
                    kind="audit_log",
                    user_id="user-2",
                    payload={"event_id": "other-pending"},
                    status=JOB_PENDING,
                    idempotency_key="audit:other-pending",
                ),
            ]
        )
        db.commit()

        assert erase_user_audit_event_state(
            db,
            user_id="user-1",
            commit=True,
        ) == 4

        target_outbox = (
            db.query(AuditEventOutbox)
            .filter(AuditEventOutbox.id.in_(("target-pending", "target-delivered")))
            .all()
        )
        assert {row.status for row in target_outbox} == {JOB_CANCELLED}
        assert {row.user_id for row in target_outbox} == {""}
        for row in target_outbox:
            assert row.reason is None
            assert row.details is None
            assert row.ip_address is None
            assert row.user_agent is None
            assert row.error_code == "account_erased"

        active = db.query(DurableWorkerJob).filter_by(id="target-active-job").one()
        terminal = db.query(DurableWorkerJob).filter_by(id="target-terminal-job").one()
        assert active.status == JOB_CANCELLED
        assert active.reconciled_at is not None
        assert terminal.status == JOB_SUCCEEDED
        for row in (active, terminal):
            assert row.user_id is None
            assert row.payload is None
            assert row.result is None
            assert row.lease_owner is None

        assert db.query(AuditEventOutbox).filter_by(id="other-pending").one().details == {
            "keep": True
        }
        assert db.query(DurableWorkerJob).filter_by(id="other-job").one().status == JOB_PENDING
    finally:
        db.close()


def test_account_restore_cancels_only_the_pending_audit_erasure_handoff():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            AuditEventSubjectState.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    try:
        db.add(
            AuditEventSubjectState(
                subject_fingerprint=audit_event_subject_fingerprint("user-1"),
                erased_at=datetime.now(timezone.utc),
            )
        )
        db.add_all(
            [
                DurableWorkerJob(
                    id="erasure-job",
                    queue=QUEUE_EVENTS,
                    kind="audit_erasure",
                    user_id="user-1",
                    payload={"user_id": "user-1"},
                    status=JOB_PENDING,
                    idempotency_key="audit-erasure:fingerprint:boundary",
                ),
                DurableWorkerJob(
                    id="ordinary-event",
                    queue=QUEUE_EVENTS,
                    kind="audit_log",
                    user_id="user-1",
                    payload={"event_id": "event-1"},
                    status=JOB_PENDING,
                    idempotency_key="audit:event-1",
                ),
            ]
        )
        db.commit()

        assert restore_user_audit_event_subject(
            db,
            user_id="user-1",
            commit=True,
        ) is True

        erasure_job = db.query(DurableWorkerJob).filter_by(id="erasure-job").one()
        assert erasure_job.status == JOB_CANCELLED
        assert erasure_job.user_id is None
        assert erasure_job.payload is None
        assert erasure_job.reconciled_at is not None
        assert db.query(DurableWorkerJob).filter_by(id="ordinary-event").one().status == JOB_PENDING
        assert db.query(AuditEventSubjectState).one().erased_at is None
    finally:
        db.close()


def test_audit_erasure_fences_subject_only_events_and_late_enqueues(monkeypatch):
    main_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=main_engine,
        tables=[
            AuditEventOutbox.__table__,
            AuditEventSubjectState.__table__,
            AuditEventSubjectReference.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    audit_engine = create_engine("sqlite:///:memory:")
    AuditBase.metadata.create_all(bind=audit_engine, tables=[Logs.__table__])
    main_factory = sessionmaker(bind=main_engine)
    audit_factory = sessionmaker(bind=audit_engine)
    monkeypatch.setattr(events, "SessionLocal", main_factory)
    monkeypatch.setattr(events, "AuditSessionLocal", audit_factory)

    target_user_id = "user-erased"
    target_fingerprint = audit_event_subject_fingerprint(target_user_id)
    occurred_at = datetime.now(timezone.utc)
    queued = events.enqueue_audit_event(
        payload={
            "user_id": "system",
            "action": "ACCOUNT_REMOVAL_QUEUED",
            "details": {
                "owner_id": target_user_id,
            },
            "category": "system",
        },
        occurred_at=occurred_at,
    )

    db = main_factory()
    try:
        assert (
            db.query(AuditEventSubjectReference)
            .filter_by(
                event_id=queued.id,
                subject_fingerprint=target_fingerprint,
            )
            .count()
            == 1
        )
        erase_user_audit_event_state(db, user_id=target_user_id, commit=True)

        queued_row = db.query(AuditEventOutbox).filter_by(id=queued.id).one()
        queued_job = db.query(DurableWorkerJob).filter_by(
            idempotency_key=f"audit:{queued.id}"
        ).one()
        replacement = queued_row.details["owner_id"]
        assert replacement.startswith("deleted-user:")
        assert queued_row.status == JOB_PENDING
        assert queued_job.status == JOB_PENDING
        assert queued_job.user_id == "system"
        assert (
            db.query(AuditEventSubjectReference)
            .filter_by(subject_fingerprint=target_fingerprint)
            .count()
            == 0
        )
        snapshot = WorkerJobSnapshot.from_row(queued_job)
    finally:
        db.close()

    assert events._handle_audit_log(snapshot, _NeverCancelled()) == {
        "event_id": queued.id
    }
    audit_db = audit_factory()
    try:
        delivered = audit_db.query(Logs).filter_by(id=queued.id).one()
        assert delivered.details["owner_id"] == replacement
        assert target_user_id not in str(delivered.details)
    finally:
        audit_db.close()

    late_system = events.enqueue_audit_event(
        payload={
            "user_id": "system",
            "action": "LATE_CLEANUP_EVENT",
            "details": {"deleted_user_id": target_user_id},
            "category": "system",
        },
        occurred_at=occurred_at,
    )
    late_actor = events.enqueue_audit_event(
        payload={
            "user_id": target_user_id,
            "action": "LATE_USER_EVENT",
            "details": {"user_id": target_user_id},
            "category": "users",
        },
        occurred_at=occurred_at,
    )

    db = main_factory()
    try:
        persisted_system = db.query(AuditEventOutbox).filter_by(id=late_system.id).one()
        persisted_actor = db.query(AuditEventOutbox).filter_by(id=late_actor.id).one()
        assert persisted_system.status == JOB_PENDING
        assert persisted_system.details["deleted_user_id"].startswith("deleted-user:")
        assert persisted_actor.status == JOB_CANCELLED
        assert persisted_actor.user_id == ""
        assert persisted_actor.details is None
        assert (
            db.query(DurableWorkerJob)
            .filter_by(idempotency_key=f"audit:{late_actor.id}")
            .count()
            == 0
        )
        assert (
            db.query(AuditEventSubjectReference)
            .filter_by(subject_fingerprint=target_fingerprint)
            .count()
            == 0
        )
    finally:
        db.close()


def test_inline_audit_events_share_the_subject_erasure_fence(monkeypatch):
    main_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=main_engine,
        tables=[
            AuditEventOutbox.__table__,
            AuditEventSubjectState.__table__,
            AuditEventSubjectReference.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    audit_engine = create_engine("sqlite:///:memory:")
    AuditBase.metadata.create_all(bind=audit_engine, tables=[Logs.__table__])
    main_factory = sessionmaker(bind=main_engine)
    audit_factory = sessionmaker(bind=audit_engine)
    monkeypatch.setattr(events, "SessionLocal", main_factory)
    monkeypatch.setenv("AUDIT_EVENT_WORKER_MODE", "inline")

    target_user_id = "inline-erased-user"
    db = main_factory()
    try:
        erase_user_audit_event_state(db, user_id=target_user_id, commit=True)
    finally:
        db.close()

    suppressed = create_audit_log(
        db_log=audit_factory(),
        user_id=target_user_id,
        action="LATE_INLINE_ACTOR_EVENT",
        details={"user_id": target_user_id},
        category="users",
    )
    assert suppressed.user_id == ""
    assert suppressed.details is None

    delivered = create_audit_log(
        db_log=audit_factory(),
        user_id="system",
        action="LATE_INLINE_SYSTEM_EVENT",
        details={"owner_id": target_user_id},
        category="system",
    )

    audit_db = audit_factory()
    try:
        rows = audit_db.query(Logs).all()
        assert [row.id for row in rows] == [delivered.id]
        assert rows[0].user_id == "system"
        assert rows[0].details["owner_id"].startswith("deleted-user:")
        assert target_user_id not in str(rows[0].details)
    finally:
        audit_db.close()

    main_db = main_factory()
    try:
        assert main_db.query(AuditEventOutbox).count() == 0
        assert main_db.query(DurableWorkerJob).count() == 0
    finally:
        main_db.close()


def test_audit_database_rejects_a_writer_that_predates_subject_fencing():
    audit_engine = create_engine("sqlite:///:memory:")
    AuditBase.metadata.create_all(bind=audit_engine, tables=[Logs.__table__])

    with pytest.raises(IntegrityError), audit_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO logs "
                "(id, user_id, action, timestamp, category, share_refs_scrubbed) "
                "VALUES "
                "(:id, :user_id, :action, :timestamp, :category, :scrubbed)"
            ),
            {
                "id": "legacy-inline-writer",
                "user_id": "user-1",
                "action": "LEGACY_EVENT",
                "timestamp": datetime.now(timezone.utc),
                "category": "test",
                "scrubbed": True,
            },
        )


def test_database_gate_rejects_unindexed_rolling_upgrade_event():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            AuditEventOutbox.__table__,
            AuditEventSubjectState.__table__,
            AuditEventSubjectReference.__table__,
            DurableWorkerJob.__table__,
        ],
    )
    db = sessionmaker(bind=engine)()
    try:
        db.add(
            AuditEventOutbox(
                id="legacy-event",
                user_id="system",
                action="LEGACY_OWNER_EVENT",
                details={"owner_id": "user-erased"},
                subjects_indexed=False,
                status=JOB_PENDING,
            )
        )
        db.add(
            DurableWorkerJob(
                id="legacy-job",
                queue=QUEUE_EVENTS,
                kind="audit_log",
                user_id="system",
                payload={"event_id": "legacy-event"},
                status=JOB_PENDING,
                idempotency_key="audit:legacy-event",
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("an unindexed legacy writer bypassed the DB gate")

        assert db.query(AuditEventOutbox).count() == 0
        assert db.query(DurableWorkerJob).count() == 0
    finally:
        db.close()


def test_sensitive_worker_staging_is_encrypted_and_restore_clearable(
    monkeypatch, tmp_path
):
    media_dir = tmp_path / "media"
    render_dir = tmp_path / "render"
    monkeypatch.setattr(media, "MEDIA_STAGING_DIR", media_dir)
    monkeypatch.setattr(rendering, "RENDERING_STAGING_DIR", render_dir)

    audio_name = media.stage_transcription_audio(b"recognizable audio bytes")
    markdown_name = rendering._write_encrypted_staged(
        b"recognizable markdown", extension="md"
    )
    meeting_plaintext = b"recognizable large meeting media" * 100
    meeting_name, meeting_size = asyncio.run(
        media.stage_meeting_media_upload(
            UploadFile(
                file=io.BytesIO(meeting_plaintext),
                filename="meeting.mp3",
                headers=Headers({"content-type": "audio/mpeg"}),
            ),
            max_bytes=len(meeting_plaintext),
        )
    )
    assert b"recognizable audio bytes" not in (media_dir / audio_name).read_bytes()
    assert b"recognizable markdown" not in (render_dir / markdown_name).read_bytes()
    assert meeting_plaintext not in (media_dir / meeting_name).read_bytes()
    assert meeting_size == len(meeting_plaintext)
    decrypted_meeting = tmp_path / "decrypted-meeting"
    media._decrypt_staged_meeting(media_dir / meeting_name, decrypted_meeting)
    assert decrypted_meeting.read_bytes() == meeting_plaintext
    assert media.clear_media_staging_after_restore() == 2
    assert rendering.clear_rendering_staging_after_restore() == 1
    assert list(media_dir.iterdir()) == []
    assert list(render_dir.iterdir()) == []


def test_media_worker_owns_every_heavy_media_path():
    assert set(media.build_worker().handlers) == {
        "tool_call",
        "transcribe",
        "read_aloud",
        "meeting_transcript",
    }


def test_revision_rendering_requests_enable_terminal_retry(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        rendering,
        "_enqueue_rendering_job",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(id="render-job"),
    )

    rendering.enqueue_canvas_latex_render(
        actor_user_id="user-1",
        source_file_id="latex-1",
        expected_revision=4,
    )
    rendering.enqueue_presentation_rerender(
        user_id="user-1",
        presentation_id="presentation-1",
        expected_revision=8,
    )

    assert [call["kind"] for call in calls] == [
        "canvas_latex",
        "presentation_rerender",
    ]
    assert all(call["retry_terminal"] is True for call in calls)


def test_canvas_latex_worker_threads_actor_audit_context_without_postcommit_audit(
    monkeypatch,
):
    from app.files import access as file_access
    from app.tools.latex_pdf import utils as latex_utils

    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    session = FakeSession()
    monkeypatch.setattr(rendering, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        rendering,
        "_active_user",
        lambda _session, _user_id: SimpleNamespace(id="collaborator-1"),
    )
    monkeypatch.setattr(
        file_access,
        "resolve_file_for_edit",
        lambda *_args, **_kwargs: SimpleNamespace(storage_owner_user_id="owner-1"),
    )
    render_calls = []
    monkeypatch.setattr(
        latex_utils,
        "render_latex_canvas",
        lambda _session, **kwargs: render_calls.append(kwargs)
        or {"file_id": "pdf-1", "source_file_id": "source-1"},
    )
    monkeypatch.setattr(
        rendering,
        "_audit_rendering_event",
        lambda *_args, **_kwargs: pytest.fail(
            "Canvas success must be staged by render_latex_canvas"
        ),
    )
    job = WorkerJobSnapshot(
        id="job-1",
        queue=QUEUE_RENDERING,
        kind="canvas_latex",
        user_id="collaborator-1",
        payload={
            "source_file_id": "source-1",
            "expected_revision": 4,
            "audit_ip_address": "203.0.113.16",
            "audit_user_agent": "pytest-worker",
        },
        attempt_count=1,
        max_attempts=1,
        expires_at=None,
    )

    assert rendering._handle_canvas_latex(job, _NeverCancelled()) == {
        "file_id": "pdf-1",
        "source_file_id": "source-1",
    }
    assert render_calls == [
        {
            "user_id": "owner-1",
            "asset_actor_user_id": "collaborator-1",
            "source_file_id": "source-1",
            "expected_revision": 4,
            "audit_ip_address": "203.0.113.16",
            "audit_user_agent": "pytest-worker",
        }
    ]
    assert session.closed is True


def test_presentation_retry_supersedes_old_failure_before_reconciliation(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[DurableWorkerJob.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(rendering, "SessionLocal", factory)

    key = "presentation-rerender:user-1:presentation-1:7"
    original = rendering._enqueue_rendering_job(
        kind="presentation_rerender",
        user_id="user-1",
        payload={"presentation_id": "presentation-1", "expected_revision": 7},
        idempotency_key=key,
        retry_terminal=True,
    )
    db = factory()
    try:
        failed = db.query(DurableWorkerJob).filter_by(id=original.id).one()
        failed.status = JOB_FAILED
        failed.payload = None
        db.commit()
    finally:
        db.close()

    retry = rendering._enqueue_rendering_job(
        kind="presentation_rerender",
        user_id="user-1",
        payload={"presentation_id": "presentation-1", "expected_revision": 7},
        idempotency_key=key,
        retry_terminal=True,
    )
    assert retry.id != original.id

    reconciled_failures = []
    monkeypatch.setattr(
        rendering,
        "_mark_presentation_failed",
        lambda *_args, **_kwargs: reconciled_failures.append(True),
    )
    assert rendering.reconcile_terminal_rendering_jobs() == 0
    assert reconciled_failures == []

    db = factory()
    try:
        archived = db.query(DurableWorkerJob).filter_by(id=original.id).one()
        assert archived.reconciled_at is not None
        assert db.query(DurableWorkerJob).filter_by(id=retry.id).one().status == JOB_PENDING
    finally:
        db.close()


def test_read_aloud_route_delegates_to_media_worker(monkeypatch):
    from app.chats import router as chats_router

    class FakeSession:
        expired = False

        def expire_all(self):
            self.expired = True

    session = FakeSession()
    monkeypatch.setattr(tool_jobs, "external_media_enabled", lambda: True)
    monkeypatch.setattr(
        chats_router,
        "get_owned_assistant_message_read_aloud_text",
        lambda *_args, **_kwargs: "Canonical text",
    )
    monkeypatch.setattr(
        chats_router,
        "sanitize_read_aloud_text",
        lambda value: str(value),
    )
    monkeypatch.setattr(chats_router, "get_audit_request_ip", lambda *_args: "ip")
    monkeypatch.setattr(
        media,
        "enqueue_read_aloud_job",
        lambda **_kwargs: SimpleNamespace(id="media-job"),
    )
    monkeypatch.setattr(
        media,
        "wait_for_media_job",
        lambda _job: {"file_id": "audio-file"},
    )
    monkeypatch.setattr(
        chats_router,
        "download_file",
        lambda user_id, file_id, _db, inline: (user_id, file_id, inline),
    )
    audit_calls: list[object] = []
    monkeypatch.setattr(
        chats_router,
        "_log_chat_event",
        lambda *_args, **_kwargs: audit_calls.append(object()),
    )

    response = chats_router.read_aloud_message_route(
        payload=SimpleNamespace(message_id="message-1", text="Canonical text"),
        request=SimpleNamespace(headers={"user-agent": "pytest"}),
        db=session,
        db_log=object(),
        user=SimpleNamespace(id="user-1"),
    )

    assert response == ("user-1", "audio-file", True)
    assert session.expired is True
    assert audit_calls == []


def test_meeting_route_streams_to_media_staging_and_waits_off_process(monkeypatch):
    from app.chats import meeting_transcripts
    from app.chats import router as chats_router

    monkeypatch.setattr(tool_jobs, "external_media_enabled", lambda: True)
    monkeypatch.setattr(
        meeting_transcripts,
        "validate_meeting_transcript_admission",
        lambda *_args, **_kwargs: 1024,
    )

    async def stage(_upload, *, max_bytes):
        assert max_bytes == 1024
        return "a" * 32 + ".meeting", 12

    monkeypatch.setattr(media, "stage_meeting_media_upload", stage)
    monkeypatch.setattr(
        media,
        "enqueue_meeting_transcript_job",
        lambda **_kwargs: SimpleNamespace(id="meeting-job"),
    )
    expected = {"chat_id": "chat-1", "created_chat": False}

    async def wait_for_job(_job):
        return expected

    monkeypatch.setattr(media, "wait_for_media_job_async", wait_for_job)
    monkeypatch.setattr(chats_router, "get_audit_request_ip", lambda *_args: "ip")
    audit_calls: list[object] = []
    monkeypatch.setattr(
        chats_router,
        "_log_chat_event",
        lambda *_args, **_kwargs: audit_calls.append(object()),
    )

    response = asyncio.run(
        chats_router.transcribe_meeting_route(
            media=UploadFile(
                file=io.BytesIO(b"meeting data"),
                filename="meeting.mp3",
                headers=Headers({"content-type": "audio/mpeg"}),
            ),
            request=SimpleNamespace(headers={"user-agent": "pytest"}),
            chat_id="chat-1",
            project_id=None,
            browser_date_iso=None,
            browser_date_label=None,
            consent_confirmed=True,
            legal_basis="consent",
            legal_basis_details="Participants agreed",
            retention_days=30,
            db=object(),
            db_log=object(),
            user=SimpleNamespace(id="user-1", role="user"),
        )
    )

    assert response == expected
    assert audit_calls == []


@pytest.mark.anyio
async def test_meeting_staging_ownership_handoff_survives_request_cancellation(
    monkeypatch,
):
    from app.chats import meeting_transcripts
    from app.chats import router as chats_router

    monkeypatch.setattr(tool_jobs, "external_media_enabled", lambda: True)
    monkeypatch.setattr(
        meeting_transcripts,
        "validate_meeting_transcript_admission",
        lambda *_args, **_kwargs: 1024,
    )
    request_cancel_scope = None

    async def stage(_upload, *, max_bytes):
        assert max_bytes == 1024
        request_cancel_scope.cancel()
        return "a" * 32 + ".meeting", 12

    enqueued = []

    async def enqueue(**kwargs):
        await anyio.sleep(0)
        enqueued.append(kwargs["staged_name"])
        return SimpleNamespace(id="meeting-job")

    async def wait_for_job(_job):
        await anyio.sleep(0)
        pytest.fail("Cancellation should be delivered after the ownership handoff")

    discarded = []
    monkeypatch.setattr(media, "stage_meeting_media_upload", stage)
    monkeypatch.setattr(media, "enqueue_meeting_transcript_job_async", enqueue)
    monkeypatch.setattr(media, "wait_for_media_job_async", wait_for_job)
    monkeypatch.setattr(media, "discard_media_staging", discarded.append)
    monkeypatch.setattr(chats_router, "get_audit_request_ip", lambda *_args: "ip")

    with anyio.CancelScope() as cancel_scope:
        request_cancel_scope = cancel_scope
        await chats_router.transcribe_meeting_route(
            media=UploadFile(
                file=io.BytesIO(b"meeting data"),
                filename="meeting.mp3",
                headers=Headers({"content-type": "audio/mpeg"}),
            ),
            request=SimpleNamespace(headers={"user-agent": "pytest"}),
            chat_id="chat-1",
            project_id=None,
            browser_date_iso=None,
            browser_date_label=None,
            consent_confirmed=True,
            legal_basis="consent",
            legal_basis_details="Participants agreed",
            retention_days=30,
            db=object(),
            db_log=object(),
            user=SimpleNamespace(id="user-1", role="user"),
        )

    assert enqueued == ["a" * 32 + ".meeting"]
    assert discarded == []


def test_external_tool_routing_and_account_cancellation_boundaries(
    monkeypatch,
):
    monkeypatch.setenv("MEDIA_WORKER_MODE", "external")
    monkeypatch.setenv("RENDERING_WORKER_MODE", "external")
    assert tool_jobs.external_queue_for_tool("image_generation") == QUEUE_MEDIA
    assert tool_jobs.external_queue_for_tool("slide_presentation") == QUEUE_RENDERING
    assert tool_jobs.external_queue_for_tool("weather") is None

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[DurableWorkerJob.__table__])
    db = sessionmaker(bind=engine)()
    try:
        db.add_all(
            [
                DurableWorkerJob(
                    id="media-job",
                    queue=QUEUE_MEDIA,
                    kind="tool_call",
                    user_id="user-1",
                    payload={"secret": "media"},
                    status=JOB_PENDING,
                    idempotency_key="media:1",
                ),
                DurableWorkerJob(
                    id="audit-job",
                    queue=QUEUE_EVENTS,
                    kind="audit_log",
                    user_id="user-1",
                    payload={"event_id": "event-1"},
                    status=JOB_PENDING,
                    idempotency_key="audit:event-1",
                ),
            ]
        )
        db.commit()
        assert cancel_user_worker_jobs(db, user_id="user-1", commit=True) == 1
        assert db.query(DurableWorkerJob).filter_by(id="media-job").one().status == JOB_CANCELLED
        assert db.query(DurableWorkerJob).filter_by(id="audit-job").one().status == JOB_PENDING
    finally:
        db.close()


def test_realtime_gateway_rejects_every_non_realtime_application_route(monkeypatch):
    from app.realtime import gateway

    forwarded: list[str] = []

    async def fake_application(scope, _receive, _send):
        forwarded.append(str(scope.get("path")))

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    monkeypatch.setattr(gateway, "_application", fake_application)
    asyncio.run(
        gateway.app(
            {"type": "http", "path": "/api/v1/admin/users"}, receive, send
        )
    )
    assert sent[0]["status"] == 404
    assert forwarded == []

    asyncio.run(
        gateway.app(
            {"type": "websocket", "path": "/api/v1/realtime/session/socket"},
            receive,
            send,
        )
    )
    assert forwarded == ["/api/v1/realtime/session/socket"]
