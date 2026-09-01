# Full-Instance Restore

A full restore replaces Omlorix data with an earlier recovery point. It can revive accounts deleted after that point, remove newer chats and files, and roll back security, provider, retention, and audit state. Restore is deliberately unavailable in the web admin.

Use **Restore backup** in the [Server Launcher](../2_setup/1_2_server_launcher.md) or follow the exact recovery procedure in the [Server CLI](../2_setup/1_3_server_cli.md). Those operator surfaces coordinate shutdown, replacement, rollback protection, and restart.

## Before the Maintenance Window

- select and verify the exact backup
- confirm that the original field-encryption key and archive passphrase are available
- preserve current deployment configuration and recovery secrets
- preserve the server's separate permanent-erasure safeguard
- back up remote user-file storage separately
- confirm release compatibility and sufficient working space
- preserve incident or legal evidence created after the recovery point
- arrange server access and an outage window

Test the backup in an isolated compatible environment whenever possible.

## Choose the Recovery Situation

For a newly provisioned or isolated instance, use the workflow that refuses to overwrite existing Omlorix data. This is the safest way to rehearse a restore.

For an existing instance, use the in-place recovery workflow. It requires an explicit destructive confirmation and creates and verifies a safety backup of the currently included state before replacement.

The safety backup does not contain remote user-file objects, deployment secrets, cache state, external observability data, provider-side changes, or the separately maintained permanent-erasure safeguard. Protect those separately.

Permanent erasure uses a private append-only two-phase ledger outside backup payloads. An intent is flushed before the account transaction commits and is completed afterward; offline startup resolves a crash-surviving intent from the live database. Restore is privacy-biased: it treats an unresolved intent as authoritative. Before replacing either database, the coordinator also writes an external reconciliation-required marker and clears it only after restored user and security-record state has been reconciled. A checkpoint contained in an older SQL backup therefore cannot suppress this pass after a crash.

## Run and Observe the Restore

1. Keep users out of the instance for the maintenance window.
2. In the Server Launcher or Server CLI workflow, choose the verified archive or completed backup job.
3. Review the recovery point, target, exclusions, and destructive warning.
4. Start the coordinated restore and keep the server-management process and host running.
5. Follow its final restart or rollback guidance exactly.

Do not run two server-management mutation workflows at the same time.

Before restart, the restore coordinator invalidates in-flight application-worker jobs and removes ephemeral operations, media, and rendering staging. Scheduled account lifecycle work is reconstructed from the restored account records. Interrupted generations, research runs, imports, exports, file processing, media requests, rendering requests, connector calls, audit delivery, and ordinary destructive actions are not replayed automatically; users or operators can retry only after validating the restored state and any external side effect. The exception is an idempotent audit-erasure privacy handoff: if the restored snapshot contains one for an account that is still deleted, the coordinator completes it before restart; if that account is active, it cancels the handoff.

Restore also revokes every restored session, consumes outstanding password-reset tokens, clears WebAuthn challenges, native-app grants, pending browser authentication actions, pending email changes, trusted-device markers, and email-security rate-limit state, and rotates the one-time email-action epoch. Pending, retrying, or leased system email is cancelled and its recipient/payload erased so an old reset link, sign-in code, security notice, or email-change message cannot be delivered from the recovery point. Users must sign in again and request fresh one-time actions.

## If Restore Fails

The supported workflow reports whether replacement did not begin, rollback to the safety backup succeeded, or the system may be in an unsafe partial state.

If it cannot confirm safe restart:

- keep Omlorix application services stopped
- preserve the complete sanitized output, source archive, safety backup, version, and current storage volumes
- do not repeatedly retry or manually edit the database
- determine whether the database and files represent the old state, restored state, or a mixture
- escalate through the approved support path without sharing secrets or backup archives

## Post-Restore Validation

Before reopening access:

1. Confirm database, application, worker, web interface, and proxy readiness.
2. Verify the expected Omlorix version and successful startup.
3. Sign in as an Owner and an ordinary user.
4. Confirm that accounts subject to completed permanent erasure remain absent, then check roles, groups, security rules, SSO and login, providers, tools, and scheduled work.
5. Verify representative chats and locally stored files.
6. Restore and reconcile remote file objects, then test downloads.
7. Confirm audit and log continuity, backup schedules, destination access, and retention.
8. Create and verify a new post-restore backup.

Record the selected recovery point, result, validation, missing changes after that point, and follow-up actions.
