#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="${OMLORIX_ENV_FILE:-$ROOT_DIR/.env}"

decode_double_quoted_env_value() {
  value_to_decode="$1"
  # The validators are POSIX sh scripts, so use awk for the small state
  # machine needed to distinguish escaped quotes from the closing quote.
  printf '%s' "$value_to_decode" | awk '
    {
      decoded = ""
      escaped = 0
      for (position = 2; position <= length($0); position++) {
        character = substr($0, position, 1)
        if (escaped) {
          if (character == "\"" || character == "\\") {
            decoded = decoded character
          } else {
            decoded = decoded "\\" character
          }
          escaped = 0
        } else if (character == "\\") {
          escaped = 1
        } else if (character == "\"") {
          printf "%s", decoded
          exit
        } else {
          decoded = decoded character
        }
      }
      if (escaped) {
        decoded = decoded "\\"
      }
      printf "%s", decoded
    }
  '
}

get_kv() {
  key="$1"
  if [ ! -f "$ENV_FILE" ]; then
    return
  fi
  # Use the same assignment grammar as setup.sh and compose.sh so validation
  # cannot reject a value that the runtime loader accepts.
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$ENV_FILE" | tail -n 1 || true)"
  line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//')"
  case "$line" in
    export[[:space:]]*)
      line="${line#export}"
      line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//')"
      ;;
  esac
  value="${line#*=}"
  value="$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [ "${value#\"}" != "$value" ]; then
    value="$(decode_double_quoted_env_value "$value")"
  elif [ "${value#\'}" != "$value" ]; then
    value="${value#\'}"
    value="${value%%\'*}"
  else
    value="${value%%[[:space:]]#*}"
    value="$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  fi
  printf '%s' "$value"
}

compose_files=""
subcommand=""
expect_value=0

for arg in "$@"; do
  if [ "$expect_value" -eq 1 ]; then
    compose_files="${compose_files}
$(basename "$arg")"
    expect_value=0
    continue
  fi

  case "$arg" in
    -f|--file)
      expect_value=1
      ;;
    --env-file|--profile|-p|--project-name)
      expect_value=1
      ;;
    --*)
      ;;
    -*)
      ;;
    *)
      subcommand="$arg"
      break
      ;;
  esac
done

case "$subcommand" in
  ""|config|down|events|exec|images|kill|logs|ls|pause|port|ps|rm|stop|top|unpause|version)
    exit 0
    ;;
esac

managed_cloud=0
if printf '%s\n' "$compose_files" | grep -qx 'docker-compose.managed-cloud.yml'; then
  managed_cloud=1
fi

mode="$(printf '%s' "$(get_kv MODE)" | tr '[:upper:]' '[:lower:]')"
# Source development mode deliberately permits placeholder-friendly local
# workflows. Managed cloud hardcodes MODE=production inside Compose, so it must
# always receive the full production and external-services validation.
if [ "$managed_cloud" -eq 0 ] && [ -n "$mode" ] && [ "$mode" = "dev" ]; then
  exit 0
fi

errors=""

append_error() {
  message="$1"
  errors="${errors}
- ${message}"
}

require_not_blank_or_placeholder() {
  key="$1"
  label="$2"
  value="$(get_kv "$key")"
  if [ -z "$value" ] || [ "$value" = "CHANGE_ME" ]; then
    append_error "${label} (${key}) must be set to a non-placeholder value."
  fi
}

normalize_boolean() {
  value="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    1|true|yes|on) printf 'true' ;;
    *) printf 'false' ;;
  esac
}

jwt_secret="$(get_kv JWT_SECRET_KEY)"
if [ -z "$jwt_secret" ] || [ "${#jwt_secret}" -lt 32 ]; then
  append_error "JWT signing secret (JWT_SECRET_KEY) must be set and at least 32 characters long."
fi

require_not_blank_or_placeholder "ENCRYPTION_KEY" "Encryption key"

observability=0

if printf '%s\n' "$compose_files" | grep -qx 'docker-compose.observability.yml'; then
  observability=1
fi

# Source deployments use the `.env` toggles as their infrastructure source of
# truth. Managed cloud is different by definition: that Compose topology has
# no bundled database, Redis, or durable local file storage. Force its
# effective topology here so a default source-oriented `.env` cannot pass
# validation and then fail only after migration containers have started.
bundled_db="$(get_kv OMLORIX_USE_BUNDLED_DB)"
bundled_redis="$(get_kv OMLORIX_USE_BUNDLED_REDIS)"
bundled_storage="$(get_kv OMLORIX_USE_BUNDLED_STORAGE)"
redis_enabled="$(get_kv REDIS_ENABLED)"

# Normalize to true/false
[ -z "$bundled_db" ] && bundled_db="true"
[ -z "$bundled_redis" ] && bundled_redis="true"
[ -z "$bundled_storage" ] && bundled_storage="false"
[ -z "$redis_enabled" ] && redis_enabled="true"
bundled_db="$(normalize_boolean "$bundled_db")"
bundled_redis="$(normalize_boolean "$bundled_redis")"
bundled_storage="$(normalize_boolean "$bundled_storage")"
redis_enabled="$(normalize_boolean "$redis_enabled")"

