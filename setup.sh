#!/usr/bin/env bash
set -euo pipefail

# Always operate on the checkout that contains this script. This keeps an
# absolute invocation from creating configuration files in the caller's
# unrelated working directory.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

env_key_exists() {
  local env_file="$1"
  local key="$2"

  # Match only an active assignment for the exact key. Comments, values, and
  # longer names such as OTHER_MODE must not suppress a missing MODE entry.
  grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$env_file"
}

sync_env_with_example() {
  local example_file="$1"
  local target_file="$2"
  local added=0
  local appended_any=0
  local line
  local trimmed
  local key

  while IFS= read -r line || [ -n "$line" ]; do
    trimmed="${line#"${line%%[!$' \t']*}"}"
    case "$trimmed" in
      ''|'#'*) continue ;;
    esac

    if [[ "$trimmed" == export[[:space:]]* ]]; then
      trimmed="${trimmed#export}"
      trimmed="${trimmed#"${trimmed%%[!$' \t']*}"}"
    fi

    if [[ "$trimmed" != *'='* ]]; then
      continue
    fi

    key="${trimmed%%=*}"
    key="${key%"${key##*[!$' \t']}"}"

    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      continue
    fi

    if ! env_key_exists "$target_file" "$key"; then
      if [ "$appended_any" -eq 0 ]; then
        # Command substitution removes a trailing newline, so a non-empty last
        # byte is the portable signal that the file needs a separator first.
        if [ -s "$target_file" ] && [ -n "$(tail -c 1 "$target_file" 2>/dev/null || true)" ]; then
          printf '\n' >> "$target_file"
        fi
        appended_any=1
      fi
      printf '%s\n' "$line" >> "$target_file"
      added=$((added + 1))
    fi
  done < "$example_file"

  if [ "$added" -gt 0 ]; then
    printf '✅ Added %d new key(s) from %s into %s\n' "$added" "$example_file" "$target_file"
  else
    printf 'ℹ️  %s already contains all keys from %s\n' "$target_file" "$example_file"
  fi
}

printf '🔧 Setting up configuration...\n\n'

EXAMPLE_ENV=".env.example"
if [ ! -f "$EXAMPLE_ENV" ]; then
  printf '❌ Required environment template not found: %s\n' "$SCRIPT_DIR/$EXAMPLE_ENV" >&2
  exit 1
fi
if [ ! -f .env ]; then
  cp "$EXAMPLE_ENV" .env
  printf '✅ Created .env from %s\n' "$EXAMPLE_ENV"
else
  printf 'ℹ️  .env already exists; syncing new keys from %s\n' "$EXAMPLE_ENV"
  sync_env_with_example "$EXAMPLE_ENV" ".env"
fi

generate_jwt_secret() {
  python3 -c "import secrets; print(secrets.token_urlsafe(64))"
}

generate_encryption_key() {
  python3 - <<'PY'
import base64
import os

try:
    from cryptography.fernet import Fernet
except ImportError:
    print(base64.urlsafe_b64encode(os.urandom(32)).decode())
else:
    print(Fernet.generate_key().decode())
PY
}

generate_grafana_admin_user() {
  printf 'omlorix-admin'
}

percent_encode_url_component() {
  local value="$1"

  # Redis credentials are embedded in a URI. Encode every reserved character
  # so an operator-provided password such as `secret#value` reaches Redis
  # unchanged instead of being interpreted as URI syntax.
  printf '%s' "$value" | python3 -c \
    'import sys; from urllib.parse import quote; print(quote(sys.stdin.read(), safe=""), end="")'
}

decode_double_quoted_env_value() {
  local value="$1"
  local remaining="${value#\"}"
  local decoded=""
  local character
  local escaped=0

  # Walk the value instead of cutting at the first quote. Compose accepts
  # escaped quotes and backslashes in double-quoted dotenv values, so the first
  # literal `\"` is not necessarily the end of the credential. Stop at the
  # first unescaped closing quote and ignore any following inline comment.
  while [ -n "$remaining" ]; do
    character="${remaining:0:1}"
    remaining="${remaining:1}"

    if [ "$escaped" -eq 1 ]; then
      case "$character" in
        '"'|'\') decoded="${decoded}${character}" ;;
        *) decoded="${decoded}\\${character}" ;;
      esac
      escaped=0
    elif [ "$character" = '\' ]; then
      escaped=1
    elif [ "$character" = '"' ]; then
      printf '%s' "$decoded"
      return 0
    else
      decoded="${decoded}${character}"
    fi
  done

  # Preserve a trailing unmatched backslash in a malformed value. Validation
  # can then reject the actual content instead of silently dropping a byte.
  if [ "$escaped" -eq 1 ]; then
    decoded="${decoded}\\"
  fi
  printf '%s' "$decoded"
}

