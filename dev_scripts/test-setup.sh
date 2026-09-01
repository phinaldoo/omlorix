#!/usr/bin/env bash
set -euo pipefail

# Regression coverage for setup.sh deliberately runs against disposable copies.
# The real checkout's .env can contain operator secrets and must never be read or
# rewritten by this test.
REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omlorix-setup-test.XXXXXX")"

cleanup() {
  if [ -n "${TEST_ROOT:-}" ] && [ -d "$TEST_ROOT" ]; then
    rm -rf -- "$TEST_ROOT"
  fi
}
trap cleanup EXIT

fail() {
  printf 'setup regression test failed: %s\n' "$1" >&2
  exit 1
}

file_mode() {
  local file="$1"

  # GNU and BSD stat use different switches for the numeric permission mode.
  if stat -c '%a' "$file" >/dev/null 2>&1; then
    stat -c '%a' "$file"
  else
    stat -f '%Lp' "$file"
  fi
}

replace_env_line() {
  local env_file="$1"
  local key="$2"
  local replacement="$3"
  local tmp

  tmp="$(mktemp "${env_file}.XXXXXX")"
  awk -v key="$key" -v replacement="$replacement" '
    $0 ~ ("^[[:space:]]*(export[[:space:]]+)?" key "[[:space:]]*=") { print replacement; next }
    { print }
  ' "$env_file" > "$tmp"
  mv "$tmp" "$env_file"
}

fixture="$TEST_ROOT/fixture"
caller="$TEST_ROOT/caller"
mkdir -p "$fixture" "$caller"
cp "$REPO_ROOT/setup.sh" "$REPO_ROOT/.env.example" "$fixture/"

# Absolute invocation must configure the script's checkout, not the caller's
# current directory.
(
  cd "$caller"
  "$fixture/setup.sh" > "$TEST_ROOT/first-run.log"
)
[ -f "$fixture/.env" ] || fail "absolute invocation did not create fixture/.env"
[ ! -e "$caller/.env" ] || fail "absolute invocation created .env in the caller directory"
[ ! -d "$caller/certs" ] || fail "absolute invocation created certs in the caller directory"

# Bundled backend containers must use Compose DNS instead of their own
# localhost interface.
grep -Eq '^REDIS_URL="redis://:[^@]+@redis:6379/0"$' "$fixture/.env" \
  || fail "fresh setup did not generate a container-safe bundled REDIS_URL"

for key in PASSWORD_RESET_IDENTIFIER_HASH_SALT BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE; do
  grep -Eq "^${key}=\"[^\"]+\"$" "$fixture/.env" \
    || fail "fresh setup left $key blank"
done

[ "$(file_mode "$fixture/.env")" = "600" ] \
  || fail "fresh setup did not restrict .env permissions to 0600"

# A second run should leave generated credentials unchanged.
first_checksum="$(cksum "$fixture/.env")"
"$fixture/setup.sh" > "$TEST_ROOT/second-run.log"
second_checksum="$(cksum "$fixture/.env")"
[ "$first_checksum" = "$second_checksum" ] || fail "second setup run changed an initialized .env"

# REDIS_URL is derived from the bundled Redis password. A later credential
# rotation must update the URI even when it already uses the correct service
# hostname, otherwise the server and application authenticate differently.
replace_env_line "$fixture/.env" REDIS_PASSWORD \
  'REDIS_PASSWORD="rotated#redis:password@word"'
"$fixture/setup.sh" > "$TEST_ROOT/rotated-redis-password.log"
grep -Fqx 'REDIS_URL="redis://:rotated%23redis%3Apassword%40word@redis:6379/0"' "$fixture/.env" \
  || fail "setup left REDIS_URL stale after a bundled Redis password rotation"

# Permissions are a security invariant even when every value is already valid
# and setup therefore has no content to rewrite.
chmod 0644 "$fixture/.env"
"$fixture/setup.sh" > "$TEST_ROOT/permission-repair.log"
[ "$(file_mode "$fixture/.env")" = "600" ] \
  || fail "setup did not repair permissive existing .env permissions"

# Managed cloud has no bundled database or durable local storage. A fresh
# source-oriented `.env` must therefore fail before Docker is invoked instead
# of passing validation with credentials for services that are not in that
# topology.
if OMLORIX_ENV_FILE="$fixture/.env" \
  sh "$REPO_ROOT/script/validate-production-env.sh" \
    -f docker-compose.managed-cloud.yml up \
    > "$TEST_ROOT/managed-default-validation.log" 2>&1; then
  fail "managed-cloud validation accepted the default bundled-service configuration"
