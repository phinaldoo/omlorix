# Operate and Update Omlorix

Use this runbook for routine health checks, planned maintenance, updates, and first-response diagnostics. The Server Launcher and Server CLI operate the same managed server features; the controls differ, but the preparation and acceptance criteria are the same.

## Daily and weekly checks

Check the following from **Status** in the Launcher or `omlorix-server status`:

- Docker is available and every expected service is running and healthy.
- The Omlorix endpoint is reachable.
- No migration, update-compatibility, storage, proxy, or recovery-copy warning is active.
- **Visitor IPs** is verified whenever a proxy is in use.
- The always-on email and dedicated operations, generation, research, file-processing, rendering, media, connector, audit-event, account-lifecycle, and maintenance workers are healthy, together with the realtime gateway. The automation scheduler and worker must also be healthy when Redis is enabled.
- Email queue depth and oldest-message age are within the operating baseline; investigate sustained retries or any dead delivery, especially for password-reset, sign-in-code, email-change, and security-notice messages.
- Backup history contains the expected recent successful jobs; investigate failed or overdue schedules.
- Disk space covers database growth, files, logs, images, temporary update downloads, and at least one local safety backup.

Review **Services** or `omlorix-server services` when the summary is not healthy. A running container is not necessarily a ready application.

## Before a maintenance change

1. Announce the expected impact and pause workflows that must not overlap maintenance.
2. Confirm the active server home, current version, update channel, and public endpoint.
3. Create an encrypted full-instance backup and verify it.
4. Save the current complete server recovery copy outside the server home.
5. Confirm that external PostgreSQL, Redis, and user-file storage have their own current protection where applicable.
6. Read the release notes and resolve any required Launcher or CLI compatibility update first.

Never run a Launcher and CLI write operation at the same time. The shared operation lock prevents ordinary overlap, but it cannot make unrelated server homes or Compose projects safe.

## Update the server

In the Launcher, refresh **Status** to check for releases and select **Update Omlorix** when the server-update banner appears. With the CLI, use:

    omlorix-server check-update
    omlorix-server update

Start, Restart, and Update take the Compose project offline with orphan removal, run main and audit database migrations, recreate services, and wait for readiness. Volumes remain intact, while writers left behind by renamed or removed services are terminated before migration. Update first downloads the selected server release; it does not update the Launcher application or CLI binary. The source-checkout `make migrate` target uses the same boundary and deliberately leaves application services stopped until `make up`.

After the update, verify:

1. all expected services and the browser endpoint;
2. Owner and normal User sign-in;
3. one chat with a production model;
4. upload, download, and one generated file;
5. the email and ten dedicated durable workers, the realtime gateway, plus automation workers when Redis is enabled;
6. proxy visitor-IP detection;
7. backup destination access and a new verified backup;
8. Code Execution, rendering, or external services that the release affects.

If an update fails before the target migration starts, the Launcher and CLI can safely restore the previous release selection. Once migration may have started, they retain the target release, take the Compose project offline, and never start the previous image against a potentially newer schema. Preserve the logs and retry the selected target release after correcting the failure, or restore a verified backup that is compatible with the release you intend to run.

## Automatic updates

In the Launcher, **Schedule** offers **Every day**, **Weekends**, or **Custom days** at a local time. The CLI provides equivalent schedules. Keep **Create a backup before updating** and **Only update when Docker and Omlorix are healthy** enabled for production.

The pre-update backup reuses the destination and archive-encryption choices on the Launcher Dashboard. The Launcher and CLI persist that reviewed policy in the shared Server Home. For a headless host, configure the same values with `auto-update enable --destination <id>` and `--no-encrypted` only when plaintext archives are explicitly allowed. Run `auto-update status` to review the effective policy.

- Launcher schedules run while the Launcher is open and the host is awake.
- CLI schedules require `omlorix-server auto-update daemon` to be kept running by the host's service manager.
- Automatic updates skip a busy or unhealthy server when the corresponding guard is enabled.
- Keep production on **Stable** and review the recorded last result.

Automatic updates are not unattended disaster recovery. Monitor their result and retain a tested restore path.

## Service and log operations

Use **Services** in the Launcher or `omlorix-server service` to start, stop, restart, or inspect one supported service. Use **Console** or `omlorix-server logs` for recent or followed output.

