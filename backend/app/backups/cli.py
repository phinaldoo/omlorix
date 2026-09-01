from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback

from app.backups.models import (
    create_backup_job,
    create_restore_job,
    get_backup_job,
    get_restore_job,
    list_backup_artifacts,
    list_backup_destinations,
    paginate_backup_jobs,
)
from app.backups.service import (
    backup_archive_download_filename,
    build_backup_job_response,
    enqueue_backup_job,
    get_backup_runtime_capabilities,
    materialize_backup_job_artifact,
    run_backup_job_sync,
    run_restore_job_sync,
    safe_backup_error_message,
    verify_backup_job,
    verify_backup_source,
)
from app.database import SessionLocal
from app.workers.operations import external_operations_enabled


def _wait_for_backup_terminal(db, job_id: str):
    try:
        timeout = float(os.getenv("BACKUP_CLI_WAIT_SECONDS", "86400") or "86400")
    except (TypeError, ValueError):
        timeout = 86400.0
    deadline = time.monotonic() + max(60.0, min(timeout, 7 * 86400.0))
    while True:
        db.expire_all()
        row = get_backup_job(db, job_id)
        if row is None or row.status in {"success", "failed", "deleted"}:
            return row
        if time.monotonic() >= deadline:
            raise TimeoutError("Backup worker did not finish before the CLI deadline")
        time.sleep(0.5)


def _cmd_create(args: argparse.Namespace) -> int:
    """Handle backup create command."""
    db = SessionLocal()
    try:
        job = create_backup_job(
            db,
            trigger_type="manual",
            destination_id=args.destination,
            requested_by_user_id=None,
            options={
                "source": "cli",
                "encryption_enabled": (False if bool(args.no_encrypted) else True),
            },
        )
        if external_operations_enabled():
            enqueue_backup_job(job.id)
            _wait_for_backup_terminal(db, job.id)
        else:
            run_backup_job_sync(job.id)
        # The backup runner opens its own database session, so this CLI session
        # can still hold the original queued BackupJob in SQLAlchemy's identity
        # map. Expire cached rows before reading the final status that controls
        # the process exit code shown by the server launcher.
        db.expire_all()
        final = get_backup_job(db, job.id)
        artifacts = list_backup_artifacts(db, job.id)
        payload = {
            "job_id": job.id,
            "status": final.status if final else "unknown",
            "error": final.error if final else None,
            "destination_id": getattr(final, "destination_id", args.destination)
            if final
            else args.destination,
            "encryption_enabled": bool(
                (
                    getattr(final, "options", None)
                    if final and isinstance(getattr(final, "options", None), dict)
                    else {}
                ).get(
                    "encryption_enabled",
                    not bool(args.no_encrypted),
                )
            ),
            "size_bytes": getattr(final, "size_bytes", None) if final else None,
        }
        # Existing command-line users may rely on artifact URIs. The launcher
        # explicitly requests the safe shape because its streamed command
        # output is rendered in the desktop console.
        if not bool(getattr(args, "safe_output", False)):
            payload["artifacts"] = [a.storage_uri for a in artifacts]
        print(json.dumps(payload, indent=2))
        return 0 if final and final.status == "success" else 1
    finally:
        db.close()


def _cmd_options(_args: argparse.Namespace) -> int:
    """Print the safe backup controls that a local launcher may present.

    Destination configurations can contain cloud credentials. This command is
    intentionally narrower than the Admin API: it exposes only identifiers and
    display labels needed to select an enabled destination, plus non-secret
    runtime capabilities used to mirror the Admin backup form.
    """
    db = SessionLocal()
    try:
        destinations = [
            {
                "id": row.id,
                "name": row.name,
                "provider": row.provider,
            }
            for row in list_backup_destinations(db)
            if row.enabled
        ]
        print(
            json.dumps(
                {
                    "destinations": destinations,
                    "capabilities": get_backup_runtime_capabilities(),
                },
                indent=2,
            )
        )
        return 0
    finally:
        db.close()


def _json_default(value):
    """Serialize database timestamps without exposing ORM implementation details."""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _cmd_list(args: argparse.Namespace) -> int:
    """List a bounded page of backup jobs using safe, redacted artifact metadata."""
    db = SessionLocal()
    try:
        page_size = min(100, max(1, int(args.page_size)))
        jobs, total, total_pages, page = paginate_backup_jobs(
            db,
            page=max(1, int(args.page)),
            page_size=page_size,
        )
        payload = {
            "items": [build_backup_job_response(db, job) for job in jobs],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        }
        print(json.dumps(payload, indent=2, default=_json_default))
        return 0
    finally:
        db.close()


def _cmd_show(args: argparse.Namespace) -> int:
    """Show one backup job without returning its internal storage URI."""
    db = SessionLocal()
    try:
        job = get_backup_job(db, args.job_id)
        if job is None:
            print("Backup job not found", file=sys.stderr)
            return 2
        print(
            json.dumps(
                build_backup_job_response(db, job), indent=2, default=_json_default
            )
        )
        return 0
    finally:
        db.close()


