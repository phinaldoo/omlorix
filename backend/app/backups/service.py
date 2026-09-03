from __future__ import annotations

import configparser
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import io
import json
import logging
import os
import posixpath
import re
from pathlib import Path
import secrets
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
import threading
from typing import Any
from urllib.parse import urlparse
import uuid

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
import psycopg2
import zstandard

from app.backups.errors import BackupArchivePolicyError
from app.backups.models import (
    BackupArtifact,
    BackupDestination,
    BackupJob,
    BackupSchedule,
    RestoreJob,
    create_backup_artifact,
    create_backup_job,
    decrypt_destination_config,
    get_backup_artifact,
    get_backup_destination,
    get_backup_job,
    get_restore_job,
    list_backup_artifacts,
    list_backup_destinations,
    mark_backup_artifact_verified,
    update_backup_job_status,
    update_restore_job_status,
)
from app.backups.state import activate_write_freeze, deactivate_write_freeze
from app.backups.storage import build_storage_adapter
from app.database import (
    AUDIT_DATABASE_SCHEMA,
    AUDIT_DATABASE_CONFIG,
    DATABASE_CONFIG,
    DATABASE_SCHEMA,
    LOGS_DATABASE_SCHEMA,
    SessionLocal,
    audit_engine,
    build_postgres_connection_kwargs,
    engine,
)
from app.paths import BACKEND_DIR, DATA_DIR, LOG_DIR, PROJECT_ROOT
from app.redis_client import new_lock_owner, release_lock, try_acquire_lock
from app.settings.utils import invalidate_settings_cache
from app.users.erasure_ledger import (
    ERASURE_LEDGER_PATH,
    ERASURE_RECONCILIATION_REQUIRED_PATH,
    erasure_pending_dir,
    mark_restore_erasure_reconciliation_required,
    reconcile_completed_user_erasures_after_restore,
)
from app.utils.encryption import decrypt_value, encrypt_value
from app.utils.export_versions import matches_export_version
from app.version import APP_VERSION
from app.workers.models import DurableWorkerJob, QUEUE_OPERATIONS, enqueue_worker_job


logger = logging.getLogger(__name__)


BACKUP_JOB_LOCK_NAME = "backup_job_lock"
RESTORE_JOB_LOCK_NAME = "restore_job_lock"
BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE_ENV = "BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE"
BACKUP_EXPORT_FORMAT = "omlorix-backup"
BACKUP_EXPORT_VERSION = 1.0
BACKUP_ENCRYPTED_ARCHIVE_FORMAT = "omlorix-backup-encrypted"
ENCRYPTED_ARCHIVE_MAGIC = b"OMLORIXBKENC1.0\n"
BACKUP_CRYPTO_PROBE = "omlorix-backup-probe-v1.0"
ENCRYPTED_ARCHIVE_TAG_BYTES = 16
BACKUP_ARCHIVE_SUFFIX = ".tar.zst"
BACKUP_ENCRYPTED_ARCHIVE_SUFFIX = ".tar.zst.enc"
# Backup jobs created by Omlorix use UUIDs. Recovery also accepts the short,
# human-readable IDs used by tests and development tools, but deliberately
# limits them to one portable filename component before using them on disk.
BACKUP_RECOVERY_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

BACKUP_LOCAL_DIR = Path(os.getenv("BACKUP_LOCAL_DIR") or (DATA_DIR / "backups"))
BACKUP_STAGING_DIR = BACKUP_LOCAL_DIR / "staging"
BACKUP_ARCHIVE_DIR = BACKUP_LOCAL_DIR / "archives"
BACKUP_DOWNLOAD_CACHE_DIR = BACKUP_LOCAL_DIR / "download-cache"

BACKUP_WRITE_FREEZE_MAX_SECONDS = max(60, int(os.getenv("BACKUP_WRITE_FREEZE_MAX_SECONDS", "300") or "300"))
BACKUP_RESTORE_MAX_DECOMPRESSED_BYTES = max(
    1,
    int(
        os.getenv(
            "BACKUP_RESTORE_MAX_DECOMPRESSED_BYTES",
            str(20 * 1024 * 1024 * 1024),
        )
        or str(20 * 1024 * 1024 * 1024)
    ),
)
BACKUP_RESTORE_MAX_EXTRACTED_BYTES = max(
    1,
    int(
        os.getenv(
            "BACKUP_RESTORE_MAX_EXTRACTED_BYTES",
            str(BACKUP_RESTORE_MAX_DECOMPRESSED_BYTES),
        )
        or str(BACKUP_RESTORE_MAX_DECOMPRESSED_BYTES)
    ),
)
BACKUP_RESTORE_MIN_FREE_BYTES = max(
    1,
    int(os.getenv("BACKUP_RESTORE_MIN_FREE_BYTES", str(128 * 1024 * 1024)) or str(128 * 1024 * 1024)),
)
BACKUP_RESTORE_LOCK_TIMEOUT_SECONDS = max(
    5,
    int(os.getenv("BACKUP_RESTORE_LOCK_TIMEOUT_SECONDS", "30") or "30"),
)
BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS = max(
    60,
    int(os.getenv("BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS", "7200") or "7200"),
)
# pg_restore expands compressed custom-format dumps into plain SQL before psql
# executes them. Reserve a conservative multiple of the larger database dump;
# the main and audit restores run sequentially, so only one SQL file peaks at a
# time.
BACKUP_POSTGRES_PLAIN_SQL_EXPANSION_FACTOR = 5
BACKUP_URI_TEXT_PATTERN = re.compile(
    r"\b(?:file|local|s3|gs|azure|webdav|postgres|postgresql(?:\+[a-z0-9_]+)?)://[^\s'\"<>]+",
    re.IGNORECASE,
)
BACKUP_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"\b(?P<key>password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|refresh[_-]?token)"
    r"\s*[:=]\s*"
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)

# libpq exposes the database password through PGPASSWORD, but encrypted
# certificate-key and OAuth client secrets have no equivalent environment
# variable. Those exceptional parameters require a private service file so
# that no secret is ever included in subprocess argv.
_POSTGRES_FILE_ONLY_SECRET_PARAMETERS = frozenset(
    {
        "oauth_client_secret",
        "sslpassword",
    }
)
_POSTGRES_EPHEMERAL_SERVICE_NAME = "omlorix_ephemeral_connection"
_POSTGRES_CONNINFO_PARAMETER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_TRUE_VALUES = {"1", "true", "yes", "on"}
BACKUP_ALLOW_PLAINTEXT_ARCHIVES = (os.getenv("BACKUP_ALLOW_PLAINTEXT_ARCHIVES") or "").strip().lower() in _TRUE_VALUES

BACKUP_REQUIRED_PATHS = {
    "manifest": "manifest.json",
    "main_dump": "db/main.dump",
    "audit_dump": "db/audit.dump",
    "app_data_tar": "files/app_data.tar",
    "app_logs_tar": "files/app_logs.tar",
    "checksums": "integrity/sha256sums.txt",
    "crypto_probe": "integrity/crypto_probe.txt",
}

@dataclass(frozen=True)
class RestoreJobContext:
    """Database-independent copy of the restore job needed during schema replacement.

    The live ``restore_job`` table is part of a full-instance archive. Keeping
    only plain Python values here lets the SQLAlchemy session be closed before
    ``pg_restore --clean`` starts and lets us recreate this operation's status
    row after the archived table has replaced the live table.
    """

    id: str
    source_uri: str
    target_mode: str
    requested_by_user_id: str | None
    confirmed_by_user_id: str | None
    options: dict[str, Any]
    created_at: datetime
    started_at: datetime | None


@dataclass(frozen=True)
class BackupArtifactRecoveryContext:
    """Database-independent metadata for one recovery-critical archive.

    A full restore replaces the tables that normally map backup job IDs to
    their storage objects.  Keeping this small value object in memory lets the
    restore coordinator recreate that mapping through a fresh database
    connection after the archived schema has been installed.
    """

    id: str
    storage_uri: str
    checksum_sha256: str
    bytes: int
    verified_at: datetime
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class BackupCatalogRecoveryContext:
    """Terminal backup job metadata that must survive a database restore."""

    id: str
    trigger_type: str
    manifest_json: dict[str, Any]
    options: dict[str, Any]
    size_bytes: int
    requested_by_user_id: str | None
    started_at: datetime | None
    finished_at: datetime
    created_at: datetime
    artifact: BackupArtifactRecoveryContext


class BackupArchiveSizeLimitError(RuntimeError):
    """Raised when a restore archive exceeds a configured byte limit."""

    def __init__(self, *, reason: str, limit_bytes: int, observed_bytes: int) -> None:
        self.reason = reason
        self.limit_bytes = limit_bytes
        self.observed_bytes = observed_bytes
        super().__init__(
            f"Backup restore archive exceeds {reason} limit "
            f"({observed_bytes} bytes > {limit_bytes} bytes)"
        )


def get_backup_runtime_capabilities() -> dict[str, Any]:
    """Get backup runtime capabilities."""
    passphrase_configured = bool((os.getenv(BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE_ENV) or "").strip())
    return {
        "archive_encryption_default_enabled": True,
        "archive_encryption_available": passphrase_configured,
        "archive_passphrase_configured": passphrase_configured,
        "plaintext_archives_allowed": bool(BACKUP_ALLOW_PLAINTEXT_ARCHIVES),
    }


def ensure_backup_directories() -> None:
    """Ensure backup directories exist."""
    for path in (
        BACKUP_LOCAL_DIR,
        BACKUP_STAGING_DIR,
        BACKUP_ARCHIVE_DIR,
        BACKUP_DOWNLOAD_CACHE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)


def cleanup_stale_backup_work_files(
    *,
    retention_hours: int = 72,
    batch_size: int = 1000,
) -> int:
    """Remove abandoned private staging/decryption files after process death."""

    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=max(24, min(int(retention_hours), 24 * 14))
    )
    candidates: list[Path] = []
    if BACKUP_STAGING_DIR.exists():
        candidates.extend(BACKUP_STAGING_DIR.iterdir())
    if BACKUP_DOWNLOAD_CACHE_DIR.exists():
        # This directory contains only re-downloadable materializations. Their
        # names are derived from backup jobs, artifacts, restore jobs, and
        # verification timestamps, so a prefix allowlist inevitably misses
        # valid cache entries.
        candidates.extend(BACKUP_DOWNLOAD_CACHE_DIR.iterdir())
    if BACKUP_ARCHIVE_DIR.exists():
        candidates.extend(
            path
            for path in BACKUP_ARCHIVE_DIR.iterdir()
            if path.name.startswith(".") and path.name.endswith(".tmp")
        )

    removed = 0
    for path in candidates[: max(1, min(int(batch_size), 5000))]:
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if modified_at >= cutoff:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink(missing_ok=True)
            else:
                continue
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            logger.debug("Could not remove stale backup work path %s", path.name)
    return removed


def redact_backup_uri_metadata(storage_uri: str | None) -> dict[str, Any] | None:
    """Return non-sensitive metadata for a backup storage URI."""
    if not isinstance(storage_uri, str) or not storage_uri.strip():
        return None
    parsed = urlparse(storage_uri)
    scheme = (parsed.scheme or "unknown").lower()
    return {
        "scheme": scheme,
        "location": "local" if scheme in {"file", "local"} else "remote",
        "fingerprint": hashlib.sha256(storage_uri.encode("utf-8")).hexdigest()[:12],
    }


def _backup_local_path_patterns() -> list[re.Pattern[str]]:
    roots: set[str] = set()
    for path in (
        BACKUP_LOCAL_DIR,
        BACKUP_STAGING_DIR,
        BACKUP_ARCHIVE_DIR,
        BACKUP_DOWNLOAD_CACHE_DIR,
    ):
        try:
            roots.add(str(path.resolve()))
        except Exception:
            continue
    return [
        re.compile(rf"{re.escape(root)}[^\s'\"<>|]*")
        for root in sorted(roots, key=len, reverse=True)
    ]


def redact_backup_uri_text(value: str | None) -> str | None:
    """Redact backup URIs and local backup paths embedded in free-form text."""
    if not isinstance(value, str):
        return value

    def _replace(match: re.Match[str]) -> str:
        uri = match.group(0)
        meta = redact_backup_uri_metadata(uri) or {}
        scheme = meta.get("scheme") or "unknown"
        fingerprint = meta.get("fingerprint") or "unknown"
        return f"{scheme}://[redacted:{fingerprint}]"

    redacted = BACKUP_URI_TEXT_PATTERN.sub(_replace, value)
    for pattern in _backup_local_path_patterns():
        redacted = pattern.sub("[backup-path-redacted]", redacted)
    return redacted


def _redact_secret_assignments(value: str) -> str:
    """Redact common key/value secret fragments that may appear in exception text."""
    return BACKUP_SENSITIVE_ASSIGNMENT_PATTERN.sub(r"\g<key>=[redacted]", value)


def _called_process_command_name(command: Any) -> str:
    """Return a safe executable name from a subprocess command without exposing arguments."""
    if isinstance(command, (list, tuple)) and command:
        return Path(str(command[0])).name or "external command"
    if isinstance(command, str) and command.strip():
        return Path(command.strip().split()[0]).name or "external command"
    return "external command"


def _subprocess_failure_message(*, operation: str, command_name: str, returncode: int | str | None) -> str:
    """Build the admin-facing subprocess failure text without argv, paths, or credentials."""
    status = f" with status {returncode}" if returncode is not None else ""
    return f"{operation} failed because command '{command_name}' exited{status}. Check server logs for details."


def safe_backup_error_message(error: Any, *, operation: str) -> str | None:
    """Return a persisted/admin-facing backup error that does not expose secrets."""
    if error is None:
        return None

    # CalledProcessError.__str__ includes the full command argv, which can contain
    # internal paths and connection metadata, so never persist that raw text.
    if isinstance(error, subprocess.CalledProcessError):
        return _subprocess_failure_message(
            operation=operation,
            command_name=_called_process_command_name(error.cmd),
            returncode=error.returncode,
        )

    # TimeoutExpired also embeds the complete argv in its string form. Report
    # only the executable and timeout instead of persisting connection metadata
    # or internal paths.
    if isinstance(error, subprocess.TimeoutExpired):
        command_name = _called_process_command_name(error.cmd)
        return (
            f"{operation} timed out while running command '{command_name}' "
            f"after {error.timeout} seconds. Check server logs for details."
        )

    text = str(error).strip()
    if not text:
        return f"{operation} failed. Check server logs for details."

    redacted = redact_backup_uri_text(text) or ""
    redacted = _redact_secret_assignments(redacted).strip()
    if not redacted:
        return f"{operation} failed. Check server logs for details."
    if len(redacted) > 500:
        return f"{redacted[:497]}..."
    return redacted