fi
grep -Fq 'Managed cloud DATABASE_URL must be set' "$TEST_ROOT/managed-default-validation.log" \
  || fail "managed-cloud validation did not require DATABASE_URL"
grep -Fq 'Managed cloud FILE_STORAGE_PROVIDER must be a non-local provider' \
  "$TEST_ROOT/managed-default-validation.log" \
  || fail "managed-cloud validation did not require external file storage"

managed_dev_env="$TEST_ROOT/managed-dev.env"
sed 's/^MODE=.*/MODE="dev"/' "$fixture/.env" > "$managed_dev_env"
if OMLORIX_ENV_FILE="$managed_dev_env" \
  sh "$REPO_ROOT/script/validate-production-env.sh" \
    -f docker-compose.managed-cloud.yml up \
    > "$TEST_ROOT/managed-dev-validation.log" 2>&1; then
  fail "managed-cloud validation trusted dev mode even though Compose forces production"
fi
grep -Fq 'Managed cloud DATABASE_URL must be set' "$TEST_ROOT/managed-dev-validation.log" \
  || fail "managed-cloud dev-mode input bypassed external database validation"

# Existing-but-empty credentials are just as unusable as missing credentials
# and must be regenerated before setup reports success.
for key in \
  DATABASE_PASSWORD \
  REDIS_PASSWORD \
  MINIO_ROOT_USER \
  MINIO_ROOT_PASSWORD \
  GRAFANA_ADMIN_PASSWORD \
  JWT_SECRET_KEY \
  ENCRYPTION_KEY \
  PASSWORD_RESET_IDENTIFIER_HASH_SALT \
  BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE
do
  replace_env_line "$fixture/.env" "$key" "${key}=\"\""
done
"$fixture/setup.sh" > "$TEST_ROOT/blank-credentials.log"
for key in \
  DATABASE_PASSWORD \
  REDIS_PASSWORD \
  MINIO_ROOT_USER \
  MINIO_ROOT_PASSWORD \
  GRAFANA_ADMIN_PASSWORD \
  JWT_SECRET_KEY \
  ENCRYPTION_KEY \
  PASSWORD_RESET_IDENTIFIER_HASH_SALT \
  BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE
do
  grep -Eq "^${key}=\"[^\"]+\"$" "$fixture/.env" \
    || fail "$key remained blank after setup"
done

# A configured salt that is too short does not provide the installation-level
# entropy required by the CLI validation rules and must be replaced.
replace_env_line "$fixture/.env" PASSWORD_RESET_IDENTIFIER_HASH_SALT \
  'PASSWORD_RESET_IDENTIFIER_HASH_SALT="short"'
