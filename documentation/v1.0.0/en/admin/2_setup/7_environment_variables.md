# Server Configuration Reference

Use this page to decide what belongs in deployment configuration and how to change it safely. It intentionally uses the names shown in the Server Launcher. Operators using the Server CLI or a source checkout should make the equivalent choice through that workflow's supported configuration editor.

Application behavior that can be changed in **Admin Settings** is documented there. Do not maintain the same setting in two places.

## Choose the management surface

| Installation | Supported configuration workflow |
|---|---|
| **Server Launcher** | Use **Settings**, **Secrets**, **Proxy**, and **Environment**. Prefer the named controls; use custom environment entries only when a release note explicitly requires one. |
| **Server CLI** | Use `omlorix-server config edit` for deployment settings, `config import` for a reviewed partial update, and `config replace` only for an authoritative replacement. Use `secrets` commands for recovery material. |
| **Source checkout** | Run the deployment setup assistant, review the generated server settings file, and use the comments in the matching release template. |

Launcher and CLI changes target the same server home when both are pointed at that exact location. Never edit settings in one surface while another operation is running.

## Settings and consequences

### Release and identity

| Visible setting | Guidance |
|---|---|
| **Mode** | Use **Production** for every real server. Development mode exposes conveniences that are inappropriate for public use. |
| **Update channel** | Use **Stable** unless you are intentionally testing Beta releases. |
| **Version** | Select a reviewed release. Update the management application or CLI separately when compatibility requires it. |
| **Compose project name** | Treat this as installation identity. Changing it can create a second stack instead of updating the existing one. |

The server home, installation identity, and project name must continue to refer to the same managed instance. Use the migration or adoption workflow when moving an existing deployment; do not improvise by copying only selected settings.

An unlabeled Compose project from an older Omlorix release can be adopted only through the explicit Launcher confirmation or `omlorix-server init --attach-project <project>`. Confirm that every container belongs to the intended Server Home first. Projects carrying another Omlorix installation identity must not be adopted.

### Server secrets

| Visible setting | Consequence of loss or replacement |
|---|---|
| **JWT secret key** (`JWT_SECRET_KEY`) | Must contain at least 64 bytes. Replacing it signs every user out after restart. |
| **Encryption key** (`ENCRYPTION_KEY`) | Must be a valid Fernet key. Losing or replacing it makes previously encrypted provider and connection credentials unreadable. |
| **Password reset salt** (`PASSWORD_RESET_IDENTIFIER_HASH_SALT`) | Must contain at least 16 characters. It keeps password-reset identifier and user-agent fingerprints stable across replicas and restarts for throttling and audit correlation; replacement breaks that continuity. It does not encrypt reset links or tokens. |
| **Audit/IP hash salt** (`LOG_IP_HASH_SALT`) | Required in production and must contain at least 16 characters. It keeps pseudonymous audit and password-reset IP fingerprints stable across replicas and restarts. Keep it independent from `JWT_SECRET_KEY`; replacement prevents new fingerprints from correlating with older ones. |
| **Backup archive passphrase** (`BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE`) | Required to restore encrypted archives created with that passphrase. Changing it does not re-encrypt existing archives. |

Generate secrets through the Launcher, CLI, or setup assistant. Keep the complete protected recovery copy outside the server folder on storage that survives loss of the host. Launcher and CLI recovery exports contain the complete deployment environment plus management context, including all five values above; active database, Redis, object-storage, proxy, and observability credentials where configured; topology choices; installation identity; update channel; and managed proxy state. A hand-copied subset is not an equivalent recovery file. Do not paste secrets into documentation, tickets, terminal recordings, or source control.

### Database connection

Choose **Bundled PostgreSQL** for a normal single-host installation or **External PostgreSQL** when your organization operates the database.

- For bundled storage, protect and monitor the persistent volume.
- For an external database, require a dedicated database and least-privilege account, private network access, encrypted transport where needed, backups, capacity monitoring, and a tested recovery process.
- Keep **Auto-create databases** off when the platform pre-provisions databases or the account is not allowed to create them.
- Change **Application schema**, **Audit log schema**, or **Operational logs schema** only before first use or with a reviewed database migration plan.
- **Database traffic manager** is available only with bundled PostgreSQL. Use it when measured connection demand warrants pooling.
- Start with **Transaction** pool mode; use **Session** only when application testing demonstrates that session affinity is required. Statement pooling is unsupported because Omlorix uses multi-statement transactions.
- Managed workflows route long-running application, scheduler, and worker services through PgBouncer while migrations connect directly to PostgreSQL so bootstrap can reach the control database. Treat the derived application and migration host overrides as management-owned topology, not ordinary external-database settings.

