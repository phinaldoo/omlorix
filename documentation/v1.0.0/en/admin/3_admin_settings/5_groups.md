# Groups

**Admin Settings > Groups** is the primary policy boundary for ordinary users. A user's active group controls workspace features, sharing, chat behavior, files, context, managed skills, connections, access windows, temporary accounts, and delegated management.

## Create and Organize Groups

- search the group list and inspect hierarchy, members, and managers
- select **New Group** or edit an existing group
- set an optional **Parent group** for hierarchy and delegated management scope
- assign **Owners**, **Managers**, and **Coordinators**
- duplicate a group when you need a similar starting policy

A parent does not make a child inherit settings. Review each group's settings independently. Keep at least one eligible Owner for a managed group and use the least powerful delegated role that fits the task.

- **Owners** can view members, promote them to higher group roles, manage settings, and manage temporary accounts.
- **Managers** can view members and manage settings and temporary accounts, but cannot promote members.
- **Coordinators** can view members and manage temporary accounts without changing group settings.

Delegated scope can include descendant groups in the hierarchy. Review the whole subtree before assigning or removing a role.

**Default user group** is assigned to new email, social, and enterprise SSO accounts unless a provisioning rule selects another group. Keep this group valid and minimally permissive.

## Policy and Context

Review the visible sections before adding members, especially **Skills**, **Projects**, **Automations**, **Agents**, **BYOK**, **Sharing permissions**, **Chat experience**, **Context enrichment**, **Data controls**, **File storage**, **User permissions**, **Connections**, **Compliance**, and **Access Windows**.

**Enable group context** applies **Context instructions** and selected **Context files** to conversations for the group. Save a new group before uploading context files. Treat instructions and files as content sent to eligible models; remove personal or confidential material that should not be shared with every member.

For managed workspace tools, enable the specific choices under **Enabled workspace connections**. **Allow file storage connections** is a separate opt-in for supported file-storage connections.

**Data controls** governs a member's complete Omlorix account archive export/import and user-scoped ChatGPT archive import. It does not grant access to the administrator-only Open WebUI migration page.

## Temporary Accounts

**Allow temporary accounts** lets delegated managers create expiring accounts for the group. Use **Max active temporary accounts** to cap simultaneous accounts and **Credential length** to set generated PIN-style credential strength. Revoked or expired temporary accounts follow the separate temporary-account policy under [Users](4_1_users.md).

Use temporary accounts only for a defined short-lived purpose. Test expiry and revocation, deliver credentials through an approved secure channel, and review the account's group permissions before issuing a batch.

## Import, Export, and Duplication

**Export Groups** downloads all group definitions and delegated-manager assignments. **Import Groups** previews selected definitions and creates each group independently, so some rows can succeed while other group, hierarchy, or manager rows fail. Select a parent together with its children unless that exact parent ID already exists on the destination. Existing group IDs or names are conflicts rather than updates.

Delegated-manager assignments refer to exact user IDs. Import the corresponding user archives first and confirm that those IDs were retained if the assignments must be restored; an email or display-name match is not used. Review the separate manager and hierarchy errors even when the groups themselves were created.

Group exports contain sensitive policy, context references, and connection-related configuration. They do not independently back up the underlying context-file contents or user accounts. The protected Artificial Analysis credential is stored encrypted and is not portable to an instance with a different `ENCRYPTION_KEY`; re-enter it after cross-instance import. After import, verify the hierarchy, managers, context files, protected credentials, and every access setting before assigning users.

Duplication is useful within one instance, but the copy is a separate policy object. Review its name, managers, context, credentials, and access windows rather than assuming later changes stay synchronized.

## Delete a Group

The current default group cannot be deleted. Deleting another group moves its users to the configured default group, which can immediately change their model, file, sharing, connection, and sign-in access. Update identity-provider mappings and delegated roles before deletion.

For every setting and the delegated-role workflow, see [Group Management](../4_user_group_management/5_group_settings.md).
