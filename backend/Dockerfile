FROM python:3.14.7-slim@sha256:656d12e70054d5fda18a045e2494c96701e9792dd1445f95b3d038df954f57e9 AS python-deps-base

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Build runtime and development dependency trees independently. The production
# image copies only the runtime tree, while the dev target retains test tools.
FROM python-deps-base AS python-runtime-deps

COPY app/requirements.txt /tmp/requirements.txt
RUN python -m pip install --prefix=/install --no-cache-dir --require-hashes -r /tmp/requirements.txt

# The installed psycopg2-binary extension is LGPL-3.0-or-later. Keep the exact
# corresponding upstream source beside the image's retained license text.
RUN python -c "from pathlib import Path; import hashlib, urllib.request; url='https://files.pythonhosted.org/packages/2a/60/a3624f79acea344c16fbef3a94d28b89a8042ddfb8f3e4ca83f538671409/psycopg2_binary-2.9.12.tar.gz'; data=urllib.request.urlopen(url, timeout=60).read(); expected='5ac9444edc768c02a6b6a591f070b8aae28ff3a99be57560ac996001580f294c'; actual=hashlib.sha256(data).hexdigest(); assert actual == expected, (actual, expected); target=Path('/third-party-sources/psycopg2_binary-2.9.12.tar.gz'); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)"

FROM python-deps-base AS python-dev-deps

COPY app/requirements.txt /tmp/requirements.txt
COPY app/requirements-dev.txt /tmp/requirements-dev.txt
RUN python -m pip install --prefix=/install --no-cache-dir --require-hashes \
    -r /tmp/requirements.txt \
    -r /tmp/requirements-dev.txt

FROM python:3.14.7-slim@sha256:656d12e70054d5fda18a045e2494c96701e9792dd1445f95b3d038df954f57e9 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    ORT_DISABLE_TELEMETRY=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       gnupg \
    && mkdir -p /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       | gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg \
    && . /etc/os-release \
    && printf 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] https://apt.postgresql.org/pub/repos/apt %s-pgdg main\n' "$VERSION_CODENAME" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       gosu \
       ffmpeg \
       fonts-noto-core \
       libstdc++6 \
       postgresql-client-18 \
       tesseract-ocr \
    && apt-get purge -y --auto-remove \
       curl \
       gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-runtime-deps /install /usr/local
COPY --from=python-runtime-deps /third-party-sources /usr/share/doc/omlorix/sources

RUN useradd -m -u 10001 appuser

WORKDIR /app

FROM runtime AS app-code

COPY app /app/app
COPY alembic_main /app/alembic_main
COPY alembic_audit /app/alembic_audit
COPY alembic_main.ini /app/alembic_main.ini
COPY alembic_audit.ini /app/alembic_audit.ini
COPY migrate.sh /app/migrate.sh
COPY google-workspace-worker /usr/local/bin/google-workspace-worker

RUN chmod +x /app/migrate.sh /usr/local/bin/google-workspace-worker \
    && mkdir -p /app/app/logs /app/app/data /app/backups \
    && chown -R appuser:appuser /app

EXPOSE 8000

FROM app-code AS dev

# Overlay the complete development dependency tree only in the dev target.
COPY --from=python-dev-deps /install /usr/local

CMD ["sh", "-c", "mkdir -p /app/app/logs /app/app/data /app/backups && chown appuser:appuser /app/app/logs /app/app/data /app/backups && exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --proxy-headers --forwarded-allow-ips \"${UVICORN_FORWARDED_ALLOW_IPS:-127.0.0.1,::1}\""]

FROM app-code AS prod

CMD ["sh", "-c", "mkdir -p /app/app/logs /app/app/data /app/backups && chown appuser:appuser /app/app/logs /app/app/data /app/backups && exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"${UVICORN_FORWARDED_ALLOW_IPS:-127.0.0.1,::1}\""]
