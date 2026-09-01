# User File Storage

File storage holds uploads and generated artifacts. Choose it during Launcher setup or in deployment configuration; **Admin Settings > File Storage** reports usage but does not change the storage provider.

## Choose a Provider

| Provider | Use it when | Important responsibility |
|---|---|---|
| **Local storage** | One application host with persistent Docker storage | Back up the volume and do not add independent replicas |
| **Bundled MinIO** | One Launcher/CLI host that should run private object storage | Protect its credentials, volume, and administration ports |
| **S3-compatible** | AWS S3, MinIO, R2, or another compatible object store | Configure bucket, region/endpoint, credentials, prefix, and lifecycle policy |
| **Google Cloud Storage** | You operate a GCS bucket | Provide bucket, optional prefix/project, and service credentials |
| **Azure Blob Storage** | You operate an Azure container | Provide container plus a connection string or account URL and credential |
| **WebDAV** | You operate a compatible WebDAV service | Provide URL, credentials, prefix, TLS verification, and timeout |

Use the smallest storage credential that can read, write, list, and delete objects in the selected bucket/container and prefix. Keep server-side encryption, versioning, retention, monitoring, and provider backups aligned with your policy.

## Configuration Rules

- The selected provider must be reachable from the Omlorix service network, not only from the host.
- Prefixes isolate Omlorix objects inside shared buckets; do not change one without a migration plan.
- Keep TLS verification enabled. A private certificate should be trusted through the deployment's CA configuration rather than bypassed permanently.
- Local storage paths are container paths and require persistent mounts.
- Changing provider settings does not move existing objects.
- Database rows may reference remote objects, but full-instance backups do not necessarily contain the objects themselves. Back up external storage separately.

## Test Before Use

- **Server Launcher:** save the storage settings, use the storage probe, and resolve every permission or connectivity error before restart.
- **Server CLI:** run `omlorix-server storage probe`.
- **Source checkout:** run `make source-probe`.

After restart, test upload, download, preview, deletion, and a generated artifact with a normal user.

## Migrate Between Providers

Create and verify both the full-instance backup and the provider-native storage backup before migration. Configure the destination first, but do not switch users to it until the copy is verified.

Supported scopes include all stored content, ordinary files, Deep Research artifacts, and presentations. The migration can filter by user, date, or prior migration source and can retry or batch work.

### Server Launcher

Open the storage migration workflow, select source, destination, scope, and optional filters, then:

1. Run a dry run.
2. Review matching records, conflicts, missing objects, and access errors.
3. Run the copy without deleting the source.
4. Switch the selected storage provider and restart Omlorix.
5. Verify real user files and generated artifacts.
6. Delete old source objects only under an approved cleanup change.

### Server CLI

For an explicit migration:

```bash
omlorix-server storage migrate --from-provider local --to-provider s3 --dry-run
omlorix-server storage migrate --from-provider local --to-provider s3
```

When local is the source and the configured provider is the destination:

```bash
omlorix-server storage migrate-local --dry-run
omlorix-server storage migrate-local
```

Use `--delete-source` only after the destination and application access have been verified. Use `--force` only when overwriting conflicts is intentional. Run `omlorix-server --help` for all filters.

### Source Checkout

Use `make files-migrate` for an explicit source and destination, or `make files-migrate-local` when moving from local storage to the configured provider. Run `make help` for the checked-out release's accepted migration inputs. Start with a dry run and remove that option only after reviewing the result.

## Scaling and Cutover

Independent application replicas must share the same database and storage provider. Local volumes are safe only when every replica sees the same filesystem with suitable consistency and failure behavior.

During a cutover, prevent new writes or schedule a maintenance window so objects are not created in the old provider after the copy. Keep the old storage read-only until the new provider passes backup and restore tests.

## Troubleshooting

- **Probe fails:** check service-reachable DNS, TLS trust, credential scope, bucket/container existence, and prefix permissions.
- **Uploads work on one replica only:** the replicas are using different local filesystems or configuration.
- **Rows exist but files are missing:** restore the corresponding external-storage snapshot with the original object names.
- **Migration reports conflicts:** compare source and destination objects; do not use force until the desired winner is documented.
- **WebDAV is slow:** verify server limits and timeout rather than disabling TLS checks.
