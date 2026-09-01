"""Restore-resistant ledger for completed permanent user erasures.

The ledger deliberately lives outside database and application-data backup
payloads. Each record contains only the internal user ID, erasure time, and the
log policies needed to reapply the original result after an in-place restore.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid

from sqlalchemy.exc import IntegrityError

from app.paths import DATA_DIR

ERASURE_LEDGER_PATH = Path(
    os.getenv("OMLORIX_ERASURE_LEDGER_PATH") or (DATA_DIR / ".erasure-ledger.jsonl")
).expanduser().resolve()
ERASURE_RECONCILIATION_REQUIRED_PATH = Path(
    os.getenv("OMLORIX_ERASURE_RECONCILIATION_REQUIRED_PATH")
    or ERASURE_LEDGER_PATH.with_name(".erasure-reconciliation-required")
).expanduser().resolve()
_AUDIT_ERASURE_RECONCILIATION_KEY = "legacy-ledger-audit-erasure-v1"


def erasure_pending_dir() -> Path:
    """Return the private sparse index of unresolved two-phase operations."""

    return ERASURE_LEDGER_PATH.with_name(".erasure-pending")


def _empty_audit_reconciliation_result() -> dict[str, int]:
    return {
        "subjects_reconciled": 0,
        "audit_logs_deleted": 0,
        "notifications_deleted": 0,
    }


def audit_erasure_reconciliation_pending() -> bool:
    """Return whether the one-time pre-fence cleanup still needs to run."""

    from app.database import SessionLocal
    from app.workers.models import AuditErasureReconciliationCheckpoint

    checkpoint_db = SessionLocal()
    try:
        return checkpoint_db.get(
            AuditErasureReconciliationCheckpoint,
            _AUDIT_ERASURE_RECONCILIATION_KEY,
        ) is None
    finally:
        checkpoint_db.close()


def _utc_datetime(value: Any) -> datetime:
    """Normalize an ISO timestamp to an aware UTC datetime."""

    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ensure_erasure_ledger_writable() -> None:
    """Create the private append-only ledger before irreversible deletion."""

    ERASURE_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        ERASURE_LEDGER_PATH,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_parent(path: Path) -> None:
    """Flush a directory entry where the platform permits it."""

    try:
        descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _append_erasure_record(record: dict[str, Any]) -> None:
    ensure_erasure_ledger_writable()
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    descriptor = os.open(
        ERASURE_LEDGER_PATH,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        written = 0
        while written < len(payload):
            chunk_size = os.write(descriptor, payload[written:])
            if chunk_size <= 0:
                raise OSError("Could not append to the user-erasure ledger")
            written += chunk_size
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_parent(ERASURE_LEDGER_PATH)


def _pending_erasure_path(operation_id: str) -> Path:
    digest = hashlib.sha256(str(operation_id).encode("utf-8")).hexdigest()
    return erasure_pending_dir() / f"{digest}.json"


def _write_pending_erasure_state(record: dict[str, Any]) -> None:
    directory = erasure_pending_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    target = _pending_erasure_path(str(record["operation_id"]))
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        written = 0
        while written < len(payload):
            chunk_size = os.write(descriptor, payload[written:])
            if chunk_size <= 0:
                raise OSError("Could not persist pending user erasure state")
            written += chunk_size
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        _fsync_parent(target)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_pending_erasure_state(operation_id: str) -> None:
    target = _pending_erasure_path(operation_id)
    target.unlink(missing_ok=True)
    if target.parent.exists():
        _fsync_parent(target)


def _erasure_record(
    user_id: str,
    *,
    operation_id: str,
    state: str,
    auth_policy: dict[str, Any],
    audit_policy: dict[str, Any],
    erased_at: datetime,
    retention_started_at: datetime,
) -> dict[str, Any]:
    return {
        "version": 2,
        "operation_id": str(operation_id),
        "state": str(state),
        "user_id": str(user_id),
        "erased_at": _utc_datetime(erased_at).isoformat(),
        "retention_started_at": _utc_datetime(retention_started_at).isoformat(),
        "auth_policy": dict(auth_policy),
        "audit_policy": dict(audit_policy),
    }


def record_user_erasure_intent(
    user_id: str,
    *,
    auth_policy: dict[str, Any],
    audit_policy: dict[str, Any],
    erased_at: datetime | None = None,
    retention_started_at: datetime | None = None,
    operation_id: str | None = None,
) -> str:
    """Durably announce an erasure before its database transaction commits."""

    timestamp = _utc_datetime(erased_at or datetime.now(timezone.utc))
    normalized_operation_id = str(operation_id or uuid.uuid4())
    record = _erasure_record(
        user_id,
        operation_id=normalized_operation_id,
        state="intent",
        auth_policy=auth_policy,
        audit_policy=audit_policy,
        erased_at=timestamp,
        retention_started_at=_utc_datetime(retention_started_at or timestamp),
    )
    # The sparse marker precedes the append so normal startup need inspect only
    # unresolved operations, not a multi-million-line historical ledger.
    _write_pending_erasure_state(record)
    try:
        _append_erasure_record(record)
    except Exception:
        _remove_pending_erasure_state(normalized_operation_id)
        raise
    return normalized_operation_id


def record_completed_user_erasure(
    user_id: str,
    *,
    auth_policy: dict[str, Any],
    audit_policy: dict[str, Any],
    erased_at: datetime | None = None,
    retention_started_at: datetime | None = None,
    operation_id: str | None = None,
) -> None:
    """Append a durable completed-erasure record and flush it to storage."""

    timestamp = _utc_datetime(erased_at or datetime.now(timezone.utc))
    normalized_operation_id = str(operation_id or uuid.uuid4())
    _append_erasure_record(
        _erasure_record(
            user_id,
            operation_id=normalized_operation_id,
            state="completed",
            auth_policy=auth_policy,
            audit_policy=audit_policy,
            erased_at=timestamp,
            retention_started_at=_utc_datetime(retention_started_at or timestamp),
        )
    )
    _remove_pending_erasure_state(normalized_operation_id)


def record_cancelled_user_erasure(
    user_id: str,
    *,
    operation_id: str,
    auth_policy: dict[str, Any],
    audit_policy: dict[str, Any],
    erased_at: datetime,
    retention_started_at: datetime,
) -> None:
    """Close an intent whose database transaction did not commit."""

    record = _erasure_record(
        user_id,
        operation_id=operation_id,
        state="cancelled",
        auth_policy=auth_policy,
        audit_policy=audit_policy,
        erased_at=erased_at,
        retention_started_at=retention_started_at,
    )
    # Publish the terminal sparse state first. If the ledger append fails, both
    # normal startup and restore still know this intent was rolled back.
    _write_pending_erasure_state(record)
    _append_erasure_record(record)
    _remove_pending_erasure_state(operation_id)


def mark_restore_erasure_reconciliation_required() -> None:
    """Persist an external restore fence before archived databases replace live state."""

    ERASURE_RECONCILIATION_REQUIRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        ERASURE_RECONCILIATION_REQUIRED_PATH,
        os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, datetime.now(timezone.utc).isoformat().encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_parent(ERASURE_RECONCILIATION_REQUIRED_PATH)


def restore_erasure_reconciliation_pending() -> bool:
    return ERASURE_RECONCILIATION_REQUIRED_PATH.exists()


def clear_restore_erasure_reconciliation_required() -> None:
    ERASURE_RECONCILIATION_REQUIRED_PATH.unlink(missing_ok=True)
    _fsync_parent(ERASURE_RECONCILIATION_REQUIRED_PATH)


def _load_erasure_operations(
    source_path: Path | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Load the latest state of every ledger operation."""

    path = source_path or ERASURE_LEDGER_PATH
    if not path.exists():
        return {}

    operations: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as ledger:
        for line_number, line in enumerate(ledger, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise TypeError("ledger record must be an object")
                version = record.get("version")
                if version == 1:
                    state = "completed"
                    operation_id = f"legacy:{line_number}"
                elif version == 2:
                    state = str(record.get("state") or "")
                    operation_id = str(record.get("operation_id") or "").strip()
                    if state not in {"intent", "completed", "cancelled"}:
                        raise ValueError("invalid erasure state")
                    if not operation_id:
                        raise ValueError("missing operation_id")
                else:
                    raise ValueError("unsupported ledger record version")
                if not isinstance(record.get("user_id"), str):
                    raise TypeError("user_id must be a string")
                user_id = record["user_id"].strip()
                record["erased_at"] = _utc_datetime(record["erased_at"])
                record["retention_started_at"] = _utc_datetime(
                    record.get("retention_started_at") or record["erased_at"]
                )
                if not user_id:
                    raise ValueError("empty user_id")
                if not isinstance(record.get("auth_policy"), dict) or not isinstance(
                    record.get("audit_policy"), dict
                ):
                    raise ValueError("missing retention policy")
                for policy_name in ("auth_policy", "audit_policy"):
                    policy = record[policy_name]
                    mode = policy.get("mode")
                    if mode not in {"delete_instantly", "delete_after_days", "retain"}:
                        raise ValueError(f"invalid {policy_name} mode")
                    if mode == "delete_after_days":
                        days = int(policy.get("retention_days"))
                        if not 0 <= days <= 3650:
                            raise ValueError(f"invalid {policy_name} retention_days")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Invalid user-erasure ledger record at line {line_number}"
                ) from exc
            record["version"] = int(version)
            record["state"] = state
            record["operation_id"] = operation_id
            record["_line_number"] = line_number
            operations[(user_id, operation_id)] = record
    return operations