escape_sed_replacement() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//&/\\&}
  value=${value//|/\\|}
  printf '%s' "$value"
}

get_env_value() {
  local env_file="$1"
  local key="$2"
  local line
  local value

  if [ ! -f "$env_file" ]; then
    return 0
  fi

  # Keep this assignment grammar aligned with env_key_exists() and
  # set_env_value(). The Compose wrapper accepts leading whitespace and an
  # optional `export`, so setup must not mistake those legitimate forms for a
  # missing credential and append a generated override later in the file.
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$env_file" | tail -n 1 || true)"
  line="${line#"${line%%[!$' \t']*}"}"
  if [[ "$line" == export[[:space:]]* ]]; then
    line="${line#export}"
    line="${line#"${line%%[!$' \t']*}"}"
  fi
  value="${line#*=}"
  value="$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

  if [ "${value#\"}" != "$value" ]; then
    value="$(decode_double_quoted_env_value "$value")"
  elif [ "${value#\'}" != "$value" ]; then
    value="${value#\'}"
    value="${value%%\'*}"
  else
    # Match Compose dotenv semantics: `#` begins an inline comment only when
    # separated from an unquoted value by whitespace. A literal `#` is valid
    # inside credentials and must not be silently truncated.
    value="${value%%[[:space:]]#*}"
    value="$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  fi

  printf '%s' "$value"
}

env_value_is_blank_or_placeholder() {
  local env_file="$1"
  local key="$2"
  local value

  value="$(get_env_value "$env_file" "$key")"
  [ -z "$value" ] || [ "$value" = "CHANGE_ME" ]
}

