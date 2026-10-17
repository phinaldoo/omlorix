# Omlorix Helm Deployment

Chart path: `deploy/helm/omlorix`

## Validate templates

```bash
helm lint deploy/helm/omlorix -f deploy/helm/omlorix/values-production.example.yaml
helm template omlorix deploy/helm/omlorix -f deploy/helm/omlorix/values-production.example.yaml
```

The default values intentionally require production-specific settings before an
install can render. For chart development syntax checks without install-ready
values, disable validation explicitly:

```bash
helm lint deploy/helm/omlorix --set validation.enabled=false
helm template omlorix deploy/helm/omlorix --set validation.enabled=false
```

## Deployment profile values

- Single Server Plus style overrides: `deploy/helm/omlorix/values-single-server-plus.yaml`
- Kubernetes production baseline overrides: `deploy/helm/omlorix/values-kubernetes.yaml`
- Complete production example: `deploy/helm/omlorix/values-production.example.yaml`

The chart deploys the frontend, API, automation worker, automation scheduler,
and migration job. Production Kubernetes deployments should normally use
external Postgres, external Redis, and object storage.

The default local file storage mode creates a PersistentVolumeClaim and mounts
it into the API, worker, and scheduler at `/app/data/userFiles`. Because local
storage must be shared by those pods, the default access mode is
`ReadWriteMany`. Use object storage for scaled production installs when your
cluster does not provide RWX volumes. To run a short-lived evaluation install
without persistent local storage, set `fileStorage.local.persistence.enabled=false`
and `fileStorage.local.allowEphemeral=true`.

## Install / upgrade

```bash
helm upgrade --install omlorix deploy/helm/omlorix -f deploy/helm/omlorix/values-production.example.yaml
```

Copy the production example to your own values file and replace the host names,
image tags, storage settings, and secret name before installing.

## Migration behavior

The chart installs a pre-install/pre-upgrade migration Job hook:

- command: `python -m app.migrations.cli run`
- app deployments run with `DB_MIGRATIONS_MODE=off`
- migration job forces `DB_MIGRATIONS_MODE=run`

This avoids running migrations in every API pod.

## Required secrets

Set these through `envSecret` or `existingSecretName` before production use:

- `JWT_SECRET_KEY`
- `ENCRYPTION_KEY`
- database credentials or `DATABASE_URL`
- Redis credentials in `REDIS_URL`
- object storage credentials

Recommended production installs should use `existingSecretName` so secrets stay
out of values files. The Secret must provide at least:

- `JWT_SECRET_KEY`
- `ENCRYPTION_KEY`
- `DATABASE_URL`, or `DATABASE_USER` and `DATABASE_PASSWORD` when database host
  settings are supplied through `env`
- object storage credentials required by the selected storage provider

Example:

```bash
kubectl create secret generic omlorix-production-env \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=ENCRYPTION_KEY="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
  --from-literal=DATABASE_USER="<database-user>" \
  --from-literal=DATABASE_PASSWORD="<database-password>" \
  --from-literal=FILE_STORAGE_S3_ACCESS_KEY_ID="<s3-access-key>" \
  --from-literal=FILE_STORAGE_S3_SECRET_ACCESS_KEY="<s3-secret-key>"
```

The ingress sends `/api`, `/docs`, `/redoc`, and `/openapi.json` to the API
service. The root path `/` goes to the frontend service when `frontend.enabled`
is true.

## Auth origin setting

Add the Kubernetes ingress origin to the `general.public_url` list and place the canonical origin first. Before any public URL is configured, Host-header and sensitive authentication origin enforcement are disabled so a fresh installation can complete setup through any IP address or domain. If operators intentionally continue using port-forwarded localhost or literal private-IP browser access after configuring a public URL, explicitly set `ALLOW_LOCAL_OR_PRIVATE_ORIGINS=true`; it defaults to `false`. The opt-in applies both to sensitive cookie-auth origin checks and to Host-header validation for those local/private addresses. After setup, arbitrary DNS hostnames must be listed in `TRUSTED_HOSTS` or `general.public_url`.
