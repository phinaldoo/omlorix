# MCP Servers

Model Context Protocol (MCP) connects Omlorix models to external tools and data. Administrators can create shared MCP servers; users may create personal servers only when their group permits it. Managed **Workspace Connections** are configured separately.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then apply the MCP-specific allowlist and authorization checks below.

## Add an administrator MCP server

1. Open **Admin Settings > Tools > MCP Servers** and create a server.
2. Enter a clear **Name**, **Description**, and unique **Namespace**.
3. Choose **Streamable HTTP** or **SSE (legacy)**, according to the remote server.
4. Enter the remote server URL and configure its required headers or OAuth authorization.
5. Select **Test Connection** and review the discovered tools.
6. Limit **Allowed Tools**, save, and assign the MCP server only to intended models.
7. Test a read-only call, an error, and an approved write action as a normal user.

**Test Connection** verifies discovery at that moment. It does not prove that every tool call, user-specific permission, OAuth grant, file result, or long-running action will work.

## Resources and MCP Apps

Omlorix can read bounded text or base64-encoded binary MCP resources. A resource response may contain at most 100 content items, and the selected text or binary payload may be at most 5,000,000 bytes. Invalid base64, empty unsupported content, excessive content counts, and oversized payloads are rejected as resource-content errors.

Interactive MCP App frames are created only from resources whose declared MIME type has a supported HTML essence: `text/html`, `text/html+skybridge`, or `application/xhtml+xml`. Parameters such as the MCP App profile or character set are allowed; a value that merely contains the word `html` is not enough. A normal text or binary resource can still be returned without opening an app.

A content-policy rejection does not mark an otherwise reachable MCP server as unavailable. Treat it as a resource or server-implementation problem, keep the connection status separate from content compatibility, and inspect the resource MIME type, encoding, item count, and size before retrying.

## Access layers

An MCP tool reaches a chat only when all relevant layers allow it:

- the MCP server is enabled and healthy;
- the specific tool is allowed;
- the model includes MCP and the server;
- the user's effective group permits the feature;
- the user has any required connection or OAuth grant.

**Enable personal MCP servers** controls user-created servers only. It does not enable administrator MCP servers or managed [Workspace Connections](13_workspace_connections.md).

## Security

MCP tools can disclose data, modify external systems, return files, or run commands on the connected service. Use the smallest tool allowlist, prefer read-only access, and test authorization with a non-admin account. Do not assume the model will ask for confirmation before a destructive action.

Use HTTPS and least-privilege OAuth or credentials. Avoid secrets in names, descriptions, or exported files. **SSE (legacy)** is available only for servers that still require it; prefer **Streamable HTTP** for new connections.

Review the server operator, data sent in tool arguments, result retention, prompt-injection exposure, rate and cost limits, incident response, and offboarding. [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md) also applies.

## Import and export

Use **Export All** and **Import All** on **Admin Settings > Tools > MCP Servers** for administrator servers. The version 2.0 bundle is remote-server configuration only:

- it includes the source ID as relationship metadata plus name, icon, description, namespace, transport, URL, enabled state, authentication mode, allowed tools, and timeout;
- it excludes header values, OAuth tokens and grants, runtime status, and local-process configuration;
- import creates a fresh server ID, preserves the exported enabled state and allowed-tool list, and never updates an existing server;
- model references to the source server ID are not remapped. Replace the IDs in a protected model-export copy or reselect the imported server on every model;
- read every per-item import error, add required headers or complete OAuth again, test discovery, review the current tool catalog, and only then assign models.

Personal MCP servers imported through an account archive also receive new local IDs. Compatible Automation selections are remapped when destination access permits; unavailable selections are reported for review.

Disable a server before rotating or retiring it. Deleting the Omlorix server removes availability but does not revoke credentials or delete data at the external service.