def _cmd_download(args: argparse.Namespace) -> int:
    """Materialize a catalogued artifact and stream it without exposing its URI."""
    db = SessionLocal()
    try:
        archive_path, _artifact_id = materialize_backup_job_artifact(db, args.job_id)
        if bool(args.metadata):
            print(
                json.dumps(
                    {
                        "job_id": args.job_id,
                        "filename": backup_archive_download_filename(
                            args.job_id, archive_path
                        ),
                        "bytes": archive_path.stat().st_size,
                    },
                    indent=2,
                )
            )
            return 0

        # stdout is reserved exclusively for archive bytes in stream mode.
        # Expected failures are rendered to stderr by main(), allowing host
        # callers to discard their temporary file without parsing mixed output.
        with archive_path.open("rb") as source:
            shutil.copyfileobj(source, sys.stdout.buffer, length=1024 * 1024)
        sys.stdout.buffer.flush()
        return 0
    finally:
        db.close()


def _restore_source_for_job(db, backup_job_id: str) -> str:
    """Return the newest restorable artifact URI for a successful backup job."""
    backup_job = get_backup_job(db, backup_job_id)
    artifacts = list_backup_artifacts(db, backup_job_id)
    if backup_job is None or backup_job.status != "success" or not artifacts:
        raise RuntimeError("Backup job is not a successful restorable backup")

    # list_backup_artifacts is explicitly newest-first (including an ID
    # tie-breaker), so interrupted/retried jobs resolve deterministically.
    return str(artifacts[0].storage_uri)


def _cmd_restore_preflight(args: argparse.Namespace) -> int:
    """Verify a restore source and target without changing application data."""
    db = SessionLocal()
    try:
        source = getattr(args, "source", None)
        backup_job_id = getattr(args, "job_id", None)
        if backup_job_id:
            try:
                source = _restore_source_for_job(db, backup_job_id)
            except RuntimeError as exc:
                # Preflight is consumed by the host-side restore coordinator,
                # so expected lookup failures must use the same structured
                # result shape as source and target validation failures.
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "backup_job_id": backup_job_id,
                            "preflight": {
                                "ok": False,
                                "reason": "backup_job_not_restorable",
                                "detail": str(exc),
                            },
                        },
                        indent=2,
                    )
                )
                return 1

        result = verify_backup_source(db, source, target_mode=args.target)
        if backup_job_id:
            # Provider URIs and internal paths are not needed when the operator
            # selected an opaque job ID. Keep the terminal output safe to copy.
            result.pop("source_uri", None)
            result["backup_job_id"] = backup_job_id
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    finally:
        db.close()


def _cmd_restore(args: argparse.Namespace) -> int:
    """Handle restore without retaining a connection across schema replacement."""
    if not args.offline:
        print(
            "Full restore requires --offline after stopping Omlorix application services. "
            "Use the Server Launcher for the guided workflow.",
            file=sys.stderr,
        )
        return 2
    if args.target == "in_place" and args.confirm != "RESTORE-IN-PLACE":
        print("In-place restore requires --confirm RESTORE-IN-PLACE", file=sys.stderr)
        return 2

    source = getattr(args, "source", None)
    backup_job_id = getattr(args, "job_id", None)
    if bool(source) == bool(backup_job_id):
        print("Restore requires exactly one of --source or --job-id", file=sys.stderr)
        return 2

    # Resolve job references inside the backend container. This avoids
    # exposing provider URIs or host filesystem layout merely to feed a backup
    # created by this CLI back into its restore command.
    if backup_job_id:
        lookup_db = SessionLocal()
        try:
            try:
                source = _restore_source_for_job(lookup_db, backup_job_id)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        finally:
            lookup_db.close()

    # Keep only the scalar job ID before restoration. The restore coordinator
    # intentionally terminates other PostgreSQL sessions while replacing the
    # schemas, so an ORM object or connection opened here cannot safely survive
    # until the terminal status read.
    creation_db = SessionLocal()
    try:
        job = create_restore_job(
            creation_db,
            source_uri=source,
            target_mode=args.target,
            requested_by_user_id=None,
            confirmed_by_user_id="cli" if args.target == "in_place" else None,
            options={
                "source": "cli",
                # Retain the operator-selected identity for diagnostics.  The
                # restore service also recovers identity from the archive
                # manifest so copied-file restores receive the same repair.
                "source_backup_job_id": backup_job_id,
            },
        )
        restore_job_id = str(job.id)
    finally:
        creation_db.close()

    run_restore_job_sync(restore_job_id)

    # Open a genuinely new session after restoration. Besides avoiding the
    # terminated pre-restore connection, this reads the tracking row recreated
    # in whichever database state (restored or rolled back) is now active.
    result_db = SessionLocal()
    try:
        final = get_restore_job(result_db, restore_job_id)
        final_preflight = final.preflight_json if final else None
        recovery = (
            final_preflight.get("recovery")
            if isinstance(final_preflight, dict)
            else None
        )
        print(
            json.dumps(
                {
                    "restore_job_id": restore_job_id,
                    "status": final.status if final else "unknown",
                    "error": final.error if final else None,
                    "preflight": final_preflight,
                    "recovery": recovery,
                },
                indent=2,
            )
        )
        return 0 if final and final.status == "success" else 1
    finally:
        result_db.close()


