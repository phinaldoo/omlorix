# Manage Code Execution with the Server Launcher

Use the Server Launcher when it already manages the Omlorix installation. The Launcher creates and operates the same Code Execution instances available through the CLI.

## Create a service

1. Start Docker. The Omlorix backend must also be running before **Connect** can attach it to the private helper network or before the service can be used.
2. Open **Code Execution** in the Launcher and select **Add service**.
3. Enter a **Service name** and choose a published **Version**.
4. Review **Local health port**, **Sandbox memory**, **Concurrent executions**, and **Idle session timeout in seconds**.
5. Leave **Allow sandbox network access** and **Allow requested pip packages** off for the first deployment.
6. Select **Create service**, then **Start** and wait for **Healthy**.

Increase resources only after measuring real workloads. **Allow sandbox network access** lets user-submitted code contact other destinations. **Allow requested pip packages** lets it install and execute third-party package code inside the sandbox.

## Connect it to Omlorix

Select **Connect** on the healthy service, then use **Paste from launcher** under **Admin Settings > Service Connections**. Review the enabled purposes and save. Clear the clipboard afterward because it contained the service API key. Finish the end-to-end checks in [Connect and Enable the Service](2_setup.md).

## Operate the service

- **Start**, **Stop**, and **Restart** control the instance independently of the main Omlorix server.
- **Logs** shows service output for diagnosis.
- **Settings** changes version, local health port, resources, timeout, and sandbox policy; a running service is recreated when required.
- **Check update** and **Install update** manage published releases. A failed update attempts to restore the previous version.
- **Connect** copies the current private URL and API key.
- **Server files** opens the instance directory; treat its configuration as secret.
- **Delete** requires a second confirmation and permanently removes the instance settings, active sessions, containers, and Redis volume. Pulled Docker images can remain in the host's shared image cache.

Stopping the Launcher or service does not delete its instance. Before deletion, preserve required user outputs in Omlorix and remove or replace its Service Connection. The managed instance is outside a full Omlorix application backup; follow the [backup and migration boundary](1_introduction.md#backup-and-migration-boundary).
