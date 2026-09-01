# Personal MCP Servers

Personal MCP servers expose remote-service tools to compatible models. Open **Workspace > Connections > Custom MCP connections**. This section appears only when personal MCP servers are allowed for your account.

## Add a server

Select **Add server** and complete the fields shown:

- **Server name**, **Icon**, and **Description** identify it in Omlorix.
- **Server URL** is the address of the trusted remote MCP server.
- **Transport** selects **Streamable HTTP** or **SSE (legacy)** as required by the server.
- **Namespace** helps distinguish its tools.
- **Authentication** uses static **Headers** or **OAuth 2.0**.
- **Allowed tools** can restrict the connection to named tools; leaving it empty allows every discovered tool.
- **Timeout** controls how long Omlorix waits for the server.

Select **Test connection** and inspect every discovered tool, description, and parameter in **Live tool preview** before saving. For **OAuth 2.0**, save the server, select **Connect OAuth**, and then test it.

## Use and manage safely

Enable the server, choose a compatible model, and add the connection to the request. A connection makes its allowed tools available; the model still decides whether to call them, and a successful connection test does not prove every later action is safe.

Use a trusted server and the least-privileged account possible. Treat authentication headers, OAuth access, tool inputs, and returned data as sensitive. Restrict **Allowed tools** when the server offers actions you do not need.

Editing, disabling, or deleting the connection does not reverse earlier tool actions. Revoke headers or OAuth access at the MCP service if they may have been exposed.

## Move a server with an account archive

The complete archive under **Settings > Data Control** includes personal MCP server definitions but excludes reusable headers, OAuth tokens, and other credentials. Import creates a new local server ID and remaps compatible Automation selections to it when your destination model and access policy allow the server. A selection that cannot be restored is removed from the Automation and reported as needing review rather than silently granting access.

After import, re-enter headers or reconnect OAuth, test the server, inspect its discovered and allowed tools, and review any Automations that reference it. See [Data Control](../6_privacy_data/2_data_controls.md) and [Automated Tasks](../7_notifications_automation/1_automated_tasks.md).

See [MCP Apps in Chat](../2_chat_features/8_mcp_apps.md).
