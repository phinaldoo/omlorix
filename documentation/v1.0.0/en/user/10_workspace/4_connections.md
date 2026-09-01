# Connections

Open **Workspace > Connections** to connect external services made available to your account. A connection belongs to your Omlorix account, so verify the external account before approving access.

## Server connections

Search for a service and select it. Depending on the service, use **Connect with OAuth**, **Connect with access token**, or another displayed sign-in method. Review the requested account and permissions before approving.

A connection can be:

- a tool connection that a supported model can use in chat; or
- a **File source adapter** that appears in the chat file menu but is not available to the model as a tool.

On the connection page, inspect **Connection tools**, then enable or disable, reconnect, replace credentials, or remove the connection. Disabled connections are unavailable in chats without being deleted. Access tokens are protected and are not shown again after saving.

Add an eligible connection through `@` > **Connectors** or the displayed connection selector. A compatible model can search, read, create, update, send, or delete external data only according to the connection's available tools. Review the account, destination, and exact change before approving or relying on it.

Removing a connection does not undo actions it already performed or delete files already imported. Revoke Omlorix's access at the external service too when an account or token may be exposed, and verify important changes in the original service.

The complete account archive carries supported personal connection metadata, but strips access tokens, refresh tokens, API credentials, and temporary OAuth handshake state. Import does not reconnect the external account. On the destination instance, confirm that the provider is available, connect again, and verify the account and permissions before using its tools. Browser-local BYOK providers are separate and are not part of this connection archive.

See [Cloud File Imports](5_cloud_file_imports_sync.md) and [Personal MCP Servers](4_personal_mcp_servers.md).
