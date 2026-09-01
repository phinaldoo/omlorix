#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_ENV="$REPO_ROOT/.env"
CALLER_COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME-}"
CALLER_COMPOSE_PROJECT_NAME_SET=0
CALLER_DOCKER_COMPOSE_BIN="${DOCKER_COMPOSE_BIN-}"
CALLER_PATH="${PATH-}"
DOCKER_BIN="$(command -v docker || true)"

if [[ -n "${COMPOSE_PROJECT_NAME+x}" ]]; then
  CALLER_COMPOSE_PROJECT_NAME_SET=1
fi

load_env_file() {
  local env_file="$1"
  local line trimmed key value

  while IFS= read -r line || [[ -n "$line" ]]; do
    trimmed="${line#"${line%%[!$' \t\r\n']*}"}"
    trimmed="${trimmed%"${trimmed##*[!$' \t\r\n']}"}"

    if [[ -z "$trimmed" || "${trimmed:0:1}" == "#" ]]; then
      continue
    fi

    if [[ "$trimmed" == export[[:space:]]* ]]; then
      trimmed="${trimmed#export}"
      trimmed="${trimmed#"${trimmed%%[!$' \t']*}"}"
    fi

    if [[ "$trimmed" != *=* ]]; then
      echo "[compose.sh] Warning: ignoring invalid env line in $env_file" >&2
      continue
    fi

    key="${trimmed%%=*}"
    value="${trimmed#*=}"
    key="${key%"${key##*[!$' \t']}"}"
    value="${value#"${value%%[!$' \t']*}"}"

    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "[compose.sh] Warning: ignoring invalid env key in $env_file" >&2
      continue
    fi

    if [[ "$key" == "DOCKER_COMPOSE_BIN" ]]; then
      echo "[compose.sh] Warning: ignoring DOCKER_COMPOSE_BIN from $env_file; set it in the caller environment instead." >&2
      continue
    fi

    if [[ "$value" == \"* ]]; then
      value="$(decode_double_quoted_env_value "$value")"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    else
      value="${value%%[[:space:]]#*}"
      value="${value%"${value##*[!$' \t']}"}"
    fi

    export "$key=$value"
  done < "$env_file"
}

decode_double_quoted_env_value() {
  local value="$1"
  local remaining="${value#\"}"
  local decoded=""
  local character
  local escaped=0

  # Match Docker Compose dotenv quoting: escaped quotes and backslashes belong
  # to the value, while an unescaped quote terminates it before any comment.
  while [[ -n "$remaining" ]]; do
    character="${remaining:0:1}"
    remaining="${remaining:1}"

    if [[ "$escaped" -eq 1 ]]; then
      case "$character" in
        '"'|'\') decoded="${decoded}${character}" ;;
        *) decoded="${decoded}\\${character}" ;;
      esac
      escaped=0
    elif [[ "$character" == '\' ]]; then
      escaped=1
    elif [[ "$character" == '"' ]]; then
      printf '%s' "$decoded"
      return 0
    else
      decoded="${decoded}${character}"
    fi
  done

  if [[ "$escaped" -eq 1 ]]; then
    decoded="${decoded}\\"
  fi
  printf '%s' "$decoded"
}

build_compose_profiles() {
  local profiles=""
  local redis_enabled="${REDIS_ENABLED:-true}"
  env_flag_enabled "$redis_enabled"                        && redis_enabled="true" || redis_enabled="false"
  env_flag_enabled "${OMLORIX_USE_BUNDLED_DB:-false}"       && profiles="${profiles:+$profiles,}bundled-db"
  [[ "$redis_enabled" == "true" ]]                       && profiles="${profiles:+$profiles,}redis-enabled"
  [[ "$redis_enabled" == "true" ]] && env_flag_enabled "${OMLORIX_USE_BUNDLED_REDIS:-false}" \
    && profiles="${profiles:+$profiles,}bundled-redis"
  env_flag_enabled "${OMLORIX_USE_PGBOUNCER:-false}"        && profiles="${profiles:+$profiles,}pgbouncer"
  env_flag_enabled "${OMLORIX_USE_BUNDLED_STORAGE:-false}"  && profiles="${profiles:+$profiles,}bundled-storage"
  echo "$profiles"
}

env_flag_enabled() {
  local value
  value="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ "${OMLORIX_SKIP_ENV_SOURCE:-false}" == "true" ]]; then
  :
elif [[ -f "$CONFIG_ENV" ]]; then
  load_env_file "$CONFIG_ENV"
else
  echo "[compose.sh] Warning: $CONFIG_ENV not found; falling back to current environment." >&2
fi

if env_flag_enabled "${OMLORIX_USE_PGBOUNCER:-false}"; then
  if ! env_flag_enabled "${OMLORIX_USE_BUNDLED_DB:-false}"; then
    echo "[compose.sh] PgBouncer requires OMLORIX_USE_BUNDLED_DB=true." >&2
    exit 1
  fi
  case "$(printf '%s' "${PGBOUNCER_POOL_MODE:-transaction}" | tr '[:upper:]' '[:lower:]')" in
    transaction|session) ;;
    *)
      echo "[compose.sh] PGBOUNCER_POOL_MODE must be transaction or session." >&2
      exit 1
      ;;
  esac
fi

if [[ "$CALLER_COMPOSE_PROJECT_NAME_SET" -eq 1 ]]; then
  COMPOSE_PROJECT_NAME="$CALLER_COMPOSE_PROJECT_NAME"
elif [[ -z "${COMPOSE_PROJECT_NAME:-}" ]]; then
  COMPOSE_PROJECT_NAME="$(basename "$REPO_ROOT")"
fi

export COMPOSE_PROJECT_NAME

# Build COMPOSE_PROFILES from toggles and export it for docker compose.
COMPOSE_PROFILES="$(build_compose_profiles)"
export COMPOSE_PROFILES

# Keep environment-derived service host overrides in this parent shell.  The
# profile builder runs via command substitution, so any export done inside that
# function would be lost before docker compose sees it.
if env_flag_enabled "${OMLORIX_USE_BUNDLED_DB:-false}" \
  && env_flag_enabled "${OMLORIX_USE_PGBOUNCER:-false}"; then
  export DATABASE_URL=""
  export DATABASE_HOST_OVERRIDE="pgbouncer"
  export DATABASE_PORT_OVERRIDE="5432"
  export DATABASE_MIGRATION_HOST_OVERRIDE="postgres"
  export DATABASE_MIGRATION_PORT_OVERRIDE="5432"
elif env_flag_enabled "${OMLORIX_USE_BUNDLED_DB:-false}"; then
  export DATABASE_URL=""
  export DATABASE_HOST_OVERRIDE="postgres"
  export DATABASE_PORT_OVERRIDE="5432"
  export DATABASE_MIGRATION_HOST_OVERRIDE="postgres"
  export DATABASE_MIGRATION_PORT_OVERRIDE="5432"
fi

if [[ -n "$CALLER_DOCKER_COMPOSE_BIN" ]]; then
  # shellcheck disable=SC2206
  DOCKER_COMPOSE_CMD=($CALLER_DOCKER_COMPOSE_BIN)
elif [[ -n "$DOCKER_BIN" ]]; then
  DOCKER_COMPOSE_CMD=("$DOCKER_BIN" compose)
else
  DOCKER_COMPOSE_CMD=(docker compose)
fi

cd "$REPO_ROOT"
export PATH="$CALLER_PATH"
sh "$REPO_ROOT/script/validate-production-env.sh" "$@"
exec "${DOCKER_COMPOSE_CMD[@]}" "$@"
