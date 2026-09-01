# Code Execution Service

The **Code Execution service** runs model-generated Python and Bash in short-lived Docker sandboxes managed separately from the Omlorix application service. The same managed service can also provide LaTeX and slide rendering.

Omlorix works without it. Deploy the service only when users need **Code Execution**, [LaTeX rendering](../7_tools/15_latex_pdf.md), or [Slide Presentation](../7_tools/10_slides_presentation.md).

## What operators should expect

- A saved chat can reuse a sandbox session until it expires or the service is replaced. Temporary chats do not keep that binding.
- Variables, installed packages, and working files can survive between calls in the same session. Important results should be returned as Omlorix files.
- Generated files are copied into the user's Omlorix storage and count toward quotas.
- A healthy service can still be at capacity or reject a specific workload.
- Code execution, LaTeX, and Slides are enabled independently in a [Service Connection](../7_tools/14_service_connections.md).

## Deployment options

- [Server Launcher](3_server_launcher.md) for the desktop operator workflow.
- [`omlorix-server` CLI](4_cli.md) for headless or automated administration.
- [Source checkout](5_source_checkout.md) for advanced self-managed deployments and release validation.

The Launcher and CLI manage the same feature set and can share the same instances when they use the exact same Server Home. Avoid creating duplicate instances in different homes.

## Security baseline

Docker containers reduce risk but are not a virtual-machine security boundary. Keep the service private, protect its API key, and do not pass Omlorix or infrastructure secrets into sandboxes. Leave **Allow sandbox network access** and **Allow requested pip packages** disabled unless the workload requires them; either option expands what submitted code can reach or execute.

Launcher- and CLI-managed instances bind the host health endpoint to loopback, attach the gateway to a private shared Docker network, require API authentication, use a restricted Docker socket proxy, and create sandboxes with a read-only root filesystem and Docker's default seccomp profile. They do not require a strong runtime such as gVisor or Kata. For hostile or high-risk users, use a dedicated host or a reviewed stronger-isolation deployment. Monitor capacity, logs, image updates, session cleanup, and failed health checks.

## Backup and migration boundary

The managed instance registry, generated API key, Compose files, and instance settings live under `<Server Home>/code-execution`. Runtime containers, active sessions, and their Redis volume are managed separately from Omlorix's application data.

A full Omlorix backup preserves the Service Connection row in the main database, but it does not include this Server Home directory, the managed Code Execution containers or volumes, or active sandbox sessions. Imported chats also do not reuse a source instance's sandbox binding. For migration, recreate a managed instance on the destination, create or update its Service Connection with the new private URL and key, and rerun every enabled workload. Protect Server Home through the host's recovery process, but never treat active sessions as durable application data.

Continue with [Connect and Enable the Service](2_setup.md), then complete the [Tool Rollout Checklist](../7_tools/0_tool_rollout.md) before broad access.
