# Installation Prerequisites

Install only what your chosen [installation method](1_1_setup.md) needs.

| Method | Requirements |
|---|---|
| Server Launcher | Docker Desktop or Docker Engine, Docker Compose v2, desktop session |
| `omlorix-server` CLI | Docker Desktop or Docker Engine, Docker Compose v2 |
| Source checkout | Docker with Compose v2, Git, Python 3, Bash, Make, and `curl` with trusted CA certificates |

The Launcher and CLI do not require Git, Python, Node.js, Go, or Make.

## Verify Docker

Run these commands as the same operating-system user that will manage Omlorix:

```bash
docker --version
docker info
docker compose version
```

All three must succeed. Install Docker from the official [Docker Desktop](https://docs.docker.com/desktop/setup/install/) instructions on macOS or Windows, or the [Docker Engine](https://docs.docker.com/engine/install/) instructions for your Linux distribution.

On Linux, membership in the Docker group grants highly privileged host access. Limit it to trusted operators. If you intentionally require `sudo` for Docker, use it consistently and make sure Omlorix's server files remain owned by the intended service account.

## Size and network the host

Allow space for container images, database and Redis volumes, user files, backups, temporary update files, and logs. Capacity depends on usage; monitor free disk space from the first day.

The default private browser endpoint listens on loopback port `8080`. Optional proxy, database, storage, and observability services need additional ports. Keep database, Redis, object-storage administration, and monitoring interfaces bound to loopback or a protected network.

The host needs outbound HTTPS access to download Omlorix images and updates. Runtime provider access depends on the services you enable.

## Source-Checkout Check

For a source installation, verify:

```bash
git --version
python3 --version
bash --version
make --version
curl --version
```

Return to [Install Omlorix](1_1_setup.md) and follow one method. Before downloading a release, confirm that the host architecture and operating system match the selected Launcher or CLI artifact.
