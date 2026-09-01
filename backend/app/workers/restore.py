from __future__ import annotations

from datetime import datetime, timezone

from app.admin.export_jobs.models import AdminUserExportJob
from app.backups.models import BackupJob, RestoreJob
from app.files.models import FileProcessingArtifact
from app.llm.models import (
    RATE_LIMIT_ADMISSION_FAILED,
    RATE_LIMIT_ADMISSION_OPEN,
    RateLimitDurationAdmission,
)
from app.tools.deep_research.models import DeepResearchRun, TERMINAL_RUN_STATUSES
from app.workers.models import (
    AuditEventOutbox,
    DurableWorkerJob,
    JOB_ACTIVE_STATUSES,
    JOB_CANCELLED,
    JOB_FAILED,
    QUEUE_EVENTS,
    QUEUE_LIFECYCLE,
    QUEUE_MEDIA,
    worker_job_reconciliation_required,
)


def reconcile_worker_state_after_restore(db) -> dict[str, int]:
    """Invalidate snapshot-replayed work before restored services restart.

    Queue state is operational, not business history. Replaying it after a
    point-in-time restore could repeat provider calls, imports, exports, or
    destructive actions that already happened outside the restored snapshot.
    Scheduled lifecycle intent is reconstructed from authoritative user rows by
    its scheduler, so those queue rows are deleted instead of replayed.
    """

    current = datetime.now(timezone.utc)
    try:
        transcription_admission_ids = {
            str((row.payload or {}).get("admission_id") or "").strip()
            for row in db.query(DurableWorkerJob)
            .filter(
                DurableWorkerJob.queue == QUEUE_MEDIA,
                DurableWorkerJob.kind == "transcribe",
                DurableWorkerJob.status.in_(JOB_ACTIVE_STATUSES),
            )
            .all()
        }
        transcription_admission_ids.discard("")
        duration_admissions = 0
        if transcription_admission_ids:
            duration_admissions = int(
                db.query(RateLimitDurationAdmission)
                .filter(
                    RateLimitDurationAdmission.id.in_(transcription_admission_ids),
                    RateLimitDurationAdmission.status == RATE_LIMIT_ADMISSION_OPEN,
                )
                .update(
                    {
                        RateLimitDurationAdmission.status: RATE_LIMIT_ADMISSION_FAILED,
                        RateLimitDurationAdmission.consumed_seconds: 0,
                        RateLimitDurationAdmission.completed_at: current,
                    },
                    synchronize_session=False,
                )
                or 0
            )
        lifecycle_jobs = int(
            db.query(DurableWorkerJob)
            .filter(DurableWorkerJob.queue == QUEUE_LIFECYCLE)
            .delete(synchronize_session=False)
            or 0
        )
        active_jobs = int(
            db.query(DurableWorkerJob)
            .filter(
                DurableWorkerJob.queue != QUEUE_LIFECYCLE,
                DurableWorkerJob.status.in_(JOB_ACTIVE_STATUSES),
                ~(
                    (DurableWorkerJob.queue == QUEUE_EVENTS)
                    & (DurableWorkerJob.kind == "audit_erasure")
                ),
            )
            .update(
                {
                    DurableWorkerJob.status: JOB_CANCELLED,
                    DurableWorkerJob.cancel_requested: True,
                    DurableWorkerJob.payload: None,
                    DurableWorkerJob.result: None,
                    DurableWorkerJob.progress: 0,
                    DurableWorkerJob.error_code: "restore_invalidated",
                    DurableWorkerJob.leased_at: None,
                    DurableWorkerJob.lease_expires_at: None,
                    DurableWorkerJob.lease_owner: None,
                    DurableWorkerJob.finished_at: current,
                    DurableWorkerJob.reconciled_at: current,
                    DurableWorkerJob.updated_at: current,
                },
                synchronize_session=False,
            )
            or 0
        )

        backup_jobs = int(
            db.query(BackupJob)
            .filter(BackupJob.status.in_(("queued", "running")))
            .update(
                {
                    BackupJob.status: "failed",
                    BackupJob.error: "restore_invalidated",
                    BackupJob.finished_at: current,
                    BackupJob.updated_at: current,
                },
                synchronize_session=False,
            )
            or 0
        )
        restore_jobs = int(
            db.query(RestoreJob)
            .filter(RestoreJob.status.in_(("queued", "running")))
            .update(
                {
                    RestoreJob.status: "failed",
                    RestoreJob.error: "restore_invalidated",
                    RestoreJob.finished_at: current,
                    RestoreJob.updated_at: current,
                },
                synchronize_session=False,
            )
            or 0
        )
        admin_export_jobs = int(
            db.query(AdminUserExportJob)
            .filter(AdminUserExportJob.status.in_(("queued", "running")))
            .update(
                {
                    AdminUserExportJob.status: "failed",
                    AdminUserExportJob.error: "restore_invalidated",
                    AdminUserExportJob.finished_at: current,
                    AdminUserExportJob.updated_at: current,
                },
                synchronize_session=False,
            )
            or 0
        )
        research_runs = int(
            db.query(DeepResearchRun)
            .filter(~DeepResearchRun.status.in_(TERMINAL_RUN_STATUSES))
            .update(
                {
                    DeepResearchRun.status: JOB_FAILED,
                    DeepResearchRun.phase: "failed",
                    DeepResearchRun.cancel_requested: True,
                    DeepResearchRun.error_code: "restore_invalidated",
                    DeepResearchRun.error_message_key: "deep_research_failed",
                    DeepResearchRun.completed_at: current,
                    DeepResearchRun.updated_at: current,
                },
                synchronize_session=False,
            )
            or 0
        )
        file_artifacts = int(
            db.query(FileProcessingArtifact)
            .filter(FileProcessingArtifact.status.in_(("pending", "running")))
            # File-processing artifacts are derived cache, not durable user
            # state.  A restored queue cannot safely replay their old jobs, so
            # remove active identities and let the next authorized request
            # create a fresh artifact/job pair.
            .delete(synchronize_session=False)
            or 0
        )
        audit_events = int(
            db.query(AuditEventOutbox)
            .filter(AuditEventOutbox.status.in_(("pending", "retry", "processing")))
            .update(
                {
                    AuditEventOutbox.status: JOB_CANCELLED,
                    AuditEventOutbox.reason: None,
                    AuditEventOutbox.details: None,
                    AuditEventOutbox.ip_address: None,
                    AuditEventOutbox.user_agent: None,
                    AuditEventOutbox.error_code: "restore_invalidated",
                    AuditEventOutbox.updated_at: current,
                },
                synchronize_session=False,
            )
            or 0
        )

        # Erasure reconciliation can have cancelled rows before this pass.
        # Their streams are not part of a database backup, and every dependent
        # domain record above is now terminal, so they are safe to retain/purge.
        reconciled_jobs = int(
            db.query(DurableWorkerJob)
            .filter(
                DurableWorkerJob.status.in_((JOB_FAILED, JOB_CANCELLED)),
                DurableWorkerJob.reconciled_at.is_(None),
                worker_job_reconciliation_required(),
            )
            .update(
                {
                    DurableWorkerJob.reconciled_at: current,
                    DurableWorkerJob.updated_at: current,
                },
                synchronize_session=False,
            )
            or 0
        )
        db.commit()
        return {
            "active_jobs": active_jobs,
            "lifecycle_jobs": lifecycle_jobs,
            "backup_jobs": backup_jobs,
            "restore_jobs": restore_jobs,
            "admin_export_jobs": admin_export_jobs,
            "research_runs": research_runs,
            "file_artifacts": file_artifacts,
            "audit_events": audit_events,
            "duration_admissions": duration_admissions,
            "reconciled_jobs": reconciled_jobs,
        }
    except Exception:
        db.rollback()
        raise
