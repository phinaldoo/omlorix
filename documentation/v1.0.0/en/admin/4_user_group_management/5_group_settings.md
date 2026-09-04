# Group Management

Groups control the features, sharing, limits, retention, and access windows applied to Users. Admin accounts are not a reliable test for group restrictions; always test with an ordinary member.

Open **Admin Settings → Groups** to create, edit, duplicate, import, export, or delete groups. Use **Admin Settings → Users → Edit user** to move a user.

## Hierarchy and defaults

Each group can have one **Parent group**. The hierarchy extends delegated management scope to descendants, but settings are independent: children do not inherit settings from parents.

Use **Registration defaults → Default user group** for new accounts when no provisioning rule chooses another group. The Default group cannot be deleted. Omlorix also prevents circular hierarchy and deletion of a group that still has children.

## Create or edit

1. Select **New Group** or open an existing group.
2. Under **General**, set **Group name**, optional **Parent group**, and description.
3. Under **Management**, assign delegated roles and configure temporary accounts if needed.
4. Review every relevant settings section.
5. Select **Save Group**, reopen it, and test with a member.

Group names must be unique. A new group starts with standard defaults, not its parent's configuration.

## Settings reference

| Section | Controls |
|---|---|
| **Management** | **Owners**, **Managers**, **Coordinators**, temporary-account permission, active limit, credential length |
| **Skills**, **Projects**, **Automations**, **Todo lists**, **Notes**, **Memories**, **Prompt library**, **Bookmarks**, **Agents** | Feature availability and related sharing where offered. Memories also selects the dedicated completion model used after every user message; an empty selection uses the current chat model. |
| **BYOK** | User-supplied provider access, allowed tools, and search/scrape/title defaults |
| **Sharing permissions** | Chat and artifact sharing |
| **Chat experience** | Temporary chats, response actions, message/chat deletion, automatic and shadow-deletion retention |
| **Context enrichment** | Group instructions and context files added to conversations |
| **Data controls** | Complete Omlorix account archive export/import and ChatGPT archive import permission |
| **File storage** | Upload permission, files per upload, and per-user storage |
| **User permissions** | Profile picture, name/email/password changes, and self-account deletion |
| **Connections** | Personal MCP servers, file-storage connections, and selected workspace connections |
| **Leaderboard** | Leaderboard availability and Artificial Analysis configuration |
| **Compliance** | Chat warning and export watermark |
| **Access Windows** | Timezone, allow/block schedule, next available time, and blocked message |

Options appear only when their parent feature is enabled. A sharing switch does not grant the underlying feature, a model assignment does not override group policy, and a personal preference cannot grant access that the group denies.

Disabling Memories for a group hides its members' **Workspace > Memories** page, stops automatic extraction, and omits stored memory from new model requests. It does not delete existing facts. Re-enabling it resumes the same collection. Select a low-cost model with enough context for the complete current profile and enough output for structured extraction; the model is never exposed a Memories tool.

### Chat retention

- **Persist temporary chats** stores temporary chats; optional temporary-chat retention later deletes them.
- **Allow manual chat deletion** exposes deletion to members.
- **Shadow delete chats** hides manually deleted chats; optional shadow-deletion retention permanently removes them later.
- **Auto delete chats** removes chats after the configured period independently of manual deletion.

Confirm legal, backup, and user-notice consequences before enabling automatic deletion.

### Access Windows

Turn on **Enable access windows**, choose **Timezone** and **Access mode**, then add **Access rules** manually or with a quick preset. **Allowlist** permits only the configured periods; **Blocklist** denies them. A rule whose end is earlier than its start continues overnight.

Use **Show next available time** and **Blocked message** when helpful. Owner and Admin access remains available for recovery, so test with an ordinary user in the exact group.

## Delegated management

Delegated assignments cover the selected group and descendants but do not grant Admin Settings access or group membership.

| Role | View members | Edit supported group settings | Promote members | Manage temporary accounts |
|---|---:|---:|---:|---:|
| **Owner** | Yes | Yes | Yes | Yes |
| **Manager** | Yes | Yes | No | Yes |
| **Coordinator** | Yes | No | No | Yes |

Delegated users work under **Settings → Managed Groups**. Owners and Managers can edit a limited subset of group settings there; Coordinators can view settings and manage temporary accounts. Only an Owner can promote a direct member to a higher delegated role. Lowering or removing a role requires Admin Settings.

Assign only active, approved, permanent accounts. Once a group has an eligible Owner, keep at least one; add a replacement in the same save before removing the previous one.

## Temporary accounts

Enable **Allow temporary accounts**, then set **Max active temporary accounts** and **Credential length**. Delegated managers create and revoke them under **Managed Groups → Temporary Access**.

Generated credentials are displayed once. Copy them immediately and send them securely. Expired or revoked account data follows the temporary-account policy under **Admin Settings → Users → User Data Retention**.

## Duplicate, import, export, and delete

- **Duplicate group** copies configuration but not members or delegated assignments.
- **Export Groups** downloads group metadata, settings, hierarchy references, and delegated-manager assignments, but not users or context-file contents.
- **Import Groups** creates selected groups; it does not update conflicts. Include selected parents with their children, import referenced users first when exact manager user IDs must survive, and review group, hierarchy, and manager errors because partial success is possible.
- **Delete group** is permanent. Move/reparent children and choose another Default group first. Direct members move to the Default group after deletion.

Treat exports as sensitive configuration. A protected Artificial Analysis credential cannot be reused across instances with different `ENCRYPTION_KEY` values; re-enter it after import. Then test access windows, sharing, retention, connections, context files, and tool access with a direct member and a descendant-group delegate. Keep a current export after major changes, but use [full backups](../3_admin_settings/23_1_backups.md) for instance recovery.
