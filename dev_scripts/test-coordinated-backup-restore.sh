#!/usr/bin/env bash
set -euo pipefail

# Regression coverage for the source-checkout restore coordinator uses a fake
# Compose wrapper. No test in this file may contact Docker or modify real data.

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/omlorix-restore-test.XXXXXX")"

cleanup() {
  if [[ -n "${TEST_ROOT:-}" && -d "$TEST_ROOT" ]]; then
    rm -r -- "$TEST_ROOT"
  fi
}
trap cleanup EXIT

fail() {
  printf 'coordinated restore regression test failed: %s\n' "$1" >&2
  exit 1
}

fixture="$TEST_ROOT/fixture"
mkdir -p "$fixture/script"
cp "$REPO_ROOT/script/coordinated-backup-restore.sh" "$fixture/script/"

# Record exact high-level Compose operations and return controlled backend
# results. Restore output deliberately includes progress before JSON to verify
# that recovery parsing does not assume pristine stdout.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$TEST_COMPOSE_LOG"' \
  'command_line=" $* "' \
  'if [[ "$command_line" == *" config --services "* ]]; then' \
  '  printf "postgres\nmigrate\nemail_worker\noperations_worker\ngeneration_worker\nresearch_worker\nfile_processing_worker\naccount_lifecycle_worker\nmaintenance_worker\nrendering_worker\nmedia_worker\nconnector_worker\naudit_event_worker\nrealtime_gateway\nfastapi\nfrontend\n"' \
  '  if [[ "${TEST_REDIS_ENABLED:-true}" == "true" ]]; then' \
  '    printf "automation_scheduler\nautomation_worker\n"' \
  '  fi' \
  '  exit "${TEST_CONFIG_STATUS:-0}"' \
  'fi' \
  'if [[ "$command_line" == *" ps --all --orphans --format json "* ]]; then' \
  '  printf "{\"ID\":\"aaaaaaaaaaaa\",\"Service\":\"postgres\",\"State\":\"running\",\"Labels\":\"com.docker.compose.oneoff=%s\"}\n" "${TEST_INFRA_ONEOFF_LABEL:-False}"' \
  '  printf "{\"ID\":\"bbbbbbbbbbbb\",\"Service\":\"removed_worker\",\"State\":\"running\"}\n"' \
  '  printf "{\"ID\":\"eeeeeeeeeeee\",\"Service\":\"postgres\",\"State\":\"running\",\"Labels\":{\"com.docker.compose.oneoff\":\"True\"}}\n"' \
  '  exit "${TEST_INVENTORY_STATUS:-0}"' \
  'fi' \
  'if [[ "$command_line" == *" app.backups.cli restore-preflight "* ]]; then' \
  '  printf "{\"ok\":%s}\n" "$([[ "${TEST_VERIFY_STATUS:-0}" == "0" ]] && printf true || printf false)"' \
  '  exit "${TEST_VERIFY_STATUS:-0}"' \
  'fi' \
  'if [[ "$command_line" == *" stop frontend email_worker operations_worker generation_worker research_worker file_processing_worker account_lifecycle_worker maintenance_worker rendering_worker media_worker connector_worker audit_event_worker realtime_gateway automation_scheduler automation_worker fastapi "* ]]; then' \
  '  exit "${TEST_STOP_STATUS:-0}"' \
  'fi' \
  'if [[ "$command_line" == *" app.backups.cli restore "* ]]; then' \
  '  printf "restore progress\n"' \
  '  preflight="{}"' \
  '  if [[ "${TEST_RESTORE_EMBEDDED_SAFE:-false}" == "true" ]]; then' \
  '    preflight="{\"manifest\":{\"attacker_controlled\":{\"recovery\":{\"safe_to_restart\":true}}}}"' \
  '  fi' \
  '  if [[ "${TEST_RESTORE_TRUNCATED_SAFE:-false}" == "true" ]]; then' \
  '    printf "{\"status\":\"failed\",\"preflight\":{\"manifest\":{\"attacker_controlled\":{\"recovery\":{\"safe_to_restart\":true}}\n"' \
  '    exit "${TEST_RESTORE_STATUS:-9}"' \
  '  fi' \
  '  printf "{\"status\":\"%s\",\"error\":%s,\"preflight\":%s,\"recovery\":{\"state\":\"%s\",\"safe_to_restart\":%s}}\n" \' \
  '    "$([[ "${TEST_RESTORE_STATUS:-0}" == "0" ]] && printf success || printf failed)" \' \
  '    "$([[ "${TEST_RESTORE_STATUS:-0}" == "0" ]] && printf null || printf \"restore_failed\")" \' \
  '    "$preflight" \' \
  '    "$([[ "${TEST_RESTORE_SAFE:-true}" == "true" ]] && printf not_started || printf unsafe)" \' \
  '    "${TEST_RESTORE_SAFE:-true}"' \
  '  if [[ "${TEST_RESTORE_STDERR_SAFE:-false}" == "true" ]]; then' \
  '    printf "{\"recovery\":{\"state\":\"not_started\",\"safe_to_restart\":true}}\n" >&2' \
  '  fi' \
  '  exit "${TEST_RESTORE_STATUS:-0}"' \
  'fi' \
  'if [[ "$command_line" == *" up -d "* ]]; then' \
  '  exit "${TEST_START_STATUS:-0}"' \
  'fi' \
  'exit 0' \
  > "$fixture/script/compose.sh"
