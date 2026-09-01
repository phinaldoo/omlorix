# Manage Code Execution with the Server CLI

Use the CLI on a headless server or when Omlorix is already managed with `omlorix-server`. Use the same `--home` value for every command; the CLI and Server Launcher see the same instances only when they share the exact Server Home.

## Create and connect a service

```bash
omlorix-server code-execution versions
omlorix-server code-execution create --name "Local Code Execution"
omlorix-server code-execution start <instance-id>
omlorix-server code-execution list
omlorix-server code-execution connection <instance-id> --json
```

When omitted, the CLI chooses the latest published version and an available local health port. The connection output contains the API key; do not store it in logs, tickets, chat, or an unprotected file. Add the details under **Admin Settings > Service Connections**, then follow [Connect and Enable the Service](2_setup.md).

`connection --json` returns the private URL `http://codeexec-<instance-id>:8000`, the API key, weight `1`, and all three purposes enabled. It also makes best-effort attempts to create the shared helper network and attach the Omlorix backend. Review the purposes before saving; disable LaTeX or Slides when the instance should not serve them.

## Resource and policy options

Set non-default values only when the workload requires them:

```bash
omlorix-server code-execution create \
  --name "Analysis Runner" \
  --version <published-version> \
  --port 8010 \
  --memory 1g \
  --max-concurrent 10 \
  --session-timeout 1200
```

Creation defaults are 512 MiB memory, 10 concurrent executions, a 1,200-second idle session timeout, and both network and requested pip packages disabled. Without `--port`, the first free loopback port in 8000-8999 is selected.

Valid memory choices are `256m`, `512m`, `1g`, `2g`, `4g`, and `8g`. `--max-concurrent` accepts 1-100, `--session-timeout` accepts 60-86,400 seconds, and an explicit port accepts 1-65,535 when it is free. Use `--network-access` and `--allow-pip` only after review; `edit` also accepts `--no-network-access` and `--no-allow-pip`.

## Day-to-day commands

```bash
omlorix-server code-execution edit <instance-id> --memory 1g
omlorix-server code-execution start <instance-id>
omlorix-server code-execution stop <instance-id>
omlorix-server code-execution restart <instance-id>
omlorix-server code-execution logs <instance-id> --follow
omlorix-server code-execution check-update <instance-id>
omlorix-server code-execution update <instance-id>
```

Use `logs <instance-id> --lines <count>` for a bounded snapshot (1-5,000 lines) and add `--follow` only for an attended live stream. `list --json` includes each instance's private URL, loopback URL, and instance-home path for automation and diagnostics; it does not include the API key.

An update recreates the service and waits for health; on failure the CLI attempts to restore the previous release. Run the end-to-end tests in [Connect and Enable the Service](2_setup.md) after every update, even when health succeeds.

Deletion permanently removes the instance settings, active sessions, containers, and service state:

```bash
omlorix-server code-execution delete <instance-id> --confirm <instance-id>
```

Before deletion, preserve required user outputs in Omlorix and remove or replace the corresponding Service Connection. Deletion does not remove pulled images from Docker's shared image cache. Managed instances are outside a full Omlorix application backup; follow the [backup and migration boundary](1_introduction.md#backup-and-migration-boundary).
