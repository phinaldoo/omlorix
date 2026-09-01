#!/usr/bin/env sh
set -eu

ROOT_DIR="${OMLORIX_SERVER_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)}"
ENV_FILE="${OMLORIX_ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_BIN="${DOCKER_COMPOSE_BIN:-docker compose}"

log() {
  printf '==> %s\n' "$*"
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

open_url() {
  url="$1"
  case "${OMLORIX_OPEN_BROWSER:-auto}" in
    0|false|False|no|NO)
      return
      ;;
  esac

  if [ "$(uname -s 2>/dev/null || true)" = "Darwin" ] && have_cmd open; then
    open "$url" >/dev/null 2>&1 || true
    return
  fi
  if have_cmd xdg-open && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
    xdg-open "$url" >/dev/null 2>&1 || true
  fi
}

generate_secret() {
  secret_bytes="${1:-48}"
  if have_cmd openssl; then
    openssl rand -base64 "$secret_bytes" | tr -d '\n'
    return
  fi
  if [ -r /dev/urandom ]; then
    od -An -N"$secret_bytes" -tx1 /dev/urandom | tr -d ' \n'
    return
  fi
  die "Unable to generate a secret. Install openssl and retry."
}

generate_url_secret() {
  if have_cmd openssl; then
    openssl rand -base64 48 | tr '+/' '-_' | tr -d '=\n'
    return
  fi
  if [ -r /dev/urandom ]; then
    od -An -N48 -tx1 /dev/urandom | tr -d ' \n'
    return
  fi
  die "Unable to generate a URL-safe secret. Install openssl and retry."
}

percent_encode_url_component() {
  value_to_encode="$1"

  # Encode UTF-8 bytes without relying on Python, Perl, or another runtime that
  # may not exist on a freshly provisioned server. URI unreserved bytes remain
  # readable; every delimiter that could change Redis URI parsing is escaped.
  LC_ALL=C printf '%s' "$value_to_encode" | od -An -tx1 | awk '
    BEGIN { hex = "0123456789abcdef" }
    {
      for (index_in_line = 1; index_in_line <= NF; index_in_line++) {
        byte = tolower($index_in_line)
        if (byte ~ /^(2d|2e|3[0-9]|4[1-9a-f]|5[0-9a]|5f|6[1-9a-f]|7[0-9a]|7e)$/) {
          high = index(hex, substr(byte, 1, 1)) - 1
          low = index(hex, substr(byte, 2, 1)) - 1
          printf "%c", (high * 16) + low
        } else {
          printf "%%%s", toupper(byte)
        }
      }
    }
  '
}

generate_fernet_key() {
  if have_cmd openssl; then
    openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
    return
  fi
  die "Unable to generate ENCRYPTION_KEY. Install openssl and retry."
}

default_grafana_admin_user() {
  printf '%s' "omlorix-admin"
}

set_kv() {
  key="$1"
  value="$2"
  if awk -v key="$key" 'index($0, key "=") == 1 { found = 1; exit } END { exit found ? 0 : 1 }' "$ENV_FILE" 2>/dev/null; then
    tmp="${ENV_FILE}.tmp.$$"
    # Match the key literally instead of interpolating it into a sed regex, so
    # values containing '/', '&', '\', or other metacharacters cannot alter the
    # replacement command.
    awk -v key="$key" -v value="$value" '{ if (index($0, key "=") == 1) print key "=" value; else print }' "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

decode_double_quoted_env_value() {
  value_to_decode="$1"

  # Keep the standalone launcher aligned with Docker Compose dotenv parsing:
  # an escaped quote belongs to the value and does not terminate it.
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
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  value="${line#*=}"
  value="$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [ "${value#\"}" != "$value" ]; then
    value="$(decode_double_quoted_env_value "$value")"
  elif [ "${value#\'}" != "$value" ] && [ "${value%\'}" != "$value" ]; then
    value="${value#\'}"
    value="${value%\'}"
  elif [ "${value#\'}" != "$value" ]; then
    value="${value#\'}"
    value="${value%%\'*}"
  else
    # Compose treats `#` as an inline comment only after whitespace. Preserve
    # literal hashes in unquoted credentials so URI encoding sees every byte.
    value="${value%%[[:space:]]#*}"
    value="$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  fi
  printf '%s' "$value"
}

sync_local_redis_url() {
  redis_password="$1"
  redis_url="$(get_kv REDIS_URL)"
  encoded_redis_password="$(percent_encode_url_component "$redis_password")"
  expected_redis_url="redis://:${encoded_redis_password}@redis:6379/0"

  # Bundled REDIS_URL is derived from REDIS_PASSWORD. Always compare against
  # the complete expected URI so credential rotation repairs an otherwise
  # well-formed but stale service URL.
  if [ "$redis_url" != "$expected_redis_url" ]; then
    set_kv "REDIS_URL" "\"$expected_redis_url\""
  fi
}

# read_toggle reads a boolean toggle from the env file. Defaults to false.
read_toggle() {
  key="$1"
  value="$(get_kv "$key")"
  case "$value" in
    1|true|True|TRUE|yes|YES|on|ON)
      printf '%s' "true"
      ;;
    *)
      printf '%s' "false"
      ;;
  esac
}

