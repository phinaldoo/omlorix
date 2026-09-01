# Set Up with the Server CLI

The standalone `omlorix-server` CLI is the release-based management surface for headless hosts, SSH administration, and automation. It embeds the supported deployment assets and provides the same ordinary workflows as the [Server Launcher](1_2_server_launcher.md).

## Install and Initialize

1. Install and start Docker with Compose v2.
2. Download the correct Stable CLI binary and matching checksum from [Omlorix Releases](https://github.com/phinaldoo/omlorix/releases).
3. Keep the binary and its `.sha256` file in the same directory, verify the path-free checksum entry with `sha256sum --check <file>.sha256` on Linux or `shasum -a 256 -c <file>.sha256` on macOS, then make the binary executable where required and place it in an administrator-controlled executable location.
4. Run:

   ```bash
   omlorix-server version
   omlorix-server doctor
   omlorix-server init
   ```

Initialization creates the server home, deployment files, unique installation identity, restricted settings, generated secrets, and a bundled single-host topology. Do not initialize two homes against the same database or volumes.

Use `--home <path>` on every command when you choose a non-default server home. On Linux, a dedicated service account and a stable, access-controlled path are recommended.

If an older unlabeled Compose project already belongs to this Server Home, inspect its project name and containers before using `omlorix-server init --attach-project <project>`. This arms a one-time adoption; the next start recreates the resources with the current installation identity and then disables the exception. Never attach a project whose ownership is uncertain or whose containers carry another Omlorix installation identity.

With observability enabled, `doctor` reports whether host metrics are available. Linux includes a hardened node-exporter without a host-root mount. macOS and Windows safely omit node-exporter and its Prometheus target while keeping the remaining observability services available.

PgBouncer can be enabled only with bundled PostgreSQL. Use transaction or session pooling; statement pooling is rejected. The CLI derives pooled endpoints for long-running application services while keeping migrations on the direct PostgreSQL endpoint. Do not override those internal routes to work around validation.

## Configure and Protect the Installation

Inspect configuration without displaying secret values:

```bash
omlorix-server config path
omlorix-server config list
omlorix-server update-channel
omlorix-server proxy settings
```

Use `config edit` for ordinary reviewed changes. Use `config import <file>` to merge supplied settings and `config replace <file>` to make a complete operator configuration authoritative. Both validate the result; neither restarts running containers. Use `config set` or `config unset` only when current release documentation identifies the exact setting.

Before replacing configuration, stop the old stack if the Compose project identity could change. Otherwise the old project can remain running after the new one starts. After any runtime-affecting change, run `restart` and verify the selected home and endpoint.

Create a complete protected recovery copy outside the server home before starting:

```bash
omlorix-server secrets export /protected/off-host/omlorix-recovery
omlorix-server secrets backup-status
```

The remembered copy is refreshed after managed changes. Use `secrets save-now` to refresh it, `secrets import <file>` to recover the same instance, and `secrets disable-backup` only when another documented process protects the complete server configuration. `secrets regenerate` changes critical secrets immediately; do not run it to inspect available choices. Read the installed command help and save a current recovery copy first.

This recovery file is the complete deployment environment plus recovery-only management context, not just the JWT and encryption keys. It includes both stable hashing salts, the backup passphrase, active database/Redis/storage credentials and topology, installation identity, update channel, and managed proxy state. Treat `secrets import` as an authoritative same-instance recovery operation; use `config import` for a reviewed partial configuration merge.

Never use `--show-secrets` in recorded terminals or automation logs. Replacing the authentication signing secret signs out users; replacing the encryption key breaks access to credentials encrypted with the old key. Replacing either stable hashing salt breaks continuity of the corresponding pseudonymous security correlations.

## Start and Complete Browser Setup

```bash
omlorix-server start --open
omlorix-server status
```

If the host has no browser, omit `--open` and open the reported address from an authorized client. Wait for readiness, then complete [First Steps](2_first_steps.md).

`start` and `restart` first take the Compose project offline and remove orphaned containers, reset the one-shot migration container, and complete main and audit migrations before application services return. Data volumes remain intact. This boundary also catches writers left behind by renamed services, so do not use per-service controls as a substitute for a full start after changing the server release.

Create and verify the first encrypted backup before inviting users:

```bash
omlorix-server backup-options
omlorix-server backup
omlorix-server backup list
omlorix-server backup-verify --job-id <job-id>
```

## Command Guide

| Task | Command |
|---|---|
| Diagnose host and configuration | `doctor` |
| Check server state | `status` or `status --json` |
| Start, stop, restart, or open | `start`, `stop`, `restart`, `open` |
| List or operate services | `services`, `service start|stop|restart|logs <name>` |
| Read logs | `logs`, with optional service, time, line, or follow filters |
| Check or install server updates | `check-update`, `update`, `update-channel` |
| Schedule unattended updates | `auto-update status|enable|disable|run|daemon` |
| Manage deployment settings | `config list|get|set|unset|path|edit|export|import|replace` |
| Manage recovery material | `secrets regenerate|export|import|backup-status|save-now|disable-backup` |
| Back up and verify | `backup-options`, `backup`, `backup list`, `backup show`, `backup-verify` |
| Download a completed backup | `backup download <job-id> --output <new-path>` |
| Restore | `restore --source <uri>` or `restore --job-id <job-id>` |
| Operate native ingress | `proxy status|settings|configure|enable|disable|start|stop|restart|install-service|refresh-service|uninstall-service` |
| Diagnose visitor IPs | `visitor-ip status|detect|repair|verify` |
| Check or migrate file storage | `storage probe`, `storage migrate`, `storage migrate-local` |
| Operate Code Execution | `code-execution list|versions|create|edit|check-update|start|stop|restart|update|logs|connection|delete` |

Run `omlorix-server --help` for current flags. Use `--json` where offered for machine-readable output.

An update failure before target migration starts can safely restore the previous release selection. After migration may have started, the CLI keeps the target release selected, drains the Compose project, and refuses to start the previous image. Correct and retry the target release, or restore a verified backup compatible with the release you intend to run.

Machine-readable commands also keep failures machine-readable and exit nonzero. Automation should branch on the documented error code rather than matching the human-readable message, and must never enable secret output in recorded jobs.

## Public Access

The native proxy supports HTTP/HTTPS listeners, certificate and key files, optional HTTP-to-HTTPS redirect, a public hostname, and service-manager installation. Configure it through `proxy configure`, enable it, start it, then run the visitor-IP detection and verification workflow.

Alternatively, keep the browser endpoint on loopback and use your existing reverse proxy or tunnel. Configure only narrow trusted proxy sources and make the proxy overwrite client-supplied forwarding headers. Follow [Set Up HTTPS](3_setup_https.md).

## Automatic updates

`auto-update enable` configures daily, weekend, or selected-weekday checks at a local time. A service manager must keep `omlorix-server auto-update daemon` running; configuration alone does not create a persistent daemon.

Keep backup-before-update and health checks enabled in production. Set the reviewed pre-update backup destination with `--destination <id>`. Archive encryption remains enabled by default; use `--no-encrypted` only when the server permits plaintext archives, and use `--no-encrypted=false` to enable it again. `auto-update status` reports the persisted destination and encryption mode shared with the Launcher. Update the CLI binary separately when release compatibility requires it. The shared maintenance procedure and failure rules are in [Operate and Update Omlorix](4_operations.md).

## Backup, Restore, and Storage Safety

- Prefer encrypted backups and keep the passphrase plus encryption key outside the archive.
- Use `backup-verify` before restore.
- Use `backup download <job-id> --output <new-path>` to materialize a successful catalogued artifact on the host. The command fails for incomplete, missing, deleted, or integrity-mismatched jobs, writes through a private sibling temporary file, and never overwrites the requested path. Add `--json` for a machine-readable job ID, final path, and byte count.
- Restoring into an existing instance requires the explicit in-place target and confirmation phrase and overwrites current data.
- Run storage migration with `--dry-run` first. Do not use `--delete-source` until copied objects and application access are verified.

See [Backups](../3_admin_settings/23_1_backups.md), [Full-Instance Restore](../3_admin_settings/23_2_restore.md), and [User File Storage](6_1_user_file_storage.md).

## Troubleshooting

- Run `doctor`, then `status --json` and recent `logs`.
- For bounded diagnostics, use `logs --lines 200 --since 5m` or `service logs <name> --lines 200 --since 5m`; add `--follow` only while an operator is watching and stop the stream before another long-running read.
- If a command targets the wrong instance, supply the correct `--home` and inspect `config path` before changing anything.
- If an operation reports a lock, wait for the active Launcher or CLI mutation to finish.
- If startup fails, inspect migration logs and validate external database, Redis, and storage connectivity from the Omlorix service network.
- If imported settings are not active, restart after confirming the intended Compose project.
- If the binary is missing under a service manager, use its absolute path and an explicit server home.