def _load_sparse_erasure_operations() -> dict[tuple[str, str], dict[str, Any]]:
    directory = erasure_pending_dir()
    if not directory.exists():
        return {}
    operations: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        operations.update(_load_erasure_operations(path))
    return operations


def _reconcile_sparse_terminal_states() -> int:
    """Finish a terminal append interrupted after its atomic sparse update."""

    reconciled = 0
    for (_user_id, operation_id), record in _load_sparse_erasure_operations().items():
        if record["state"] not in {"completed", "cancelled"}:
            continue
        persisted = _erasure_record(
            record["user_id"],
            operation_id=operation_id,
            state=record["state"],
            auth_policy=record["auth_policy"],
            audit_policy=record["audit_policy"],
            erased_at=record["erased_at"],
            retention_started_at=record["retention_started_at"],
        )
        _append_erasure_record(persisted)
        _remove_pending_erasure_state(operation_id)
        reconciled += 1
    return reconciled


def _latest_user_records_for_state(
    operations: dict[tuple[str, str], dict[str, Any]],
    *,
    state: str,
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for (user_id, _operation_id), record in operations.items():
        if record["state"] != state:
            continue
        previous = records.get(user_id)
        if previous is None or (
            record["erased_at"],
            record["_line_number"],
        ) > (
            previous["erased_at"],
            previous["_line_number"],
        ):
            records[user_id] = record
    for record in records.values():
        record.pop("_line_number", None)
    return records


def load_completed_user_erasures() -> dict[str, dict[str, Any]]:
    """Load the latest valid ledger entry for every permanently erased user."""

    return _latest_user_records_for_state(
        _load_erasure_operations(),
        state="completed",
    )


def load_pending_user_erasures(
    *,
    include_ledger: bool = False,
) -> dict[str, dict[str, Any]]:
    """Load unresolved pre-commit erasure intents, keyed by user ID."""

    operations = _load_erasure_operations() if include_ledger else {}
    operations.update(_load_sparse_erasure_operations())
    return _latest_user_records_for_state(operations, state="intent")


def resolve_pending_user_erasure_intents() -> dict[str, int]:
    """Resolve crash-surviving intents against the authoritative live database.

    This helper is called only by the offline migration process. A missing user
    proves that the destructive transaction committed; a present user proves it
    did not. Restore uses a separate privacy-first path because an archived user
    row cannot answer what happened in the newer live database.
    """

    _reconcile_sparse_terminal_states()
    pending = load_pending_user_erasures()
    if not pending:
        return {"completed": 0, "cancelled": 0}

    from app.database import SessionLocal
    from app.users.models import User

    db = SessionLocal()
    try:
        existing_ids: set[str] = set()
        user_ids = sorted(pending)
        for offset in range(0, len(user_ids), 500):
            existing_ids.update(
                str(value)
                for (value,) in db.query(User.id)
                .filter(User.id.in_(user_ids[offset : offset + 500]))
                .all()
            )
    finally:
        db.close()

    completed = 0
    cancelled = 0
    for user_id, record in pending.items():
        arguments = {
            "operation_id": record["operation_id"],
            "auth_policy": record["auth_policy"],
            "audit_policy": record["audit_policy"],
            "erased_at": record["erased_at"],
            "retention_started_at": record["retention_started_at"],
        }
        if user_id in existing_ids:
            record_cancelled_user_erasure(user_id, **arguments)
            cancelled += 1
        else:
            record_completed_user_erasure(user_id, **arguments)
            completed += 1
    return {"completed": completed, "cancelled": cancelled}


def _apply_retention_policy_after_restore(
    db_log,
    *,
    user_id: str,
    erased_at: datetime,
    policy: dict[str, Any],
    cancel_pending,
    delete_now,
    schedule,
) -> str:
    """Reapply one stored policy without extending its original deadline."""

    mode = str(policy.get("mode") or "delete_after_days")
    if mode == "retain":
        cancel_pending(db_log, user_id)
        return "retained"

    retention_days = policy.get("retention_days")
    if mode == "delete_instantly" or policy.get("delete_immediately"):
        cancel_pending(db_log, user_id)
        delete_now(db_log, user_id)
        return "deleted"

    try:
        days = max(int(retention_days), 0)
    except (TypeError, ValueError):
        days = 30
    scheduled_for = erased_at + timedelta(days=days)
    if scheduled_for <= datetime.now(timezone.utc):
        cancel_pending(db_log, user_id)
        delete_now(db_log, user_id)
        return "deleted"

    schedule(db_log, user_id, days, scheduled_for=scheduled_for)
    return "scheduled"


def _completed_audit_erasure_targets(
    records: dict[str, dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[tuple[str, datetime]]:
    """Resolve ledger subjects whose stored audit-retention deadline elapsed."""

    current = _utc_datetime(now or datetime.now(timezone.utc))
    eligible: list[tuple[str, datetime]] = []
    for user_id, record in records.items():
        policy = record.get("audit_policy") or {}
        mode = str(policy.get("mode") or "")
        erased_at = _utc_datetime(record.get("erased_at") or current)
        if mode == "delete_instantly" or bool(policy.get("delete_immediately")):
            eligible.append((user_id, erased_at))
            continue
        if mode != "delete_after_days":
            continue
        try:
            retention_days = max(int(policy.get("retention_days")), 0)
        except (TypeError, ValueError):
            retention_days = 30
        retention_started = _utc_datetime(
            record.get("retention_started_at") or erased_at
        )
        if retention_started + timedelta(days=retention_days) <= current:
            eligible.append((user_id, erased_at))
    return eligible


def _seed_completed_audit_erasure_fences(
    eligible: list[tuple[str, datetime]],
    *,
    now: datetime,
) -> int:
    if not eligible:
        return 0

    from app.database import SessionLocal
    from app.workers.models import (
        audit_event_subject_fingerprint,
        lock_audit_event_subject_states,
    )

    db = SessionLocal()
    try:
        seeded = 0
        for offset in range(0, len(eligible), 500):
            batch = eligible[offset : offset + 500]
            erased_by_fingerprint = {
                audit_event_subject_fingerprint(user_id): erased_at
                for user_id, erased_at in batch
            }
            states = lock_audit_event_subject_states(
                db,
                subject_fingerprints=set(erased_by_fingerprint),
            )
            for fingerprint, erased_at in erased_by_fingerprint.items():
                state = states[fingerprint]
                existing_erased_at = (
                    _utc_datetime(state.erased_at)
                    if state.erased_at is not None
                    else None
                )
                if existing_erased_at is None or existing_erased_at > erased_at:
                    state.erased_at = erased_at
                state.updated_at = now
            db.commit()
            seeded += len(batch)
        return seeded
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def seed_completed_audit_erasure_fences() -> int:
    """Recreate hash-only fences for ledgered audit erasures after upgrades."""

    now = datetime.now(timezone.utc)
    return _seed_completed_audit_erasure_fences(
        _completed_audit_erasure_targets(
            load_completed_user_erasures(),
            now=now,
        ),
        now=now,
    )


def reconcile_completed_audit_erasures() -> dict[str, int]:
    """Reapply completed audit erasures to current and historical storage.

    This runs while application services are offline during schema migration.
    It repairs both future-write fences and rows that a pre-fence inline writer
    may have recreated before an upgrade or database restore.
    """

    from app.database import AuditSessionLocal, SessionLocal
    from app.logging.models import (
        cancel_audit_log_deletions_for_user,
        delete_admin_notifications_for_user,
        delete_audit_logs_for_user,
    )
    from app.workers.models import AuditErasureReconciliationCheckpoint

    if not audit_erasure_reconciliation_pending():
        return _empty_audit_reconciliation_result()

    now = datetime.now(timezone.utc)
    eligible = _completed_audit_erasure_targets(
        load_completed_user_erasures(),
        now=now,
    )
    _seed_completed_audit_erasure_fences(eligible, now=now)

    db_log = AuditSessionLocal()
    deleted_logs = 0
    deleted_notifications = 0
    try:
        for user_id, _erased_at in eligible:
            cancel_audit_log_deletions_for_user(db_log, user_id)
            deleted_logs += delete_audit_logs_for_user(db_log, user_id)
            deleted_notifications += delete_admin_notifications_for_user(
                db_log,
                user_id,
            )
    finally:
        db_log.close()

    # The audit operations above commit independently because the audit and
    # main schemas may use different databases. Mark completion only after all
    # destructive work succeeds; a crash before this commit safely repeats the
    # idempotent fence and deletion passes. The restore workflow deliberately
    # ignores this marker and reapplies the complete external ledger.
    checkpoint_db = SessionLocal()
    try:
        checkpoint_db.add(
            AuditErasureReconciliationCheckpoint(
                key=_AUDIT_ERASURE_RECONCILIATION_KEY,
                completed_at=datetime.now(timezone.utc),
            )
        )
        checkpoint_db.commit()
    except IntegrityError:
        checkpoint_db.rollback()
        # Normal migration startup is serialized by the advisory migration
        # lock. Still make the helper safe when an operator explicitly disables
        # that lock and another process completed the same idempotent pass.
        if checkpoint_db.get(
            AuditErasureReconciliationCheckpoint,
            _AUDIT_ERASURE_RECONCILIATION_KEY,
        ) is None:
            raise
    except Exception:
        checkpoint_db.rollback()
        raise
    finally:
        checkpoint_db.close()

    return {
        "subjects_reconciled": len(eligible),
        "audit_logs_deleted": deleted_logs,
        "notifications_deleted": deleted_notifications,
    }


def reconcile_completed_user_erasures_after_restore() -> dict[str, Any]:
    """Remove restored subjects and reapply their original log-retention policy."""

    _reconcile_sparse_terminal_states()
    operations = _load_erasure_operations()
    operations.update(_load_sparse_erasure_operations())
    completed_records = _latest_user_records_for_state(
        operations,
        state="completed",
    )
    pending_records = _latest_user_records_for_state(
        operations,
        state="intent",
    )
    # A restore cannot infer whether an intent committed by inspecting an older
    # database snapshot. Privacy therefore wins: unresolved intents are treated
    # as authoritative and completed only after the restored subject is gone.
    records = dict(pending_records)
    records.update(completed_records)
    if not records:
        clear_restore_erasure_reconciliation_required()
        return {
            "ledger_records": 0,
            "pending_intents_completed": 0,
            "users_removed": 0,
            "policies_reapplied": 0,
        }

    from app.database import AuditSessionLocal, SessionLocal
    from app.logging.models import (
        cancel_audit_log_deletions_for_user,
        cancel_auth_log_deletions_for_user,
        delete_admin_notifications_for_user,
        delete_audit_logs_for_user,
        delete_authentication_logs_for_user,
        schedule_audit_log_deletion,
        schedule_auth_log_deletion,
    )
    from app.users.models import User, hard_delete_user

    db = SessionLocal()
    db_log = AuditSessionLocal()
    users_removed = 0
    policies_reapplied = 0
    try:
        restored_user_ids = {
            str(user_id)
            for (user_id,) in db.query(User.id).filter(User.id.in_(records)).all()
        }
        for user_id, record in records.items():
            if user_id in restored_user_ids:
                deleted = hard_delete_user(
                    db,
                    user_id,
                    allow_administrative_target=True,
                    record_erasure=False,
                    notify_user=False,
                )
                users_removed += int(bool(deleted))

            retention_started_at = _utc_datetime(record["retention_started_at"])
            _apply_retention_policy_after_restore(
                db_log,
                user_id=user_id,
                erased_at=retention_started_at,
                policy=record.get("auth_policy") or {},
                cancel_pending=cancel_auth_log_deletions_for_user,
                delete_now=delete_authentication_logs_for_user,
                schedule=schedule_auth_log_deletion,
            )

            def delete_audit_and_notifications(session, target_user_id):
                delete_audit_logs_for_user(session, target_user_id)
                delete_admin_notifications_for_user(session, target_user_id)

            _apply_retention_policy_after_restore(
                db_log,
                user_id=user_id,
                erased_at=retention_started_at,
                policy=record.get("audit_policy") or {},
                cancel_pending=cancel_audit_log_deletions_for_user,
                delete_now=delete_audit_and_notifications,
                schedule=schedule_audit_log_deletion,
            )
            policies_reapplied += 1
    finally:
        db_log.close()
        db.close()

    pending_completed = 0
    for user_id, record in pending_records.items():
        record_completed_user_erasure(
            user_id,
            operation_id=record["operation_id"],
            auth_policy=record["auth_policy"],
            audit_policy=record["audit_policy"],
            erased_at=record["erased_at"],
            retention_started_at=record["retention_started_at"],
        )
        pending_completed += 1

    # The external marker is the final commit point for restore reconciliation.
    # If the process dies before here, the next offline startup repeats every
    # idempotent deletion even when the restored database contains an old SQL
    # checkpoint claiming that reconciliation already ran.
    clear_restore_erasure_reconciliation_required()

    return {
        "ledger_records": len(records),
        "pending_intents_completed": pending_completed,
        "users_removed": users_removed,
        "policies_reapplied": policies_reapplied,
    }