chmod 0700 "$fixture/script/compose.sh" "$fixture/script/coordinated-backup-restore.sh"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$TEST_DOCKER_LOG"' \
  'exit "${TEST_FENCE_STOP_STATUS:-0}"' \
  > "$fixture/docker"
chmod 0700 "$fixture/docker"

COMPOSE_LOG="$TEST_ROOT/compose.log"
STDOUT_LOG="$TEST_ROOT/stdout.log"
STDERR_LOG="$TEST_ROOT/stderr.log"
DOCKER_LOG="$TEST_ROOT/docker.log"
RESTORE_STATUS=0

run_restore() {
  : > "$COMPOSE_LOG"
  : > "$STDOUT_LOG"
  : > "$STDERR_LOG"
  : > "$DOCKER_LOG"
  set +e
  TEST_COMPOSE_LOG="$COMPOSE_LOG" \
    TEST_VERIFY_STATUS="${TEST_VERIFY_STATUS:-0}" \
    TEST_CONFIG_STATUS="${TEST_CONFIG_STATUS:-0}" \
    TEST_INVENTORY_STATUS="${TEST_INVENTORY_STATUS:-0}" \
    TEST_INFRA_ONEOFF_LABEL="${TEST_INFRA_ONEOFF_LABEL:-False}" \
    TEST_STOP_STATUS="${TEST_STOP_STATUS:-0}" \
    TEST_RESTORE_STATUS="${TEST_RESTORE_STATUS:-0}" \
    TEST_RESTORE_SAFE="${TEST_RESTORE_SAFE:-true}" \
    TEST_RESTORE_EMBEDDED_SAFE="${TEST_RESTORE_EMBEDDED_SAFE:-false}" \
    TEST_RESTORE_STDERR_SAFE="${TEST_RESTORE_STDERR_SAFE:-false}" \
    TEST_RESTORE_TRUNCATED_SAFE="${TEST_RESTORE_TRUNCATED_SAFE:-false}" \
    TEST_REDIS_ENABLED="${TEST_REDIS_ENABLED:-true}" \
    TEST_START_STATUS="${TEST_START_STATUS:-0}" \
    TEST_FENCE_STOP_STATUS="${TEST_FENCE_STOP_STATUS:-0}" \
    TEST_DOCKER_LOG="$DOCKER_LOG" \
    OMLORIX_RESTORE_DOCKER_BIN="$fixture/docker" \
    "$fixture/script/coordinated-backup-restore.sh" \
      -f docker-compose.server.yml -- "$@" \
      > "$STDOUT_LOG" 2> "$STDERR_LOG"
  RESTORE_STATUS=$?
  set -e
}

