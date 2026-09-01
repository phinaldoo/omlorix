# MCP Apps in Chat

An MCP App is an interactive view supplied by a connected service after a model uses one of its tools. A compatible connection can return either an app or a normal tool result.

## Use an app

Set up the service under **Workspace > Connections**, choose a compatible model, and add it from **@ > Connectors**. Describe the information or action you need. A connection can return a normal tool result without opening an app.

Wait until the app is ready before using its controls. If it prepares a message for the chat, review the destination, attachments, and complete text before confirming the send.

Omlorix opens an app only when the service declares a supported HTML or XHTML MCP App resource. A plain text or binary resource can be shown as a normal result instead. Oversized, invalidly encoded, or unsupported app content is refused even when the connection itself remains healthy; if this persists, ask the connection administrator or service operator to check the resource MIME type and payload.

## Review actions and access

An app can read information and may offer actions that change data in the connected service. Check the signed-in account, destination, record, and exact action before confirming. Verify important changes in the original service afterward.

When an app or preview asks to load external content, review the listed destinations and allow only services you trust. Reloading or downloading a chat may preserve only a static representation of the app.

The connected service and AI provider may receive the information needed for the request. Use a least-privileged account and never enter passwords, keys, recovery codes, or unrelated confidential information. Apps can change independently of Omlorix, so recheck them before sensitive work.

See [Workspace Connections](../10_workspace/4_connections.md) and [Personal MCP Servers](../10_workspace/4_personal_mcp_servers.md).
