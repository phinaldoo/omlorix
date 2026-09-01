# Connect and Enable the Code Execution Service

Complete these steps after creating a service through the [Server Launcher](3_server_launcher.md), [`omlorix-server` CLI](4_cli.md), or a [source checkout](5_source_checkout.md).

## Add the Service Connection

You need the service **Base URL** reachable from the Omlorix application service and its **API key**. An address that works in your browser may not work inside a container; do not use `localhost` for a service in another container or host.

1. Open **Admin Settings > Service Connections**.
2. Select **Add Connection**.
3. Enter a clear **Name**, the service **Base URL**, and its **API key**.
4. Enable **Code Execution**. Enable **LaTeX rendering** or **Slides** only when this service should provide them.
5. Keep the default **Weight** for a single service.
6. Save and confirm that every enabled purpose becomes available.

For a Launcher-managed instance, **Connect** copies the private connection details. Use **Paste from launcher** on the Service Connections page, review the imported settings, save, then clear the clipboard because it contained the API key.

If [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md) blocks the private service, allow only its exact destination. This controls Omlorix's connection to the service; **Allow sandbox network access** is a separate setting.

## Enable the tool

Open **Admin Settings > Tools > Code Execution**. **Max Output Length** defaults to 50,000 characters and accepts 100-100,000. Increase it only when legitimate stdout, stderr, or error text is truncated; large results should normally be returned as files.

Then edit a pilot model and select **Code Execution**. A healthy Service Connection does not automatically grant the tool to models or groups.

## Verify end to end

As a normal pilot user, test:

1. a small Python calculation and a small Bash command;
2. a generated text or chart file;
3. an intentional error;
4. a network request, confirming it is blocked when **Allow sandbox network access** is off;
5. package installation only when **Allow requested pip packages** is intentionally on;
6. one real LaTeX or slide render for every additional enabled purpose.

If the connection is unavailable, check the running service, application-service reachability, API key, exact enabled purpose, outbound policy, and service logs. If a chat loses its session, the previous sandbox expired or was removed; save important outputs as Omlorix files. Service Connections and managed instances have different [backup and migration boundaries](1_introduction.md#backup-and-migration-boundary).