Changing database destination on a live instance does not move data. Treat it as a migration or restore, not a normal restart.

### Redis connection

**Bundled Redis** is the normal single-host choice. **External Redis** needs its own authentication, encrypted transport where required, availability, persistence, and monitoring. **Off** disables the queue and coordination features that require Redis, including scheduled automation processing.

Changing Redis mode can interrupt background work and sessions. Drain or pause scheduled work, save the setting, restart, and verify the scheduler and worker services.

### Dedicated workers

Managed production Compose files run operations, generation, memory, research, file processing, rendering, media, connector ingestion, audit events, account lifecycle, and maintenance as eleven separate durable workers, plus a realtime gateway. `OPERATIONS_WORKER_MODE`, `GENERATION_WORKER_MODE`, `MEMORY_WORKER_MODE`, `RESEARCH_WORKER_MODE`, `FILE_PROCESSING_WORKER_MODE`, `RENDERING_WORKER_MODE`, `MEDIA_WORKER_MODE`, `CONNECTOR_WORKER_MODE`, `AUDIT_EVENT_WORKER_MODE`, `ACCOUNT_LIFECYCLE_WORKER_MODE`, `MAINTENANCE_WORKER_MODE`, and `REALTIME_GATEWAY_MODE` are topology-owned settings; do not override them in an ordinary Launcher or CLI deployment. Redis-off installations retain compatible inline or buffered streaming paths while PostgreSQL-backed queues continue to isolate non-streaming work.

Every durable worker has bounded polling, batch, lease, and health-age controls derived from its queue prefix. The tuning prefixes are `OPERATIONS`, `GENERATION`, `MEMORY`, `RESEARCH`, `FILES`, `RENDERING`, `MEDIA`, `CONNECTOR`, `AUDIT_EVENT`, `LIFECYCLE`, and `MAINTENANCE`. In particular, file-processing and account-lifecycle tuning uses `FILES_*` and `LIFECYCLE_*`, not the service-facing `FILE_PROCESSING_*` and `ACCOUNT_LIFECYCLE_*` mode prefixes; ingestion and event tuning explicitly uses `CONNECTOR_*` and `AUDIT_EVENT_*`. Examples include `FILES_WORKER_BATCH_SIZE`, `LIFECYCLE_WORKER_LEASE_SECONDS`, `CONNECTOR_WORKER_POLL_SECONDS`, and `AUDIT_EVENT_WORKER_HEALTH_MAX_AGE_SECONDS`. Managed health checks read the atomic heartbeat timestamp through a standard-library-only probe at distinct 31–59 second intervals; changing the health-age limit does not change that probe cadence. Workers with dependent UI or domain state also accept the corresponding `*_WORKER_RECONCILE_SECONDS`. Live workers renew leases every 30 seconds or sooner. Change concurrency and lease controls only after capacity testing. `WORKER_JOB_RETENTION_DAYS` controls terminal queue records; `WORKER_STAGING_RETENTION_HOURS` controls abandoned operations, backup/decryption, media, and rendering files; `AUDIT_EVENT_OUTBOX_RETENTION_DAYS` controls delivered or restore-cancelled outbox rows. Compatibility endpoints have bounded waits through `MEDIA_REQUEST_WAIT_SECONDS`, `RENDERING_REQUEST_WAIT_SECONDS`, and `CONNECTOR_REQUEST_WAIT_SECONDS`. A parent generation waits at most `RESEARCH_SUBAGENT_QUEUE_START_TIMEOUT_SECONDS` (60 seconds; enforced range 5–600) for a Research Worker to start a queued subagent, and at most `RESEARCH_SUBAGENT_COMPLETION_TIMEOUT_SECONDS` (21,600 seconds; enforced range 60–86,400) for the nested run to finish. Both deadlines cancel the durable job and close its shared stream, preventing a Research Worker outage from consuming the Generation Worker pool indefinitely. See the [operations runbook](4_operations.md) for responsibilities and scaling guidance.