def sanitize_backup_response_metadata(value: Any) -> Any:
    """Redact storage/source URI fields from nested response metadata."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_lower = str(key).strip().lower()
            if key_lower in {"storage_uri", "source_uri", "pre_restore_uri"}:
                sanitized[key] = redact_backup_uri_metadata(item)
            else:
                sanitized[key] = sanitize_backup_response_metadata(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_backup_response_metadata(item) for item in value]
    if isinstance(value, str):
        return redact_backup_uri_text(value)
    return value


def _sha256_file(path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _safe_relative_paths(root: Path) -> list[Path]:
    """Get safe relative paths from root directory."""
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file():
            files.append(path.relative_to(root))
    return sorted(files)


def _directory_size_bytes(root: Path) -> int:
    """Calculate total size of directory in bytes."""
    total = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            total += int(path.stat().st_size)
        except FileNotFoundError:
            continue
    return total


def _copy_stream_with_byte_limit(source, target, *, max_bytes: int, reason: str) -> int:
    """Copy stream data without writing beyond a configured byte limit."""
    total = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        observed = total + len(chunk)
        if observed > max_bytes:
            raise BackupArchiveSizeLimitError(
                reason=reason,
                limit_bytes=max_bytes,
                observed_bytes=observed,
            )
        target.write(chunk)
        total = observed
    return total


def _disk_write_budget_bytes(path: Path) -> int | None:
    """Return bytes writable while preserving the restore free-space reserve."""
    try:
        usage = shutil.disk_usage(str(path))
    except Exception:
        return None
    return max(0, int(usage.free) - BACKUP_RESTORE_MIN_FREE_BYTES)


def _restore_write_limit_for_path(configured_limit: int, path: Path) -> int:
    """Combine the configured restore cap with current free disk budget."""
    disk_budget = _disk_write_budget_bytes(path)
    if disk_budget is None:
        return configured_limit
    return min(configured_limit, disk_budget)


def _estimate_tar_restore_bytes(
    tar_path: Path,
    *,
    member_root: str | None = None,
) -> int:
    """Estimate regular-file bytes restored from a tar archive.

    ``member_root`` limits the estimate to one top-level directory. This is
    used for external local user-file storage, which is embedded below the
    ``userFiles`` directory in the application-data tar but restored onto a
    potentially separate filesystem.
    """
    total = 0
    normalized_root = str(member_root or "").strip().strip("/")
    with tarfile.open(tar_path, mode="r") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            if member.isreg():
                if normalized_root:
                    member_parts = Path(member.name).parts
                    if not member_parts or member_parts[0] != normalized_root:
                        continue
                total += max(0, int(member.size or 0))
                continue
            raise RuntimeError(
                f"Unsupported tar member type in '{tar_path.name}': {member.name}"
            )
    return total


def _protected_data_tar_collisions(
    tar_path: Path,
    protected_names: set[str],
) -> list[str]:
    """Return protected top-level entries present in an application-data tar.

    The normalization intentionally matches ``_safe_extract_tar`` so aliases
    such as ``./name`` and leading slashes cannot bypass the pre-mutation
    collision check.
    """

    if not protected_names:
        return []
    collisions: set[str] = set()
    with tarfile.open(tar_path, mode="r") as archive:
        for member in archive.getmembers():
            raw_name = member.name
            if not raw_name:
                raise RuntimeError(
                    f"Unsupported empty tar member name in '{tar_path.name}'"
                )
            normalized = posixpath.normpath(raw_name.lstrip("/"))
            if normalized in {".", ""}:
                continue
            if normalized.startswith("../") or normalized == ".." or "\x00" in normalized:
                raise RuntimeError(
                    f"Unsafe tar member path in '{tar_path.name}': {raw_name}"
                )
            top_level_name = normalized.split("/", 1)[0]
            if top_level_name in protected_names:
                collisions.add(top_level_name)
    return sorted(collisions)


def _safe_file_size(path: Path) -> int:
    """Get file size safely."""
    try:
        return max(0, int(path.stat().st_size))
    except Exception:
        return 0


def _estimate_database_snapshot_bytes(config: dict[str, str]) -> int:
    """Estimate database snapshot size in bytes."""
    driver = (config.get("driver") or "").lower()
    if driver != "sqlite":
        return 0

    url = config.get("url")
    if not url:
        return 0

    try:
        sqlite_path = _resolve_sqlite_file(url)
    except Exception:
        return 0

    return _safe_file_size(sqlite_path)


def _write_sha256sums(staging_dir: Path, checksums: dict[str, str]) -> None:
    """Write SHA256 checksums file."""
    checksums_path = staging_dir / BACKUP_REQUIRED_PATHS["checksums"]
    checksums_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{digest}  {name}" for name, digest in sorted(checksums.items())]
    checksums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_instance_id() -> str:
    """Build instance ID for backup naming."""
    configured = (os.getenv("BACKUP_INSTANCE_ID") or "").strip()
    if configured:
        return configured
    hostname = socket.gethostname().strip() or "omlorix-instance"
    return hostname.replace("/", "-")


def _build_storage_relative_path(backup_job_id: str, when: datetime, *, extension: str = ".tar.zst") -> str:
    """Build storage relative path for backup archive."""
    instance_id = _build_instance_id()
    ts = when.strftime("%Y%m%dT%H%M%SZ")
    normalized_extension = extension if extension.startswith(".") else f".{extension}"
    return (
        f"omlorix-backups/{instance_id}/{when.strftime('%Y')}/{when.strftime('%m')}/{when.strftime('%d')}/"
        f"{ts}-{backup_job_id}{normalized_extension}"
    )


def _build_manifest(
    *,
    backup_job: BackupJob,
    now: datetime,
    checksums: dict[str, str],
    size_hints: dict[str, int],
    archive_encryption: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build backup manifest."""
    return {
        "format": BACKUP_EXPORT_FORMAT,
        "export_version": BACKUP_EXPORT_VERSION,
        "generated_at": now.isoformat(),
        "backup_job_id": backup_job.id,
        "trigger_type": backup_job.trigger_type,
        "scope": {
            "main_database": True,
            "audit_database": True,
            "app_data": True,
            "app_logs": True,
            "redis": False,
            "observability": False,
        },
        "environment": {
            "instance_id": _build_instance_id(),
            "app_version": APP_VERSION,
        },
        "paths": dict(BACKUP_REQUIRED_PATHS),
        "checksums": checksums,
        "size_hints": size_hints,
        "secret_policy": {
            "env_secrets_included": False,
            "encryption_key_required_on_restore": True,
        },
        "archive_encryption": archive_encryption or {"enabled": False},
    }


def _create_crypto_probe(staging_dir: Path) -> None:
    """Create crypto probe file for encryption verification."""
    probe_cipher = encrypt_value(BACKUP_CRYPTO_PROBE)
    probe_path = staging_dir / BACKUP_REQUIRED_PATHS["crypto_probe"]
    probe_path.parent.mkdir(parents=True, exist_ok=True)
    probe_path.write_text(probe_cipher, encoding="utf-8")


def _resolve_sqlite_file(url: str) -> Path:
    """Resolve SQLite file path from URL."""
    if not url.startswith("sqlite:///"):
        raise ValueError("Unsupported sqlite URL format")
    raw_path = url[len("sqlite:///") :]
    return Path(raw_path)


def _dump_database(config: dict[str, str], output_path: Path, *, schemas: list[str] | None = None) -> None:
    """Dump database to file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    driver = (config.get("driver") or "").lower()

    if driver.startswith("postgresql"):
        if not str(config.get("url") or "").strip():
            raise RuntimeError("Missing database URL for PostgreSQL backup dump")
        cmd = [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(output_path),
        ]
        for schema_name in schemas or []:
            cmd.extend(["--schema", schema_name])
        with _postgres_cli_connection(
            config,
            application_name="omlorix-backup",
        ) as (connection_value, environment):
            cmd.append(connection_value)
            subprocess.run(
                cmd,
                check=True,
                cwd=str(PROJECT_ROOT),
                env=environment,
                timeout=BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS,
            )
        return

    if driver == "sqlite":
        url = config.get("url")
        if not url:
            raise RuntimeError("Missing sqlite URL for backup dump")
        src = _resolve_sqlite_file(url)
        if not src.exists():
            output_path.write_bytes(b"")
            return
        shutil.copy2(src, output_path)
        return

    raise RuntimeError(f"Unsupported database driver for backup: {driver}")


def _uses_unified_postgres_runtime() -> bool:
    if not str(DATABASE_CONFIG.get("driver") or "").lower().startswith("postgresql"):
        return False
    return all(
        str(DATABASE_CONFIG.get(key) or "") == str(AUDIT_DATABASE_CONFIG.get(key) or "")
        for key in ("database_host", "database_port", "database_name", "database_user")
    )


def _logical_dump_schemas(*, logical_target: str) -> list[str] | None:
    if not _uses_unified_postgres_runtime():
        return None
    if logical_target == "main":
        return [DATABASE_SCHEMA, LOGS_DATABASE_SCHEMA]
    if logical_target == "audit":
        return [AUDIT_DATABASE_SCHEMA]
    raise ValueError(f"Unsupported logical dump target: {logical_target}")


def _postgres_connection_kwargs(config: dict[str, str], *, application_name: str) -> dict[str, Any]:
    """Build the restore coordinator's complete libpq connection mapping."""
    connection_kwargs = build_postgres_connection_kwargs(
        config,
        application_name=application_name,
    )
    if not connection_kwargs.get("dbname"):
        raise RuntimeError(
            "PostgreSQL restore isolation requires an explicit database name"
        )
    return connection_kwargs


@contextmanager
def _quiesce_postgres_connections(config: dict[str, str]):
    """Keep competing sessions out of the target database during destructive DDL.

    An admin-triggered full restore necessarily invalidates in-flight database
    work. The HTTP maintenance middleware prevents new web requests, while this
    coordinator also disconnects pooled/background sessions from every Omlorix
    process. Repeating the termination query closes the small race in which a
    scheduler or worker reconnects between the initial drain and a later DDL
    statement in ``pg_restore``.
    """
    if not str(config.get("driver") or "").lower().startswith("postgresql"):
        yield None
        return

    restore_application_name = f"omlorix-restore-{uuid.uuid4().hex[:12]}"
    coordinator_application_name = f"{restore_application_name}-coordinator"
    stop_event = threading.Event()
    ready_event = threading.Event()
    startup_errors: list[Exception] = []

    def _drain_connections() -> None:
        connection = None
        try:
            connection = psycopg2.connect(
                **_postgres_connection_kwargs(
                    config,
                    application_name=coordinator_application_name,
                )
            )
            connection.autocommit = True
            with connection.cursor() as cursor:
                while not stop_event.is_set():
                    cursor.execute(
                        """
                        SELECT pg_terminate_backend(pid)
                        FROM pg_stat_activity
                        WHERE datname = current_database()
                          AND pid <> pg_backend_pid()
                          AND application_name <> %s
                        """,
                        (restore_application_name,),
                    )
                    if not ready_event.is_set():
                        ready_event.set()
                    stop_event.wait(0.25)
        except Exception as exc:  # noqa: BLE001
            if not ready_event.is_set():
                startup_errors.append(exc)
                ready_event.set()
            else:
                logger.exception("PostgreSQL restore connection isolation stopped unexpectedly")
        finally:
            if connection is not None:
                connection.close()

    coordinator = threading.Thread(
        target=_drain_connections,
        name="RestoreConnectionCoordinator",
        daemon=True,
    )
    coordinator.start()
    if not ready_event.wait(timeout=10):
        stop_event.set()
        coordinator.join(timeout=5)
        raise RuntimeError("Timed out while isolating PostgreSQL connections for restore")
    if startup_errors:
        stop_event.set()
        coordinator.join(timeout=5)
        raise RuntimeError("Could not isolate PostgreSQL connections for restore") from startup_errors[0]

    try:
        yield restore_application_name
    finally:
        stop_event.set()
        coordinator.join(timeout=10)


def _postgres_service_file_value(value: Any, *, parameter: str) -> str:
    """Validate one value before writing it to libpq's INI service format."""
    normalized = str(value)
    if "\x00" in normalized or "\n" in normalized or "\r" in normalized:
        # Newlines could create additional INI keys or sections. Failing closed
        # is safer than silently changing a configured connection policy.
        raise RuntimeError(
            f"PostgreSQL connection parameter '{parameter}' cannot contain line breaks"
        )
    return normalized


def _postgres_connection_parameter_name(parameter: Any) -> str:
    """Return one safe libpq keyword for conninfo or service-file output."""
    normalized = str(parameter)
    if not _POSTGRES_CONNINFO_PARAMETER_PATTERN.fullmatch(normalized):
        raise RuntimeError(
            f"Invalid PostgreSQL connection parameter name: {normalized!r}"
        )
    return normalized


def _serialize_postgres_conninfo(parameters: dict[str, Any]) -> str:
    """Serialize libpq keyword/value pairs without version-specific validation.

    ``psycopg2.extensions.make_dsn()`` validates through psycopg2's linked
    libpq. The installed PostgreSQL command-line client can be newer and accept
    connection parameters that this in-process libpq does not know yet. The
    keyword/value syntax itself is stable: values are single-quoted, with
    backslashes and quotes escaped by a backslash.
    """
    serialized: list[str] = []
    for parameter, raw_value in parameters.items():
        keyword = _postgres_connection_parameter_name(parameter)
        value = str(raw_value)
        if "\x00" in value:
            raise RuntimeError(
                f"PostgreSQL connection parameter '{keyword}' cannot contain NUL bytes"
            )
        escaped_value = value.replace("\\", "\\\\").replace("'", "\\'")
        serialized.append(f"{keyword}='{escaped_value}'")
    return " ".join(serialized)


@contextmanager
def _postgres_cli_connection(
    config: dict[str, str],
    *,
    application_name: str,
    add_restore_lock_timeout: bool = False,
):
    """Yield a secret-free libpq target and environment for PostgreSQL clients.

    Most connections use a passwordless libpq conninfo string in argv and pass
    the database password through the existing PGPASSWORD mechanism. Options
    such as ``sslpassword`` and ``oauth_client_secret`` have no environment
    equivalent, so those uncommon configurations use a mode-0600 temporary
    service file that is removed immediately after the client exits.
    """
    connection_parameters = build_postgres_connection_kwargs(
        config,
        application_name=application_name,
    )
    environment = dict(os.environ)
    environment["PGAPPNAME"] = application_name
    service_reference = connection_parameters.get("service")
    has_direct_options = "options" in connection_parameters
    file_only_secrets = _POSTGRES_FILE_ONLY_SECRET_PARAMETERS.intersection(
        connection_parameters
    )

    # A service-file value overrides its environment-variable counterpart.
    # Moving a direct URL password into PGPASSWORD would therefore let a
    # service-file password silently win. A nested temporary service cannot
    # preserve the relationship because libpq rejects nested service entries.
    if service_reference and "password" in connection_parameters:
        raise RuntimeError(
            "PostgreSQL service references cannot be combined with an explicit password "
            "during backup or restore; keep the password in the selected service or "
            "use a direct PostgreSQL URL"
        )

    # ``options`` is one scalar libpq parameter. Adding a direct lock timeout
    # would replace an unresolved service-file options value in its entirety.
    # A direct URL options value already has higher precedence than the service,
    # so that supported form can be extended without changing its semantics.
    if service_reference and add_restore_lock_timeout and not has_direct_options:
        raise RuntimeError(
            "PostgreSQL service references used for restore must define options directly "
            "in DATABASE_URL so the restore lock timeout can be merged without replacing "
            "service-file options"
        )

    # Replacing PGSERVICEFILE with a private file would hide the operator-owned
    # service definition, and libpq does not allow one service entry to include
    # another. Reject file-only URL secrets with service references explicitly.
    if service_reference and file_only_secrets:
        raise RuntimeError(
            "PostgreSQL service references cannot be combined with URL-provided "
            "sslpassword or oauth_client_secret during backup or restore"
        )

    # Direct URL/conninfo parameters override libpq environment defaults. Add
    # the restore lock timeout to the effective URL options rather than merely
    # to PGOPTIONS, otherwise a DATABASE_URL options= value would hide it.
    if add_restore_lock_timeout:
        inherited_options = str(environment.get("PGOPTIONS") or "").strip()
        lock_option = f"-c lock_timeout={BACKUP_RESTORE_LOCK_TIMEOUT_SECONDS}s"
        environment["PGOPTIONS"] = f"{inherited_options} {lock_option}".strip()
        existing_options = str(
            connection_parameters.get("options")
            or inherited_options
            or ""
        ).strip()
        connection_parameters["options"] = f"{existing_options} {lock_option}".strip()

    # Keep the ordinary database password out of both argv and the temporary
    # service file. This preserves the established PGPASSWORD behavior.
    database_password = connection_parameters.pop("password", None)
    if database_password is not None:
        environment["PGPASSWORD"] = str(database_password)

    if not file_only_secrets:
        connection_value = _serialize_postgres_conninfo(connection_parameters)
        yield connection_value, environment
        return

    parser = configparser.RawConfigParser(interpolation=None)
    parser.optionxform = str
    parser[_POSTGRES_EPHEMERAL_SERVICE_NAME] = {
        _postgres_connection_parameter_name(key): _postgres_service_file_value(
            value,
            parameter=str(key),
        )
        for key, value in connection_parameters.items()
    }

    service_path: Path | None = None
    try:
        # NamedTemporaryFile creates the file with owner-only access on POSIX.
        # fchmod makes that security requirement explicit and testable.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="omlorix-postgres-",
            suffix=".service.conf",
            delete=False,
        ) as service_file:
            service_path = Path(service_file.name)
            os.fchmod(service_file.fileno(), stat.S_IRUSR | stat.S_IWUSR)
            parser.write(service_file, space_around_delimiters=False)

        environment["PGSERVICEFILE"] = str(service_path)
        connection_value = f"service={_POSTGRES_EPHEMERAL_SERVICE_NAME}"
        yield connection_value, environment
    finally:
        if service_path is not None:
            service_path.unlink(missing_ok=True)


