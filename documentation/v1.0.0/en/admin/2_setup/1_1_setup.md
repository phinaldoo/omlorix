# Install Omlorix

Choose one installation method and keep using its management workflow for that instance.

| Method | Best for | Required on the host |
|---|---|---|
| [Server Launcher](1_2_server_launcher.md) | Recommended guided setup on a desktop host | Docker with Compose v2 and a desktop session |
| [`omlorix-server` CLI](1_3_server_cli.md) | Headless servers, SSH administration, and automation | Docker with Compose v2 and the release CLI binary |
| [Source checkout](1_5_source_checkout.md) | Advanced self-managed builds and deliberate local modifications | Docker, Git, Python, Bash, and Make |

The Launcher and CLI use the same release images and expose matching operator workflows. A normal managed single-host installation runs the browser application, API service, database migrations, email worker, ten responsibility-specific durable workers, realtime gateway, PostgreSQL, Redis, and file storage. Redis-backed automation services and other optional components run when the selected topology enables them; PostgreSQL, Redis, and file storage can instead use supported external services.

## Decide Before Installation

- where the server home and persistent data will live
- whether PostgreSQL and Redis are bundled or externally managed
- whether user files use local, bundled object, or external storage
- which public hostname and HTTPS terminator users will use
- where the protected environment recovery copy and off-host backups will live

Local file storage is suitable for one application host. Use shared storage before adding independent replicas.

## Keep One Management Identity

Do not point unrelated Launcher, CLI, or source installations at the same database, volumes, or server settings. Lifecycle commands verify the managed installation and serialize changes, but they cannot make two unrelated homes safe.

Before moving an instance to another management path:

1. Create and verify an encrypted full backup.
2. Preserve the complete environment recovery copy and encryption secrets.
3. Stop the old deployment.
4. Confirm that the new path targets the intended database, storage, and Compose project.
5. Start and verify the new deployment before retiring recovery material.

## Production Baseline

- use **Production** mode and the **Stable** channel
- expose only required ports and use HTTPS outside the host
- restrict Docker access, server settings, TLS keys, recovery copies, and backups to trusted operators
- keep the primary **Public URLs** entry accurate
- test restore, sign-in, model access, uploads, and password recovery with a normal user

After the server starts, continue with [First Steps](2_first_steps.md), then adopt the [operations and update runbook](4_operations.md).