`GENERATION_WORKER_BATCH_SIZE` controls concurrent durable jobs in each Generation Worker replica (default `1`; enforced range `1`–`50`). `PROVIDER_SYNC_STREAM_WORKERS` separately bounds the compatibility executor used by synchronous provider streams (default `16`; enforced range `4`–`200`). If the provider-specific setting is absent, an explicitly configured generation batch size remains the compatibility fallback, subject to the provider executor's range. A fully integrated native async provider adapter bypasses that executor, but its job still counts against the worker batch. Every replica receives its own bounds; installation-wide concurrency therefore also depends on replica counts. Increase either setting only after load testing provider quotas, PostgreSQL/PgBouncer, Redis, CPU, and memory headroom.

Automatic Memory updates use the dedicated `memory_worker` service and PostgreSQL `memory` queue in managed production deployments, including Redis-off installations. `MEMORY_WORKER_BATCH_SIZE` controls concurrent extractions per replica (default `1`; range `1`–`50`), independently of chat generation. Memory requires no shared streaming service. Outside managed Compose, set `MEMORY_WORKER_MODE=external` and run `python -m app.workers.memory run` to use a separate process. The default `inline` mode starts a dedicated durable-queue consumer inside each API process, with the same `MEMORY_WORKER_BATCH_SIZE` concurrency limit (default `1`). Pending jobs stay encrypted in the database; they are not submitted to an unbounded in-process executor. The previous `MEMORY_GENERATION_WORKERS` and `MEMORY_GENERATION_MAX_PENDING` settings no longer apply. Both modes atomically stage the memory job with the source message; a process crash cannot leave a committed source without its accepted job. Provider failures affect the memory job only; a database admission failure rolls back the source transaction. Each API replica has its own inline concurrency budget, so use external mode to control installation-wide capacity separately from API replicas. Jobs expire 24 hours after the source message, and the merge repeats that age check after provider I/O. Content-free deletion guards last at least 48 hours and are then removed in bounded maintenance batches. Guards are operational replay protection, excluded from portable user/project memory exports and preserved by full database backups. An explicit later statement may establish a new fact in the same semantic slot. The Maintenance Worker checks lifecycle transitions with `MEMORY_RETENTION_INTERVAL_SECONDS` (default `3600`; range `60`–`86400`), `MEMORY_RETENTION_BATCH_SIZE` (default `1000`; range `10`–`5000`), and `MEMORY_RETENTION_MAX_BATCHES` (default `20`; range `1`–`1000`). These controls do not change the fact-specific review and expiration periods. Keep the defaults unless measured queue age or cleanup lag justifies a capacity-tested change.

`ASGI_BULK_IO_MAX_WORKERS` bounds each API replica's dedicated compatibility pool for large uploads, encryption, connector downloads, FFmpeg/document work, and other blocking file operations (default `8`; enforced range `1`–`32`). This pool is separate from Starlette's general-purpose thread capacity, so slow bulk work cannot consume every token needed by authentication and short ORM dependencies. Each replica has its own bound. Increase it only with load-test evidence and enough CPU, memory, storage bandwidth, object-storage capacity, and database headroom.

Authenticated imports reserve shared staging capacity in PostgreSQL before any upload bytes are written. Reservations include partial `.part` files and queued work across all API replicas, and remain charged until staged bytes are removed. `OPERATIONS_IMPORT_MAX_BYTES` limits one import (default 512 MiB). `OPERATIONS_IMPORT_STAGING_GLOBAL_MAX_BYTES` and `OPERATIONS_IMPORT_STAGING_GLOBAL_MAX_SLOTS` cap the whole installation (defaults 8 GiB and 1,000 imports); `OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_BYTES` and `OPERATIONS_IMPORT_STAGING_PRINCIPAL_MAX_SLOTS` cap one authenticated user or administrator (defaults 1 GiB and 10 imports). Size these below the free capacity of the shared `app_data` volume and keep the same values on API, Operations Worker, and Maintenance Worker replicas.

### Email worker