ensure_minio_env() {
  minio_user="$(get_kv MINIO_ROOT_USER)"
  minio_password="$(get_kv MINIO_ROOT_PASSWORD)"
  if [ -z "$minio_user" ] || [ "$minio_user" = "CHANGE_ME" ]; then
    set_kv "MINIO_ROOT_USER" "\"omlorix-$(generate_secret | tr -cd 'A-Za-z0-9' | cut -c1-24)\""
  fi
  if [ -z "$minio_password" ] || [ "$minio_password" = "CHANGE_ME" ]; then
    set_kv "MINIO_ROOT_PASSWORD" "\"$(generate_secret)\""
  fi
}

require_external_db_env() {
  database_url="$(get_kv DATABASE_URL)"
  if [ -z "$database_url" ]; then
    die "External database requires DATABASE_URL in $ENV_FILE."
  fi
}

require_external_redis_env() {
  redis_url="$(get_kv REDIS_URL)"
  if [ -z "$redis_url" ]; then
    die "External Redis requires REDIS_URL in $ENV_FILE."
  fi
  case "$redis_url" in
    *CHANGE_ME*|redis://*localhost:*|rediss://*localhost:*|redis://*127.0.0.1:*|rediss://*127.0.0.1:*)
      die "External Redis requires REDIS_URL to point to your external Redis service, not a placeholder or localhost."
      ;;
  esac
  if [ "$redis_url" = "redis://redis:6379/0" ]; then
    die "External Redis requires REDIS_URL to include your external Redis host and credentials."
  fi
}

require_external_storage_env() {
  storage_provider="$(get_kv FILE_STORAGE_PROVIDER)"
  if [ -z "$storage_provider" ] || [ "$storage_provider" = "local" ]; then
    die "External storage requires FILE_STORAGE_PROVIDER to be s3, gcs, azure, or webdav."
  fi
}