env_flag_enabled() {
  local env_file="$1"
  local key="$2"
  local default_value="$3"
  local value

  value="$(get_env_value "$env_file" "$key")"
  if [ -z "$value" ]; then
    value="$default_value"
  fi
  value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"

  case "$value" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

sed_in_place() {
  local file="$1"
  local expression="$2"
  local tmp
  tmp="$(mktemp "${file}.XXXXXX")"
  chmod 600 "$tmp"

  trap 'rm -f "$tmp"' RETURN
  if ! sed -E "$expression" "$file" >"$tmp"; then
    return 1
  fi
  if ! mv "$tmp" "$file"; then
    return 1
  fi
  trap - RETURN
  return 0
}

set_env_value() {
  local env_file="$1"
  local key="$2"
  local value="$3"
  local escaped_value

  escaped_value="$(escape_sed_replacement "$value")"
  if env_key_exists "$env_file" "$key"; then
    # Normalize every active spelling of the key. Replacing all occurrences
    # also prevents a pre-existing duplicate from retaining a conflicting
    # effective value after the requested update.
    sed_in_place "$env_file" "s|^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=.*|${key}=\"${escaped_value}\"|"
  else
    printf '%s=\"%s\"\n' "$key" "$value" >> "$env_file"
  fi
}

sync_bundled_redis_url() {
  local env_file="$1"
  local redis_password="$2"
  local redis_url
  local encoded_redis_password
  local expected_redis_url
  local encode_status=0

  # External and disabled Redis configurations belong to the operator. Only
  # synthesize a URL when the bundled Compose service is the selected topology.
  if ! env_flag_enabled "$env_file" REDIS_ENABLED true \
    || ! env_flag_enabled "$env_file" OMLORIX_USE_BUNDLED_REDIS true; then
    return 0
  fi

  encoded_redis_password=$(percent_encode_url_component "$redis_password") || encode_status=$?
  if [ "$encode_status" -ne 0 ] || [ -z "$encoded_redis_password" ]; then
    printf '❌ Failed to encode REDIS_PASSWORD for REDIS_URL (python3 exit=%s)\n' "$encode_status" >&2
    return 1
  fi

  # REDIS_URL is derived state for the bundled service. Recompute it on every
  # setup run so a manual password rotation cannot leave application processes
  # authenticating with an older credential.
  redis_url="$(get_env_value "$env_file" REDIS_URL)"
  expected_redis_url="redis://:${encoded_redis_password}@redis:6379/0"
  if [ "$redis_url" != "$expected_redis_url" ]; then
    # Backend processes run inside Compose containers, where bundled Redis is
    # reachable through its service DNS name rather than localhost.
    set_env_value "$env_file" REDIS_URL "$expected_redis_url"
    printf '✅ Synced bundled REDIS_URL with REDIS_PASSWORD\n'
  fi
}

ensure_required_keys() {
  local env_file="$1"

  # Replace placeholder service credentials with generated values so copied
  # example configs are not deployable with known defaults.
  local generated_value

  if env_value_is_blank_or_placeholder "$env_file" DATABASE_PASSWORD; then
    local generated_status=0
    generated_value=$(generate_jwt_secret) || generated_status=$?
    if [ "$generated_status" -ne 0 ] || [ -z "${generated_value}" ]; then
      printf '❌ Failed to generate DATABASE_PASSWORD (python3 exit=%s)\n' "$generated_status" >&2
      return 1
    fi
    set_env_value "$env_file" DATABASE_PASSWORD "$generated_value"
    printf '✅ Generated DATABASE_PASSWORD\n'
  fi

  if env_value_is_blank_or_placeholder "$env_file" REDIS_PASSWORD; then
    local generated_status=0
    generated_value=$(generate_jwt_secret) || generated_status=$?
    if [ "$generated_status" -ne 0 ] || [ -z "${generated_value}" ]; then
      printf '❌ Failed to generate REDIS_PASSWORD (python3 exit=%s)\n' "$generated_status" >&2
      return 1
    fi
    set_env_value "$env_file" REDIS_PASSWORD "$generated_value"
    printf '✅ Generated REDIS_PASSWORD\n'
  fi
  sync_bundled_redis_url "$env_file" "$(get_env_value "$env_file" REDIS_PASSWORD)"

  if env_value_is_blank_or_placeholder "$env_file" MINIO_ROOT_USER; then
    local generated_status=0
    generated_value="omlorix-$(generate_jwt_secret)" || generated_status=$?
    if [ "$generated_status" -ne 0 ] || [ -z "${generated_value}" ]; then
      printf '❌ Failed to generate MINIO_ROOT_USER (python3 exit=%s)\n' "$generated_status" >&2
      return 1
    fi
    set_env_value "$env_file" MINIO_ROOT_USER "$generated_value"
    printf '✅ Generated MINIO_ROOT_USER\n'
  fi

  if env_value_is_blank_or_placeholder "$env_file" MINIO_ROOT_PASSWORD; then
    local generated_status=0
    generated_value=$(generate_jwt_secret) || generated_status=$?
    if [ "$generated_status" -ne 0 ] || [ -z "${generated_value}" ]; then
      printf '❌ Failed to generate MINIO_ROOT_PASSWORD (python3 exit=%s)\n' "$generated_status" >&2
      return 1
    fi
    set_env_value "$env_file" MINIO_ROOT_PASSWORD "$generated_value"
    printf '✅ Generated MINIO_ROOT_PASSWORD\n'
  fi

  if env_value_is_blank_or_placeholder "$env_file" GRAFANA_ADMIN_USER \
    || [ "$(get_env_value "$env_file" GRAFANA_ADMIN_USER)" = "admin" ]; then
    set_env_value "$env_file" GRAFANA_ADMIN_USER "$(generate_grafana_admin_user)"
    printf '✅ Set GRAFANA_ADMIN_USER to a non-default value\n'
  fi

  if env_value_is_blank_or_placeholder "$env_file" GRAFANA_ADMIN_PASSWORD; then
    local generated_status=0
    generated_value=$(generate_jwt_secret) || generated_status=$?
    if [ "$generated_status" -ne 0 ] || [ -z "${generated_value}" ]; then
      printf '❌ Failed to generate GRAFANA_ADMIN_PASSWORD (python3 exit=%s)\n' "$generated_status" >&2
      return 1
    fi
    set_env_value "$env_file" GRAFANA_ADMIN_PASSWORD "$generated_value"
    printf '✅ Generated GRAFANA_ADMIN_PASSWORD\n'
  fi

  # Check and generate JWT_SECRET_KEY if empty
  if env_value_is_blank_or_placeholder "$env_file" JWT_SECRET_KEY; then
    local jwt_key
    local jwt_status=0
    jwt_key=$(generate_jwt_secret) || jwt_status=$?
    if [ "$jwt_status" -ne 0 ] || [ -z "${jwt_key}" ]; then
      printf '❌ Failed to generate JWT_SECRET_KEY (python3 exit=%s)\n' "$jwt_status" >&2
      return 1
    fi
    set_env_value "$env_file" JWT_SECRET_KEY "$jwt_key"
    printf '✅ Generated JWT_SECRET_KEY\n'
  fi

  # Check and generate ENCRYPTION_KEY if empty
  if env_value_is_blank_or_placeholder "$env_file" ENCRYPTION_KEY; then
    local enc_key
    local enc_status=0
    enc_key=$(generate_encryption_key) || enc_status=$?
    if [ "$enc_status" -ne 0 ] || [ -z "${enc_key}" ]; then
      printf '❌ Failed to generate ENCRYPTION_KEY (python3 exit=%s)\n' "$enc_status" >&2
      return 1
    fi
    set_env_value "$env_file" ENCRYPTION_KEY "$enc_key"
    printf '✅ Generated ENCRYPTION_KEY\n'
  fi

  # Keep password-reset fingerprints stable across workers and restarts. A
  # process-local fallback is intentionally available in the backend, but an
  # installation created by setup should always have a persistent salt.
  local reset_salt
  reset_salt="$(get_env_value "$env_file" PASSWORD_RESET_IDENTIFIER_HASH_SALT)"
  if [ "$reset_salt" = "CHANGE_ME" ] || [ "${#reset_salt}" -lt 16 ]; then
    local reset_salt_status=0
    reset_salt=$(generate_jwt_secret) || reset_salt_status=$?
    if [ "$reset_salt_status" -ne 0 ] || [ -z "${reset_salt}" ]; then
      printf '❌ Failed to generate PASSWORD_RESET_IDENTIFIER_HASH_SALT (python3 exit=%s)\n' "$reset_salt_status" >&2
      return 1
    fi
    set_env_value "$env_file" PASSWORD_RESET_IDENTIFIER_HASH_SALT "$reset_salt"
    printf '✅ Generated PASSWORD_RESET_IDENTIFIER_HASH_SALT\n'
  fi

  # Audit and password-reset IP fingerprints use a dedicated salt so rotating
  # JWT_SECRET_KEY cannot change their pseudonymous identifiers.
  local log_ip_salt
  log_ip_salt="$(get_env_value "$env_file" LOG_IP_HASH_SALT)"
  if [ "$log_ip_salt" = "CHANGE_ME" ] || [ "${#log_ip_salt}" -lt 16 ]; then
    local log_ip_salt_status=0
    log_ip_salt=$(generate_jwt_secret) || log_ip_salt_status=$?
    if [ "$log_ip_salt_status" -ne 0 ] || [ -z "${log_ip_salt}" ]; then
      printf '❌ Failed to generate LOG_IP_HASH_SALT (python3 exit=%s)\n' "$log_ip_salt_status" >&2
      return 1
    fi
    set_env_value "$env_file" LOG_IP_HASH_SALT "$log_ip_salt"
    printf '✅ Generated LOG_IP_HASH_SALT\n'
  fi

  # Encrypted archives are the default and plaintext archives are disabled, so
  # a blank passphrase would make the advertised backup workflow unusable.
  if env_value_is_blank_or_placeholder "$env_file" BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE; then
    local backup_passphrase
    local backup_passphrase_status=0
    backup_passphrase=$(generate_jwt_secret) || backup_passphrase_status=$?
    if [ "$backup_passphrase_status" -ne 0 ] || [ -z "${backup_passphrase}" ]; then
      printf '❌ Failed to generate BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE (python3 exit=%s)\n' "$backup_passphrase_status" >&2
      return 1
    fi
    set_env_value "$env_file" BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE "$backup_passphrase"
    printf '✅ Generated BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE\n'
  fi

  return 0
}

# Auto-generate required keys if they're empty
if [ -f .env ]; then
  # .env contains database, cache, encryption, and backup secrets. Enforce the
  # same owner-only mode used by the Server CLI even when setup does not need to
  # rewrite any values during this run.
  chmod 600 .env
  ensure_required_keys .env
  # Keep the invariant explicit after atomic replacements as well; do not rely
  # on platform-specific mktemp defaults for the final secret file mode.
  chmod 600 .env
fi

cat <<'EONEXT'

Setup complete.

Next steps:
  1. Review your generated credentials in .env before production use & optionally save them securely (e.g. password manager)
  2. Start the app: make up
EONEXT