def _quote_postgres_identifier(identifier: str) -> str:
    """Quote one trusted PostgreSQL identifier for a schema-reset statement."""
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _postgres_schema_reset_sql(
    schema_names: list[str],
    *,
    required_extensions: dict[str, str] | None = None,
) -> str:
    """Build the reset executed in the same transaction as restoration."""
    unique_names = list(dict.fromkeys(name.strip() for name in schema_names if name.strip()))
    if not unique_names:
        raise RuntimeError("PostgreSQL restore requires at least one target schema")
    statements = [
        f"DROP EXTENSION IF EXISTS {_quote_postgres_identifier(extension_name)} CASCADE;"
        for extension_name in (required_extensions or {})
    ]
    statements.extend(
        f"DROP SCHEMA IF EXISTS {_quote_postgres_identifier(name)} CASCADE;"
        for name in unique_names
    )
    statements.extend(
        f"CREATE SCHEMA {_quote_postgres_identifier(name)};"
        for name in unique_names
    )
    statements.extend(
        "CREATE EXTENSION "
        f"{_quote_postgres_identifier(extension_name)} "
        f"WITH SCHEMA {_quote_postgres_identifier(schema_name)};"
        for extension_name, schema_name in (required_extensions or {}).items()
    )
    return "\n".join(statements)


def _write_postgres_restore_toc(
    dump_path: Path,
    toc_path: Path,
    *,
    schema_names: list[str],
) -> None:
    """Write a restore TOC that omits schemas created by the reset preamble."""
    result = subprocess.run(
        ["pg_restore", "--list", str(dump_path)],
        check=True,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS,
    )
    target_schemas = set(schema_names)
    filtered_lines: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        # A custom archive schema entry has the stable shape:
        # ``dump_id; catalog_oid object_oid SCHEMA - name owner``.
        if len(fields) >= 6 and fields[3:5] == ["SCHEMA", "-"] and fields[5] in target_schemas:
            line = f"; {line}"
        filtered_lines.append(line)
    toc_path.write_text("\n".join(filtered_lines) + "\n", encoding="utf-8")


def _restore_postgres_schemas(
    config: dict[str, str],
    dump_path: Path,
    *,
    schema_names: list[str],
    required_extensions: dict[str, str] | None = None,
) -> None:
    """Replace schemas atomically without pg_restore's partition cleanup bug.

    PostgreSQL 18 rejects attempts to drop an inherited constraint directly
    from a partition. A normal ``pg_restore --clean`` emits exactly that SQL
    for partitioned primary keys. Instead, render the custom archive as plain
    SQL without cleanup, then use one psql transaction to drop the old schemas
    and load the archive. Any SQL error rolls the schema drops back too.
    """
    with tempfile.NamedTemporaryFile(
        prefix="omlorix-pg-restore-",
        suffix=".sql",
        dir=dump_path.parent,
        delete=False,
    ) as sql_handle:
        sql_path = Path(sql_handle.name)
    with tempfile.NamedTemporaryFile(
        prefix="omlorix-pg-restore-",
        suffix=".toc",
        dir=dump_path.parent,
        delete=False,
    ) as toc_handle:
        toc_path = Path(toc_handle.name)

    try:
        _write_postgres_restore_toc(
            dump_path,
            toc_path,
            schema_names=schema_names,
        )
        subprocess.run(
            [
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--use-list",
                str(toc_path),
                "--file",
                str(sql_path),
                str(dump_path),
            ],
            check=True,
            cwd=str(PROJECT_ROOT),
            timeout=BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS,
        )

        database_name = str(config.get("database_name") or "").strip()
        if not database_name:
            raise RuntimeError("PostgreSQL restore requires an explicit database name")

        with _quiesce_postgres_connections(config) as application_name:
            with _postgres_cli_connection(
                config,
                application_name=str(application_name),
                add_restore_lock_timeout=True,
            ) as (connection_value, environment):
                subprocess.run(
                    [
                        "psql",
                        "--no-psqlrc",
                        "--quiet",
                        "--set",
                        "ON_ERROR_STOP=1",
                        "--single-transaction",
                        "--dbname",
                        connection_value,
                        "--command",
                        _postgres_schema_reset_sql(
                            schema_names,
                            required_extensions=required_extensions,
                        ),
                        "--file",
                        str(sql_path),
                    ],
                    check=True,
                    cwd=str(PROJECT_ROOT),
                    env=environment,
                    timeout=BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS,
                )
    finally:
        sql_path.unlink(missing_ok=True)
        toc_path.unlink(missing_ok=True)


def _restore_database(
    config: dict[str, str],
    dump_path: Path,
    *,
    schema_names: list[str] | None = None,
    required_extensions: dict[str, str] | None = None,
) -> None:
    """Restore one database dump atomically after disconnecting other clients."""
    driver = (config.get("driver") or "").lower()

    if driver.startswith("postgresql"):
        # Validate the complete CLI connection before rendering restore SQL or
        # disconnecting active sessions. Unsupported service-file combinations
        # must fail without causing a partial restore outage.
        with _postgres_cli_connection(
            config,
            application_name="omlorix-restore-preflight",
            add_restore_lock_timeout=True,
        ):
            pass

        if schema_names:
            _restore_postgres_schemas(
                config,
                dump_path,
                schema_names=schema_names,
                required_extensions=required_extensions,
            )
            return

        database_name = str(config.get("database_name") or "").strip()
        if not database_name:
            raise RuntimeError("PostgreSQL restore requires an explicit database name")
        with _quiesce_postgres_connections(config) as application_name:
            with _postgres_cli_connection(
                config,
                application_name=str(application_name),
                add_restore_lock_timeout=True,
            ) as (connection_value, environment):
                cmd = [
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-privileges",
                    "--dbname",
                    connection_value,
                    str(dump_path),
                ]
                subprocess.run(
                    cmd,
                    check=True,
                    cwd=str(PROJECT_ROOT),
                    env=environment,
                    timeout=BACKUP_RESTORE_COMMAND_TIMEOUT_SECONDS,
                )
        return

    if driver == "sqlite":
        url = config.get("url")
        if not url:
            raise RuntimeError("Missing sqlite URL for restore")
        dst = _resolve_sqlite_file(url)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dump_path, dst)
        return

    raise RuntimeError(f"Unsupported database driver for restore: {driver}")


def _is_relative_to(path: Path, parent: Path) -> bool:
    """Return whether path is contained by parent after resolving both paths."""
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def _backup_local_data_exclusions() -> set[Path]:
    """Return backup work paths that must not be copied into app data.

    The default backup root lives below ``DATA_DIR``. Excluding that root keeps
    staging files and prior archives out of the app-data component. If an
    operator deliberately makes both roots identical, retain ordinary app data
    and exclude only the service-owned backup subdirectories.
    """
    data_root = DATA_DIR.resolve(strict=False)
    backup_root = BACKUP_LOCAL_DIR.resolve(strict=False)
    candidates = (
        (BACKUP_STAGING_DIR, BACKUP_ARCHIVE_DIR, BACKUP_DOWNLOAD_CACHE_DIR)
        if backup_root == data_root
        else (BACKUP_LOCAL_DIR,)
    )
    exclusions: set[Path] = set()
    for candidate in candidates:
        try:
            relative = candidate.resolve(strict=False).relative_to(data_root)
        except ValueError:
            continue
        if relative.parts:
            exclusions.add(relative)
    return exclusions


def _erasure_ledger_data_entries() -> set[Path]:
    """Return top-level data entries reserved for external erasure state.

    Backup creation and restore replacement must use this exact same boundary.
    A nested custom ledger path therefore reserves its containing top-level
    entry rather than allowing the archive to recreate that entry underneath
    the live, preserved directory.
    """
    entries: set[Path] = set()
    for state_path in (
        ERASURE_LEDGER_PATH,
        ERASURE_RECONCILIATION_REQUIRED_PATH,
        erasure_pending_dir(),
    ):
        if not _is_relative_to(state_path, DATA_DIR):
            continue
        relative_path = state_path.relative_to(DATA_DIR)
        if relative_path.parts:
            entries.add(Path(relative_path.parts[0]))
    return entries


def _configured_local_file_storage_path() -> Path | None:
    """Return configured local user-file storage path when local storage is active."""
    provider = (os.getenv("FILE_STORAGE_PROVIDER") or "local").strip().lower()
    if provider != "local":
        return None

    base_path_raw = (os.getenv("FILE_STORAGE_LOCAL_BASE_PATH") or "").strip()
    if not base_path_raw:
        return DATA_DIR / "userFiles"
    return Path(base_path_raw).expanduser().resolve()


def _external_local_file_storage_backup_source() -> tuple[str, Path] | None:
    """Return external local file storage source to fold into app-data backups."""
    local_storage_path = _configured_local_file_storage_path()
    if local_storage_path is None or _is_relative_to(local_storage_path, DATA_DIR):
        return None
    return "userFiles", local_storage_path


def _create_directory_tar(
    source_dir: Path,
    output_tar: Path,
    *,
    extra_sources: dict[str, Path] | None = None,
    excluded_relative_paths: set[Path] | None = None,
) -> None:
    """Create tar archive from directory, optionally overlaying extra paths."""
    output_tar.parent.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    extra_sources = extra_sources or {}
    excluded_relative_paths = excluded_relative_paths or set()
    excluded_roots = set(extra_sources)
    resolved_output_tar = output_tar.resolve(strict=False)

    with tarfile.open(output_tar, mode="w") as archive:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            # Always protect the generic helper from reading the archive while
            # it is simultaneously writing it. The higher-level backup flow
            # also excludes its whole work tree, but this local guard keeps all
            # callers safe when their output happens to be nested.
            if path.resolve(strict=False) == resolved_output_tar:
                continue
            relative_path = path.relative_to(source_dir)
            if relative_path.parts and relative_path.parts[0] in excluded_roots:
                continue
            if any(
                relative_path == excluded or excluded in relative_path.parents
                for excluded in excluded_relative_paths
            ):
                continue
            archive.add(path, arcname=relative_path.as_posix())

        for arc_root, extra_source in sorted(extra_sources.items()):
            if not extra_source.exists():
                continue
            archive.add(extra_source, arcname=Path(arc_root).as_posix(), recursive=False)
            for path in sorted(extra_source.rglob("*")):
                if not path.is_file():
                    continue
                archive_name = (Path(arc_root) / path.relative_to(extra_source)).as_posix()
                archive.add(path, arcname=archive_name)


def _safe_extract_tar(
    archive: tarfile.TarFile,
    target_dir: Path,
    *,
    context: str,
    max_payload_bytes: int | None = BACKUP_RESTORE_MAX_EXTRACTED_BYTES,
) -> None:
    """Safely extract tar archive with security checks."""
    target_dir.mkdir(parents=True, exist_ok=True)
    root = target_dir.resolve(strict=False)
    extracted_bytes = 0
    if max_payload_bytes is not None:
        max_payload_bytes = _restore_write_limit_for_path(max_payload_bytes, target_dir)

    def _reject(reason: str, member: tarfile.TarInfo) -> None:
        raise RuntimeError(f"Refusing to extract unsafe tar member ({context}): {member.name} ({reason})")

    def _ensure_no_symlink_parents(path: Path) -> None:
        try:
            rel = path.resolve(strict=False).relative_to(root)
        except Exception:
            raise RuntimeError(f"Refusing to extract unsafe tar member ({context}): {path}")

        current = root
        for part in rel.parts[:-1]:
            current = current / part
            try:
                st = os.lstat(current)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(st.st_mode):
                raise RuntimeError(
                    f"Refusing to extract unsafe tar member ({context}): parent is symlink: {current}"
                )

    for member in archive.getmembers():
        raw_name = member.name
        if not raw_name:
            _reject("empty_name", member)

        member_name = raw_name.lstrip("/")
        normalized = posixpath.normpath(member_name)
        if normalized in {".", ""}:
            continue
        if normalized.startswith("../") or normalized == "..":
            _reject("path_traversal", member)
        if posixpath.isabs(normalized):
            _reject("absolute_path", member)
        if "\x00" in normalized:
            _reject("nul_byte", member)

        if member.issym() or member.islnk():
            _reject("symlink_or_hardlink", member)
        if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
            _reject("special_file", member)

        dst_path = (target_dir / normalized)
        dst_resolved = dst_path.resolve(strict=False)

        if root not in dst_resolved.parents and dst_resolved != root:
            _reject("outside_target_dir", member)

        if member.isdir():
            dst_path.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(dst_path, member.mode)
            except Exception:
                pass
            continue

        if not member.isreg():
            _reject("unsupported_type", member)

        member_size = max(0, int(member.size or 0))
        if max_payload_bytes is not None:
            observed_bytes = extracted_bytes + member_size
            if observed_bytes > max_payload_bytes:
                raise BackupArchiveSizeLimitError(
                    reason="archive_extracted_size_exceeded",
                    limit_bytes=max_payload_bytes,
                    observed_bytes=observed_bytes,
                )

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        _ensure_no_symlink_parents(dst_path)

        src = archive.extractfile(member)
        if src is None:
            _reject("missing_file_payload", member)

        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(dst_path, flags, member.mode or 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                if max_payload_bytes is None:
                    shutil.copyfileobj(src, out, length=1024 * 1024)
                else:
                    copied_bytes = _copy_stream_with_byte_limit(
                        src,
                        out,
                        max_bytes=max_payload_bytes - extracted_bytes,
                        reason="archive_extracted_size_exceeded",
                    )
                    extracted_bytes += copied_bytes
        finally:
            src.close()

        try:
            os.chmod(dst_path, member.mode)
        except Exception:
            pass


def _extract_directory_tar(input_tar: Path, target_dir: Path) -> None:
    """Extract directory tar archive."""
    target_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(input_tar, mode="r") as archive:
        _safe_extract_tar(archive, target_dir, context="directory_tar")


@contextmanager
def _private_atomic_binary_output(target_path: Path):
    """Publish a fully flushed private file without exposing partial output."""

    target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    handle = os.fdopen(descriptor, "wb")
    try:
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temporary, target_path)
        os.chmod(target_path, 0o600)
    except Exception:
        if not handle.closed:
            handle.close()
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _write_zstd_archive(source_dir: Path, raw_out) -> None:
    compressor = zstandard.ZstdCompressor(level=6)
    with compressor.stream_writer(raw_out, closefd=False) as compressed_out:
        with tarfile.open(fileobj=compressed_out, mode="w|") as archive:
            for relative_path in _safe_relative_paths(source_dir):
                archive.add(
                    source_dir / relative_path,
                    arcname=relative_path.as_posix(),
                )


def _create_zstd_archive(source_dir: Path, archive_path: Path) -> None:
    """Create a private, atomically published zstandard tar archive."""

    with _private_atomic_binary_output(archive_path) as raw_out:
        _write_zstd_archive(source_dir, raw_out)


def _extract_zstd_archive(
    archive_path: Path,
    extract_dir: Path,
    *,
    max_decompressed_bytes: int = BACKUP_RESTORE_MAX_DECOMPRESSED_BYTES,
    max_extracted_bytes: int = BACKUP_RESTORE_MAX_EXTRACTED_BYTES,
) -> None:
    """Extract zstandard compressed tar archive."""
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="omlorix-backup-", suffix=".tar", delete=False) as tmp_tar_handle:
        tmp_tar_path = Path(tmp_tar_handle.name)

    try:
        max_decompressed_bytes = _restore_write_limit_for_path(max_decompressed_bytes, tmp_tar_path.parent)
        with archive_path.open("rb") as raw_in:
            decompressor = zstandard.ZstdDecompressor()
            with decompressor.stream_reader(raw_in) as reader:
                with tmp_tar_path.open("wb") as tmp_out:
                    _copy_stream_with_byte_limit(
                        reader,
                        tmp_out,
                        max_bytes=max_decompressed_bytes,
                        reason="archive_decompressed_size_exceeded",
                    )

        max_extracted_bytes = _restore_write_limit_for_path(max_extracted_bytes, extract_dir)
        with tarfile.open(tmp_tar_path, mode="r") as archive:
            _safe_extract_tar(
                archive,
                extract_dir,
                context="zstd_archive",
                max_payload_bytes=max_extracted_bytes,
            )
    finally:
        tmp_tar_path.unlink(missing_ok=True)


