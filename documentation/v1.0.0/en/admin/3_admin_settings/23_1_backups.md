# Backups, Destinations & Schedules

**Admin Settings > Database** creates full-instance backup archives. A backup includes the main and audit databases, persistent application data and logs, and user files stored in Omlorix-managed local storage.

A full-instance backup does not include cache state, external observability data, deployment secrets or configuration, or file objects held in S3, GCS, Azure, or WebDAV user-file storage. Database references to remote objects can be present without the objects. Protect remote storage separately; see [User File Storage](../2_setup/6_1_user_file_storage.md).

## Recovery Material

Keep a protected recovery bundle outside the backup archive with:

- the original field-encryption key
- every archive passphrase required by retained backups
- authentication and stable-hashing secrets
- database, storage, email, identity, provider, and destination credentials
- deployment configuration, certificates, and the compatible Omlorix release
- a separate backup of remote user-file storage
- the server's permanent-erasure safeguard, which is deliberately kept outside full-instance backup archives

Losing the original field-encryption key can make protected restored values unreadable. A newly configured archive passphrase does not unlock an older backup created with another passphrase. Without the separate permanent-erasure safeguard, an older archive restored on a newly provisioned server can contain accounts that were permanently deleted later.

## Backup Destinations

Supported providers are **Local**, **S3**, **GCS**, **Azure**, and **WebDAV**.

Local destinations keep their backup artifacts in Omlorix's durable backup volume. S3, GCS, Azure, and WebDAV destinations are remote-only: Omlorix uses the local backup volume as temporary workspace while it builds and uploads an archive, then removes that job's local archive on both success and failure. Size the local volume for the peak workspace needed to create one backup, but not for the cumulative retention of remote backups. If the process is interrupted abruptly, scheduled maintenance removes abandoned staging work after the configured staging-retention period.

1. Select **Add destination**.
2. Enter **Destination name** and choose **Provider**.
3. Complete **Provider details**.
4. Keep **Verify TLS certificate** enabled for WebDAV.
5. Turn on **Destination can receive backups**, save, and select **Test**.

Leave an existing protected secret field blank to retain its saved value. Use **Remove saved secret** only when the credential should be cleared. Use **Additional JSON settings** only for a documented provider option that is not represented by the named fields.

A successful test confirms a small operation with the current credential. It does not prove that a large backup, retention cleanup, encryption, or restore will succeed. Keep at least one copy outside the Omlorix host and outside the production account or credential boundary.

## Backup Now

1. Choose **Destination**.
2. Keep **Create encrypted archive** enabled.
3. Select **Create Full Backup**.
4. Follow the job in **Backup History**.
5. When it completes, select **Verify**.

If encrypted backup creation is unavailable, configure the required archive passphrase through the server-management workflow, restart Omlorix, and return to this page. Do not place a passphrase in an unrelated field. Create an unencrypted archive only when the deployment explicitly allows it and an approved temporary exception requires it.

Verification detects a missing, changed, or incomplete stored artifact. A rehearsed restore into an isolated compatible instance remains the strongest recovery test.

## Backup Schedules

Select **Open Schedule Modal**. In **Add Backup Schedule**, configure:

- **Schedule name**
- **Timezone**
- **Frequency**: hourly, daily, or weekly
- the displayed minute, time, or weekdays
- **Retention policy** by count, age, or both
- **Destination**
- **Schedule is active**

Use **Run now** after saving. Monitor the first scheduled run and alert on later failures. Confirm that retention actually removes old artifacts from the destination, including any provider version history that your policy covers.

Schedule retention applies to successful backups created by that schedule. When both count and age are set, a backup is removed as soon as either limit selects it. Manually created backups need their own review and deletion process.

## Backup History

History shows status, creation time, size, artifact location, and errors. Completed jobs can offer **Verify**, **Download**, and **Delete**.

Deleting a job also requests deletion of its stored artifact. Confirm destination-side deletion and versioning when required. A downloaded archive contains sensitive instance data; restrict, encrypt, and delete local copies according to policy.

## Download through the Launcher or CLI

The server-management surfaces can materialize a completed job without revealing its backing storage URI:

- In the Launcher, open **Status > Backup & recovery**, select a job under **Download a completed backup**, and choose **Download selected backup**.
- With the CLI, run `omlorix-server backup download <job-id> --output <new-path>`. Add `--json` for automation-safe result metadata.

Only successful catalogued jobs with an available artifact can be downloaded. The download path is committed only after the complete artifact has streamed to a private temporary file. Existing destination paths are never overwritten, and interrupted downloads remove their partial temporary file. Catalog checksum or size mismatches fail the operation.

Download does not create a new backup, delete the destination artifact, or prove that restore will succeed. Run **Verify** or `backup-verify`, retain the original recovery secrets separately, and protect every downloaded copy.

## Routine Checklist

- Run encrypted backups on an approved schedule and after major changes.
- Keep at least one verified off-host and separately protected copy.
- Monitor capacity, credentials, certificates, retention, and failed jobs.
- Preserve recovery secrets separately and test access to them.
- Rehearse [Full-Instance Restore](23_2_restore.md), including remote file storage.
