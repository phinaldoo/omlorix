# Run the Code Execution Service from a Source Checkout

Use a source checkout only when your operations team owns builds, dependency pinning, updates, and rollback. Most installations should use the [Server Launcher](3_server_launcher.md) or [Server CLI](4_cli.md).

## Prepare and start

Install Git, Docker, and Docker Compose, obtain access to the Code Execution repository, and clone it into the directory name expected by the development tooling:

```bash
git clone https://github.com/phinaldoo/omlorix-code-execution.git
cd omlorix-code-execution
```

Check out a reviewed release or revision and follow that checkout's own README for its current environment-generation, build, and Docker Compose startup commands. Those commands belong to the separately versioned service and must not be inferred from Omlorix's managed Launcher bundle. Keep every generated environment file private; it contains the service credential.

Confirm that all service containers are running and that the authenticated health check succeeds. Process liveness alone is not proof that session storage, Docker access, and the sandbox image are ready.

## Connect Omlorix

The service **Base URL** must be reachable from the Omlorix application service. When the two projects run in separate container networks, attach them to a private shared network through durable Compose overrides and give the Code Execution service a stable internal name. Avoid one-off container changes because they disappear when containers are recreated.

Add the internal URL and generated API key under **Admin Settings > Service Connections**, then follow [Connect and Enable the Service](2_setup.md).

## Update and stop

Pull reviewed changes, rebuild both service images, recreate the stack, and repeat health plus end-to-end tests. Pin dependencies or revisions when you need reproducible validation.

Stop the Compose project without deleting its volumes unless you intentionally want to remove service state. Source deployments do not receive the managed update and rollback workflow of the Launcher or CLI, so document a tested rollback and credential-recovery plan before production use.

The source service and its volumes are not included in a full Omlorix application backup. Back them up under this deployment's own documented procedure, or recreate them and update the Service Connection during migration. Do not attempt to carry active sandbox sessions between installations.