def _get_archive_encryption_passphrase(*, required: bool) -> str | None:
    """Get archive encryption passphrase."""
    raw = (os.getenv(BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE_ENV) or "").strip()
    if raw:
        return raw
    if required:
        raise RuntimeError(
            f"Archive encryption passphrase is required. Set {BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE_ENV}."
        )
    return None


def ensure_backup_archive_policy(encryption_enabled: bool) -> None:
    """Reject archive modes that this server cannot safely create."""
    if encryption_enabled:
        if _get_archive_encryption_passphrase(required=False) is None:
            raise BackupArchivePolicyError(
                code="backup_archive_encryption_unavailable",
                message=(
                    "Archive encryption passphrase is required. "
                    f"Set {BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE_ENV}."
                ),
            )
        return

    if not BACKUP_ALLOW_PLAINTEXT_ARCHIVES:
        raise BackupArchivePolicyError(
            code="backup_plaintext_archives_disabled",
            message=(
                "Plaintext backup archives are disabled. "
                "Set BACKUP_ALLOW_PLAINTEXT_ARCHIVES=true to allow unencrypted archives."
            ),
        )


def _resolve_archive_encryption_enabled(options: dict[str, Any]) -> bool:
    """Resolve archive encryption enabled flag."""
    requested = options.get("encryption_enabled")
    encryption_enabled = True if requested is None else bool(requested)
    ensure_backup_archive_policy(encryption_enabled)
    return encryption_enabled


def _archive_looks_encrypted(path: Path) -> bool:
    """Check whether an archive uses the Omlorix encrypted wrapper."""
    if not path.exists() or path.stat().st_size < len(ENCRYPTED_ARCHIVE_MAGIC):
        return False
    with path.open("rb") as handle:
        prefix = handle.read(len(ENCRYPTED_ARCHIVE_MAGIC))
    return prefix == ENCRYPTED_ARCHIVE_MAGIC


def backup_archive_download_filename(backup_job_id: str, archive_path: Path) -> str:
    """Build a download filename that matches the actual archive encoding."""
    extension = (
        BACKUP_ENCRYPTED_ARCHIVE_SUFFIX
        if _archive_looks_encrypted(Path(archive_path))
        else BACKUP_ARCHIVE_SUFFIX
    )
    return f"omlorix-backup-{backup_job_id}{extension}"


def _build_archive_encryption_metadata(passphrase: str) -> dict[str, Any]:
    """Build archive encryption metadata."""
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    iterations = 390_000
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)
    return {
        "format": BACKUP_ENCRYPTED_ARCHIVE_FORMAT,
        "export_version": BACKUP_EXPORT_VERSION,
        "algorithm": "AES-256-GCM",
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": iterations,
        "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
        "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "key": key,
    }


def _encrypt_archive_file(source_path: Path, target_path: Path, *, passphrase: str) -> None:
    """Encrypt an existing archive into a private atomic destination."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("The 'cryptography' package is required for archive encryption") from exc

    metadata = _build_archive_encryption_metadata(passphrase)
    key = metadata.pop("key")
    nonce = base64.urlsafe_b64decode(metadata["nonce"].encode("ascii"))

    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    with source_path.open("rb") as source, _private_atomic_binary_output(target_path) as target:
        target.write(ENCRYPTED_ARCHIVE_MAGIC)
        target.write(json.dumps(metadata, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n")
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            target.write(encryptor.update(chunk))
        target.write(encryptor.finalize())
        target.write(encryptor.tag)


class _ArchiveEncryptingWriter(io.RawIOBase):
    """File-like adapter that encrypts compressed archive bytes as they arrive."""

    def __init__(self, target, encryptor) -> None:
        super().__init__()
        self._target = target
        self._encryptor = encryptor

    def writable(self) -> bool:
        return True

    def write(self, value) -> int:
        payload = bytes(value)
        if payload:
            self._target.write(self._encryptor.update(payload))
        return len(payload)

    def flush(self) -> None:
        if not self._target.closed:
            self._target.flush()


def _create_encrypted_zstd_archive(
    source_dir: Path,
    target_path: Path,
    *,
    passphrase: str,
) -> None:
    """Compress directly into AES-GCM ciphertext without a plaintext archive."""

    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "The 'cryptography' package is required for archive encryption"
        ) from exc

    metadata = _build_archive_encryption_metadata(passphrase)
    key = metadata.pop("key")
    nonce = base64.urlsafe_b64decode(metadata["nonce"].encode("ascii"))
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()

    with _private_atomic_binary_output(target_path) as target:
        target.write(ENCRYPTED_ARCHIVE_MAGIC)
        target.write(
            json.dumps(
                metadata,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            + b"\n"
        )
        encrypted_writer = _ArchiveEncryptingWriter(target, encryptor)
        try:
            _write_zstd_archive(source_dir, encrypted_writer)
        finally:
            encrypted_writer.close()
        target.write(encryptor.finalize())
        target.write(encryptor.tag)


def _write_restore_limited_chunk(target, chunk: bytes, *, current_bytes: int, max_bytes: int, target_dir: Path, reason: str) -> int:
    """Write a restore temp chunk while enforcing byte and free-space limits."""
    if not chunk:
        return current_bytes
    observed = current_bytes + len(chunk)
    if observed > max_bytes:
        raise BackupArchiveSizeLimitError(
            reason=reason,
            limit_bytes=max_bytes,
            observed_bytes=observed,
        )
    disk_budget = _disk_write_budget_bytes(target_dir)
    if disk_budget is not None and len(chunk) > disk_budget:
        raise BackupArchiveSizeLimitError(
            reason=reason,
            limit_bytes=current_bytes + disk_budget,
            observed_bytes=observed,
        )
    target.write(chunk)
    return observed


def _decrypt_archive_file(
    source_path: Path,
    target_path: Path,
    *,
    passphrase: str,
    max_decrypted_bytes: int = BACKUP_RESTORE_MAX_DECOMPRESSED_BYTES,
) -> None:
    """Decrypt archive file."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("The 'cryptography' package is required for archive decryption") from exc

    with source_path.open("rb") as source:
        prefix = source.read(len(ENCRYPTED_ARCHIVE_MAGIC))
        if prefix != ENCRYPTED_ARCHIVE_MAGIC:
            raise RuntimeError("Archive is not in encrypted Omlorix backup format")

        header = source.readline()
        if not header:
            raise RuntimeError("Encrypted archive header missing")
        metadata = json.loads(header.decode("utf-8"))

        # Encrypted archives have one unreleased format contract. Checking the
        # header before deriving a key prevents unsupported or future layouts from
        # being interpreted as the current 1.0 structure.
        if (
            not isinstance(metadata, dict)
            or metadata.get("format") != BACKUP_ENCRYPTED_ARCHIVE_FORMAT
            or not matches_export_version(
                metadata.get("export_version"), BACKUP_EXPORT_VERSION
            )
        ):
            raise RuntimeError(
                f"Unsupported encrypted backup export version. Expected {BACKUP_EXPORT_VERSION}."
            )

        try:
            iterations = int(metadata["iterations"])
            salt = base64.urlsafe_b64decode(str(metadata["salt"]).encode("ascii"))
            nonce = base64.urlsafe_b64decode(str(metadata["nonce"]).encode("ascii"))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Invalid encrypted archive metadata: {exc}") from exc

        key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).decryptor()

        target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        max_decrypted_bytes = _restore_write_limit_for_path(
            max_decrypted_bytes,
            target_path.parent,
        )
        with _private_atomic_binary_output(target_path) as target:
            tail = b""
            bytes_written = 0
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                combined = tail + chunk
                if len(combined) <= ENCRYPTED_ARCHIVE_TAG_BYTES:
                    tail = combined
                    continue
                body = combined[:-ENCRYPTED_ARCHIVE_TAG_BYTES]
                tail = combined[-ENCRYPTED_ARCHIVE_TAG_BYTES:]
                bytes_written = _write_restore_limited_chunk(
                    target,
                    decryptor.update(body),
                    current_bytes=bytes_written,
                    max_bytes=max_decrypted_bytes,
                    target_dir=target_path.parent,
                    reason="archive_decompressed_size_exceeded",
                )

            if len(tail) != ENCRYPTED_ARCHIVE_TAG_BYTES:
                raise RuntimeError("Encrypted archive authentication tag missing or truncated")

            try:
                final_chunk = decryptor.finalize_with_tag(tail)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Failed to decrypt backup archive (passphrase mismatch or corruption): {exc}") from exc
            bytes_written = _write_restore_limited_chunk(
                target,
                final_chunk,
                current_bytes=bytes_written,
                max_bytes=max_decrypted_bytes,
                target_dir=target_path.parent,
                reason="archive_decompressed_size_exceeded",
            )


@contextmanager
def _prepare_archive_for_restore(source_path: Path):
    """Prepare archive for restore, decrypting if needed."""
    if not _archive_looks_encrypted(source_path):
        yield source_path
        return

    passphrase = _get_archive_encryption_passphrase(required=True)
    decrypted_target = BACKUP_DOWNLOAD_CACHE_DIR / f"decrypted-{uuid.uuid4().hex}.tar.zst"
    try:
        _decrypt_archive_file(
            source_path,
            decrypted_target,
            passphrase=passphrase,
            max_decrypted_bytes=BACKUP_RESTORE_MAX_DECOMPRESSED_BYTES,
        )
        yield decrypted_target
    finally:
        decrypted_target.unlink(missing_ok=True)


@contextmanager
def distributed_lock(lock_name: str, ttl_seconds: int):
    """Distributed lock context manager."""
    lock_owner = new_lock_owner()
    acquired = try_acquire_lock(lock_name, lock_owner, ttl_seconds)
    if not acquired:
        raise RuntimeError(f"Lock '{lock_name}' is currently held by another operation")
    try:
        yield
    finally:
        release_lock(lock_name, lock_owner)


def _resolve_destination(db: Session, destination_id: str | None) -> tuple[BackupDestination | None, dict[str, Any]]:
    """Resolve backup destination and config."""
    if not destination_id:
        return None, {}

    destination = get_backup_destination(db, destination_id)
    if destination is None:
        raise RuntimeError("Backup destination not found")
    if not destination.enabled:
        raise RuntimeError("Backup destination is disabled")

    config = decrypt_destination_config(destination.config_encrypted)
    return destination, config


def _component_sizes(component_paths: dict[str, Path]) -> dict[str, int]:
    """Get sizes of backup components."""
    size_map: dict[str, int] = {}
    for name, path in component_paths.items():
        size_map[name] = path.stat().st_size if path.exists() else 0
    return size_map


def _create_backup_components(staging_dir: Path) -> dict[str, Path]:
    """Create backup components."""
    component_paths = {
        "main_dump": staging_dir / BACKUP_REQUIRED_PATHS["main_dump"],
        "audit_dump": staging_dir / BACKUP_REQUIRED_PATHS["audit_dump"],
        "app_data_tar": staging_dir / BACKUP_REQUIRED_PATHS["app_data_tar"],
        "app_logs_tar": staging_dir / BACKUP_REQUIRED_PATHS["app_logs_tar"],
    }

    _dump_database(
        DATABASE_CONFIG,
        component_paths["main_dump"],
        schemas=_logical_dump_schemas(logical_target="main"),
    )
    _dump_database(
        AUDIT_DATABASE_CONFIG,
        component_paths["audit_dump"],
        schemas=_logical_dump_schemas(logical_target="audit"),
    )
    external_file_storage_source = _external_local_file_storage_backup_source()
    extra_data_sources = (
        dict([external_file_storage_source])
        if external_file_storage_source
        else {}
    )
    _create_directory_tar(
        DATA_DIR,
        component_paths["app_data_tar"],
        extra_sources=extra_data_sources,
        excluded_relative_paths=(
            # Restore preserves the ledger's top-level data entry. Excluding
            # that same entry keeps archive and restore granularity aligned for
            # custom paths such as ``DATA_DIR / "erasure/ledger.jsonl"`` and
            # prevents a restored directory from being nested inside its live
            # counterpart.
            _erasure_ledger_data_entries() | _backup_local_data_exclusions()
        ),
    )
    _create_directory_tar(LOG_DIR, component_paths["app_logs_tar"])
    _create_crypto_probe(staging_dir)

    return component_paths


def _build_checksums(staging_dir: Path) -> dict[str, str]:
    """Build checksums for staging directory."""
    checksums: dict[str, str] = {}
    for relative in _safe_relative_paths(staging_dir):
        if relative.as_posix() == BACKUP_REQUIRED_PATHS["checksums"]:
            continue
        checksums[relative.as_posix()] = _sha256_file(staging_dir / relative)
    return checksums


def _resolve_adapter_for_destination(
    destination: BackupDestination | None,
    destination_config: dict[str, Any],
):
    """Resolve storage adapter for destination."""
    if destination is None:
        return build_storage_adapter("local", {"base_path": str(BACKUP_ARCHIVE_DIR)}, default_local_dir=BACKUP_ARCHIVE_DIR)

    adapter = build_storage_adapter(
        destination.provider,
        destination_config,
        default_local_dir=BACKUP_ARCHIVE_DIR,
    )
    return adapter


def _destination_uses_remote_storage(destination: BackupDestination | None) -> bool:
    """Return whether a destination stores its durable artifact off-host."""
    if destination is None:
        return False
    return str(destination.provider or "").strip().lower() != "local"


def apply_backup_schedule_retention(db: Session, schedule: BackupSchedule) -> None:
    """Prune successful backup artifacts created by one backup schedule."""
    retention_count = schedule.retention_count if isinstance(schedule.retention_count, int) and schedule.retention_count > 0 else None
    retention_days = schedule.retention_days if isinstance(schedule.retention_days, int) and schedule.retention_days > 0 else None

    if retention_count is None and retention_days is None:
        return

    candidate_jobs = (
        db.query(BackupJob)
        .filter(BackupJob.destination_id == schedule.destination_id)
        .filter(BackupJob.status == "success")
        .order_by(BackupJob.created_at.desc())
        .all()
    )
    jobs = [
        job
        for job in candidate_jobs
        if isinstance(job.options, dict) and job.options.get("schedule_id") == schedule.id
    ]

    to_delete_ids: set[str] = set()

    if retention_count is not None and len(jobs) > retention_count:
        for job in jobs[retention_count:]:
            to_delete_ids.add(job.id)

    if retention_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        for job in jobs:
            if job.created_at and job.created_at < cutoff:
                to_delete_ids.add(job.id)

    for job_id in to_delete_ids:
        try:
            delete_backup_job_and_artifacts(db, job_id, delete_remote=True)
        except Exception:
            logger.exception("Failed applying backup retention for job %s", job_id)


def run_backup_job_sync(backup_job_id: str) -> BackupJob:
    """Run backup job synchronously."""
    ensure_backup_directories()
    db = SessionLocal()
    try:
        return _run_backup_job_with_session(db, backup_job_id)
    finally:
        db.close()


def enqueue_backup_job(backup_job_id: str) -> DurableWorkerJob:
    """Durably enqueue a backup for the dedicated operations worker."""
    ensure_backup_directories()
    db = SessionLocal()
    try:
        backup_job = get_backup_job(db, str(backup_job_id))
        if backup_job is None:
            raise RuntimeError("Backup job not found")
        try:
            return enqueue_worker_job(
                db,
                queue=QUEUE_OPERATIONS,
                kind="backup",
                user_id=backup_job.requested_by_user_id,
                payload={"backup_job_id": str(backup_job_id)},
                idempotency_key=f"backup:{backup_job_id}",
                # Backups can reach remote storage before a process dies. Avoid an
                # automatic replay with unknown side effects; reconciliation marks
                # an interrupted catalog row failed for an explicit operator retry.
                max_attempts=1,
                priority=20,
                commit=True,
            )
        except Exception:
            db.rollback()
            update_backup_job_status(
                db,
                job_id=str(backup_job_id),
                status="failed",
                error=None,
            )
            raise
    finally:
        db.close()


