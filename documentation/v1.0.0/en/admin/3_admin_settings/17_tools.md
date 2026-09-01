# Tools

**Admin Settings > Tools** manages built-in tools, custom tools, MCP servers, and shared service connections.

## Tool Access

Use the tool cards and table to inspect availability, enablement, and access. A tool is usable only when every part of its access chain succeeds:

- the tool and any required global feature are enabled
- the user is included directly or through an active group
- the selected model supports the required tool behavior
- any required provider, connection, or credential is healthy
- the applicable [Rate Limit](16_rate_limits.md) permits the invocation

Roll out a high-impact tool to a small group first. Test it with an ordinary user, review confirmation behavior and audit records, then expand access. For the complete workflow, see [Tool Rollout](../7_tools/0_tool_rollout.md).

## Custom Tools and MCP Servers

Custom tools and MCP servers extend Omlorix beyond the built-in catalog. Treat their descriptions, schemas, credentials, and endpoints as privileged configuration.

Before enabling one:

1. confirm who operates the service and what data it receives
2. restrict network destinations and credentials to the minimum required scope
3. review every action the tool can perform
4. assign a limited user or group audience
5. test invalid input, timeouts, service failure, and access removal
6. document incident and credential-rotation ownership

A tool description guides model selection but is not a security boundary. Enforce authorization in the connected service as well as in Omlorix.

## Service Connections

The **Service Connections** section supplies shared processing services used by built-in features.

For each connection, configure:

- **Name**
- **Base URL**
- **API key**
- at least one of **Code execution**, **LaTeX rendering**, or **Slide renderer**
- **Weight** from 1 to 100

Select **Refresh Status** after saving to verify service health. **Paste from launcher** can fill values copied from the server-management workflow; inspect them before saving and never share the copied credential in chat or a ticket.

Healthy connections assigned to the same purpose participate in weighted routing. Weight expresses relative preference, not a guaranteed short-term split. A configured but unhealthy connection is not a substitute for tested capacity.

For feature-specific usage and troubleshooting, see [Service Connections](../7_tools/14_service_connections.md).

## Change and Removal

Before disabling a tool or deleting a service connection, identify users, groups, models, Agents, Automations, and presentations that depend on it. Test the expected failure or fallback path, make the change during a monitored window, and keep a rollback value in protected operational records.
