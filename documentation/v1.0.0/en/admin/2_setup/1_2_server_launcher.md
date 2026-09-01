# Set Up with the Server Launcher

The Server Launcher is the recommended graphical installer and operator console for a single Docker host. It manages the Omlorix server; users still open Omlorix in a browser.

## Install the Launcher

1. Install and start Docker with Compose v2.
2. Download the **Stable** Launcher for macOS, Windows, or Linux from the [Omlorix download page](/downloads). Release assets use path-free filenames and have a matching SHA-256 file when manual verification is required.
3. Verify the release asset according to your platform policy, then install the macOS DMG or Windows installer, or make the Linux AppImage executable and open it.
4. Resolve the Docker readiness checks shown by the Launcher.

You do not need a source checkout or development tools. See [Installation Prerequisites](1_2_install_prerequisites.md).

## Complete Guided Setup

Choose **Recommended** for **Production** mode and the **Stable** channel. Use **Custom** only when you intentionally need another topology or the Beta channel.

The setup flow asks you to configure:

- **Database connection:** bundled or external PostgreSQL. PgBouncer is available only with bundled PostgreSQL and supports **Transaction** or **Session** mode; statement pooling is not supported. Application services use the pool while bootstrap migrations connect directly to PostgreSQL.
- **Redis connection:** bundled, external, or disabled. Disabling Redis also disables features that depend on its queues and coordination.
- **File storage:** **Local storage**, **Bundled MinIO**, or an external S3-compatible, Google Cloud Storage, Azure Blob Storage, or WebDAV service.
- **Access:** only this computer, local network, or a domain/public address through the Launcher proxy or an existing proxy.
- **Secrets:** generated server secrets and a protected automatic recovery copy outside the server folder.

Keep local storage for a single application host only. Review [User File Storage](6_1_user_file_storage.md) before selecting or changing storage.

The Launcher can terminate HTTPS when you supply a certificate and private key, but it does not issue certificates. Keep public access disabled until [HTTPS](3_setup_https.md) is valid.

When the observability stack is enabled, Linux includes hardened host metrics without mounting the host root filesystem. macOS and Windows omit node-exporter because Docker Desktop cannot safely provide the required Linux host interfaces; the rest of the observability stack remains available.

## Start and Verify

Review the summary and correct every validation error. Select **Start server** when Docker and Compose are ready. If Docker is not ready, select **Save setup** or open the Launcher Dashboard, finish the host installation, and start Omlorix later. The recovery-copy step must be complete either way. Start and Restart take the Compose project offline with orphan removal, run main and audit migrations, and only then return application services; data volumes remain intact and the first start may take several minutes.

On **Status**, confirm:

- Docker is ready and expected services are healthy
- the endpoint opens successfully
- the automatic environment recovery copy is current
- **Visitor IPs** passes when a proxy is enabled
- no setup or update warning remains

Then select **Open Omlorix** and complete [First Steps](2_first_steps.md).

## Operator Pages

| Page | Main tasks |
|---|---|
| **Status** | Health, lifecycle actions, updates, visitor-IP checks, backup creation/download, verification, and restore entry points |
| **Settings** | Release, topology, storage, observability, and automatic updates |
| **Secrets** | Manage server credentials and the protected automatic environment recovery copy |
| **Proxy** | Listener, public hostname, HTTP/HTTPS, certificate, and trusted-ingress settings |
| **Environment** | Review named deployment settings, add an explicitly documented custom setting, or perform a reviewed merge/replacement import |
| **Services** | Inspect, start, stop, restart, and read logs for individual services |
| **Console** | Load or follow aggregate/per-service logs with line and time bounds, alongside operator output |
| **Code Execution** | Create and operate independent Code Execution services |

Changes that affect containers or startup security do not become active merely because the field was saved. Use **Restart** after reviewing the change.

## Configuration and secret recovery

The automatic recovery copy contains the complete deployment environment plus recovery-only management context. That includes the JWT signing key, field-encryption key, password-reset identifier salt, audit/IP hash salt, backup passphrase, active database/Redis/storage credentials and topology, installation identity, update channel, and managed proxy state. Store it outside the server folder on protected storage that survives loss of the host.

- **Apply import** with **Reset variables missing from the file** off changes supplied settings and keeps ordinary omitted settings.
- Turning on **Reset variables missing from the file** returns omitted known settings to Launcher defaults and removes omitted custom settings. Review the displayed impact before applying it.
- Use the complete recovery restore action on **Secrets** only for a trusted recovery copy from the same instance.
- **Disable automatic backup** on **Secrets** stops future synchronization and clears the active destination after confirmation. The existing recovery file is retained for explicit operator cleanup.

