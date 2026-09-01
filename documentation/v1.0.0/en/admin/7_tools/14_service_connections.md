# Service Connections

**Service Connections** are shared administrator-managed services for **Code Execution**, **LaTeX rendering**, and **Slides**. They are infrastructure connections, not user Workspace Connections or MCP servers.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then apply the service health and routing checks below.

## Add a connection

1. Deploy a compatible service and protect it with authentication and network controls.
2. Open **Admin Settings > Service Connections** and select **Add Connection**.
3. Enter a clear **Name**, **Base URL**, credential, and **Weight**.
4. Enable only the supported purposes: **Code Execution**, **LaTeX rendering**, and/or **Slides**.
5. Save and check the health status.
6. Run one real workload for every enabled purpose.

**Weight** accepts 1-100 and influences routing among healthy connections; it is not a capacity guarantee. A healthy status only confirms the service health check, not rendering or execution with real inputs. API keys are encrypted in the database and are never returned in administrator API responses; the UI shows only whether one is configured.

## Operations

- Use a stable **Base URL** reachable from the Omlorix application service. A container's `localhost` is not another container or host service.
- Use HTTPS across untrusted networks and a dedicated credential for each environment.
- Keep service processes isolated from Omlorix and from sensitive infrastructure.
- Monitor health, latency, capacity, storage, and errors. Test failover when using multiple connections.
- Disable a purpose before maintenance or credential rotation. Delete a connection only after dependent tools have another healthy route.

## Backup and migration

Service Connections have no feature-specific JSON import/export. **Paste from launcher** is a one-time clipboard handoff for creating a connection; it is not a backup or restore format.

A verified full-instance backup protects the Service Connection database row as part of Omlorix's main database. It does not back up the external Code Execution, LaTeX, or Slides service, its provider-side state, or a separately managed Code Execution instance under Server Home. On another host, deploy or restore the external service first, verify its URL and credential from the Omlorix service network, then update or recreate the Service Connection and run a real workload.

If a tool reports no service, check that at least one enabled connection is healthy for that exact purpose and that the relevant tool settings are enabled. If health passes but work fails, inspect the service logs, request limits, file storage, timeout, and supported version.