ensure_env() {
  if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ROOT_DIR/.env.example" ]; then
      cp "$ROOT_DIR/.env.example" "$ENV_FILE"
    else
      touch "$ENV_FILE"
    fi
  fi

  # Set sensible defaults for toggles if absent so the rest of the function
  # reads the correct effective state.
  if [ -z "$(get_kv OMLORIX_USE_BUNDLED_DB)" ]; then
    set_kv "OMLORIX_USE_BUNDLED_DB" "true"
  fi
  if [ -z "$(get_kv OMLORIX_USE_BUNDLED_REDIS)" ]; then
    set_kv "OMLORIX_USE_BUNDLED_REDIS" "true"
  fi
  if [ -z "$(get_kv REDIS_ENABLED)" ]; then
    set_kv "REDIS_ENABLED" "true"
  fi
  if [ -z "$(get_kv OMLORIX_USE_PGBOUNCER)" ]; then
    set_kv "OMLORIX_USE_PGBOUNCER" "false"
  fi
  if [ -z "$(get_kv OMLORIX_USE_BUNDLED_STORAGE)" ]; then
    set_kv "OMLORIX_USE_BUNDLED_STORAGE" "false"
  fi

  if grep -q '^JWT_SECRET_KEY=""' "$ENV_FILE" || ! grep -q '^JWT_SECRET_KEY=' "$ENV_FILE"; then
    set_kv "JWT_SECRET_KEY" "\"$(generate_secret 64)\""
  fi
  if grep -q '^ENCRYPTION_KEY=""' "$ENV_FILE" || ! grep -q '^ENCRYPTION_KEY=' "$ENV_FILE"; then
    set_kv "ENCRYPTION_KEY" "\"$(generate_fernet_key)\""
  fi
  password_reset_salt="$(get_kv PASSWORD_RESET_IDENTIFIER_HASH_SALT)"
  if [ "${#password_reset_salt}" -lt 16 ]; then
    set_kv "PASSWORD_RESET_IDENTIFIER_HASH_SALT" "\"$(generate_url_secret)\""
  fi
  if [ -z "$(get_kv BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE)" ]; then
    set_kv "BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE" "\"$(generate_secret)\""
  fi
  if grep -Eq '^DATABASE_PASSWORD=("CHANGE_ME"|CHANGE_ME)' "$ENV_FILE" || ! grep -q '^DATABASE_PASSWORD=' "$ENV_FILE"; then
    set_kv "DATABASE_PASSWORD" "\"$(generate_secret)\""
  fi
  if grep -Eq '^REDIS_PASSWORD=("CHANGE_ME"|CHANGE_ME)' "$ENV_FILE" || ! grep -q '^REDIS_PASSWORD=' "$ENV_FILE"; then
    set_kv "REDIS_PASSWORD" "\"$(generate_url_secret)\""
  fi

  bundled_db="$(read_toggle OMLORIX_USE_BUNDLED_DB)"
  bundled_redis="$(read_toggle OMLORIX_USE_BUNDLED_REDIS)"
  redis_enabled="$(read_toggle REDIS_ENABLED)"
  bundled_storage="$(read_toggle OMLORIX_USE_BUNDLED_STORAGE)"
  use_pgbouncer="$(read_toggle OMLORIX_USE_PGBOUNCER)"

  if [ "$bundled_db" != "true" ] && [ "$use_pgbouncer" = "true" ]; then
    set_kv "OMLORIX_USE_PGBOUNCER" "false"
    use_pgbouncer="false"
  fi
  if [ "$bundled_db" = "true" ]; then
    set_kv "DATABASE_URL" '""'
    if [ "$use_pgbouncer" = "true" ]; then
      set_kv "DATABASE_HOST_OVERRIDE" "pgbouncer"
    else
      set_kv "DATABASE_HOST_OVERRIDE" "postgres"
    fi
    set_kv "DATABASE_PORT_OVERRIDE" "5432"
    set_kv "DATABASE_MIGRATION_HOST_OVERRIDE" "postgres"
    set_kv "DATABASE_MIGRATION_PORT_OVERRIDE" "5432"
  fi
  if [ "$use_pgbouncer" = "true" ]; then
    case "$(get_kv PGBOUNCER_POOL_MODE | tr '[:upper:]' '[:lower:]')" in
      ""|transaction|session) ;;
      *) die "PGBOUNCER_POOL_MODE must be transaction or session." ;;
    esac
  fi

  if [ "$redis_enabled" = "true" ] && [ "$bundled_redis" = "true" ]; then
    sync_local_redis_url "$(get_kv REDIS_PASSWORD)"
  fi
  if grep -Eq '^GRAFANA_ADMIN_USER=("CHANGE_ME"|CHANGE_ME|"admin"|admin)([[:space:]]+#.*)?$' "$ENV_FILE" || ! grep -q '^GRAFANA_ADMIN_USER=' "$ENV_FILE"; then
    set_kv "GRAFANA_ADMIN_USER" "\"$(default_grafana_admin_user)\""
  fi
  if grep -Eq '^GRAFANA_ADMIN_PASSWORD=("CHANGE_ME"|CHANGE_ME)([[:space:]]+#.*)?$' "$ENV_FILE" || ! grep -q '^GRAFANA_ADMIN_PASSWORD=' "$ENV_FILE"; then
    set_kv "GRAFANA_ADMIN_PASSWORD" "\"$(generate_secret)\""
  fi

  if [ "$bundled_storage" = "true" ]; then
    ensure_minio_env
  fi
  if [ "$bundled_db" = "false" ] && { [ "$redis_enabled" = "false" ] || [ "$bundled_redis" = "false" ]; }; then
    require_external_storage_env
  fi
  if [ "$bundled_db" = "false" ]; then
    require_external_db_env
  fi
  if [ "$redis_enabled" = "true" ] && [ "$bundled_redis" = "false" ]; then
    require_external_redis_env
  fi

  set_kv "FRONTEND_HTTP_HOST_PORT" "${FRONTEND_HTTP_HOST_PORT:-8080}"
}

