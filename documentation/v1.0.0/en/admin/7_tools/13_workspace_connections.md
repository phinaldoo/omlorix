# Workspace Connections

**Workspace Connections** let users connect approved services from **Workspace > Connections**. Some connections expose MCP tools in chat; others import cloud files into Omlorix.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then apply the connection-specific OAuth and offboarding checks below.

## Connection types

- Managed MCP connections can provide tools from services such as GitHub, Notion, Slack, Gmail, and Google Calendar.
- Google Drive can import cloud files into Omlorix.
- Personal MCP servers are user-defined endpoints controlled by a separate group setting.

The available provider list in your installation is authoritative.

## Configure a managed connection

1. Configure Omlorix's exact **Public URL** and HTTPS before creating OAuth applications.
2. Create the OAuth or service application at the external provider using the redirect address shown by Omlorix.
3. Request the smallest scopes needed for the intended read and write actions.
4. Enter the client details in the relevant administrator OAuth settings.
5. Add the provider to the group's **Enabled workspace connections**. For cloud imports, also enable **Allow file storage connections**.
6. Connect as a normal pilot user and use **Show tools** or the available file picker to test real access.
7. Enable the connection on selected models only after the user connection works.

OAuth success proves account authorization, not MCP health or tool permissions. Test both connection and a representative action.

## Group policy

- **Enabled workspace connections** controls the managed catalog.
- **Allow file storage connections** controls cloud-file imports.
- **Enable personal MCP servers** controls user-created MCP servers only.

For users in multiple groups, test the effective result rather than assuming one group wins.

## Security and lifecycle

Connections can read or change external data. Review every requested scope, available tool, user confirmation behavior, data destination, provider retention, rate limit, and audit trail. Prefer read-only scopes and exclude destructive tools unless there is a clear operational need.

Imported cloud files become Omlorix files and remain after the connection is removed until separately deleted. Removing a connection from Omlorix does not always revoke the provider grant; include provider-side revocation in offboarding.

A user or administrator account archive includes supported connection metadata but removes the reusable `secrets` field, and temporary OAuth handshake state is explicitly excluded. An imported connection cannot authorize provider access until that user reconnects or reauthorizes it. Imported Google Drive files remain ordinary Omlorix files, but the archive does not preserve live access to the cloud account. Instance OAuth client configuration and provider-side grants must be restored and verified separately.

Allow required provider destinations through [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md). Rotate client secrets before expiry, retest after scope changes, and communicate reauthorization to users.

If a provider card is missing, check the user's effective group settings and completed administrator configuration. For redirect errors, compare the exact public origin and registered callback. If **Connected** appears but tools fail, check scopes, provider account access, **Show tools**, model assignment, and outbound policy.