pgbouncer="$(normalize_boolean "$(get_kv OMLORIX_USE_PGBOUNCER)")"
if [ "$pgbouncer" = "true" ]; then
  if [ "$bundled_db" != "true" ]; then
    append_error "PgBouncer requires bundled PostgreSQL (OMLORIX_USE_BUNDLED_DB=true)."
  fi
  pgbouncer_pool_mode="$(printf '%s' "$(get_kv PGBOUNCER_POOL_MODE)" | tr '[:upper:]' '[:lower:]')"
  [ -z "$pgbouncer_pool_mode" ] && pgbouncer_pool_mode="transaction"
  case "$pgbouncer_pool_mode" in
    transaction|session) ;;
    *) append_error "PGBOUNCER_POOL_MODE must be transaction or session." ;;
  esac
fi

if [ "$managed_cloud" -eq 1 ]; then
  bundled_db="false"
  bundled_redis="false"
  bundled_storage="false"
fi

# Redis Off supersedes the stored bundled/external choice. Keep credentials in
# the file for future re-enabling, but do not validate either connection mode.
if [ "$redis_enabled" != "true" ]; then
  bundled_redis="false"
fi

if [ "$bundled_db" = "true" ]; then
  require_not_blank_or_placeholder "DATABASE_PASSWORD" "Bundled Postgres password"
fi

if [ "$redis_enabled" = "true" ] && [ "$bundled_redis" = "true" ]; then
  require_not_blank_or_placeholder "REDIS_PASSWORD" "Bundled Redis password"
  redis_url="$(get_kv REDIS_URL)"
  if [ -z "$redis_url" ] || printf '%s' "$redis_url" | grep -q 'CHANGE_ME'; then
    append_error "Bundled Redis connection URL (REDIS_URL) must be set and must not contain CHANGE_ME."
  fi
fi

if [ "$bundled_db" = "false" ]; then
  database_url="$(get_kv DATABASE_URL)"
  if [ -z "$database_url" ] || printf '%s' "$database_url" | grep -q 'CHANGE_ME'; then
    if [ "$managed_cloud" -eq 1 ]; then
      append_error "Managed cloud DATABASE_URL must be set and must not contain CHANGE_ME."
    else
      append_error "External DATABASE_URL must be set and must not contain CHANGE_ME when bundled DB is disabled."
    fi
  fi
fi

if [ "$redis_enabled" = "true" ] && [ "$bundled_redis" = "false" ]; then
  redis_url="$(get_kv REDIS_URL)"
  if [ -z "$redis_url" ] || printf '%s' "$redis_url" | grep -q 'CHANGE_ME'; then
    if [ "$managed_cloud" -eq 1 ]; then
      append_error "Managed cloud REDIS_URL must be set and must not contain CHANGE_ME when Redis is enabled."
    else
      append_error "External REDIS_URL must be set and must not contain CHANGE_ME when bundled Redis is disabled."
    fi
  fi
  case "$redis_url" in
    redis://*localhost:*|rediss://*localhost:*|redis://*127.0.0.1:*|rediss://*127.0.0.1:*|redis://redis:6379/0|rediss://redis:6379/0)
      append_error "External REDIS_URL must point to your external Redis service, not localhost or the bundled redis hostname."
      ;;
  esac
fi

if [ "$bundled_db" = "false" ] && [ "$bundled_storage" = "false" ]; then
  storage_provider="$(printf '%s' "$(get_kv FILE_STORAGE_PROVIDER)" | tr '[:upper:]' '[:lower:]')"
  if [ -z "$storage_provider" ] || [ "$storage_provider" = "local" ]; then
    if [ "$managed_cloud" -eq 1 ]; then
      append_error "Managed cloud FILE_STORAGE_PROVIDER must be a non-local provider (s3, gcs, azure, or webdav)."
    else
      append_error "FILE_STORAGE_PROVIDER must be set to a non-local provider (s3, gcs, azure, or webdav) when bundled storage is disabled."
    fi
  fi
fi

if [ "$observability" -eq 1 ]; then
  grafana_user="$(get_kv GRAFANA_ADMIN_USER)"
  grafana_password="$(get_kv GRAFANA_ADMIN_PASSWORD)"
  if [ -z "$grafana_user" ] || [ "$grafana_user" = "CHANGE_ME" ] || [ "$grafana_user" = "admin" ]; then
    append_error "Grafana admin username (GRAFANA_ADMIN_USER) must be set to a non-default value when observability is enabled."
  fi
  if [ -z "$grafana_password" ] || [ "$grafana_password" = "CHANGE_ME" ]; then
    append_error "Grafana admin password (GRAFANA_ADMIN_PASSWORD) must be set to a non-placeholder value when observability is enabled."
  fi
fi

# Check Docker Compose version for required: false support (Compose 2.20+/Docker Engine 24.0+)
# docker-compose.server.yml uses required: false in depends_on for profiled services.
compose_version="$(docker compose version --short 2>/dev/null || true)"
if [ -n "$compose_version" ]; then
  compose_major="$(printf '%s' "$compose_version" | cut -d. -f1)"
  compose_minor="$(printf '%s' "$compose_version" | cut -d. -f2)"
  if [ -n "$compose_major" ] && [ -n "$compose_minor" ]; then
    if [ "$compose_major" -lt 2 ] || { [ "$compose_major" -eq 2 ] && [ "$compose_minor" -lt 20 ]; }; then
      append_error "Docker Compose $compose_version is too old. docker-compose.server.yml requires Compose 2.20+ (Docker Engine 24.0+). Update Docker or use the older compose files."
    fi
  fi
fi

if [ -n "$errors" ]; then
  printf 'Production env preflight failed for %s:%s\n' "$ENV_FILE" "$errors" >&2
  exit 1
fi