The Launcher **Console** and CLI share aggregate/per-service scope, a 1–5,000 line bound, and optional `since` semantics. A Launcher follow is explicit and cancellable, and remains independent of lifecycle operations so startup, migration, update, exit, and restart output can be observed as it arrives.

Restarting a single dependency can interrupt requests or background work. Prefer a full managed restart after topology, secret, or release changes so every service receives the same configuration.

Collect only the time range and services needed for investigation. Redact credentials, connection addresses, private file names, and user data before sharing logs.

## Concurrency and I/O model

Omlorix uses selective async I/O instead of treating the entire application as async. Native ASGI paths handle connection-heavy waits such as Redis-backed request limiting, cross-process chat-stream subscriptions, non-blocking durable-job waits, and transcription calls where the provider SDK offers an async client. Database-backed request policy is cached or refreshed outside the event loop so ordinary requests do not perform synchronous database work there.

The existing SQLAlchemy model layer and several provider-chat, file, connector, and storage libraries remain synchronous. Compatibility paths run that work in bounded worker threads with thread-owned database sessions, while long-running work is isolated in the durable external worker services below. Synchronous provider streams use a bounded adapter with backpressure; a fully integrated native async provider adapter can bypass that compatibility executor. Do not assume that every provider, including OpenAI chat, or every storage backend is natively async.

Async I/O increases the number of requests that can wait concurrently; it does not make one database query, provider response, filesystem operation, or CPU-heavy transformation intrinsically faster. Size API replicas, worker replicas, thread concurrency, PostgreSQL/PgBouncer capacity, Redis, shared storage, and provider quotas together.

## Dedicated application workers

Production deployments keep long-running work out of the API process. Each responsibility has its own independently restartable service:

| Service | Responsibility |
|---|---|
| `operations_worker` | Backups, restores, user/admin exports, and supported imports |
| `generation_worker` | Streaming LLM responses and their tool execution |
| `memory_worker` | Automatic memory extraction on its own durable queue, independently bounded from chat generation; works without Redis |
| `research_worker` | Deep Research runs, long-running subagents, and other agent workflows |
| `file_processing_worker` | Document text extraction, OCR, PDF inspection, and preview rendering |
| `rendering_worker` | Presentation generation and refresh, Canvas Markdown PDF export, and LaTeX compilation |
| `media_worker` | Image, video, audio, and music generation; dictation; assistant read-aloud TTS; and meeting-media conversion/transcription |
| `connector_worker` | Google Drive and future connector imports with execution-time connection and policy checks |
| `audit_event_worker` | Encrypted audit outbox delivery and deferred IP geolocation enrichment |
| `account_lifecycle_worker` | Scheduled account deletion and temporary-account expiry |
| `maintenance_worker` | Retention, cleanup, provider synchronization, connectivity checks, and statistics maintenance |
| `realtime_gateway` | Strictly scoped realtime HTTP/WebSocket ingress with independent connection scaling; this is a gateway, not a queue consumer |

Memory source messages and extraction jobs are committed atomically. Inline memory mode also consumes this durable queue, with no in-memory submission backlog. Fact revisions, projection validity, and job status are independent.

The workers claim encrypted jobs from PostgreSQL with idempotency keys and renewable leases. Terminal jobs erase their request payload; maintenance expires abandoned jobs and removes old queue records, private backup/decryption work files, encrypted staging, delivered audit outbox rows, and processing artifacts. Policy-driven audit erasure first fences the subject with a one-way identifier and uses an indexed subject-to-event reference to cancel or pseudonymize queued events before deleting audit rows; both inline writes and worker delivery serialize against that fence, while a separate hash-only guard prevents restoration—including SCIM activation—from becoming visible until cross-database cleanup finishes. Immediate deletion also commits a durable Audit Event Worker cleanup handoff with the account-state change, and offline startup completes it after a crash. Legacy unindexed events are cancelled and redacted during migration, database constraints reject writes from application versions that predate the fence, and a checkpointed upgrade reconciliation reapplies elapsed policies from the restore-resistant erasure ledger to historical inline rows exactly once. Full restore writes an external required-reconciliation marker before database replacement and always evaluates the complete external ledger independently of a restored SQL checkpoint. Concurrent and later system/admin events therefore cannot reintroduce an erased subject. Encrypted backups stream Tar/Zstandard output directly into a private AES-GCM destination and atomically publish only completed ciphertext; no full plaintext archive is created. Media and rendering inputs or outputs too large for a queue row use mode-0600 authenticated-encryption staging in the shared application-data volume; large meeting uploads are encrypted and decrypted incrementally so they never become giant queue rows or in-memory copies. Generation, Research, Media, and Rendering use Redis where available for live cross-process events and cancellation. The parent generation applies separate bounded Research Worker start and completion deadlines, cancels a timed-out subagent job, and closes its stream. An installation explicitly configured with Redis **Off** retains compatible buffered or inline paths.