# Invalid destructive options must fail without invoking Compose at all.
run_restore --source file:///app/backups/example.tar.zst --target in_place
[[ "$RESTORE_STATUS" -eq 2 ]] || fail "missing in-place confirmation returned the wrong status"
[[ ! -s "$COMPOSE_LOG" ]] || fail "invalid restore options invoked Compose"

run_restore \
  --source file:///app/backups/example.tar.zst \
  --job-id existing-job
[[ "$RESTORE_STATUS" -eq 2 ]] || fail "multiple restore sources returned the wrong status"
[[ ! -s "$COMPOSE_LOG" ]] || fail "multiple restore sources invoked Compose"

# Archive verification happens while the application is still running. A
# verification failure must never reach the stop phase.
TEST_VERIFY_STATUS=7 run_restore --source file:///app/backups/broken.tar.zst
[[ "$RESTORE_STATUS" -eq 1 ]] || fail "verification failure returned the wrong status"
grep -Fq 'app.backups.cli restore-preflight --source file:///app/backups/broken.tar.zst --target empty' "$COMPOSE_LOG" \
  || fail "restore did not verify its source"
if grep -Fq ' stop ' "$COMPOSE_LOG"; then
  fail "verification failure stopped application services"
fi

# A structured safe failure restarts the application but still returns the
# original restore error. Redis-off mode must not reactivate profiled workers.
TEST_RESTORE_STATUS=9 TEST_RESTORE_SAFE=true TEST_REDIS_ENABLED=false \
  run_restore --source file:///app/backups/example.tar.zst
if [[ "$RESTORE_STATUS" -ne 9 ]]; then
  printf '%s\n' '--- restore stdout ---' >&2
  sed -n '1,160p' "$STDOUT_LOG" >&2
  printf '%s\n' '--- restore stderr ---' >&2
  sed -n '1,160p' "$STDERR_LOG" >&2
  printf '%s\n' '--- compose commands ---' >&2
  sed -n '1,160p' "$COMPOSE_LOG" >&2
  printf '%s\n' '--- docker commands ---' >&2
  sed -n '1,160p' "$DOCKER_LOG" >&2
  fail "safe restore failure lost its original status"
fi
grep -Fq 'stop frontend email_worker operations_worker generation_worker research_worker file_processing_worker account_lifecycle_worker maintenance_worker rendering_worker media_worker connector_worker audit_event_worker realtime_gateway automation_scheduler automation_worker fastapi' "$COMPOSE_LOG" \
  || fail "restore did not stop every possibly stale application service"
grep -Fq 'stop --time 60 bbbbbbbbbbbb eeeeeeeeeeee' "$DOCKER_LOG" \
  || fail "restore did not fence an active orphaned application container"
if grep -Eq 'stop --time 60 .*aaaaaaaaaaaa' "$DOCKER_LOG"; then
  fail "restore stopped the normal PostgreSQL infrastructure container"
fi
grep -Fq 'up -d --no-deps --force-recreate --remove-orphans frontend email_worker operations_worker generation_worker research_worker file_processing_worker account_lifecycle_worker maintenance_worker rendering_worker media_worker connector_worker audit_event_worker realtime_gateway fastapi' "$COMPOSE_LOG" \
  || fail "safe Redis-off failure did not restart the core application"
if grep -E 'up -d .*automation_(scheduler|worker)' "$COMPOSE_LOG" >/dev/null; then
  fail "Redis-off recovery restart activated automation services"
fi

# Without a positive recovery decision, no application process may be started
# against potentially partial restored data.
TEST_RESTORE_STATUS=9 TEST_RESTORE_SAFE=false TEST_REDIS_ENABLED=true \
  run_restore --source file:///app/backups/example.tar.zst
[[ "$RESTORE_STATUS" -eq 9 ]] || fail "unsafe restore failure returned the wrong status"
if grep -Fq ' up -d ' "$COMPOSE_LOG"; then
  fail "unsafe restore failure restarted application services"
fi

# Nested manifest data and stderr are untrusted for the restart decision. Only
# the final top-level recovery object on stdout may authorize a restart.
TEST_RESTORE_STATUS=9 TEST_RESTORE_SAFE=false \
  TEST_RESTORE_EMBEDDED_SAFE=true TEST_RESTORE_STDERR_SAFE=true \
  run_restore --source file:///app/backups/crafted.tar.zst
