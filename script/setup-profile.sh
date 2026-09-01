#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAIN_ENV="$ROOT_DIR/.env"

set_kv() {
  local file="$1"
  local key="$2"
  local value="$3"

  if [ ! -f "$file" ]; then
    touch "$file"
  fi

  if grep -qE "^${key}=" "$file" 2>/dev/null; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "$file"
    rm -f "$file.bak"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

prompt_yes_no() {
  local prompt="$1"
  local default="${2:-yes}"
  local answer

  while true; do
    if [ "$default" = "yes" ]; then
      printf "%s [Y/n]: " "$prompt" >&2
    else
      printf "%s [y/N]: " "$prompt" >&2
    fi
    read -r answer
    answer="$(echo "$answer" | tr '[:upper:]' '[:lower:]' | xargs)"
    if [ -z "$answer" ]; then
      answer="$default"
    fi
    case "$answer" in
      y|yes) echo "true"; return ;;
      n|no) echo "false"; return ;;
    esac
    echo "Please answer yes or no." >&2
  done
}

prompt_choice() {
  local prompt="$1"
  shift
  local options=("$@")
  local idx=1

  echo "$prompt" >&2
  for opt in "${options[@]}"; do
    echo "  $idx) $opt" >&2
    idx=$((idx + 1))
  done

  local selected
  while true; do
    printf "Choose [1-%s]: " "${#options[@]}" >&2
    read -r selected
    if [[ "$selected" =~ ^[0-9]+$ ]] && [ "$selected" -ge 1 ] && [ "$selected" -le "${#options[@]}" ]; then
      echo "${options[$((selected - 1))]}"
      return
    fi
    echo "Invalid choice. Try again." >&2
  done
}

generate_secret() {
  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
}

declare -a pairs=()

add_pair() {
  local key="$1"
  local value="$2"
  pairs+=("$key=$value")
}

apply_pairs() {
  local pair key value
  for pair in "${pairs[@]}"; do
    key="${pair%%=*}"
    value="${pair#*=}"
    set_kv "$MAIN_ENV" "$key" "$value"
  done
}

append_external_s3_defaults() {
  add_pair "FILE_STORAGE_PROVIDER" "s3"
  add_pair "FILE_STORAGE_S3_BUCKET" "omlorix-user-files"
  add_pair "FILE_STORAGE_S3_REGION" "us-east-1"
  add_pair "FILE_STORAGE_S3_PREFIX" ""
  add_pair "FILE_STORAGE_S3_ENDPOINT_URL" ""
  add_pair "FILE_STORAGE_S3_ACCESS_KEY_ID" ""
  add_pair "FILE_STORAGE_S3_SECRET_ACCESS_KEY" ""
}

echo "Omlorix Deployment Configuration Setup"
echo

bundled_db="$(prompt_yes_no "Use bundled PostgreSQL?" "yes")"
bundled_redis="$(prompt_yes_no "Use bundled Redis?" "yes")"
if [ "$bundled_db" = "true" ]; then
  pgbouncer="$(prompt_yes_no "Use PgBouncer connection pooler?" "no")"
else
  pgbouncer="false"
fi
bundled_storage="$(prompt_yes_no "Use bundled MinIO object storage?" "no")"

add_pair "OMLORIX_USE_BUNDLED_DB" "$bundled_db"
add_pair "OMLORIX_USE_BUNDLED_REDIS" "$bundled_redis"
# This setup path chooses between bundled and external Redis; Redis Off is
# managed by the desktop launcher's three-state settings control.
add_pair "REDIS_ENABLED" "true"
add_pair "OMLORIX_USE_PGBOUNCER" "$pgbouncer"
add_pair "OMLORIX_USE_BUNDLED_STORAGE" "$bundled_storage"
add_pair "DB_MIGRATIONS_MODE" "off"
add_pair "DB_MIGRATIONS_LOCK_TIMEOUT_SECONDS" "120"

