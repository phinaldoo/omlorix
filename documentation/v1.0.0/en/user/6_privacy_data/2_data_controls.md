# Data Control

Open **Settings > Data Control** to delete broad categories of data or move supported account data between compatible Omlorix accounts.

## Delete chats or files

- **Delete Chats** permanently deletes all chats when deletion is allowed, or hides them when your organization's retention policy requires shadow deletion.
- **Delete Files** can delete all files, files older than a selected age, or web-search files only. A bulk deletion can finish partially; read the reported result before retrying it.

File deletion can break references in chats, projects, Notes, Automations, Skills, Agents, Canvases, presentations, and shared folders. Deleting chats does not automatically delete separately stored Workspace files. However, a meeting-transcript file referenced only by a deleted chat can be cleaned up with that chat.

## Download the complete account archive

**Download Everything** creates one JSON account archive. Existing records are written to fixed sections; an empty section simply has no records. The archive contains:

- account profile, email and user ID, sanitized account settings, and group metadata;
- every chat the server still retains, including archived, temporary, and shadow-deleted chats, with messages, bookmark state, Subagent history, and embedded Deep Research runs and artifacts;
- owned Notes and their history, personal Memories, Todo lists and tasks, Prompts, Projects, Agents and their assets, Skills and their bundled resources, Automations, and slide presentations and their artifacts;
- owned Workspace files, folders, and file contents;
- accepted shared Skill, Prompt, folder, and Agent subscription records;
- personal connection metadata, personal MCP server definitions, model-setting presets, usage statistics, feedback, and available activity and non-secret authentication metadata; and
- an export-coverage manifest that identifies intentionally excluded sections.

The JSON can contain private conversation text and inline Base64-encoded file content. Store it like a sensitive backup and delete unneeded copies securely.

This is a portable account archive, not a complete server backup or exact account clone. The following sections are present for access, audit, or portability records but are **export-only** in self-service import: group metadata, non-secret authentication metadata, activity logs, feedback, usage statistics, and shared Agent subscriptions.

The archive deliberately excludes credentials and instance-managed security state:

- passwords, passkeys, session tokens, social sign-in bindings, and reusable connection or MCP credentials;
- OAuth handshake state, queued email-delivery jobs, pending email changes, one-time pending authentication actions, and trusted-device notification markers;
- SCIM provisioning state and Workspace notifications; and
- browser-local BYOK provider/model setup and its protected tab credential tokens.

Those records must not replay on another instance. After import, sign in again when asked, request a new email change or recovery action, and reconnect each external provider.

Only retained data can be exported. A Temporary Chat configured for no server storage, or any record already purged, is absent. Retained temporary and shadow-deleted chats are included but remain hidden after import. The archive includes personal Memories, not shared project Memories. It includes owned Projects, not project memberships. Sharing restoration varies by item type and must be reviewed separately.

## Import an account archive

**Import Account Data** accepts only the complete JSON archive created by **Download Everything**. It does not accept an individual chat JSON, Note MD/PDF, or a category-only export.

After you choose a file, the confirmation checks that it is JSON with the supported account-archive type and version and shows its top-level section count. It is not a full validation preview. Select **Start Import** only when you trust the archive and its source; each feature validates its own records during the merge.

Import adds supported content to the signed-in account. It does not replace the account or change its email, identity, password, passkeys, role, group, or server-managed settings. Portable General, Appearance, Chat, and Memory settings are merged, together with profile visibility, the supported personal-information access choices, and whether the first-run welcome card was dismissed. Organization-managed settings remain authoritative.

The restore runs section by section and is not one atomic transaction. One section can succeed while another fails, and there is no single undo. Some records are deduplicated or skipped, while others are recreated with new local IDs and can produce additional copies on a retry. Always read the final imported, failed, skipped, and **needs review** counts before retrying a partial import.

Imported chats, Projects, Todo lists, Notes, Prompts, and Agents do not reactivate their source share links or invitations. Owned Skill and ordinary folder records can carry supported share identifiers into the destination; conflicting folder identifiers can be regenerated, while a Skill conflict can cause that section to fail. Accepted shared Skill and Prompt subscriptions are attempted only when their source records still resolve. Shared-folder subscriptions are restored only when Omlorix can resolve the folder or active share; unresolved subscriptions produce a warning. Shared Agent subscriptions are export-only, and Note and Todo subscriptions are not restored. Review **Settings > Shared Items** immediately. For a cross-instance move, revoke any carried Skill or folder link and create a new destination-specific link instead of relying on an imported identifier.

Personal connection records are restored without OAuth tokens or access credentials. Personal MCP definitions receive new local IDs and exclude reusable headers, OAuth tokens, and other credentials. Automation references are remapped to recreated MCP servers when the destination model and access policy allow them. If selected MCP context cannot be restored, the Automation remains available but its missing selection is removed and reported as needing review.

After import:

- reconnect external services and re-enter credentials;
- review shared items and project memberships;
- open restored Notes, Agents, Skills, Projects, presentations, and Automations and check their files, models, MCP servers, and other dependencies;
- confirm that referenced imported files open; and
- review available models and imported model-setting presets before continuing restored chats.

Archive actions can be hidden by group policy. Importing a record does not enable a feature that is disabled for the destination account. Open WebUI archives and whole-user administrative migrations require an administrator workflow.

## Import from ChatGPT

Keep the ChatGPT export as a ZIP; do not unpack and repackage it. Under **ChatGPT Export Archive**, select **Choose Archive**, review the inline confirmation, and start the import. Omlorix imports conversations, messages, and supported attachments into the current account. It validates archive paths, compressed and uncompressed sizes, entry counts, compression ratios, attachment types, per-file upload limits, and account file/storage quotas before or during the import.

For a branched ChatGPT conversation, Omlorix imports the branch ending at the exported current node. System and visually hidden messages are omitted; supported reasoning, tool-result text, citations, and attachments are converted into the closest Omlorix message blocks. Missing or unsupported content can be skipped, and ChatGPT share-index entries do not recreate share links or memberships.

Previously imported ChatGPT conversations are detected by their ChatGPT conversation IDs and skipped. The completion message reports imported chats, messages, and files as well as skipped conversations and duplicates. A malformed or unsupported archive is rejected without replacing existing account data. Because valid conversations are committed independently, inspect the reported counts before retrying an archive that was only partly imported. Resolve a file type, quota, or archive limit before retrying rather than repeatedly uploading the same archive.

For a readable copy of one conversation, use [Download Chats](../4_chat_conversations/6_download_chats.md). To delete the account itself, use **Settings > Profile > Delete Account**. Exporting data does not create a backup schedule; deleting data does not automatically revoke [Shared Items](3_shared_items.md) or erase information already held by an external service.