[[ "$RESTORE_STATUS" -eq 9 ]] || fail "poisoned unsafe restore returned the wrong status"
if grep -Fq ' up -d ' "$COMPOSE_LOG"; then
  fail "embedded or stderr recovery JSON authorized an unsafe restart"
fi

# If the CLI output is truncated before its authoritative outer object closes,
# a complete nested recovery object at EOF must not become authoritative.
TEST_RESTORE_STATUS=9 TEST_RESTORE_TRUNCATED_SAFE=true \
  run_restore --source file:///app/backups/truncated.tar.zst
[[ "$RESTORE_STATUS" -eq 9 ]] || fail "truncated restore returned the wrong status"
if grep -Fq ' up -d ' "$COMPOSE_LOG"; then
  fail "truncated outer JSON authorized a restart from nested recovery data"
fi

# Successful job-ID restore starts the complete Redis-enabled application set.
TEST_RESTORE_STATUS=0 TEST_RESTORE_SAFE=true TEST_REDIS_ENABLED=true \
  run_restore --job-id successful-job --target in_place --confirm RESTORE-IN-PLACE
[[ "$RESTORE_STATUS" -eq 0 ]] || fail "successful restore returned a failure status"
grep -Fq 'app.backups.cli restore-preflight --job-id successful-job --target in_place' "$COMPOSE_LOG" \
  || fail "job-ID restore did not verify the selected backup"
grep -Fq 'app.backups.cli restore --offline --job-id successful-job --target in_place --confirm RESTORE-IN-PLACE' \
  "$COMPOSE_LOG" \
  || fail "job-ID restore did not preserve validated restore options"
grep -Fq 'up -d --no-deps --force-recreate --remove-orphans frontend email_worker operations_worker generation_worker research_worker file_processing_worker account_lifecycle_worker maintenance_worker rendering_worker media_worker connector_worker audit_event_worker realtime_gateway automation_scheduler automation_worker fastapi' \
  "$COMPOSE_LOG" \
  || fail "Redis-enabled restore did not restart the complete application set"

# A partial stop failure attempts recovery but must never launch the backend
# restore command.
TEST_STOP_STATUS=8 TEST_REDIS_ENABLED=false \
  run_restore --source file:///app/backups/example.tar.zst
[[ "$RESTORE_STATUS" -eq 8 ]] || fail "stop failure returned the wrong status"
if grep -Fq 'app.backups.cli restore --offline' "$COMPOSE_LOG"; then
  fail "stop failure still launched the destructive restore"
fi
grep -Fq 'up -d --no-deps --force-recreate --remove-orphans frontend email_worker operations_worker generation_worker research_worker file_processing_worker account_lifecycle_worker maintenance_worker rendering_worker media_worker connector_worker audit_event_worker realtime_gateway fastapi' "$COMPOSE_LOG" \
  || fail "stop failure did not attempt a topology-correct recovery restart"

# Failure to stop a discovered orphan or one-off must fail closed before the
# backend restore command starts.
TEST_FENCE_STOP_STATUS=6 TEST_REDIS_ENABLED=false \
  run_restore --source file:///app/backups/example.tar.zst
[[ "$RESTORE_STATUS" -eq 1 ]] || fail "container fence failure returned the wrong status"
if grep -Fq 'app.backups.cli restore --offline' "$COMPOSE_LOG"; then
  fail "container fence failure still launched the destructive restore"
fi

# Infrastructure exemptions require an unambiguous normal-container label.
# A malformed label must abort instead of skipping a possible one-off writer.
TEST_INFRA_ONEOFF_LABEL=maybe TEST_REDIS_ENABLED=false \
  run_restore --source file:///app/backups/example.tar.zst
[[ "$RESTORE_STATUS" -eq 1 ]] || fail "malformed one-off label returned the wrong status"
if grep -Fq 'app.backups.cli restore --offline' "$COMPOSE_LOG"; then
  fail "malformed one-off label still launched the destructive restore"
fi

printf 'coordinated restore regression tests passed\n'
