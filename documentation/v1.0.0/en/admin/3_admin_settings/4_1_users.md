# Users

**Admin Settings > Users** is the account directory, individual settings editor, deleted-user queue, and canonical administrator workflow for complete user transfer.

## Account Directory and Provisioning

Search or page through **Name**, **Email**, **Group**, **Role**, **Status**, **Last Active**, and **Actions**.

- **Add user** opens **Create User** for one local account and can require a password change at first sign-in.
- **Bulk import users** opens **Bulk Import Users** and uses the downloadable CSV or XLSX template. Each created user receives a unique temporary password derived from the supplied base password; copy the results before closing them.
- **Manage Notifications** opens [User Notifications](4_2_user_notifications.md).
- **Deleted Users** opens the restore and permanent-deletion queue.
- **Import/Export Users** moves complete Omlorix user archives.

Activation, role changes, profile access, account deletion, imports, and exports are audited.

## Edit and Recover an Account

Opening **Edit User** requires a reason. Omlorix records that reason with the categories of personal and security data viewed.

The editor can manage the user's profile, group, account lock, failed sign-in count, permitted preferences, and security state. For locally managed accounts:

- changing **Email** takes effect immediately, signs the user out on every device, invalidates pending reset/email/authentication actions, and queues security notices to both the old and new addresses
- setting **New Password** replaces the password, signs the user out on every device, invalidates other password-reset links, pending email changes, and one-time authentication state, and queues a security notice
- **Reset 2FA** clears enrollment and pending verification so the user can enroll again
- **Force password change** requires a new password at the next sign-in

Changing an email address or password requires the initiating administrator to complete recent security verification with an available password, enrolled 2FA method, or passkey. A freshly authenticated session is accepted only when that administrator has no usable step-up factor. Administrative email replacement is different from a user's verified self-service email-change flow; confirm the new address and authorization before saving.

Save or discard other pending edits before resetting 2FA. Do not change **User State** legal-acceptance fields to claim that a person accepted a document; use them only for an approved, audited correction. Identity fields for externally managed accounts must be changed at the identity provider.

Use **Edit user** only for the profile, settings, and security state needed for the approved task. Conversation content is reviewed through the separately audited [Chats](12_chats.md) workspace, which requires its own access reason.

Only the **Owner** can manage Admin accounts or grant and remove the **Admin** role. The Owner account is protected, administrators cannot change their own role or status here, and Omlorix prevents removal or deactivation of the last active administrator. See [Roles and Instance Ownership](../4_user_group_management/2_roles_and_ownership.md).

## Deletion and Restore

The **User Data Retention** section controls ordinary and temporary accounts:

- **User deletion mode:** **Delete instantly**, **Delete after N days (allows restore)**, or **Keep forever (soft-delete only)**
- **Temporary account deletion mode:** the equivalent policy for expired or revoked temporary accounts

A deletion can be blocked while the user is required for delegated group leadership. Reassign that responsibility first.

A soft-deleted user cannot sign in. Starting a soft deletion or selecting **Delete permanently** requires the initiating administrator's recent security verification. In **Deleted Users**, use **Restore user** to reactivate a retained account or **Cancel scheduled deletion** to remove its scheduled purge. Permanent deletion erases the account immediately and cannot be undone. Backups, downloaded exports, and external providers keep their own retention schedules; review [Post-Deletion Retention](22_6_post_deletion_retention.md).

## Canonical User Archives

Use **Import/Export Users**, not **Chats**, for Omlorix-to-Omlorix migration:

1. **Export one user** or **Export all users** queues a background job.
2. Return to **Export Jobs**, refresh until complete, and download the ZIP.
3. Delete the generated job file and downloaded copies when no longer needed.
4. **Import one or more users** previews the archive and lets you select accounts.
5. Supply the destination **Default password** and decide whether to **Force password change**.

Archives contain portable sections for the account profile and user-owned content: every chat the server still retains, including archived, saved-temporary, and shadow-deleted chats, with message bookmarks and embedded Subagent and Deep Research history; owned files, folders, and projects; Notes and their Version History; Todo lists and tasks; personal Memories; Skills and bundled resources; Agents and assets; Prompts; Automations; model presets; presentations and artifacts; personal connection metadata; personal MCP server definitions; and sanitized user settings. Accepted shared Skill, Prompt, folder, and Agent subscription records are also present, although not all of them are restorable. Project memberships and shared project Memories are not included.

Personal MCP servers receive new IDs, and portable Automation references are remapped when destination access permits. Personal connections are restored as metadata only. Reusable connection credentials, MCP headers or OAuth tokens, and browser-local BYOK provider/model definitions and tab credentials must be recreated.

Group metadata, non-secret active-token metadata, activity logs, feedback, usage statistics, and shared Agent subscriptions are exported as evidence but are not restored as authority. Password hashes, password-reset tokens, and live session credentials are excluded, as are normalized social sign-in bindings, OAuth handshake state, authoritative SCIM links and memberships, queued system email, pending email-change proofs, one-time browser authentication actions, and trusted-device notification markers. User Notifications, group definitions and policy, identity-provider configuration, and other instance-owned state remain outside the archive. Authentication-management fields—mode, provider attribution, and timestamp—can appear as profile evidence, but import does not apply them as authority. Sanitized user settings can still contain SAML/OIDC and LDAP linkage metadata and legacy SCIM settings; that metadata is not proof that the destination identity integration is configured or that the identity has been re-provisioned.

Sharing capabilities are not handled uniformly. Imported chats, projects, Prompts, Agents, and Todo lists are private. Administrator import can retain an owned Note's share identifiers, an owned Skill can retain its share identifiers, and an ordinary owned folder can retain supported share identifiers. A conflicting Note or folder identifier is regenerated; a conflicting Skill identifier can fail the Skill section. Accepted shared Skill, Prompt, and folder subscriptions are attempted only where their referenced source still resolves. Shared Agent subscriptions are export-only, Note and Todo subscriptions are skipped, and project memberships are not restored. After every import, audit the account's shared items and subscriptions. For a cross-instance move, revoke every carried Note, Skill, or folder link and create a destination-specific replacement before treating an old bearer URL as invalid.

An email match updates the existing destination account and merges supported content; supplying the import password also replaces that account's password, revokes every session, and clears pending browser authentication actions, native-app grants, and WebAuthn challenges. New imported accounts are created as active ordinary users in the configured Default group. Imports do not restore the exported role or group or create administrative authority. They merge retained SSO/LDAP linkage metadata and legacy SCIM settings, but do not recreate normalized social bindings, authoritative SCIM records, provider configuration, or external-management authority. Before granting access after a cross-instance import, clear or reconcile that metadata and re-provision and test the destination identity integration. Content sections have independent failure boundaries, so a later failure does not roll back sections already committed and a retry can create additional copies. Treat the supplied default password as a credential, deliver it through an approved channel, and review selected emails, destination groups, warnings, skipped duplicates, and partial failures before granting access.

Exports contain extensive personal data. Require a valid purpose, restrict access, and use a full [Backup](23_1_backups.md) when the goal is disaster recovery for the whole instance.

Use [Chats](12_chats.md) only for Open WebUI conversation migration.
