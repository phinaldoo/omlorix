# Post-Deletion Retention

Deleting an account starts a lifecycle that can use different schedules for account data and security records. Configure the policies together so the outcome matches approved privacy, legal-hold, incident-response, and recovery requirements.

## Account Data

Under **Admin Settings > Users > User Data Retention**:

- **User deletion mode** controls ordinary deleted accounts: **Delete instantly**, **Delete after N days (allows restore)**, or **Keep forever (soft-delete only)**.
- **Retention window (days)** appears for delayed deletion.
- **Temporary account deletion mode** and **Temporary account retention window (days)** apply after a temporary account expires or is revoked.

With delayed deletion, the account appears in **Deleted Users** until its scheduled erasure. An authorized administrator can restore it or cancel the scheduled deletion during that window. Starting deletion or selecting **Permanently delete** requires the initiating administrator's recent security verification; permanent deletion removes the account immediately and cannot be undone through Omlorix. Transfer any sole delegated group-management responsibility before deletion; Omlorix can block a deletion that would leave a managed group without eligible leadership.

Soft deletion revokes sessions, invalidates password-reset links and pending email/authentication actions, clears trusted-device markers, cancels queued system messages, and queues an account-deactivation or scheduled-deletion security notice. Restoring the account does not restore those credentials or transient proofs; the user must authenticate through a currently valid method.

**Keep forever (soft-delete only)** prevents automatic erasure. Use it only with a documented purpose, restricted restoration access, and recurring review.

## What Permanent Account Deletion Removes

Permanent deletion removes the user's Omlorix account, active sessions and authentication state, pending email and outbox state, worker jobs, social and provisioning links, conversations and messages, current owned file objects and folders, projects, notes, todos, memories, prompts, skills, Agents, Automations, presentations, personal connections, MCP servers, sharing subscriptions, delegated memberships, feedback, and attributed generation, tool, and realtime statistics. Omlorix queues a final detached account-deletion security notice when notification is enabled; delivery still depends on the configured email service.

Omlorix requests deletion of current owned file objects from the configured local or remote storage. If the action reports a storage-cleanup failure, treat erasure as incomplete and investigate the storage destination before confirming completion.

Audit records, authentication logs, and user-scoped Admin Notifications follow the separate Security policies below. Backups, exports, remote systems, and third-party processors are not erased by the account deletion itself.

Because deletion is broad, inspect or export required account data before approving it; see [Users](4_1_users.md). Never keep a user active merely to preserve records that belong under a defined retention or legal-hold process.

## Security Records

Under **Admin Settings > Security**:

- **Per-user deletion retention** controls authentication records.
- **Post-deletion retention** applies one policy to audit records and user-scoped Admin Notifications.

Each offers **Delete instantly**, **Delete after N days**, or **Keep forever**. Routine authentication-log age or count cleanup is separate and can remove records earlier under its own policy.

For immediate audit deletion, Omlorix atomically fences the subject, cancels and redacts queued audit delivery, and records a durable cross-database cleanup handoff in the same account-state transaction. The Audit Event Worker completes existing audit-row and Admin Notification cleanup; offline startup repeats any crash-surviving handoff, including in inline-worker deployments. Restoring a soft-deleted account serializes against that cleanup and cancels an unstarted handoff.

The [Audit Logs](22_4_audit_logs.md) page shows the effective deleted-user audit policy with each result snapshot and export. That summary is context, not proof that every expected event still exists.

## Systems Outside Account Deletion

Deleting an Omlorix account does not automatically erase:

- full backup archives or infrastructure snapshots
- storage-provider versions, snapshots, replicas, or other copies outside the current file object
- archives downloaded by users or administrators
- data already sent to model, search, tool, identity, mail, telemetry, or connection providers
- external logs, support tickets, legal holds, replicas, or cached copies

Document these systems in the [Processor & Transfer Register](22_5_processor_transfer_register.md). Give each an owner, deletion path, and maximum backup-expiry period.

## Change Checklist

1. Map account data, authentication records, audit records, notifications, remote files, providers, exports, and backups.
2. Choose the shortest approved period for each category.
3. Define who may restore, cancel, or permanently delete and what evidence is required.
4. Test delete, restore, cancellation, expiry, and permanent deletion with a non-production account.
5. Update the Privacy Policy, user-request procedure, and recovery documentation.
6. Record the effective date and review already scheduled deletions after a policy change.

A settings change controls subsequent cleanup behavior. Verify existing exports, backups, and external copies separately.