Container health checks use a dependency-free Python probe that reads each worker's atomically written heartbeat timestamp. The managed services use distinct 31–59 second intervals so probes do not repeatedly import worker/provider code or wake every worker at once. A stale heartbeat still uses the worker-specific `*_WORKER_HEALTH_MAX_AGE_SECONDS` limit.

Scale `generation_worker`, `memory_worker`, `research_worker`, `file_processing_worker`, `rendering_worker`, `media_worker`, `connector_worker`, `audit_event_worker`, and `realtime_gateway` horizontally only after PostgreSQL, Redis, provider quotas, audit-database capacity, and shared file storage have enough capacity. `GENERATION_WORKER_BATCH_SIZE` applies per Generation Worker replica, so both replicas and the configured batch multiply installation-wide generation concurrency. Increase either only from measured queue delay while watching provider throttling, database-pool saturation, Redis stream lag, event-loop lag, CPU, and memory. Media and connector jobs intentionally use at-most-once queue attempts because an unknown provider outcome must not be replayed; users can explicitly retry a failed operation. Revision-keyed presentation and LaTeX jobs retain idempotency while active or successful, but an explicit retry after failure or cancellation receives a fresh durable job. Keep `operations_worker`, `account_lifecycle_worker`, and `maintenance_worker` at one replica unless the deployment has been reviewed for singleton operational duties. A normal managed restart and offline restore include every service automatically.

A full-instance restore invalidates in-flight queue snapshots before workers restart, terminalizes dependent domain records and dictation quota reservations, cancels restored audit outbox events, and removes ephemeral import/export, media, and rendering staging. Lifecycle queue rows are rebuilt from the restored user schedule rather than replayed. This prevents a recovery point from repeating an LLM/provider call, import, export, audit delivery, or destructive action that may already have happened outside the snapshot.

## Materialize a Backup for Off-Host Storage

The web admin, Launcher, and CLI can download a successful catalogued backup. In the Launcher, use **Status > Backup & recovery > Download a completed backup**. With the CLI, use `omlorix-server backup download <job-id> --output <new-path>`.

Both operator surfaces reject incomplete or integrity-mismatched jobs and refuse to overwrite the selected path. A successful download is another sensitive copy, not a new backup job and not a restore rehearsal. Move it through an approved encrypted channel, verify retention at the destination, and remove temporary host copies when policy permits.

## First-response sequence

When Omlorix is unavailable:

1. Stop repeated update, restore, or restart attempts.
2. Run **Status** and the Launcher readiness checks, or `omlorix-server doctor` followed by `status`.
3. Check host disk, memory, Docker state, DNS, and certificate validity.
4. Inspect the migration service first when startup follows an update.
5. Inspect the application, browser, database, Redis, and proxy services relevant to the symptom.
6. Test external database, Redis, storage, provider, and Code Execution reachability from Omlorix's service network.
7. Preserve the exact error, timestamps, version, and recent change.

For data corruption, accidental deletion, or an incompatible migration, follow [Full-Instance Restore](../3_admin_settings/23_2_restore.md). For missing stored objects, restore the matching user-file storage snapshot as well.

## Canonical procedures

- [Server Launcher](1_2_server_launcher.md) and [Server CLI](1_3_server_cli.md) for surface-specific controls
- [Server Configuration Reference](7_environment_variables.md) for deployment decisions
- [Set Up HTTPS](3_setup_https.md) for proxy and visitor-IP checks
- [User File Storage](6_1_user_file_storage.md) for probes and migration
- [Backups](../3_admin_settings/23_1_backups.md) and [Full-Instance Restore](../3_admin_settings/23_2_restore.md)
- [Code Execution Service](../9_code_execution_service/1_introduction.md)
- [OpenTelemetry and Observability](8_open_telemetry.md)

The [generation and workspace architecture](../../architecture/1_generation_memory_workspace.md) describes shared provider execution, context admission, tool receipts, and cursor-based catalogs.