"$fixture/setup.sh" > "$TEST_ROOT/short-reset-salt.log"
reset_salt="$(sed -n 's/^PASSWORD_RESET_IDENTIFIER_HASH_SALT="\([^"]*\)"$/\1/p' "$fixture/.env")"
[ "${#reset_salt}" -ge 16 ] \
  || fail "setup preserved an undersized password-reset identifier salt"

# The Compose loader accepts optional `export` and leading whitespace. Setup
# must read those same forms and preserve existing operator credentials instead
# of appending generated overrides that become the effective values.
syntax_fixture="$TEST_ROOT/assignment-syntax"
mkdir -p "$syntax_fixture"
cp "$REPO_ROOT/setup.sh" "$REPO_ROOT/.env.example" "$syntax_fixture/"
sed \
  -e 's/^MODE=.*/export MODE="dev"/' \
  -e 's/^DATABASE_PASSWORD=.*/export DATABASE_PASSWORD="operator-db-secret"/' \
  -e 's/^REDIS_PASSWORD=.*/  REDIS_PASSWORD="operator-redis-secret"/' \
  -e 's/^JWT_SECRET_KEY=.*/export JWT_SECRET_KEY=""/' \
  -e 's|^REDIS_URL=.*|REDIS_URL="redis://:operator-redis-secret@redis:6379/0"|' \
  "$syntax_fixture/.env.example" > "$syntax_fixture/.env"
"$syntax_fixture/setup.sh" > "$TEST_ROOT/assignment-syntax.log"
[ "$(grep -Ec '^[[:space:]]*(export[[:space:]]+)?DATABASE_PASSWORD[[:space:]]*=' "$syntax_fixture/.env")" -eq 1 ] \
  || fail "setup duplicated an exported DATABASE_PASSWORD assignment"
[ "$(grep -Ec '^[[:space:]]*(export[[:space:]]+)?REDIS_PASSWORD[[:space:]]*=' "$syntax_fixture/.env")" -eq 1 ] \
  || fail "setup duplicated an indented REDIS_PASSWORD assignment"
grep -Eq '^export DATABASE_PASSWORD="operator-db-secret"$' "$syntax_fixture/.env" \
  || fail "setup replaced an exported operator database password"
grep -Eq '^[[:space:]]+REDIS_PASSWORD="operator-redis-secret"$' "$syntax_fixture/.env" \
  || fail "setup replaced an indented operator Redis password"
[ "$(grep -Ec '^[[:space:]]*(export[[:space:]]+)?JWT_SECRET_KEY[[:space:]]*=' "$syntax_fixture/.env")" -eq 1 ] \
  || fail "setup duplicated an exported blank JWT_SECRET_KEY assignment"
grep -Eq '^JWT_SECRET_KEY="[^"]+"$' "$syntax_fixture/.env" \
  || fail "setup did not replace an exported blank JWT secret"

# The Makefile and compose.sh must use `.env` as their shared source of truth.
# A caller-provided MODE must not select different port overlays than the mode
# that the containers will receive after compose.sh loads `.env`.
make_output="$(MODE=production make --no-print-directory -n -f "$REPO_ROOT/Makefile" -C "$syntax_fixture" up 2>/dev/null)"
printf '%s\n' "$make_output" | grep -Fq 'docker-compose.dev-ports.yml' \
  || fail "Makefile did not prefer the exported .env MODE over caller MODE"

# The checkout always uses the production server topology with local source
# images. No configuration toggle should be required to select that stack.
printf '%s\n' "$make_output" | grep -Fq 'docker-compose.server.yml -f docker-compose.source-build.yml' \
  || fail "Makefile did not select the source-build stack"

# Legacy invocation toggles must not override the bundled topology stored in
# `.env`. Managed cloud is selected later in this test from a fully external
# `.env`, matching the Launcher and standalone CLI.
legacy_managed_output="$(USE_MANAGED_CLOUD=true BUILD=false make --no-print-directory -n -f "$REPO_ROOT/Makefile" -C "$syntax_fixture" up 2>/dev/null)"
if printf '%s\n' "$legacy_managed_output" | grep -Fq 'docker-compose.managed-cloud.yml'; then
  fail "legacy USE_MANAGED_CLOUD overrode the .env topology"
fi

# PgBouncer and bundled storage are canonical `.env` settings. Legacy Make
# aliases must not activate profiles independently of the application config;
# compose.sh will derive both COMPOSE_PROFILES and backend host overrides.
legacy_profile_output="$(USE_PGBOUNCER=true USE_BUNDLED_STORAGE=true make --no-print-directory -n -f "$REPO_ROOT/Makefile" -C "$syntax_fixture" up 2>/dev/null)"
if printf '%s\n' "$legacy_profile_output" | grep -Eq -- '--profile[[:space:]]+(pgbouncer|bundled-storage)'; then
  fail "Makefile still activates service profiles from legacy USE_* aliases"
fi

make_help="$(make --no-print-directory -f "$REPO_ROOT/Makefile" -C "$syntax_fixture" help)"
printf '%s\n' "$make_help" | grep -Fq 'OMLORIX_USE_PGBOUNCER=true' \
  || fail "Makefile help does not document the canonical PgBouncer setting"
printf '%s\n' "$make_help" | grep -Fq 'OMLORIX_USE_BUNDLED_STORAGE=true' \
  || fail "Makefile help does not document the canonical bundled-storage setting"
printf '%s\n' "$make_help" | grep -Fq 'OTEL_ENABLED=true' \
  || fail "Makefile help does not document the canonical observability setting"
if printf '%s\n' "$make_help" | grep -Fq '  USE_PGBOUNCER=true'; then
  fail "Makefile help still documents the legacy PgBouncer alias"
fi
if printf '%s\n' "$make_help" | grep -Fq '  USE_BUNDLED_STORAGE=true'; then
  fail "Makefile help still documents the legacy bundled-storage alias"
fi
if printf '%s\n' "$make_help" | grep -Fq '  OTEL=true'; then
  fail "Makefile help still documents the legacy observability alias"
fi

restore_make_output="$(make --no-print-directory -n -f "$REPO_ROOT/Makefile" -C "$syntax_fixture" \
  backup-restore BACKUP_JOB_ID=backup-1 BACKUP_TARGET=in_place BACKUP_CONFIRM=RESTORE-IN-PLACE)"
printf '%s\n' "$restore_make_output" | grep -Fq './script/coordinated-backup-restore.sh' \
  || fail "Makefile restore does not use the coordinated lifecycle wrapper"
printf '%s\n' "$restore_make_output" \
  | grep -Fq -- '--job-id "backup-1"' \
  || fail "Makefile restore did not forward the backup job ID"
printf '%s\n' "$restore_make_output" \
  | grep -Fq -- '--confirm "RESTORE-IN-PLACE"' \
  || fail "Makefile restore did not forward the in-place confirmation"

# Observability selection is also persistent `.env` state. A caller alias must
# not attach the overlay, while OTEL_ENABLED in `.env` must attach it even when
# the caller tries to disable that value.
legacy_otel_output="$(OTEL=true make --no-print-directory -n -f "$REPO_ROOT/Makefile" -C "$syntax_fixture" up 2>/dev/null)"
if printf '%s\n' "$legacy_otel_output" | grep -Fq 'docker-compose.observability.yml'; then
  fail "legacy OTEL alias overrode observability state from .env"
fi
otel_make_fixture="$TEST_ROOT/otel-make"
mkdir -p "$otel_make_fixture"
sed 's/^OTEL_ENABLED=.*/OTEL_ENABLED=true/' "$syntax_fixture/.env" > "$otel_make_fixture/.env"
otel_make_output="$(OTEL_ENABLED=false make --no-print-directory -n -f "$REPO_ROOT/Makefile" -C "$otel_make_fixture" up 2>/dev/null)"
printf '%s\n' "$otel_make_output" | grep -Fq 'docker-compose.observability.yml' \
  || fail "Makefile did not select observability from .env"
if [ "$(uname -s)" = "Linux" ]; then
  printf '%s\n' "$otel_make_output" | grep -Fq 'docker-compose.observability-linux.yml' \
    || fail "Linux Makefile topology omitted the host-metrics overlay"
else
  if printf '%s\n' "$otel_make_output" | grep -Fq 'docker-compose.observability-linux.yml'; then
    fail "non-Linux Makefile topology included the host-metrics overlay"
  fi
fi

# Exercise the real Compose wrapper without contacting Docker. This verifies
# that canonical toggles from `.env` jointly select the service profiles and
# PgBouncer host, while conflicting caller variables cannot create a split
# configuration between Make and the containers.
compose_authority_fixture="$TEST_ROOT/compose-env-authority"
mkdir -p "$compose_authority_fixture/script"
cp "$REPO_ROOT/script/compose.sh" "$REPO_ROOT/script/validate-production-env.sh" \
  "$compose_authority_fixture/script/"
sed \
  -e 's/^MODE=.*/MODE="dev"/' \
  -e 's/^OMLORIX_USE_PGBOUNCER=.*/OMLORIX_USE_PGBOUNCER=true/' \
  -e 's/^OMLORIX_USE_BUNDLED_STORAGE=.*/OMLORIX_USE_BUNDLED_STORAGE=true/' \
  -e 's|^DATABASE_URL=.*|DATABASE_URL="postgresql://external.example/other"|' \
  "$REPO_ROOT/.env.example" > "$compose_authority_fixture/.env"
fake_compose="$compose_authority_fixture/fake-compose"
printf '%s\n' \
  '#!/usr/bin/env sh' \
  'printf "mode=%s\nprofiles=%s\ndatabase_host=%s\ndatabase_url=%s\n" "$MODE" "$COMPOSE_PROFILES" "$DATABASE_HOST_OVERRIDE" "$DATABASE_URL"' \
  > "$fake_compose"
chmod 0700 "$fake_compose"
compose_authority_output="$(
  MODE=production \
  OMLORIX_USE_PGBOUNCER=false \
  OMLORIX_USE_BUNDLED_STORAGE=false \
  USE_PGBOUNCER=false \
  USE_BUNDLED_STORAGE=false \
  DOCKER_COMPOSE_BIN="$fake_compose" \
    "$compose_authority_fixture/script/compose.sh" config
)"
printf '%s\n' "$compose_authority_output" | grep -Fqx 'mode=dev' \
  || fail "compose.sh did not prefer MODE from .env"
printf '%s\n' "$compose_authority_output" \
  | grep -Fqx 'profiles=bundled-db,redis-enabled,bundled-redis,pgbouncer,bundled-storage' \
  || fail "compose.sh did not derive canonical service profiles from .env"
printf '%s\n' "$compose_authority_output" | grep -Fqx 'database_host=pgbouncer' \
  || fail "compose.sh did not route the backend through PgBouncer selected in .env"
printf '%s\n' "$compose_authority_output" | grep -Fqx 'database_url=' \
  || fail "compose.sh retained a higher-precedence DATABASE_URL in bundled mode"

# Setup and the documented `make up` path must agree on accepted assignment
# syntax. The production preflight used to reject the exported and indented
# credentials that setup deliberately preserved above. Compose applies the
# last duplicate assignment, so Make must do the same when selecting overlays.
printf '%s\n' 'MODE="production" # effective duplicate' >> "$syntax_fixture/.env"
duplicate_mode_output="$(MODE=dev make --no-print-directory -n -f "$REPO_ROOT/Makefile" -C "$syntax_fixture" up 2>/dev/null)"
if printf '%s\n' "$duplicate_mode_output" | grep -Fq 'docker-compose.dev-ports.yml'; then
  fail "Makefile did not use the last MODE assignment from .env"
fi
replace_env_line "$syntax_fixture/.env" MODE 'export MODE="production"'
production_make_output="$(MODE=dev make --no-print-directory -n -f "$REPO_ROOT/Makefile" -C "$syntax_fixture" up 2>/dev/null)"
if printf '%s\n' "$production_make_output" | grep -Fq 'docker-compose.dev-ports.yml'; then
  fail "caller MODE overrode production MODE from .env"
fi
OMLORIX_ENV_FILE="$syntax_fixture/.env" \
  sh "$REPO_ROOT/script/validate-production-env.sh" up \
  > "$TEST_ROOT/assignment-syntax-preflight.log"

# Managed-cloud validation uses a separate preflight. Exercise its parser too
# so the two supported deployment paths cannot drift again.
external_fixture="$TEST_ROOT/external-assignment-syntax.env"
sed \
  -e 's/^OMLORIX_USE_BUNDLED_DB=.*/OMLORIX_USE_BUNDLED_DB=false/' \
  -e 's/^OMLORIX_USE_BUNDLED_REDIS=.*/OMLORIX_USE_BUNDLED_REDIS=false/' \
  -e 's|^DATABASE_URL=.*|export DATABASE_URL="postgresql://omlorix:secret@db.example.com:5432/omlorix"|' \
  -e 's|^REDIS_URL=.*|  REDIS_URL="rediss://:secret@redis.example.com:6380/0"|' \
  -e 's/^FILE_STORAGE_PROVIDER=.*/export FILE_STORAGE_PROVIDER="s3"/' \
  "$syntax_fixture/.env" > "$external_fixture"
OMLORIX_ENV_FILE="$external_fixture" \
  sh "$REPO_ROOT/script/validate-external-services-env.sh" \
  > "$TEST_ROOT/external-assignment-syntax-preflight.log"
OMLORIX_ENV_FILE="$external_fixture" \
  sh "$REPO_ROOT/script/validate-production-env.sh" \
    -f docker-compose.managed-cloud.yml up \
    > "$TEST_ROOT/managed-assignment-syntax-preflight.log"

managed_make_fixture="$TEST_ROOT/managed-make"
mkdir -p "$managed_make_fixture"
cp "$external_fixture" "$managed_make_fixture/.env"
managed_make_output="$(USE_MANAGED_CLOUD=false make --no-print-directory -n -f "$REPO_ROOT/Makefile" -C "$managed_make_fixture" up 2>/dev/null)"
printf '%s\n' "$managed_make_output" | grep -Fq 'docker-compose.managed-cloud.yml' \
  || fail "Makefile did not derive managed cloud from the fully external .env topology"
if printf '%s\n' "$managed_make_output" | grep -Fq 'docker-compose.dev-ports.yml'; then
  fail "managed-cloud topology included source-only development ports"
fi

# Setup must not rewrite an operator-owned external Redis URL. Only bundled
# Redis treats REDIS_URL as derived state.
external_setup_fixture="$TEST_ROOT/external-setup"
mkdir -p "$external_setup_fixture"
cp "$REPO_ROOT/setup.sh" "$REPO_ROOT/.env.example" "$external_setup_fixture/"
sed \
  -e 's/^OMLORIX_USE_BUNDLED_REDIS=.*/OMLORIX_USE_BUNDLED_REDIS=false/' \
  -e 's/^REDIS_PASSWORD=.*/REDIS_PASSWORD="unused-bundled-password"/' \
  -e 's|^REDIS_URL=.*|REDIS_URL="rediss://:external-secret@redis.example.com:6380/0"|' \
  "$external_setup_fixture/.env.example" > "$external_setup_fixture/.env"
"$external_setup_fixture/setup.sh" > "$TEST_ROOT/external-setup.log"
grep -Fqx 'REDIS_URL="rediss://:external-secret@redis.example.com:6380/0"' "$external_setup_fixture/.env" \
  || fail "setup rewrote an operator-owned external Redis URL"

# A literal hash without preceding whitespace is part of an unquoted dotenv
# value, not an inline comment. Preserve the full Redis password and percent-
# encode it when constructing the corresponding connection URI.
hash_fixture="$TEST_ROOT/hash-password"
mkdir -p "$hash_fixture"
cp "$REPO_ROOT/setup.sh" "$REPO_ROOT/.env.example" "$hash_fixture/"
sed \
  -e 's/^REDIS_PASSWORD=.*/REDIS_PASSWORD=operator#redis:secret@word/' \
  -e 's|^REDIS_URL=.*|REDIS_URL="redis://redis:6379/0"|' \
  "$hash_fixture/.env.example" > "$hash_fixture/.env"
"$hash_fixture/setup.sh" > "$TEST_ROOT/hash-password.log"
grep -Eq '^REDIS_PASSWORD=operator#redis:secret@word$' "$hash_fixture/.env" \
  || fail "setup truncated a literal hash in REDIS_PASSWORD"
grep -Eq '^REDIS_URL="redis://:operator%23redis%3Asecret%40word@redis:6379/0"$' "$hash_fixture/.env" \
  || fail "setup did not safely encode REDIS_PASSWORD in REDIS_URL"

# The source Compose stack must consume the encoded URL written by setup.
# Reconstructing it from the raw password would reintroduce URI fragment and
# user-info delimiters into every backend process.
[ "$(grep -Fc 'REDIS_URL: ${REDIS_URL:-redis://redis:6379/0}' "$REPO_ROOT/docker-compose.server.yml")" -eq 6 ] \
  || fail "source Compose services do not consistently consume the encoded REDIS_URL"
if docker compose version >/dev/null 2>&1; then
  cp "$REPO_ROOT/docker-compose.server.yml" "$REPO_ROOT/docker-compose.source-build.yml" "$hash_fixture/"
  docker compose \
    --project-directory "$hash_fixture" \
    --env-file "$hash_fixture/.env" \
    -f "$hash_fixture/docker-compose.server.yml" \
    -f "$hash_fixture/docker-compose.source-build.yml" \
    config > "$TEST_ROOT/hash-password-compose.yml"
  # Every active rendered backend service must retain the encoded URL after
  # Compose merges the shared environment and service-specific settings.
  [ "$(grep -Fc 'REDIS_URL: redis://:operator%23redis%3Asecret%40word@redis:6379/0' "$TEST_ROOT/hash-password-compose.yml")" -ge 3 ] \
    || fail "source Compose replaced the encoded REDIS_URL"
  if grep -Fq 'REDIS_URL: redis://:operator#redis:secret@word@redis:6379/0' "$TEST_ROOT/hash-password-compose.yml"; then
    fail "source Compose reconstructed REDIS_URL from the raw password"
  fi
fi

# Escaped quotes are legal in Compose double-quoted dotenv values. Setup must
# decode the complete password before URI encoding instead of stopping at the
# escaped quote and silently desynchronizing Redis authentication.
escaped_quote_fixture="$TEST_ROOT/escaped-quote-password"
mkdir -p "$escaped_quote_fixture"
cp "$REPO_ROOT/setup.sh" "$REPO_ROOT/.env.example" "$escaped_quote_fixture/"
sed \
  -e 's/^REDIS_PASSWORD=.*/REDIS_PASSWORD="operator\\"quote"/' \
  -e 's|^REDIS_URL=.*|REDIS_URL="redis://redis:6379/0"|' \
  "$escaped_quote_fixture/.env.example" > "$escaped_quote_fixture/.env"
"$escaped_quote_fixture/setup.sh" > "$TEST_ROOT/escaped-quote-password.log"
grep -Fqx 'REDIS_PASSWORD="operator\"quote"' "$escaped_quote_fixture/.env" \
  || fail "setup rewrote an operator Redis password containing an escaped quote"
grep -Fqx 'REDIS_URL="redis://:operator%22quote@redis:6379/0"' "$escaped_quote_fixture/.env" \
  || fail "setup truncated a Redis password at an escaped quote"

# The packaged POSIX launcher must enforce the same bundled-Redis invariant as
# source setup. In particular, an already service-shaped URL may still contain
# an old credential and must be replaced after password rotation.
launcher_fixture="$TEST_ROOT/server-launcher"
mkdir -p "$launcher_fixture"
cp "$REPO_ROOT/.env.example" "$launcher_fixture/"
sed \
  -e 's/^REDIS_PASSWORD=.*/REDIS_PASSWORD=operator#redis:secret@word/' \
  -e 's|^REDIS_URL=.*|REDIS_URL="redis://:old-password@redis:6379/0"|' \
  "$launcher_fixture/.env.example" > "$launcher_fixture/.env"
OMLORIX_SERVER_HOME="$launcher_fixture" OMLORIX_SETUP_ONLY=1 \
  sh "$REPO_ROOT/script/server-launcher/start.sh" \
  > "$TEST_ROOT/server-launcher.log"
grep -Fqx 'REDIS_URL="redis://:operator%23redis%3Asecret%40word@redis:6379/0"' "$launcher_fixture/.env" \
  || fail "POSIX server launcher left bundled REDIS_URL stale or unencoded"
for key in PASSWORD_RESET_IDENTIFIER_HASH_SALT BACKUP_ARCHIVE_ENCRYPTION_PASSPHRASE; do
  grep -Eq "^${key}=\"[^\"]+\"$" "$launcher_fixture/.env" \
    || fail "POSIX server launcher left $key blank"
done

# PowerShell cannot be executed on every CI host, so retain a narrow source
# assertion alongside the runtime POSIX test. This catches regression to the
# host-only localhost URL and omission of URI component escaping.
grep -Fq '[System.Text.Encoding]::UTF8.GetBytes($Value)' "$REPO_ROOT/script/server-launcher/start.ps1" \
  || fail "PowerShell server launcher does not percent-encode Redis passwords"
grep -Fq '$expectedRedisUrl = "redis://:$encodedRedisPassword@redis:6379/0"' "$REPO_ROOT/script/server-launcher/start.ps1" \
  || fail "PowerShell server launcher does not use bundled Redis service DNS"
if grep -Eq 'REDIS_URL.*redis://:.*@localhost:' "$REPO_ROOT/script/server-launcher/start.ps1"; then
  fail "PowerShell server launcher still writes a localhost Redis URL"
fi

# Comments and longer names containing KEY= must not masquerade as an active
# assignment when new example keys are synchronized.
replace_env_line "$fixture/.env" MODE '# Documentation example: MODE=production'
"$fixture/setup.sh" > "$TEST_ROOT/comment-sync.log"
[ "$(grep -Ec '^MODE=' "$fixture/.env")" -eq 1 ] \
  || fail "MODE was not restored when only a commented example remained"

# A damaged or incomplete distribution should fail before creating output and
# must never print a false success message.
missing_template="$TEST_ROOT/missing-template"
mkdir -p "$missing_template"
cp "$REPO_ROOT/setup.sh" "$missing_template/"
if "$missing_template/setup.sh" > "$TEST_ROOT/missing-template.log" 2>&1; then
  fail "setup succeeded without .env.example"
fi
[ ! -d "$missing_template/certs" ] \
  || fail "setup created output before detecting the missing template"

if grep -q 'TODO' "$REPO_ROOT/setup.sh"; then
  fail "setup completion output still contains TODO"
fi

printf 'setup regression tests passed\n'
