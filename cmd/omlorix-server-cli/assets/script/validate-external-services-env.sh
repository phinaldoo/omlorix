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

errors=""

append_error() {
  message="$1"
  errors="${errors}\n- ${message}"
}

require_not_blank_or_placeholder() {
  key="$1"
  label="$2"
  value="$(get_kv "$key")"
  if [ -z "$value" ] || [ "$value" = "CHANGE_ME" ]; then
    append_error "${label} (${key}) must be set to a non-placeholder value."
  fi
}

require_not_blank_or_placeholder "DATABASE_URL" "Database URL"
redis_enabled="$(printf '%s' "$(get_kv REDIS_ENABLED)" | tr '[:upper:]' '[:lower:]')"
[ -z "$redis_enabled" ] && redis_enabled="true"
case "$redis_enabled" in
  1|true|yes|on) redis_enabled="true" ;;
  *) redis_enabled="false" ;;
esac
if [ "$redis_enabled" = "true" ]; then
  require_not_blank_or_placeholder "REDIS_URL" "Redis URL"
fi

storage_provider="$(printf '%s' "$(get_kv FILE_STORAGE_PROVIDER)" | tr '[:upper:]' '[:lower:]')"
if [ -z "$storage_provider" ] || [ "$storage_provider" = "local" ]; then
  append_error "FILE_STORAGE_PROVIDER must be set to a non-local provider (s3, gcs, azure, or webdav) for external services."
fi

if [ "$redis_enabled" = "true" ]; then
  redis_url="$(get_kv REDIS_URL)"
  case "$redis_url" in
    redis://*localhost:*|rediss://*localhost:*|redis://*127.0.0.1:*|rediss://*127.0.0.1:*|redis://redis:6379/0|rediss://redis:6379/0)
      append_error "REDIS_URL must point to an external Redis service, not localhost or the bundled redis hostname."
      ;;
  esac
fi

if [ -n "$errors" ]; then
  printf 'External services env validation failed for %s:%s\n' "$ENV_FILE" "$errors" >&2
  exit 1
fi