ensure_docker() {
  have_cmd docker || die "Docker is not installed. Install Docker Desktop or Docker Engine first."
  docker info >/dev/null 2>&1 || die "Docker is not running. Start Docker and retry."
  $COMPOSE_BIN version >/dev/null 2>&1 || die "Docker Compose is not available."
}

# build_compose_profiles returns a comma-separated list of Docker Compose profiles
# based on the current toggle settings.
build_compose_profiles() {
  profiles=""
  redis_enabled="$(read_toggle REDIS_ENABLED)"
  if [ "$(read_toggle OMLORIX_USE_BUNDLED_DB)" = "true" ]; then
    profiles="${profiles:+$profiles,}bundled-db"
  fi
  if [ "$redis_enabled" = "true" ]; then
    profiles="${profiles:+$profiles,}redis-enabled"
  fi
  if [ "$redis_enabled" = "true" ] && [ "$(read_toggle OMLORIX_USE_BUNDLED_REDIS)" = "true" ]; then
    profiles="${profiles:+$profiles,}bundled-redis"
  fi
  if [ "$(read_toggle OMLORIX_USE_PGBOUNCER)" = "true" ]; then
    profiles="${profiles:+$profiles,}pgbouncer"
  fi
  if [ "$(read_toggle OMLORIX_USE_BUNDLED_STORAGE)" = "true" ]; then
    profiles="${profiles:+$profiles,}bundled-storage"
  fi
  printf '%s' "$profiles"
}

# compose_files prints the docker compose -f arguments for the current toggle state.
compose_files() {
  bundled_db="$(read_toggle OMLORIX_USE_BUNDLED_DB)"
  bundled_redis="$(read_toggle OMLORIX_USE_BUNDLED_REDIS)"
  redis_enabled="$(read_toggle REDIS_ENABLED)"
  bundled_storage="$(read_toggle OMLORIX_USE_BUNDLED_STORAGE)"
  use_pgbouncer="$(read_toggle OMLORIX_USE_PGBOUNCER)"
  mode="$(get_kv MODE | tr '[:upper:]' '[:lower:]')"

  # Determine the base compose file.
  if [ "$bundled_db" = "false" ] \
    && { [ "$redis_enabled" = "false" ] || [ "$bundled_redis" = "false" ]; } \
    && [ "$bundled_storage" = "false" ] \
    && [ "$use_pgbouncer" = "false" ]; then
    # Fully external topology: use the managed-cloud file.
    base="docker-compose.managed-cloud.yml"
  else
    base="docker-compose.server.yml"
  fi

  files="-f $base"
  files="$files -f docker-compose.frontend-port.yml"

  if [ "$mode" = "dev" ] && { [ "$bundled_db" != "false" ] || { [ "$redis_enabled" = "true" ] && [ "$bundled_redis" != "false" ]; }; }; then
    files="$files -f docker-compose.dev-ports.yml"
  fi

  printf '%s' "$files"
}

cd "$ROOT_DIR"

if [ "${OMLORIX_SETUP_ONLY:-}" = "1" ]; then
  # Setup-only mode: generate secrets and set defaults without requiring Docker.
  ensure_env
  echo "Setup complete. Use the env editor to fill in the required variables, then start Omlorix."
  exit 0
fi

ensure_docker
ensure_env

FILES="$(compose_files)"
PROFILES="$(build_compose_profiles)"

# Export COMPOSE_PROFILES so Docker Compose sees the active profiles.
export COMPOSE_PROFILES="$PROFILES"

VALIDATE_PRODUCTION_ENV="$ROOT_DIR/script/validate-production-env.sh"
if [ ! -r "$VALIDATE_PRODUCTION_ENV" ]; then
  die "Production env validator is missing or not readable: $VALIDATE_PRODUCTION_ENV"
fi

if ! sh "$VALIDATE_PRODUCTION_ENV" $FILES up; then
  exit 1
fi

log "Pulling and starting Omlorix release stack"
# shellcheck disable=SC2086
$COMPOSE_BIN --env-file "$ENV_FILE" $FILES pull
# shellcheck disable=SC2086
$COMPOSE_BIN --env-file "$ENV_FILE" $FILES up -d --remove-orphans

OMLORIX_URL="http://localhost:$(get_kv FRONTEND_HTTP_HOST_PORT)"
[ "$OMLORIX_URL" = "http://localhost:" ] && OMLORIX_URL="http://localhost:8080"
log "Omlorix is starting at $OMLORIX_URL"
open_url "$OMLORIX_URL"
