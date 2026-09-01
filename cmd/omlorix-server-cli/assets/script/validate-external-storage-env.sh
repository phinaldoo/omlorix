#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="${OMLORIX_ENV_FILE:-$ROOT_DIR/.env}"

get_kv() {
  key="$1"
  if [ ! -f "$ENV_FILE" ]; then
    return
  fi
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  value="${line#*=}"
  value="$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [ "${value#\"}" != "$value" ]; then
    value="${value#\"}"
    value="${value%%\"*}"
  elif [ "${value#\'}" != "$value" ]; then
    value="${value#\'}"
    value="${value%%\'*}"
  else
    value="${value%%#*}"
    value="$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  fi
  printf '%s' "$value"
}

errors=""

append_error() {
  message="$1"
  errors="${errors}\n- ${message}"
}

storage_provider="$(printf '%s' "$(get_kv FILE_STORAGE_PROVIDER)" | tr '[:upper:]' '[:lower:]')"
if [ -z "$storage_provider" ] || [ "$storage_provider" = "local" ]; then
  append_error "FILE_STORAGE_PROVIDER must be set to a non-local provider (s3, gcs, azure, or webdav) when using external/shared storage."
fi

if [ -n "$errors" ]; then
  printf 'External storage env validation failed for %s:%s\n' "$ENV_FILE" "$errors" >&2
  exit 1
fi