Before any import, stop the server if the Compose project identity may change. After a successful import or recovery, restart deliberately and verify the endpoint. Never expose recovery files in tickets, chat, logs, or source control.

Rotating **JWT secret key** signs out every user after restart. To replace an older short signing key, open **Secrets**, regenerate only **JWT secret key**, save a new recovery copy, and restart Omlorix. Losing or replacing **Encryption key** makes previously encrypted credentials unreadable; do not rotate it as part of signing-key maintenance.

## Updates

**Update Omlorix** updates server services and runs migrations; it does not update the Launcher application. Launcher updates are offered separately and preserve the managed server home and Docker data.

Configure manual or automatic updates under **Settings**. Launcher schedules run only while the Launcher is open and the host is awake. Follow the common preparation, compatibility, backup, acceptance, and failure rules in [Operate and Update Omlorix](4_operations.md).

If failure occurs after database migration may have started, the Launcher keeps the target server version selected and leaves the Compose project offline. Review the operation log, correct the target-release failure, and retry; do not select an older image unless you first restore a compatible database backup.

The backup destination and archive-encryption controls on the Dashboard are the shared policy for manual and automatic pre-update backups. The Launcher stores these values in the Server Home used by `omlorix-server`, so either surface can review and update the same policy.

If the managed proxy runs inside the Launcher, quitting the Launcher stops public access. The native quit confirmation calls this out. Install and maintain the background proxy service when public access or scheduled server updates must continue independently of an open Launcher window.

## Backups, Restore, Proxy, Storage, and Services

The Launcher exposes the same ordinary workflows as the [`omlorix-server` CLI](1_3_server_cli.md):

- create and inspect backups, and perform a guarded full-instance restore
- configure and operate the native proxy, then detect, repair, and verify visitor IPs
- probe the selected file-storage provider and run dry-run or committed migrations
- inspect or operate individual services and logs
- create, update, stop, and troubleshoot Code Execution services

On **Status > Backup & recovery**, choose a destination and archive-encryption policy before creating a backup. The destination list comes from **Admin Settings > Database** and requires a ready Omlorix server. To download, choose a successful catalogued job under **Download a completed backup**, select **Download selected backup**, and choose a new local path. The Launcher validates the catalogued artifact, writes through a private temporary file, and does not overwrite an existing destination.

On **Console**, choose **All services** or one service, set **Lines** from 1 to 5,000, and optionally enter a relative **Since** value such as `5m` or a timestamp. **Load snapshot** performs a bounded read. **Start following** streams new output until **Stop following** is selected; following remains available while a Launcher start, restart, or update operation is running.

## Adopt a Legacy Compose Installation

Current managed resources carry a random Omlorix installation identity in addition to the Compose project label. When the Launcher finds an older project whose containers have no Omlorix ownership label, it asks whether to adopt that installation. Continue only after confirming that every listed container and the Compose project belong to this exact Server Home. Adoption recreates the project with the current installation identity and closes the one-time exception after verification.

Never adopt a project that belongs to another Server Home. A project containing a different Omlorix ownership identity is refused rather than offered for adoption.

For consequences and preparation, use the canonical pages: [Backups](../3_admin_settings/23_1_backups.md), [Full-Instance Restore](../3_admin_settings/23_2_restore.md), [HTTPS](3_setup_https.md), and [User File Storage](6_1_user_file_storage.md).

## Troubleshooting

- **Actions are disabled:** start Docker and confirm Compose is available to the current user.
- **Another operation is running:** let it finish; do not open a second Launcher or run a concurrent CLI mutation.
- **Containers run but the endpoint is unavailable:** inspect **Services** and **Console**, especially migration, application, and browser-service health.
- **Import succeeded but values appear unchanged:** restart the server; verify that the active installation and Compose project are the intended ones.
- **Public access works but visitor IPs are wrong:** use **Visitor IPs > Fix automatically** for managed ingress, or narrow and correct the external proxy trust chain.
- **Update fails:** preserve logs and confirm disk and network capacity. After migration may have started, the target release remains selected and the stack stays offline; retry that target or restore a compatible verified backup instead of forcing the previous image.
- **Legacy project warning:** confirm the Server Home and Compose project before adoption; cancel when ownership is uncertain.

Do not run installed and portable Launcher copies concurrently against the same default server home.