The always-on `email_worker` uses its own encrypted PostgreSQL outbox and is not one of the eleven generic durable-queue consumers. Multiple replicas can safely claim work from that outbox. Keep the defaults unless queue telemetry and SMTP capacity justify a measured change:

| Setting | Default and enforced range |
|---|---|
| `EMAIL_WORKER_BATCH_SIZE` | 20 messages; 1–200 |
| `EMAIL_WORKER_LEASE_SECONDS` | 600 seconds; 60–3,600 |
| `EMAIL_WORKER_POLL_SECONDS` | 2 seconds; 1–60 |
| `EMAIL_WORKER_HEALTH_MAX_AGE_SECONDS` | 90 seconds; 15–3,600 |
| `EMAIL_WORKER_MAINTENANCE_BATCH_SIZE` | 1,000 rows; 100–5,000 |
| `EMAIL_WORKER_MAINTENANCE_MAX_BATCHES` | 10 batches per pass; 1–100 |
| `EMAIL_OUTBOX_RETENTION_DAYS` | 7 days for terminal rows; 1–90 |

SMTP transport encryption is required by default. `EMAIL_ALLOW_INSECURE_SMTP=true` permits only an unauthenticated plaintext relay and exposes message contents and one-time secrets in transit; use it solely for a trusted local relay on a protected network. Monitor queue depth, oldest-message age, retry/dead outcomes, and worker health before increasing batch size or replica count.

An expired processing lease is reclaimed only while attempts remain. If the final lease expires—for example, when a process exits after SMTP accepted a message but before the outbox acknowledgement committed—the row is terminalized and redacted instead of being delivered again. SMTP cannot provide a portable exactly-once guarantee, so keep provider-side Message-ID deduplication enabled where available.

### File storage

Choose **Local storage**, **Bundled MinIO**, or an external provider. Changing the selection does not copy existing objects. Follow [User File Storage](6_1_user_file_storage.md) for provider requirements, probes, migration, cutover, and rollback.

Local storage is for a single application host. Use shared object storage or WebDAV before running independent application replicas.

### Listener, proxy, and public access

- Keep the direct Omlorix listener on loopback or a protected container network.
- Use the **Proxy** page or a trusted external proxy for public access.
- Configure the exact **Public URLs** in Admin Settings after the public origin is final.
- Trust forwarding headers only from the real proxy sources, and make the edge replace client-supplied forwarding headers.
- Keep database, Redis, object-storage administration, and observability ports private.

See [Set Up HTTPS](3_setup_https.md) for the complete ingress and visitor-IP checklist.

### Backups and restore

Deployment configuration supplies backup encryption and local storage capacity; **Admin Settings > Database** controls backup destinations and schedules.

A full-instance backup does not replace the protected server recovery copy. External user-file storage, external database platform backups, monitoring data, and provider-side resources can need separate protection. Use the canonical [Backups](../3_admin_settings/23_1_backups.md) and [Full-Instance Restore](../3_admin_settings/23_2_restore.md) procedures.

`OMLORIX_ERASURE_LEDGER_PATH` locates the restore-resistant completed-user-erasure ledger. Leave it blank to use Omlorix's persistent application-data directory. A custom path must be durable and outside data replaced by a full restore; the ledger is deliberately excluded from full-instance backup archives and must be protected separately.

### Observability

**Observability stack** runs the bundled monitoring services. Keep their host ports on loopback, replace bootstrap credentials, define retention, and enable only signals and attributes your privacy policy permits. See [OpenTelemetry and Observability](8_open_telemetry.md).

## Safe change procedure

For any deployment change:

1. Record the current server home, endpoint, version, and health.
2. Create and verify an encrypted full backup when data or compatibility could be affected.
3. Save the current complete recovery copy outside the server home.
4. Change one related group of settings through one management surface.
5. Review validation warnings before saving.
6. Restart when prompted; saving alone does not recreate running services.
7. Verify service health, the browser endpoint, sign-in, a model request, file access, scheduled work, and backups.
8. Keep the previous recovery material until the new configuration has passed an observation window.

If startup fails, stop making changes. Preserve logs and the failed configuration, identify whether the problem is connectivity, credentials, compatibility, migration, or capacity, and recover through the documented workflow rather than deleting volumes.
