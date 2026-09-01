# Set Up from a Source Checkout

Use a source checkout only when your operations team owns builds, dependency pinning, upgrade validation, and rollback. For an unmodified release, use the [Server Launcher](1_2_server_launcher.md) or [`omlorix-server` CLI](1_3_server_cli.md).

## Prepare the Checkout

Verify the [installation prerequisites](1_2_install_prerequisites.md), then clone the repository and enter it:

```bash
git clone https://github.com/phinaldoo/omlorix.git omlorix
cd omlorix
```

Run the topology assistant, then generate and validate local server settings and secrets:

```bash
./script/setup-profile.sh
./setup.sh
```

The topology assistant asks about bundled or external PostgreSQL, Redis, database connection pooling, and file storage. PgBouncer is offered only with bundled PostgreSQL and accepts transaction or session mode; statement pooling is unsupported. When enabled, application services use PgBouncer while the migration service connects directly to PostgreSQL. Review the generated settings file before starting. It contains credentials and encryption material; keep a complete protected copy outside the checkout on storage that survives loss of the host. Do not commit it.

## Review Deployment Choices

For a normal single-host stack, keep bundled PostgreSQL and Redis and local file storage. Before changing topology, review:

- **Mode:** use production for realistic security behavior; development exposes diagnostic interfaces and helper ports.
- **Browser endpoint bind and port:** loopback is the safe default.
- **PostgreSQL and Redis:** external services must be reachable from containers and have separate backup and availability plans.
- **File storage:** shared external storage is required before independent application replicas.
- **Observability:** monitoring endpoints should remain private.

See [Server Configuration Reference](7_environment_variables.md) and [User File Storage](6_1_user_file_storage.md).

## Start and Verify

```bash
make up
make ps
make logs
```

`make up` validates configuration, drains any already-running application services, builds the application images, runs migrations offline, and starts the selected stack. Wait for every selected service to become healthy, including the browser, API, email and durable workers, and realtime gateway, then open the configured address and complete [First Steps](2_first_steps.md).

Useful lifecycle targets:

| Task | Command |
|---|---|
| Show stack status | `make ps` |
| Follow logs | `make logs` |
| Restart | `make restart` |
| Stop containers and keep data | `make down` |
| Run migrations | `make migrate` |
| Test file storage | `make source-probe` |

`make migrate` uses the same application-service drain but intentionally leaves those services stopped after the synchronous migration. Run `make up` when you are ready to start the complete stack. This prevents a manually invoked migration from overlapping an older API or worker process.

The source workflow automatically selects the supported stack from the generated server settings, including external-service and observability choices. Use that generated stack consistently; unsupported combinations can break startup, updates, or recovery.

## Update

Before changing revisions:

1. Create and verify an encrypted full backup.
2. Preserve the complete server settings and any external-service credentials.
3. Read the release or branch migration notes.

Then stop dependent work and run:

```bash
make update
make up
make ps
```

`make update` rebases the checkout onto the fetched revision. Commit and protect intentional local changes first, and stop for review if the rebase reports a conflict. Validate the resulting revision outside production when you maintain local modifications.

`make up` takes the Compose project down with orphan removal before running a newly built migration container, then starts application services only after the main and audit schemas are ready. Data volumes remain intact. This short offline migration window prevents either a current process or a writer left behind by a renamed service from crossing a new privacy or compatibility boundary.

Database migrations are not automatically reversed if the new revision fails. Return to a known image or revision only with a compatible database, or restore a verified backup.

## Backup, restore, and storage

The source workflow exposes **backup-create**, **backup-verify**, **backup-restore**, **source-probe**, **files-migrate**, and **files-migrate-local** Make targets. Run `make help` for the accepted operator inputs.

Use [Backups](../3_admin_settings/23_1_backups.md) for archive scope and verification, [Full-Instance Restore](../3_admin_settings/23_2_restore.md) for the destructive restore procedure, and [User File Storage](6_1_user_file_storage.md) for dry-run-first migration. Use `make help` as the command reference for the checked-out release.

## Troubleshooting

- **Docker permission failure:** use the same trusted account and Docker access model consistently.
- **Old behavior remains:** check the active checkout, rebuild the images, and recreate the services.
- **Migration fails after switching branches:** stop and inspect the migration error; do not edit database version state or downgrade blindly.
- **External service is unreachable:** test the address from the Omlorix service network, not only from the host.