def _cmd_verify(args: argparse.Namespace) -> int:
    """Handle verify command."""
    db = SessionLocal()
    try:
        if args.source:
            result = verify_backup_source(db, args.source)
        else:
            result = verify_backup_job(db, args.job_id)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Omlorix backup and restore CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    create_cmd = sub.add_parser("create", help="Create a backup now")
    create_cmd.add_argument("--destination", default=None, help="Destination ID")
    create_cmd.add_argument(
        "--no-encrypted",
        action="store_true",
        help="Create plaintext archive (requires BACKUP_ALLOW_PLAINTEXT_ARCHIVES=true on backend)",
    )
    create_cmd.add_argument(
        "--safe-output",
        action="store_true",
        help="Omit artifact storage URIs from command output",
    )
    create_cmd.set_defaults(func=_cmd_create)

    options_cmd = sub.add_parser(
        "options",
        help="List safe backup destinations and runtime capabilities",
    )
    options_cmd.set_defaults(func=_cmd_options)

    list_cmd = sub.add_parser("list", help="List recent backup jobs")
    list_cmd.add_argument("--page", type=int, default=1, help="Result page")
    list_cmd.add_argument(
        "--page-size", type=int, default=20, help="Jobs per page (1-100)"
    )
    list_cmd.set_defaults(func=_cmd_list)

    show_cmd = sub.add_parser("show", help="Show one backup job")
    show_cmd.add_argument("job_id", help="Backup job ID")
    show_cmd.set_defaults(func=_cmd_show)

    download_cmd = sub.add_parser(
        "download", help="Materialize and stream a completed backup artifact"
    )
    download_cmd.add_argument("job_id", help="Successful backup job ID")
    download_cmd.add_argument(
        "--metadata",
        action="store_true",
        help="Print safe filename and size metadata instead of archive bytes",
    )
    download_cmd.set_defaults(func=_cmd_download)

    restore_cmd = sub.add_parser("restore", help="Restore from backup source URI")
    restore_source = restore_cmd.add_mutually_exclusive_group(required=True)
    restore_source.add_argument(
        "--source", help="Backup source URI (file://, local://, s3://, gs://, azure://)"
    )
    restore_source.add_argument("--job-id", help="Successful backup job ID")
    restore_cmd.add_argument(
        "--target",
        default="empty",
        choices=["empty", "in_place"],
        help="Restore target mode",
    )
    restore_cmd.add_argument(
        "--confirm", default=None, help="Required phrase for in-place restore"
    )
    restore_cmd.add_argument(
        "--offline",
        action="store_true",
        help="Confirm Omlorix application services are stopped for full-instance restore",
    )
    restore_cmd.set_defaults(func=_cmd_restore)

    restore_preflight_cmd = sub.add_parser(
        "restore-preflight",
        help="Verify restore source and target without changing data",
    )
    restore_preflight_source = restore_preflight_cmd.add_mutually_exclusive_group(
        required=True
    )
    restore_preflight_source.add_argument(
        "--source",
        help="Backup source URI (file://, local://, s3://, gs://, azure://)",
    )
    restore_preflight_source.add_argument("--job-id", help="Successful backup job ID")
    restore_preflight_cmd.add_argument(
        "--target",
        default="empty",
        choices=["empty", "in_place"],
        help="Restore target mode to validate",
    )
    restore_preflight_cmd.set_defaults(func=_cmd_restore_preflight)

    verify_cmd = sub.add_parser(
        "verify", help="Verify backup job artifacts or a source URI"
    )
    verify_cmd.add_argument("--job-id", default=None, help="Backup job ID")
    verify_cmd.add_argument(
        "--source",
        default=None,
        help="Backup source URI (file://, local://, s3://, gs://, azure://)",
    )
    verify_cmd.set_defaults(func=_cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "verify" and not args.job_id and not args.source:
        parser.error("verify requires --job-id or --source")
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001
        # Expected policy, validation, storage, and subprocess failures are
        # operator errors, not Python debugging sessions. Tracebacks remain an
        # explicit opt-in for local diagnosis and are never the default output.
        if os.getenv("OMLORIX_BACKUP_CLI_DEBUG", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            traceback.print_exc()
        else:
            message = safe_backup_error_message(exc, operation="Backup command")
            print(f"Error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