def _run_backup_job_with_session(db: Session, backup_job_id: str) -> BackupJob:
    """Run backup job with database session."""
    job = get_backup_job(db, backup_job_id)
    if not job:
        raise RuntimeError("Backup job not found")

    if job.status == "running":
        return job

    work_dir = BACKUP_STAGING_DIR / backup_job_id
    staging_dir = work_dir / "contents"
    try:
        # Every operation after a durable job reservation belongs inside this
        # boundary. Policy validation, passphrase lookup, filesystem setup, and
        # destination resolution can all fail before the first dump starts;
        # those failures still need a terminal catalog status.
        update_backup_job_status(db, job_id=backup_job_id, status="running", error=None)

        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        staging_dir.mkdir(exist_ok=False, mode=0o700)
        os.chmod(work_dir, 0o700)
        os.chmod(staging_dir, 0o700)

        now = datetime.now(timezone.utc)
        options = job.options if isinstance(job.options, dict) else {}
        encryption_enabled = _resolve_archive_encryption_enabled(options)
        archive_passphrase = _get_archive_encryption_passphrase(required=True) if encryption_enabled else None
        archive_extension = (
            BACKUP_ENCRYPTED_ARCHIVE_SUFFIX if encryption_enabled else BACKUP_ARCHIVE_SUFFIX
        )
        archive_relative = _build_storage_relative_path(backup_job_id, now, extension=archive_extension)
        destination, destination_config = _resolve_destination(db, job.destination_id)
        adapter = _resolve_adapter_for_destination(destination, destination_config)
        # A remote artifact is durable only at its configured destination. Keep
        # its upload source in the job work tree so normal completion and stale
        # work cleanup cannot leave a second retained copy in backups_data.
        archive_work_dir = (
            work_dir
            if _destination_uses_remote_storage(destination)
            else BACKUP_ARCHIVE_DIR
        )
        final_archive_local = archive_work_dir / (
            f"{backup_job_id}{BACKUP_ENCRYPTED_ARCHIVE_SUFFIX}"
            if encryption_enabled
            else f"{backup_job_id}{BACKUP_ARCHIVE_SUFFIX}"
        )

        with distributed_lock(BACKUP_JOB_LOCK_NAME, BACKUP_WRITE_FREEZE_MAX_SECONDS + 600):
            activate_write_freeze(reason="backup", ttl_seconds=BACKUP_WRITE_FREEZE_MAX_SECONDS)
            try:
                component_paths = _create_backup_components(staging_dir)
            finally:
                deactivate_write_freeze()

            checksums = _build_checksums(staging_dir)
            _write_sha256sums(staging_dir, checksums)

            manifest = _build_manifest(
                backup_job=job,
                now=now,
                checksums=checksums,
                size_hints=_component_sizes(component_paths),
                archive_encryption=(
                    {
                        "enabled": True,
                        "format": BACKUP_ENCRYPTED_ARCHIVE_FORMAT,
                        "export_version": BACKUP_EXPORT_VERSION,
                        "algorithm": "AES-256-GCM",
                        "kdf": "PBKDF2-HMAC-SHA256",
                    }
                    if encryption_enabled
                    else {"enabled": False}
                ),
            )
            manifest_path = staging_dir / BACKUP_REQUIRED_PATHS["manifest"]
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True), encoding="utf-8")

            checksums[BACKUP_REQUIRED_PATHS["manifest"]] = _sha256_file(manifest_path)
            _write_sha256sums(staging_dir, checksums)

            if encryption_enabled:
                _create_encrypted_zstd_archive(
                    staging_dir,
                    final_archive_local,
                    passphrase=archive_passphrase,
                )
            else:
                _create_zstd_archive(staging_dir, final_archive_local)

            archive_checksum = _sha256_file(final_archive_local)
            archive_size = final_archive_local.stat().st_size
            storage_uri = adapter.upload_file(final_archive_local, archive_relative)

            artifact = create_backup_artifact(
                db,
                backup_job_id=backup_job_id,
                storage_uri=storage_uri,
                checksum_sha256=archive_checksum,
                bytes_count=archive_size,
            )
            mark_backup_artifact_verified(db, artifact.id)

            update_backup_job_status(
                db,
                job_id=backup_job_id,
                status="success",
                error=None,
                manifest_json=manifest,
                size_bytes=archive_size,
            )
            if job.trigger_type == "scheduled" and isinstance(options.get("schedule_id"), str):
                schedule = db.query(BackupSchedule).filter(BackupSchedule.id == options["schedule_id"]).first()
                if schedule is not None:
                    apply_backup_schedule_retention(db, schedule)
            job = get_backup_job(db, backup_job_id)
            if job is None:
                raise RuntimeError("Backup job disappeared after completion")
            return job
    except Exception as exc:  # noqa: BLE001
        logger.exception("Backup job %s failed", backup_job_id)
        update_backup_job_status(
            db,
            job_id=backup_job_id,
            status="failed",
            error=safe_backup_error_message(exc, operation="Backup job"),
        )
        job = get_backup_job(db, backup_job_id)
        if job is None:
            raise RuntimeError("Backup job missing after failure")
        return job
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _resolve_local_artifact_path(source_uri: str) -> Path | None:
    """Resolve local artifact path from URI."""
    if source_uri.startswith("file://"):
        return Path(source_uri[len("file://") :])
    if source_uri.startswith("local://"):
        rel = source_uri[len("local://") :].lstrip("/")
        return BACKUP_ARCHIVE_DIR / rel
    return None


def _download_remote_artifact(db: Session, source_uri: str, target_path: Path) -> Path:
    """Download remote artifact from storage."""
    provider = None
    if source_uri.startswith("s3://"):
        provider = "s3"
    elif source_uri.startswith("gs://"):
        provider = "gcs"
    elif source_uri.startswith("azure://"):
        provider = "azure"
    elif source_uri.startswith("webdav://"):
        provider = "webdav"

    if not provider:
        raise RuntimeError(f"Unsupported backup source URI '{source_uri}'")

    candidates = [d for d in list_backup_destinations(db) if d.provider == provider and d.enabled]
    if not candidates:
        raise RuntimeError(f"No enabled destination configured for provider '{provider}'")

    last_error: Exception | None = None
    for destination in candidates:
        try:
            config = decrypt_destination_config(destination.config_encrypted)
            adapter = build_storage_adapter(destination.provider, config, default_local_dir=BACKUP_ARCHIVE_DIR)
            return adapter.download_file(source_uri, target_path)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    raise RuntimeError(f"Failed to download remote backup artifact: {last_error}")


def _materialize_source_artifact(db: Session, source_uri: str, job_id: str) -> Path:
    """Materialize source artifact to local path."""
    local_path = _resolve_local_artifact_path(source_uri)
    if local_path is not None:
        if not local_path.exists():
            raise RuntimeError(f"Backup artifact does not exist at {local_path}")
        return local_path

    downloaded = BACKUP_DOWNLOAD_CACHE_DIR / f"{job_id}.tar.zst"
    return _download_remote_artifact(db, source_uri, downloaded)


@contextmanager
def _verification_source_artifact(db: Session, source_uri: str, job_id: str):
    """Materialize a verification source and remove remote cache data eagerly."""

    remote = _resolve_local_artifact_path(source_uri) is None
    # Verification can be requested concurrently for the same catalog
    # artifact or arbitrary source. Give every materialization its own path so
    # one verifier cannot overwrite or unlink the bytes another verifier is
    # hashing/preflighting.
    materialization_id = (
        f"{job_id}-{uuid.uuid4().hex}"
        if remote
        else job_id
    )
    remote_target = BACKUP_DOWNLOAD_CACHE_DIR / f"{materialization_id}.tar.zst"
    try:
        materialized = _materialize_source_artifact(
            db,
            source_uri,
            materialization_id,
        )
    except Exception:
        if remote:
            remote_target.unlink(missing_ok=True)
        raise
    try:
        yield materialized
    finally:
        if remote:
            materialized.unlink(missing_ok=True)


def _parse_checksums(checksum_file: Path) -> dict[str, str]:
    """Parse checksums file."""
    expected: dict[str, str] = {}
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean:
            continue
        parts = clean.split("  ", 1)
        if len(parts) != 2:
            continue
        digest, rel_path = parts
        expected[rel_path.strip()] = digest.strip()
    return expected


def _estimate_postgres_plain_sql_restore_bytes(restore_root: Path) -> int:
    """Estimate peak space for the temporary SQL produced by pg_restore.

    Main and audit dumps are restored one after another, so the larger
    PostgreSQL dump determines the temporary-file peak. Non-PostgreSQL dumps
    are copied directly and do not create this extra artifact.
    """
    postgres_dump_sizes = [
        _safe_file_size(restore_root / BACKUP_REQUIRED_PATHS[dump_name])
        for dump_name, config in (
            ("main_dump", DATABASE_CONFIG),
            ("audit_dump", AUDIT_DATABASE_CONFIG),
        )
        if str(config.get("driver") or "").lower().startswith("postgresql")
    ]
    return (
        max(postgres_dump_sizes, default=0)
        * BACKUP_POSTGRES_PLAIN_SQL_EXPANSION_FACTOR
    )


def _existing_capacity_probe_path(path: Path) -> Path:
    """Return the nearest existing path used for filesystem capacity checks."""
    candidate = path.resolve(strict=False)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if not candidate.exists():
        raise RuntimeError(f"No existing parent is available for restore target '{path.name}'")
    return candidate


def _filesystem_capacity_identity(path: Path) -> int:
    """Return a stable identity for the filesystem containing ``path``."""
    return int(_existing_capacity_probe_path(path).stat().st_dev)


def _filesystem_free_bytes(path: Path) -> int:
    """Return currently available bytes on the filesystem containing ``path``."""
    probe_path = _existing_capacity_probe_path(path)
    return max(0, int(shutil.disk_usage(str(probe_path)).free))


def _build_restore_filesystem_checks(
    *,
    restore_workspace: Path,
    postgres_plain_sql_restore_bytes: int,
    app_data_restore_bytes: int,
    app_logs_restore_bytes: int,
    external_file_storage_restore_bytes: int,
    in_place_overhead_bytes: int,
) -> list[dict[str, Any]]:
    """Build phase-aware capacity checks for every restore target filesystem.

    Application data, logs, and external local file storage remain installed
    together, so their requirements are summed when they share a filesystem.
    The temporary PostgreSQL SQL file exists in an earlier phase and is removed
    before mounted-directory replacement begins. The in-place safety backup
    remains present throughout both phases and is therefore included in each.
    """
    components: list[tuple[str, str, Path, int]] = []

    if postgres_plain_sql_restore_bytes > 0:
        components.append(
            (
                "database",
                "postgres_plain_sql",
                restore_workspace,
                postgres_plain_sql_restore_bytes,
            )
        )

    if in_place_overhead_bytes > 0:
        for phase in ("database", "filesystem"):
            components.append(
                (
                    phase,
                    "pre_restore_safety_backup",
                    BACKUP_LOCAL_DIR,
                    in_place_overhead_bytes,
                )
            )

    if app_data_restore_bytes > 0:
        components.append(("filesystem", "application_data", DATA_DIR, app_data_restore_bytes))
    if app_logs_restore_bytes > 0:
        components.append(("filesystem", "application_logs", LOG_DIR, app_logs_restore_bytes))

    external_storage_path = _configured_local_file_storage_path()
    if (
        external_file_storage_restore_bytes > 0
        and external_storage_path is not None
        and not _is_relative_to(external_storage_path, DATA_DIR)
    ):
        components.append(
            (
                "filesystem",
                "external_local_file_storage",
                external_storage_path,
                external_file_storage_restore_bytes,
            )
        )

    # SQLite files survive into the mounted-directory phase, so include their
    # replacement bytes alongside the final filesystem state rather than in a
    # transient database-only phase.
    for label, config, dump_name in (
        ("main_sqlite_database", DATABASE_CONFIG, "main_dump"),
        ("audit_sqlite_database", AUDIT_DATABASE_CONFIG, "audit_dump"),
    ):
        if str(config.get("driver") or "").lower() != "sqlite":
            continue
        url = config.get("url")
        if not url:
            continue
        try:
            sqlite_path = _resolve_sqlite_file(url)
        except Exception:
            continue
        dump_bytes = _safe_file_size(restore_workspace / BACKUP_REQUIRED_PATHS[dump_name])
        if dump_bytes > 0:
            components.append(("filesystem", label, sqlite_path.parent, dump_bytes))

    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for phase, label, path, required_bytes in components:
        filesystem_id = _filesystem_capacity_identity(path)
        group_key = (phase, filesystem_id)
        group = grouped.setdefault(
            group_key,
            {
                "phase": phase,
                "filesystem_id": filesystem_id,
                "probe_path": path,
                "components": {},
                "payload_bytes": 0,
            },
        )
        group["components"][label] = group["components"].get(label, 0) + required_bytes
        group["payload_bytes"] += required_bytes

    checks: list[dict[str, Any]] = []
    for group in grouped.values():
        payload_bytes = int(group["payload_bytes"])
        safety_margin_bytes = max(
            BACKUP_RESTORE_MIN_FREE_BYTES,
            int(payload_bytes * 0.25),
        )
        required_bytes = payload_bytes + safety_margin_bytes
        free_bytes = _filesystem_free_bytes(group["probe_path"])
        checks.append(
            {
                "phase": group["phase"],
                "filesystem_id": group["filesystem_id"],
                "components": group["components"],
                "payload_bytes": payload_bytes,
                "safety_margin_bytes": safety_margin_bytes,
                "required_bytes": required_bytes,
                "free_bytes": free_bytes,
                "ok": free_bytes >= required_bytes,
            }
        )

    return sorted(checks, key=lambda item: (item["phase"], item["filesystem_id"]))