if [ "$bundled_storage" = "true" ]; then
  add_pair "FILE_STORAGE_PROVIDER" "s3"
  add_pair "FILE_STORAGE_S3_BUCKET" "omlorix-user-files"
  add_pair "FILE_STORAGE_S3_REGION" "us-east-1"
  add_pair "FILE_STORAGE_S3_PREFIX" ""
  add_pair "FILE_STORAGE_S3_ENDPOINT_URL" "http://minio:9000"
  minio_access_key="omlorix-$(generate_secret)"
  minio_secret_key="$(generate_secret)"
  add_pair "FILE_STORAGE_S3_ACCESS_KEY_ID" "$minio_access_key"
  add_pair "FILE_STORAGE_S3_SECRET_ACCESS_KEY" "$minio_secret_key"
  add_pair "MINIO_ROOT_USER" "$minio_access_key"
  add_pair "MINIO_ROOT_PASSWORD" "$minio_secret_key"
else
  storage=$(prompt_choice "Select user file storage provider:" "s3-compatible" "gcs" "azure" "webdav")
  case "$storage" in
    s3-compatible)
      append_external_s3_defaults
      ;;
    gcs)
      add_pair "FILE_STORAGE_PROVIDER" "gcs"
      add_pair "FILE_STORAGE_GCS_BUCKET" "omlorix-user-files"
      add_pair "FILE_STORAGE_GCS_PROJECT" ""
      add_pair "FILE_STORAGE_GCS_PREFIX" ""
      add_pair "FILE_STORAGE_GCS_CREDENTIALS_JSON" ""
      ;;
    azure)
      add_pair "FILE_STORAGE_PROVIDER" "azure"
      add_pair "FILE_STORAGE_AZURE_CONTAINER" "omlorix-user-files"
      add_pair "FILE_STORAGE_AZURE_PREFIX" ""
      add_pair "FILE_STORAGE_AZURE_CONNECTION_STRING" ""
      add_pair "FILE_STORAGE_AZURE_ACCOUNT_URL" ""
      add_pair "FILE_STORAGE_AZURE_CREDENTIAL" ""
      ;;
    webdav)
      add_pair "FILE_STORAGE_PROVIDER" "webdav"
      add_pair "FILE_STORAGE_WEBDAV_URL" ""
      add_pair "FILE_STORAGE_WEBDAV_USERNAME" ""
      add_pair "FILE_STORAGE_WEBDAV_PASSWORD" ""
      add_pair "FILE_STORAGE_WEBDAV_PREFIX" ""
      add_pair "FILE_STORAGE_WEBDAV_VERIFY_SSL" "true"
      add_pair "FILE_STORAGE_WEBDAV_TIMEOUT" "30"
      ;;
  esac
fi

if [ "$bundled_db" = "true" ] && [ "$bundled_redis" = "true" ]; then
  add_pair "MODE" "production"
  add_pair "FRONTEND_HTTP_HOST_PORT" "8080"
fi

if [ "$pgbouncer" = "true" ]; then
  add_pair "DATABASE_HOST_OVERRIDE" "pgbouncer"
  add_pair "DATABASE_PORT_OVERRIDE" "5432"
  add_pair "PGBOUNCER_POOL_MODE" "transaction"
  add_pair "PGBOUNCER_DEFAULT_POOL_SIZE" "40"
  add_pair "PGBOUNCER_HOST_BIND" "127.0.0.1"
fi

if [ "$bundled_storage" = "true" ]; then
  add_pair "MINIO_API_HOST_BIND" "127.0.0.1"
  add_pair "MINIO_CONSOLE_HOST_BIND" "127.0.0.1"
  add_pair "BACKUP_SCHEDULER_ENABLED" "true"
fi

if [ "$bundled_db" = "false" ] || [ "$bundled_redis" = "false" ]; then
  add_pair "BACKUP_SCHEDULER_ENABLED" "true"
fi

if [ "$bundled_db" = "false" ]; then
  add_pair "DATABASE_URL" ""
fi

if [ "$bundled_redis" = "false" ]; then
  add_pair "REDIS_URL" ""
fi

echo
apply_pairs
echo "Updated $MAIN_ENV"
echo
echo "Next steps:"
echo "  - For source checkout with local images: make prod-local-up"
echo "  - For published release images: make single-server-up"
echo "  - Run migrations manually any time with: make migrate"
echo
echo "Your toggles:"
echo "  Bundled DB:      $bundled_db"
echo "  Bundled Redis:   $bundled_redis"
echo "  PgBouncer:       $pgbouncer"
echo "  Bundled Storage: $bundled_storage"