def preflight_backup_archive(archive_path: Path, *, target_mode: str, db: Session) -> dict[str, Any]:
    """Preflight check backup archive."""
    with tempfile.TemporaryDirectory(prefix="omlorix-preflight-") as tmp_dir_raw:
        tmp_dir = Path(tmp_dir_raw)
        try:
            _extract_zstd_archive(archive_path, tmp_dir)
        except BackupArchiveSizeLimitError as exc:
            return {
                "ok": False,
                "reason": exc.reason,
                "limit_bytes": exc.limit_bytes,
                "observed_bytes": exc.observed_bytes,
            }

        missing = [
            rel for rel in BACKUP_REQUIRED_PATHS.values() if not (tmp_dir / rel).exists()
        ]
        if missing:
            return {
                "ok": False,
                "reason": "missing_required_files",
                "missing": missing,
            }

        checksums_expected = _parse_checksums(tmp_dir / BACKUP_REQUIRED_PATHS["checksums"])
        mismatches: list[dict[str, str]] = []

        for rel_path, expected_hash in checksums_expected.items():
            file_path = tmp_dir / rel_path
            if not file_path.exists():
                mismatches.append({"path": rel_path, "error": "missing"})
                continue
            actual_hash = _sha256_file(file_path)
            if actual_hash != expected_hash:
                mismatches.append({"path": rel_path, "expected": expected_hash, "actual": actual_hash})

        if mismatches:
            return {
                "ok": False,
                "reason": "checksum_mismatch",
                "mismatches": mismatches,
            }

        try:
            manifest = json.loads((tmp_dir / BACKUP_REQUIRED_PATHS["manifest"]).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "reason": "manifest_parse_failed",
                "detail": str(exc),
            }

        # Match the exact Omlorix format contract before checking its probe.
        # Unknown or future identifiers remain rejected instead of being
        # interpreted as the current payload layout.
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != BACKUP_EXPORT_FORMAT
            or not matches_export_version(
                manifest.get("export_version"), BACKUP_EXPORT_VERSION
            )
        ):
            return {
                "ok": False,
                "reason": "unsupported_export_version",
                "expected_export_version": BACKUP_EXPORT_VERSION,
            }

        probe_cipher = (tmp_dir / BACKUP_REQUIRED_PATHS["crypto_probe"]).read_text(encoding="utf-8").strip()
        try:
            probe_plain = decrypt_value(probe_cipher)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "reason": "encryption_key_mismatch",
                "detail": str(exc),
            }

        if probe_plain != BACKUP_CRYPTO_PROBE:
            return {
                "ok": False,
                "reason": "encryption_key_mismatch",
                "detail": "Probe decryption did not match expected payload",
            }

        protected_data_names = {
            path.name for path in _erasure_ledger_data_entries()
        }
        try:
            protected_data_collisions = _protected_data_tar_collisions(
                tmp_dir / BACKUP_REQUIRED_PATHS["app_data_tar"],
                protected_data_names,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "reason": "payload_tar_parse_failed",
                "detail": str(exc),
            }
        if protected_data_collisions:
            return {
                "ok": False,
                "reason": "protected_data_collision",
                "entries": protected_data_collisions,
            }

        target_is_empty = is_target_instance_empty(db)

        if target_mode == "empty" and not target_is_empty:
            return {
                "ok": False,
                "reason": "target_not_empty",
                "target_is_empty": target_is_empty,
            }

        workspace_bytes = _directory_size_bytes(tmp_dir)
        try:
            app_data_restore_bytes = _estimate_tar_restore_bytes(tmp_dir / BACKUP_REQUIRED_PATHS["app_data_tar"])
            app_logs_restore_bytes = _estimate_tar_restore_bytes(tmp_dir / BACKUP_REQUIRED_PATHS["app_logs_tar"])
            external_file_storage_restore_bytes = _estimate_tar_restore_bytes(
                tmp_dir / BACKUP_REQUIRED_PATHS["app_data_tar"],
                member_root="userFiles",
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "reason": "payload_tar_parse_failed",
                "detail": str(exc),
            }

        for payload_name, payload_bytes in (
            ("app_data_tar", app_data_restore_bytes),
            ("app_logs_tar", app_logs_restore_bytes),
        ):
            if payload_bytes > BACKUP_RESTORE_MAX_EXTRACTED_BYTES:
                return {
                    "ok": False,
                    "reason": "archive_extracted_size_exceeded",
                    "payload": payload_name,
                    "limit_bytes": BACKUP_RESTORE_MAX_EXTRACTED_BYTES,
                    "observed_bytes": payload_bytes,
                }

        postgres_plain_sql_restore_bytes = _estimate_postgres_plain_sql_restore_bytes(tmp_dir)
        estimated_restore_peak_bytes = (
            workspace_bytes
            + app_data_restore_bytes
            + app_logs_restore_bytes
            + postgres_plain_sql_restore_bytes
        )
        safety_margin_bytes = max(BACKUP_RESTORE_MIN_FREE_BYTES, int(estimated_restore_peak_bytes * 0.25))
        archive_size = _safe_file_size(archive_path)

        in_place_overhead_bytes = 0
        in_place_pre_restore_archive_estimate_bytes = 0
        in_place_temp_estimate_bytes = 0
        if target_mode == "in_place":
            current_app_payload_bytes = _directory_size_bytes(DATA_DIR) + _directory_size_bytes(LOG_DIR)
            current_db_payload_bytes = _estimate_database_snapshot_bytes(DATABASE_CONFIG) + _estimate_database_snapshot_bytes(AUDIT_DATABASE_CONFIG)

            in_place_pre_restore_archive_estimate_bytes = max(
                archive_size,
                app_data_restore_bytes + app_logs_restore_bytes,
                current_app_payload_bytes + current_db_payload_bytes,
            )
            in_place_temp_estimate_bytes = max(workspace_bytes, archive_size)
            in_place_overhead_bytes = (
                in_place_pre_restore_archive_estimate_bytes + in_place_temp_estimate_bytes
            )

        filesystem_checks = _build_restore_filesystem_checks(
            restore_workspace=tmp_dir,
            postgres_plain_sql_restore_bytes=postgres_plain_sql_restore_bytes,
            app_data_restore_bytes=app_data_restore_bytes,
            app_logs_restore_bytes=app_logs_restore_bytes,
            external_file_storage_restore_bytes=external_file_storage_restore_bytes,
            in_place_overhead_bytes=in_place_overhead_bytes,
        )
        has_space = all(check["ok"] for check in filesystem_checks)
        effective_free_bytes = min(
            (int(check["free_bytes"]) for check in filesystem_checks),
            default=_filesystem_free_bytes(tmp_dir),
        )
        required_free_bytes = max(
            (int(check["required_bytes"]) for check in filesystem_checks),
            default=0,
        )

        if not has_space:
            return {
                "ok": False,
                "reason": "insufficient_disk_space",
                "target_is_empty": target_is_empty,
                "archive_size": archive_size,
                "disk_free": effective_free_bytes,
                "effective_free_bytes": effective_free_bytes,
                "required_free_bytes": required_free_bytes,
                "filesystem_checks": filesystem_checks,
                "estimated_restore_peak_bytes": estimated_restore_peak_bytes,
                "workspace_bytes": workspace_bytes,
                "app_data_restore_bytes": app_data_restore_bytes,
                "app_logs_restore_bytes": app_logs_restore_bytes,
                "external_file_storage_restore_bytes": external_file_storage_restore_bytes,
                "postgres_plain_sql_restore_bytes": postgres_plain_sql_restore_bytes,
                "safety_margin_bytes": safety_margin_bytes,
                "in_place_overhead_bytes": in_place_overhead_bytes,
                "in_place_pre_restore_archive_estimate_bytes": in_place_pre_restore_archive_estimate_bytes,
                "in_place_temp_estimate_bytes": in_place_temp_estimate_bytes,
            }

        return {
            "ok": True,
            "target_is_empty": target_is_empty,
            "archive_size": archive_size,
            "disk_free": effective_free_bytes,
            "effective_free_bytes": effective_free_bytes,
            "required_free_bytes": required_free_bytes,
            "filesystem_checks": filesystem_checks,
            "estimated_restore_peak_bytes": estimated_restore_peak_bytes,
            "workspace_bytes": workspace_bytes,
            "app_data_restore_bytes": app_data_restore_bytes,
            "app_logs_restore_bytes": app_logs_restore_bytes,
            "external_file_storage_restore_bytes": external_file_storage_restore_bytes,
            "postgres_plain_sql_restore_bytes": postgres_plain_sql_restore_bytes,
            "safety_margin_bytes": safety_margin_bytes,
            "in_place_overhead_bytes": in_place_overhead_bytes,
            "in_place_pre_restore_archive_estimate_bytes": in_place_pre_restore_archive_estimate_bytes,
            "in_place_temp_estimate_bytes": in_place_temp_estimate_bytes,
            "disk_space_sufficient": has_space,
            "manifest": manifest,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


def is_target_instance_empty(db: Session) -> bool:
    """Return whether the target has no meaningful application data.

    PostgreSQL stores Omlorix tables in ``DATABASE_SCHEMA`` rather than in the
    inspector's default ``public`` schema. Inspecting and querying the explicit
    schema is therefore a safety boundary: a populated target must never be
    mistaken for an empty target before ``pg_restore --clean``.
    """
    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    inspector = inspect(bind)
    is_postgres = str(DATABASE_CONFIG.get("driver") or "").lower().startswith("postgresql")
    schema_name = DATABASE_SCHEMA if is_postgres else None
    available_tables = set(inspector.get_table_names(schema=schema_name))
    meaningful_tables = [
        "users",
        "chats",
        "chat_messages",
        "files",
        "projects",
        "notes",
        "automations",
        "llm_provider",
    ]

    for table_name in meaningful_tables:
        if table_name not in available_tables:
            continue
        # Both the schema and table names are fixed, validated application
        # constants. Explicit quoting keeps the check independent from each
        # connection's search_path and prevents inspecting the wrong schema.
        qualified_table = (
            f'"{schema_name}"."{table_name}"'
            if schema_name
            else f'"{table_name}"'
        )
        result = db.execute(text(f"SELECT COUNT(*) FROM {qualified_table}")).scalar()
        if int(result or 0) > 0:
            return False

    return True


def _run_migrations() -> None:
    """Run both migration trees after archived schemas have been restored.

    Alembic configuration belongs to the backend directory. In a source
    checkout that directory is ``backend/``; in the container image it is
    ``/app``. Using absolute configuration paths avoids depending on the
    repository-level ``PROJECT_ROOT``, which resolves to ``/`` in the image.
    """
    config_paths = (
        BACKEND_DIR / "alembic_main.ini",
        BACKEND_DIR / "alembic_audit.ini",
    )
    for config_path in config_paths:
        if not config_path.is_file():
            raise RuntimeError(f"Missing Alembic configuration: {config_path}")

    for config_path in config_paths:
        subprocess.run(
            ["alembic", "-c", str(config_path), "upgrade", "head"],
            check=True,
            cwd=str(BACKEND_DIR),
        )
    invalidate_settings_cache()


def _reconcile_restored_email_security_state() -> dict[str, int]:
    """Invalidate replayable auth/email state before services restart."""

    from app.email.models import reconcile_email_security_after_restore

    reconciliation_db = SessionLocal()
    try:
        return reconcile_email_security_after_restore(reconciliation_db)
    except Exception:
        reconciliation_db.rollback()
        raise
    finally:
        reconciliation_db.close()


def _reconcile_restored_worker_state() -> dict[str, int]:
    """Invalidate restored queue snapshots and their ephemeral staging."""

    from app.workers.operations import clear_operations_staging_after_restore
    from app.workers.media import clear_media_staging_after_restore
    from app.workers.rendering import clear_rendering_staging_after_restore
    from app.workers.restore import reconcile_worker_state_after_restore

    reconciliation_db = SessionLocal()
    try:
        result = reconcile_worker_state_after_restore(reconciliation_db)
        result["staging_files"] = clear_operations_staging_after_restore(
            db=reconciliation_db
        )
        result["staging_files"] += clear_media_staging_after_restore()
        result["staging_files"] += clear_rendering_staging_after_restore()
        return result
    except Exception:
        reconciliation_db.rollback()
        raise
    finally:
        reconciliation_db.close()


def _copy_directory_contents(source_dir: Path, target_dir: Path) -> None:
    """Copy directory contents from source to target."""
    if not source_dir.exists():
        return
    for child in source_dir.iterdir():
        destination = target_dir / child.name
        if child.is_dir():
            shutil.copytree(child, destination)
        elif child.is_file():
            shutil.copy2(child, destination)


def _replace_directory_contents_from_dir(
    source_dir: Path,
    target_dir: Path,
    backup_tag: str,
) -> Path | None:
    """Replace a possibly mounted target from a prepared source directory."""
    target_dir.mkdir(parents=True, exist_ok=True)
    restore_tmp = target_dir / f".restore_tmp_{backup_tag}"
    backup_dir = target_dir / f".pre_restore_{backup_tag}"

    if restore_tmp.exists():
        shutil.rmtree(restore_tmp, ignore_errors=True)
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    restore_tmp.mkdir(parents=True, exist_ok=True)
    try:
        _copy_directory_contents(source_dir, restore_tmp)
    except Exception:
        shutil.rmtree(restore_tmp, ignore_errors=True)
        raise
    return _install_staged_directory_contents(restore_tmp, target_dir, backup_dir)


def _restore_external_local_file_storage_from_data_dir(backup_tag: str) -> Path | None:
    """Restore configured external local file storage from app-data backup contents."""
    local_storage_path = _configured_local_file_storage_path()
    if local_storage_path is None or _is_relative_to(local_storage_path, DATA_DIR):
        return None
    restored_user_files = DATA_DIR / "userFiles"
    if not restored_user_files.exists():
        return None
    return _replace_directory_contents_from_dir(
        restored_user_files,
        local_storage_path,
        backup_tag,
    )


def _rollback_replaced_directory_contents(
    target_dir: Path,
    backup_dir: Path,
    *,
    preserved_names: set[str] | None = None,
) -> None:
    """Restore directory contents while preserving the mounted directory root."""
    target_dir.mkdir(parents=True, exist_ok=True)
    preserved_names = preserved_names or set()
    for child in list(target_dir.iterdir()):
        if child == backup_dir:
            continue
        if child.name in preserved_names:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    for child in list(backup_dir.iterdir()):
        shutil.move(str(child), str(target_dir / child.name))
    shutil.rmtree(backup_dir, ignore_errors=True)


def _install_staged_directory_contents(
    restore_tmp: Path,
    target_dir: Path,
    backup_dir: Path,
    *,
    preserved_names: set[str] | None = None,
) -> Path:
    """Atomically-as-practical exchange mounted-directory child entries.

    The target directory inode is deliberately preserved because Docker bind
    mounts and volume roots cannot be renamed. If any child move fails, only
    newly installed entries are removed and all originals already moved into
    the in-volume rollback directory are put back.
    """
    preserved_names = preserved_names or set()
    protected_collisions = sorted(
        child.name for child in restore_tmp.iterdir() if child.name in preserved_names
    )
    if protected_collisions:
        # Protected restore-resistant state is never archive-owned. Reject the
        # archive rather than skipping or replacing those entries, and do so
        # before moving any live target child.
        shutil.rmtree(restore_tmp, ignore_errors=True)
        raise RuntimeError(
            "Restore archive contains protected data entries: "
            + ", ".join(protected_collisions)
        )

    backup_dir.mkdir(parents=True, exist_ok=True)
    moved_restored_names: list[str] = []
    try:
        for child in list(target_dir.iterdir()):
            if child in {restore_tmp, backup_dir}:
                continue
            if child.name in preserved_names:
                continue
            shutil.move(str(child), str(backup_dir / child.name))

        for child in list(restore_tmp.iterdir()):
            child_name = child.name
            shutil.move(str(child), str(target_dir / child_name))
            moved_restored_names.append(child_name)
    except Exception:
        # Entries never moved out of the target remain untouched. This makes
        # recovery safe even if failure occurs midway through the first loop.
        for child_name in moved_restored_names:
            restored_child = target_dir / child_name
            if restored_child.is_dir():
                shutil.rmtree(restored_child, ignore_errors=True)
            else:
                restored_child.unlink(missing_ok=True)
        for original_child in list(backup_dir.iterdir()):
            shutil.move(str(original_child), str(target_dir / original_child.name))
        shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.rmtree(restore_tmp, ignore_errors=True)
        raise

    shutil.rmtree(restore_tmp, ignore_errors=True)
    return backup_dir


def _swap_dir_from_tar(
    tar_path: Path,
    target_dir: Path,
    backup_tag: str,
    *,
    preserved_names: set[str] | None = None,
) -> Path | None:
    """Replace mounted-directory contents and retain an in-volume rollback copy.

    Docker volume and bind-mount roots cannot be renamed (Linux returns
    ``EBUSY``). Stage both the incoming data and the rollback copy *inside* the
    mounted directory, then move child entries on the same filesystem. The
    out-of-process restore runner is the only application container active
    while this executes, so no server process can create new children during
    the swap.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    restore_tmp = target_dir / f".restore_tmp_{backup_tag}"
    backup_dir = target_dir / f".pre_restore_{backup_tag}"

    if restore_tmp.exists():
        shutil.rmtree(restore_tmp, ignore_errors=True)
    restore_tmp.mkdir(parents=True, exist_ok=True)
    _extract_directory_tar(tar_path, restore_tmp)

    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    return _install_staged_directory_contents(
        restore_tmp,
        target_dir,
        backup_dir,
        preserved_names=preserved_names,
    )


def _restore_from_archive(restore_job: RestoreJobContext, archive_path: Path) -> dict[str, Any]:
    """Restore from backup archive."""
    with tempfile.TemporaryDirectory(prefix="omlorix-restore-") as tmp_raw:
        tmp_dir = Path(tmp_raw)
        _extract_zstd_archive(archive_path, tmp_dir)

        main_dump = tmp_dir / BACKUP_REQUIRED_PATHS["main_dump"]
        audit_dump = tmp_dir / BACKUP_REQUIRED_PATHS["audit_dump"]
        app_data_tar = tmp_dir / BACKUP_REQUIRED_PATHS["app_data_tar"]
        app_logs_tar = tmp_dir / BACKUP_REQUIRED_PATHS["app_logs_tar"]

        backup_tag = restore_job.id[:8]
        rollback_data_dir: Path | None = None
        rollback_log_dir: Path | None = None
        rollback_file_storage_dir: Path | None = None
        preserved_data_names = {
            path.name for path in _erasure_ledger_data_entries()
        }
        protected_data_collisions = _protected_data_tar_collisions(
            app_data_tar,
            preserved_data_names,
        )
        if protected_data_collisions:
            raise RuntimeError(
                "Restore archive contains protected data entries: "
                + ", ".join(protected_data_collisions)
            )

        try:
            # This fsync-backed marker is outside SQL backups and precedes the
            # first schema replacement. A restored checkpoint can therefore
            # never suppress privacy reconciliation after a process crash.
            mark_restore_erasure_reconciliation_required()
            _restore_database(
                DATABASE_CONFIG,
                main_dump,
                schema_names=[DATABASE_SCHEMA, LOGS_DATABASE_SCHEMA],
                required_extensions={"pg_trgm": DATABASE_SCHEMA},
            )

            _restore_database(
                AUDIT_DATABASE_CONFIG,
                audit_dump,
                schema_names=[AUDIT_DATABASE_SCHEMA],
            )

            rollback_data_dir = _swap_dir_from_tar(
                app_data_tar,
                DATA_DIR,
                backup_tag,
                preserved_names=preserved_data_names,
            )
            rollback_file_storage_dir = _restore_external_local_file_storage_from_data_dir(
                backup_tag
            )
            rollback_log_dir = _swap_dir_from_tar(app_logs_tar, LOG_DIR, backup_tag)

            _run_migrations()
            worker_reconciliation = _reconcile_restored_worker_state()
            erasure_reconciliation = reconcile_completed_user_erasures_after_restore()
            from app.workers.events import reconcile_pending_audit_erasures

            audit_erasure_handoffs = 0
            while True:
                reconciled = reconcile_pending_audit_erasures()
                audit_erasure_handoffs += reconciled
                if reconciled == 0:
                    break
            worker_reconciliation["audit_erasure_handoffs"] = (
                audit_erasure_handoffs
            )
            email_security_reconciliation = _reconcile_restored_email_security_state()

            if rollback_data_dir and rollback_data_dir.exists():
                shutil.rmtree(rollback_data_dir, ignore_errors=True)
            if rollback_log_dir and rollback_log_dir.exists():
                shutil.rmtree(rollback_log_dir, ignore_errors=True)
            if rollback_file_storage_dir and rollback_file_storage_dir.exists():
                shutil.rmtree(rollback_file_storage_dir, ignore_errors=True)

            return {
                "status": "restored",
                "restored_at": datetime.now(timezone.utc).isoformat(),
                "worker_reconciliation": worker_reconciliation,
                "erasure_reconciliation": erasure_reconciliation,
                "email_security_reconciliation": email_security_reconciliation,
            }
        except Exception:
            local_storage_path = _configured_local_file_storage_path()
            if (
                rollback_file_storage_dir
                and rollback_file_storage_dir.exists()
                and local_storage_path is not None
                and not _is_relative_to(local_storage_path, DATA_DIR)
            ):
                _rollback_replaced_directory_contents(local_storage_path, rollback_file_storage_dir)
            if rollback_data_dir and rollback_data_dir.exists():
                _rollback_replaced_directory_contents(
                    DATA_DIR,
                    rollback_data_dir,
                    preserved_names=preserved_data_names,
                )
            if rollback_log_dir and rollback_log_dir.exists():
                _rollback_replaced_directory_contents(LOG_DIR, rollback_log_dir)
            raise


def run_restore_job_sync(restore_job_id: str) -> RestoreJob:
    """Run restore job synchronously."""
    ensure_backup_directories()
    db = SessionLocal()
    try:
        return _run_restore_job_with_session(db, restore_job_id)
    finally:
        db.close()


def enqueue_restore_job(restore_job_id: str) -> DurableWorkerJob:
    """Durably enqueue a restore for the dedicated operations worker."""
    ensure_backup_directories()
    db = SessionLocal()
    try:
        restore_job = get_restore_job(db, str(restore_job_id))
        if restore_job is None:
            raise RuntimeError("Restore job not found")
        try:
            return enqueue_worker_job(
                db,
                queue=QUEUE_OPERATIONS,
                kind="restore",
                user_id=restore_job.requested_by_user_id,
                payload={"restore_job_id": str(restore_job_id)},
                idempotency_key=f"restore:{restore_job_id}",
                # An interrupted restore is deliberately not replayed automatically.
                # Its pre-restore artifact remains available for operator recovery.
                max_attempts=1,
                priority=0,
                commit=True,
            )
        except Exception:
            db.rollback()
            update_restore_job_status(
                db,
                restore_job_id=str(restore_job_id),
                status="failed",
                error=None,
            )
            raise
    finally:
        db.close()


def _create_pre_restore_backup(db: Session, requested_by_user_id: str | None) -> BackupJob:
    """Create pre-restore backup."""
    backup_job = create_backup_job(
        db,
        trigger_type="pre_restore",
        destination_id=None,
        requested_by_user_id=requested_by_user_id,
        options={"pre_restore": True, "encryption_enabled": True},
    )
    return _run_backup_job_with_session(db, backup_job.id)


def _require_verified_pre_restore_artifact(db: Session, backup_job: BackupJob) -> str:
    """Return the verified pre-restore artifact URI, or fail before live restore."""
    if backup_job.status != "success":
        raise RuntimeError("In-place restore requires a successful pre-restore backup before mutating live state.")

    artifacts = list_backup_artifacts(db, backup_job.id)
    if not artifacts:
        raise RuntimeError("In-place restore requires a pre-restore backup artifact before mutating live state.")

    artifact = artifacts[0]
    with _verification_source_artifact(
        db,
        artifact.storage_uri,
        f"{backup_job.id}-pre-restore-verify",
    ) as artifact_path:
        actual_hash = _sha256_file(artifact_path)
    if actual_hash != artifact.checksum_sha256:
        raise RuntimeError("In-place restore pre-restore backup artifact checksum verification failed.")

    if artifact.verified_at is None:
        mark_backup_artifact_verified(db, artifact.id)

    return artifact.storage_uri


def _manifest_datetime(manifest: dict[str, Any], key: str) -> datetime | None:
    """Parse one optional ISO timestamp from a trusted, validated manifest."""
    raw = manifest.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _safe_recovery_backup_job_id(value: str) -> str:
    """Return a manifest job ID only when it is one portable path component."""
    candidate = str(value or "").strip()
    if not BACKUP_RECOVERY_JOB_ID_RE.fullmatch(candidate):
        raise RuntimeError("Backup manifest job ID is not a safe path component")
    return candidate


def _durable_recovery_artifact_uri(
    source_path: Path,
    *,
    backup_job_id: str,
    checksum_sha256: str,
) -> str:
    """Return a local URI that remains usable after the restore runner exits.

    A CLI file restore bind-mounts its input at ``/restore/input`` and remote
    sources are downloaded into a cache.  Neither location is an appropriate
    long-term catalog target.  The backup volume is deliberately outside the
    application data restored from the archive, so copy such sources there.
    Existing local backup artifacts already below the archive root can be
    reused without consuming a second archive's worth of disk space.
    """
    safe_job_id = _safe_recovery_backup_job_id(backup_job_id)
    source = Path(source_path).resolve(strict=True)
    archive_root = BACKUP_ARCHIVE_DIR.resolve()
    try:
        relative = source.relative_to(archive_root)
    except ValueError:
        relative = None

    if relative is not None:
        return f"local://{relative.as_posix()}"

    extension = (
        BACKUP_ENCRYPTED_ARCHIVE_SUFFIX
        if _archive_looks_encrypted(source)
        else BACKUP_ARCHIVE_SUFFIX
    )
    recovery_relative = Path("recovery") / (
        f"{safe_job_id}-{checksum_sha256[:16]}{extension}"
    )
    # Resolve the complete destination before creating or replacing anything.
    # The second check also rejects an operator-created `recovery` symlink that
    # points outside the configured archive root.
    target = (archive_root / recovery_relative).resolve(strict=False)
    if not _is_relative_to(target, archive_root):
        raise RuntimeError("Recovery artifact destination escapes the backup archive directory")
    target.parent.mkdir(parents=True, exist_ok=True)

    # A deterministic checksum-qualified name makes repeated restores
    # idempotent.  Never trust an existing file solely because its name
    # matches: verify it before placing it back in the catalog.
    if not target.exists() or _sha256_file(target) != checksum_sha256:
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            if _sha256_file(temporary) != checksum_sha256:
                raise RuntimeError("Preserved recovery artifact checksum verification failed")
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    return f"local://{recovery_relative.as_posix()}"


def _snapshot_backup_catalog_for_recovery(
    db: Session,
    *,
    source_uri: str,
    source_path: Path,
    manifest: dict[str, Any],
) -> BackupCatalogRecoveryContext:
    """Capture and durably materialize a backup catalog entry before restore.

    The archive manifest is the authoritative identity when an operator
    restores a copied file whose current database no longer has a usable
    catalog entry.  Existing rows still contribute operator metadata and stable
    IDs when available.
    """
    raw_backup_job_id = str(manifest.get("backup_job_id") or "").strip()
    if not raw_backup_job_id:
        raise RuntimeError("Backup manifest does not contain a backup job ID")
    backup_job_id = _safe_recovery_backup_job_id(raw_backup_job_id)

    checksum = _sha256_file(source_path)
    bytes_count = source_path.stat().st_size
    now = datetime.now(timezone.utc)
    generated_at = _manifest_datetime(manifest, "generated_at") or now
    existing_job = get_backup_job(db, backup_job_id)
    existing_artifacts = list_backup_artifacts(db, backup_job_id) if existing_job else []
    matching_artifacts = [
        artifact
        for artifact in existing_artifacts
        if artifact.checksum_sha256 == checksum and int(artifact.bytes) == bytes_count
    ]
    matching_artifact = next(
        (
            artifact
            for artifact in matching_artifacts
            if artifact.storage_uri == source_uri
        ),
        matching_artifacts[0] if matching_artifacts else None,
    )

    durable_uri = _durable_recovery_artifact_uri(
        source_path,
        backup_job_id=backup_job_id,
        checksum_sha256=checksum,
    )
    artifact_id = (
        str(matching_artifact.id)
        if matching_artifact is not None
        else str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"omlorix-backup-artifact:{backup_job_id}:{checksum}",
            )
        )
    )

    trigger_type = str(
        getattr(existing_job, "trigger_type", None)
        or manifest.get("trigger_type")
        or "manual"
    ).strip().lower()
    if trigger_type not in {"manual", "scheduled", "pre_restore"}:
        trigger_type = "manual"

    existing_options = getattr(existing_job, "options", None)
    options = dict(existing_options) if isinstance(existing_options, dict) else {}
    created_at = getattr(existing_job, "created_at", None) or generated_at
    started_at = getattr(existing_job, "started_at", None) or created_at
    finished_at = getattr(existing_job, "finished_at", None) or generated_at

    return BackupCatalogRecoveryContext(
        id=backup_job_id,
        trigger_type=trigger_type,
        manifest_json=dict(manifest),
        options=options,
        size_bytes=bytes_count,
        requested_by_user_id=getattr(existing_job, "requested_by_user_id", None),
        started_at=started_at,
        finished_at=finished_at,
        created_at=created_at,
        artifact=BackupArtifactRecoveryContext(
            id=artifact_id,
            storage_uri=durable_uri,
            checksum_sha256=checksum,
            bytes=bytes_count,
            verified_at=now,
            expires_at=getattr(matching_artifact, "expires_at", None),
            created_at=getattr(matching_artifact, "created_at", None) or generated_at,
        ),
    )


def _reconcile_backup_catalog_after_restore(
    db: Session,
    contexts: list[BackupCatalogRecoveryContext],
) -> None:
    """Upsert recovery-critical job/artifact rows after schema replacement."""
    for context in contexts:
        job = get_backup_job(db, context.id)
        if job is None:
            job = BackupJob(id=context.id)
            db.add(job)

        # The recovery copy is local and does not depend on a destination row
        # from either the pre-restore or archived database state.
        job.trigger_type = context.trigger_type
        job.status = "success"
        job.error = None
        job.manifest_json = context.manifest_json
        job.options = context.options
        job.size_bytes = context.size_bytes
        job.requested_by_user_id = context.requested_by_user_id
        job.destination_id = None
        job.started_at = context.started_at
        job.finished_at = context.finished_at
        job.created_at = context.created_at
        job.updated_at = datetime.now(timezone.utc)
        db.flush()

        # A backup operation publishes exactly one immutable archive.  Any
        # artifact rows for this job that came from the restored database are
        # necessarily an older or incomplete catalog view and could sort ahead
        # of the preserved source. Remove only their metadata (not storage
        # objects) so job-ID restore and verify deterministically select the
        # checksum-verified recovery copy.
        (
            db.query(BackupArtifact)
            .filter(
                BackupArtifact.backup_job_id == context.id,
                BackupArtifact.id != context.artifact.id,
            )
            .delete(synchronize_session=False)
        )

        artifact = get_backup_artifact(db, context.artifact.id)
        if artifact is None:
            artifact = BackupArtifact(id=context.artifact.id)
            db.add(artifact)
        artifact.backup_job_id = context.id
        artifact.storage_uri = context.artifact.storage_uri
        artifact.checksum_sha256 = context.artifact.checksum_sha256
        artifact.bytes = context.artifact.bytes
        artifact.verified_at = context.artifact.verified_at
        artifact.expires_at = context.artifact.expires_at
        artifact.created_at = context.artifact.created_at

    db.commit()


def _snapshot_restore_job(restore_job: RestoreJob) -> RestoreJobContext:
    """Detach the operation metadata required after the live schema is replaced."""
    return RestoreJobContext(
        id=str(restore_job.id),
        source_uri=str(restore_job.source_uri),
        target_mode=str(restore_job.target_mode),
        requested_by_user_id=restore_job.requested_by_user_id,
        confirmed_by_user_id=restore_job.confirmed_by_user_id,
        options=dict(restore_job.options) if isinstance(restore_job.options, dict) else {},
        created_at=restore_job.created_at,
        started_at=restore_job.started_at,
    )


def _release_restore_session(db: Session) -> None:
    """Release every ORM transaction and pooled connection before schema DDL."""
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        # A previous database error can already have invalidated the session.
        # Closing and disposing remain mandatory so no lock-holding connection
        # survives into pg_restore.
        logger.exception("Failed rolling back restore worker session during release")
    finally:
        db.close()

    # Closing the worker session releases its current AccessShare locks. Pool
    # disposal also closes idle connections opened by earlier requests in this
    # process; the PostgreSQL coordinator handles active and remote-process
    # sessions while the restore subprocess is running.
    engine.dispose()
    if audit_engine is not engine:
        audit_engine.dispose()


def _ensure_restore_tracking_row(db: Session, context: RestoreJobContext) -> RestoreJob:
    """Ensure the current operation exists after archive data replaced its table."""
    row = get_restore_job(db, context.id)
    if row is None:
        row = RestoreJob(id=context.id)
        db.add(row)

    row.source_uri = context.source_uri
    row.target_mode = context.target_mode
    row.status = "running"
    row.error = None
    row.preflight_json = None
    row.options = context.options
    row.requested_by_user_id = context.requested_by_user_id
    row.confirmed_by_user_id = context.confirmed_by_user_id
    row.started_at = context.started_at
    row.finished_at = None
    row.created_at = context.created_at
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


def _record_restore_terminal_status(
    context: RestoreJobContext,
    *,
    status: str,
    error: str | None,
    preflight_json: dict[str, Any] | None,
    backup_catalog_contexts: list[BackupCatalogRecoveryContext] | None = None,
) -> RestoreJob:
    """Persist final restore status through a fresh post-restore connection."""
    tracking_db = SessionLocal()
    try:
        _reconcile_backup_catalog_after_restore(
            tracking_db,
            backup_catalog_contexts or [],
        )
        _ensure_restore_tracking_row(tracking_db, context)
        row = update_restore_job_status(
            tracking_db,
            restore_job_id=context.id,
            status=status,
            error=error,
            preflight_json=preflight_json,
        )
        tracking_db.expunge(row)
        return row
    finally:
        tracking_db.close()


def _run_restore_job_with_session(db: Session, restore_job_id: str) -> RestoreJob:
    """Run restore job with database session."""
    restore_job = get_restore_job(db, restore_job_id)
    if not restore_job:
        raise RuntimeError("Restore job not found")

    update_restore_job_status(db, restore_job_id=restore_job_id, status="running", error=None)
    restore_job = get_restore_job(db, restore_job_id)
    if not restore_job:
        raise RuntimeError("Restore job not found after status update")
    restore_context = _snapshot_restore_job(restore_job)

    preflight: dict[str, Any] | None = None
    pre_restore_uri: str | None = None
    source_catalog_context: BackupCatalogRecoveryContext | None = None
    pre_restore_catalog_context: BackupCatalogRecoveryContext | None = None
    session_released = False
    restore_started = False

    try:
        with distributed_lock(RESTORE_JOB_LOCK_NAME, BACKUP_WRITE_FREEZE_MAX_SECONDS + 3600):
            source_path = _materialize_source_artifact(db, restore_context.source_uri, restore_context.id)
            with _prepare_archive_for_restore(source_path) as prepared_source_path:
                preflight = preflight_backup_archive(
                    prepared_source_path,
                    target_mode=restore_context.target_mode,
                    db=db,
                )
                if not preflight.get("ok"):
                    preflight["recovery"] = {
                        "state": "not_started",
                        "safe_to_restart": True,
                    }
                    update_restore_job_status(
                        db,
                        restore_job_id=restore_job_id,
                        status="failed",
                        error=f"Preflight failed: {preflight.get('reason')}",
                        preflight_json=preflight,
                    )
                    failed_job = get_restore_job(db, restore_job_id)
                    if failed_job is None:
                        raise RuntimeError("Restore job not found after preflight failure")
                    return failed_job

                manifest = preflight.get("manifest")
                if not isinstance(manifest, dict):
                    raise RuntimeError("Backup preflight did not return a valid manifest")
                source_catalog_context = _snapshot_backup_catalog_for_recovery(
                    db,
                    source_uri=restore_context.source_uri,
                    source_path=source_path,
                    manifest=manifest,
                )

                if restore_context.target_mode == "in_place":
                    pre_job = _create_pre_restore_backup(db, restore_context.requested_by_user_id)
                    pre_restore_uri = _require_verified_pre_restore_artifact(db, pre_job)
                    pre_restore_artifacts = list_backup_artifacts(db, pre_job.id)
                    if not pre_restore_artifacts:
                        raise RuntimeError(
                            "In-place restore safety backup lost its artifact before catalog preservation."
                        )
                    pre_restore_path = _materialize_source_artifact(
                        db,
                        pre_restore_uri,
                        f"{pre_job.id}-catalog-preserve",
                    )
                    pre_restore_manifest = (
                        dict(pre_job.manifest_json)
                        if isinstance(pre_job.manifest_json, dict)
                        else {}
                    )
                    pre_restore_catalog_context = _snapshot_backup_catalog_for_recovery(
                        db,
                        source_uri=pre_restore_uri,
                        source_path=pre_restore_path,
                        manifest=pre_restore_manifest,
                    )

                # Preflight and the optional safety backup both use the job
                # session. They leave SELECT transactions open, so release the
                # session and every idle pool connection before pg_restore asks
                # PostgreSQL for AccessExclusive locks.
                _release_restore_session(db)
                session_released = True

                activate_write_freeze(reason="restore", ttl_seconds=max(BACKUP_WRITE_FREEZE_MAX_SECONDS, 3600))
                try:
                    # From this point an interrupted process may have replaced
                    # database or filesystem state. The launcher may restart
                    # automatically only if the terminal record later confirms
                    # that rollback completed.
                    restore_started = True
                    restore_result = _restore_from_archive(restore_context, prepared_source_path)
                finally:
                    deactivate_write_freeze()

                preflight["restore_result"] = restore_result
                preflight["pre_restore_uri"] = pre_restore_uri
                preflight["recovery"] = {
                    "state": "restored",
                    "safe_to_restart": True,
                }
                return _record_restore_terminal_status(
                    restore_context,
                    status="success",
                    error=None,
                    preflight_json=preflight,
                    backup_catalog_contexts=[
                        context
                        for context in (
                            source_catalog_context,
                            pre_restore_catalog_context,
                        )
                        if context is not None
                    ],
                )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Restore job %s failed", restore_job_id)
        rollback_result: dict[str, Any] | None = None

        if not session_released:
            _release_restore_session(db)
            session_released = True

        if restore_started and restore_context.target_mode == "in_place" and pre_restore_uri:
            rollback_result = {"attempted": True, "source_uri": pre_restore_uri}
            try:
                rollback_db = SessionLocal()
                try:
                    rollback_path = _materialize_source_artifact(
                        rollback_db,
                        pre_restore_uri,
                        f"{restore_context.id}-rollback",
                    )
                finally:
                    _release_restore_session(rollback_db)
                with _prepare_archive_for_restore(rollback_path) as prepared_rollback_path:
                    activate_write_freeze(
                        reason="restore-rollback",
                        ttl_seconds=max(BACKUP_WRITE_FREEZE_MAX_SECONDS, 3600),
                    )
                    try:
                        rollback_details = _restore_from_archive(
                            restore_context,
                            prepared_rollback_path,
                        )
                    finally:
                        deactivate_write_freeze()
                rollback_result["ok"] = True
                rollback_result["details"] = rollback_details
            except Exception as rollback_exc:  # noqa: BLE001
                logger.exception("Rollback attempt failed for restore job %s", restore_job_id)
                rollback_result["ok"] = False
                rollback_result["error"] = safe_backup_error_message(
                    rollback_exc,
                    operation="Restore rollback",
                )

        error_message = safe_backup_error_message(exc, operation="Restore job")
        if rollback_result and rollback_result.get("attempted"):
            if rollback_result.get("ok"):
                error_message = f"{error_message} | rollback=success"
            else:
                error_message = f"{error_message} | rollback=failed"

        preflight_payload = preflight if isinstance(preflight, dict) else {}
        if rollback_result:
            preflight_payload = {**preflight_payload, "rollback": rollback_result}

        if not restore_started:
            recovery = {"state": "not_started", "safe_to_restart": True}
        elif rollback_result and rollback_result.get("ok"):
            recovery = {"state": "rolled_back", "safe_to_restart": True}
        else:
            # This includes a failed rollback, an empty-target restore with no
            # safety artifact, and abrupt failures before rollback could finish.
            recovery = {"state": "unsafe", "safe_to_restart": False}
        preflight_payload = {**preflight_payload, "recovery": recovery}

        return _record_restore_terminal_status(
            restore_context,
            status="failed",
            error=error_message,
            preflight_json=preflight_payload or None,
            # Before mutation starts, the current catalog is still authoritative
            # and any recovery copy already materialized above must be inserted
            # rather than left as an untracked file. A successful rollback also
            # installs the pre-restore archive, so repair both mappings there.
            backup_catalog_contexts=(
                [
                    context
                    for context in (
                        source_catalog_context,
                        pre_restore_catalog_context,
                    )
                    if context is not None
                ]
                if not restore_started or (rollback_result and rollback_result.get("ok"))
                else []
            ),
        )


def verify_backup_artifact(db: Session, artifact_id: str) -> dict[str, Any]:
    """Verify backup artifact checksum."""
    artifact = get_backup_artifact(db, artifact_id)
    if not artifact:
        raise RuntimeError("Backup artifact not found")

    with _verification_source_artifact(
        db,
        artifact.storage_uri,
        artifact.id,
    ) as local_path:
        actual_hash = _sha256_file(local_path)
        ok = actual_hash == artifact.checksum_sha256
        size = local_path.stat().st_size
        if ok:
            mark_backup_artifact_verified(db, artifact.id)

        return {
            "artifact_id": artifact.id,
            "ok": ok,
            "expected": artifact.checksum_sha256,
            "actual": actual_hash,
            "size": size,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


def verify_backup_job(db: Session, backup_job_id: str) -> dict[str, Any]:
    """Verify backup job artifacts."""
    artifacts = list_backup_artifacts(db, backup_job_id)
    if not artifacts:
        raise RuntimeError("Backup job has no artifacts")

    results = [verify_backup_artifact(db, artifact.id) for artifact in artifacts]
    return {
        "backup_job_id": backup_job_id,
        "ok": all(result.get("ok") for result in results),
        "results": results,
    }


def verify_backup_source(
    db: Session,
    source_uri: str,
    *,
    target_mode: str = "in_place",
) -> dict[str, Any]:
    """Verify a backup source against the requested non-destructive preflight."""
    try:
        with _verification_source_artifact(
            db,
            source_uri,
            f"verify-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        ) as source_path:
            with _prepare_archive_for_restore(source_path) as prepared_source_path:
                preflight = preflight_backup_archive(
                    prepared_source_path,
                    target_mode=target_mode,
                    db=db,
                )
    except Exception as exc:  # noqa: BLE001
        return {
            "source_uri": source_uri,
            "ok": False,
            "error": str(exc),
            "preflight": {"ok": False, "reason": "source_access_failed", "detail": str(exc)},
        }
    return {
        "source_uri": source_uri,
        "ok": bool(preflight.get("ok")),
        "preflight": preflight,
    }


def materialize_backup_job_artifact(db: Session, backup_job_id: str) -> tuple[Path, str]:
    """Materialize and validate a completed backup artifact.

    The admin download flow performs an authenticated HEAD preflight before it
    starts the browser-managed GET. Remote destinations would otherwise be
    downloaded from object storage twice. Backup artifacts are immutable, so a
    cached file whose size and checksum match the catalog is safe to reuse for
    the GET. Incomplete or corrupt remote downloads are removed before the
    operation fails, while local source artifacts are never modified.
    """
    backup_job = get_backup_job(db, backup_job_id)
    if backup_job is None:
        raise RuntimeError("Backup job not found")
    if backup_job.status != "success":
        raise RuntimeError("Backup job is not complete")

    artifacts = list_backup_artifacts(db, backup_job_id)
    if not artifacts:
        raise RuntimeError("Backup job has no artifacts")
    artifact = artifacts[0]

    def _matches_catalog(path: Path) -> bool:
        if not path.is_file():
            return False
        expected_size = getattr(artifact, "bytes", None)
        if expected_size is not None and path.stat().st_size != int(expected_size):
            return False
        expected_checksum = str(getattr(artifact, "checksum_sha256", "") or "")
        return not expected_checksum or _sha256_file(path) == expected_checksum

    is_remote = _resolve_local_artifact_path(artifact.storage_uri) is None
    if is_remote:
        cached_path = BACKUP_DOWNLOAD_CACHE_DIR / f"{backup_job_id}.tar.zst"
        if _matches_catalog(cached_path):
            return cached_path, artifact.id
        cached_path.unlink(missing_ok=True)

    local_path = _materialize_source_artifact(db, artifact.storage_uri, backup_job_id)
    if not _matches_catalog(local_path):
        if is_remote:
            local_path.unlink(missing_ok=True)
        raise RuntimeError("Backup artifact does not match its catalog checksum and size")
    return local_path, artifact.id


def _best_effort_delete_remote(db: Session, storage_uri: str, destination_id: str | None) -> None:
    """Best effort delete remote artifact."""
    if storage_uri.startswith("file://"):
        Path(storage_uri[len("file://") :]).unlink(missing_ok=True)
        return
    if storage_uri.startswith("local://"):
        rel = storage_uri[len("local://") :].lstrip("/")
        (BACKUP_ARCHIVE_DIR / rel).unlink(missing_ok=True)
        return

    destination = get_backup_destination(db, destination_id) if destination_id else None
    if destination is None:
        return

    config = decrypt_destination_config(destination.config_encrypted)
    adapter = build_storage_adapter(destination.provider, config, default_local_dir=BACKUP_ARCHIVE_DIR)
    adapter.delete_file(storage_uri)


def delete_backup_job_and_artifacts(
    db: Session,
    backup_job_id: str,
    *,
    delete_remote: bool = False,
    strict_remote_delete: bool = False,
) -> dict[str, Any]:
    """Delete backup job and artifacts."""
    job = get_backup_job(db, backup_job_id)
    if not job:
        raise RuntimeError("Backup job not found")

    artifacts = list_backup_artifacts(db, backup_job_id)
    deleted_remote: list[dict[str, Any]] = []
    remote_delete_errors: list[dict[str, Any]] = []

    if delete_remote:
        for artifact in artifacts:
            try:
                _best_effort_delete_remote(db, artifact.storage_uri, job.destination_id)
                deleted_remote.append(
                    {
                        "artifact_id": artifact.id,
                        "storage": redact_backup_uri_metadata(artifact.storage_uri),
                    }
                )
            except Exception as exc:
                logger.exception(
                    "Failed deleting remote backup artifact %s",
                    redact_backup_uri_metadata(artifact.storage_uri),
                )
                remote_delete_errors.append(
                    {
                        "artifact_id": artifact.id,
                        "storage": redact_backup_uri_metadata(artifact.storage_uri),
                        "error": str(exc),
                    }
                )

        if strict_remote_delete and remote_delete_errors:
            raise RuntimeError(
                "Failed deleting one or more remote backup artifacts: "
                + "; ".join(item.get("artifact_id") or "unknown" for item in remote_delete_errors)
            )

    from app.backups.models import delete_backup_job as delete_job_record

    delete_job_record(db, backup_job_id)
    (BACKUP_ARCHIVE_DIR / f"{backup_job_id}.tar.zst").unlink(missing_ok=True)
    (BACKUP_ARCHIVE_DIR / f"{backup_job_id}.tar.zst.enc").unlink(missing_ok=True)
    (BACKUP_DOWNLOAD_CACHE_DIR / f"{backup_job_id}.tar.zst").unlink(missing_ok=True)
    (BACKUP_DOWNLOAD_CACHE_DIR / f"{backup_job_id}.tar.zst.enc").unlink(missing_ok=True)

    return {
        "status": "success",
        "backup_job_id": backup_job_id,
        "deleted_remote": deleted_remote,
        "remote_delete_errors": remote_delete_errors,
    }


def test_backup_destination(db: Session, destination_id: str) -> dict[str, Any]:
    """Test backup destination connection."""
    destination = get_backup_destination(db, destination_id)
    if destination is None:
        raise RuntimeError("Backup destination not found")

    config = decrypt_destination_config(destination.config_encrypted)
    adapter = build_storage_adapter(destination.provider, config, default_local_dir=BACKUP_ARCHIVE_DIR)
    result = adapter.test_connection()
    result["destination_id"] = destination.id
    return result


def create_scheduled_backup_job(db: Session, schedule: BackupSchedule) -> BackupJob:
    """Create scheduled backup job."""
    options = {
        "schedule_id": schedule.id,
        "encryption_enabled": True,
    }
    return create_backup_job(
        db,
        trigger_type="scheduled",
        destination_id=schedule.destination_id,
        requested_by_user_id=None,
        options=options,
    )


def build_backup_job_response(
    db: Session,
    job: BackupJob,
    *,
    artifacts: list[BackupArtifact] | None = None,
) -> dict[str, Any]:
    """
    Build a backup job response.

    Callers rendering a page can provide preloaded artifacts to avoid an
    additional query per job. Single-job callers retain the existing fallback.
    """
    job_artifacts = artifacts if artifacts is not None else list_backup_artifacts(db, job.id)
    return {
        "id": job.id,
        "trigger_type": job.trigger_type,
        "status": job.status,
        "error": safe_backup_error_message(job.error, operation="Backup job"),
        "manifest_json": sanitize_backup_response_metadata(job.manifest_json),
        "options": sanitize_backup_response_metadata(job.options),
        "size_bytes": job.size_bytes,
        "requested_by_user_id": job.requested_by_user_id,
        "destination_id": job.destination_id,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "artifacts": [
            {
                "id": artifact.id,
                "backup_job_id": artifact.backup_job_id,
                "storage": redact_backup_uri_metadata(artifact.storage_uri),
                "checksum_sha256": artifact.checksum_sha256,
                "bytes": artifact.bytes,
                "verified_at": artifact.verified_at,
                "expires_at": artifact.expires_at,
                "created_at": artifact.created_at,
            }
            for artifact in job_artifacts
        ],
    }


def build_restore_job_response(job: RestoreJob) -> dict[str, Any]:
    """Build restore job response without exposing internal storage URIs."""
    return {
        "id": job.id,
        "source": redact_backup_uri_metadata(job.source_uri),
        "target_mode": job.target_mode,
        "status": job.status,
        "error": safe_backup_error_message(job.error, operation="Restore job"),
        "preflight_json": sanitize_backup_response_metadata(job.preflight_json),
        "options": sanitize_backup_response_metadata(job.options),
        "requested_by_user_id": job.requested_by_user_id,
        "confirmed_by_user_id": job.confirmed_by_user_id,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
