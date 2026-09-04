#!/usr/bin/env bash
set -euo pipefail

# Coordinate a full-instance restore for source-checkout Make workflows.
#
# The backend performs the destructive database/filesystem work and reports a
# structured recovery decision. This wrapper owns only the host-side lifecycle:
# validate everything possible while Omlorix is still running, stop every
# process that could touch restored data, and restart only when the backend has
# positively confirmed that doing so is safe.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=("$REPO_ROOT/script/compose.sh")
SOURCE=""
JOB_ID=""
TARGET="empty"
CONFIRM=""

usage() {
  cat >&2 <<'EOF'
Usage:
  coordinated-backup-restore.sh [compose -f arguments] -- \
    (--source <container-visible-uri> | --job-id <id>) \
    [--target empty|in_place] [--confirm RESTORE-IN-PLACE]
EOF
}

fail_usage() {
  printf 'Restore configuration error: %s\n' "$1" >&2
  usage
  exit 2
}

# Compose file arguments precede a required separator. Keeping them as an
# array preserves exact argument boundaries and avoids evaluating command text.
while [[ "$#" -gt 0 && "$1" != "--" ]]; do
  COMPOSE+=("$1")
  shift
done
if [[ "$#" -eq 0 ]]; then
  fail_usage "missing -- separator before restore options"
fi
shift

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --source)
      [[ "$#" -ge 2 ]] || fail_usage "--source requires a value"
      SOURCE="$2"
      shift 2
      ;;
    --job-id)
      [[ "$#" -ge 2 ]] || fail_usage "--job-id requires a value"
      JOB_ID="$2"
      shift 2
      ;;
    --target)
      [[ "$#" -ge 2 ]] || fail_usage "--target requires a value"
      TARGET="$2"
      shift 2
      ;;
    --confirm)
      [[ "$#" -ge 2 ]] || fail_usage "--confirm requires a value"
      CONFIRM="$2"
      shift 2
      ;;
    *)
      fail_usage "unsupported option: $1"
      ;;
  esac
done

if [[ -n "$SOURCE" && -n "$JOB_ID" ]]; then
  fail_usage "provide exactly one of --source or --job-id"
fi
if [[ -z "$SOURCE" && -z "$JOB_ID" ]]; then
  fail_usage "provide exactly one of --source or --job-id"
fi
case "$TARGET" in
  empty|in_place) ;;
  *) fail_usage "--target must be empty or in_place" ;;
esac
if [[ "$TARGET" == "in_place" && "$CONFIRM" != "RESTORE-IN-PLACE" ]]; then
  fail_usage "in-place restore requires --confirm RESTORE-IN-PLACE"
fi

if [[ -n "$SOURCE" ]]; then
  SOURCE_ARGS=(--source "$SOURCE")
else
  SOURCE_ARGS=(--job-id "$JOB_ID")
fi

# Verify source access, archive integrity, compatibility, and decryption before
# creating an outage. The restore command repeats its own authoritative
# preflight after shutdown because the target can still change between phases.
printf 'Verifying the backup before stopping Omlorix ...\n'
if ! "${COMPOSE[@]}" run --rm --no-deps fastapi \
  python -m app.backups.cli restore-preflight \
    "${SOURCE_ARGS[@]}" --target "$TARGET"; then
  printf 'Backup verification failed; Omlorix was not stopped.\n' >&2
  exit 1
fi

# Ask Compose for the effective service model after compose.sh has loaded
# `.env`. Explicitly naming a profiled service during `up` activates it, so the
# worker services must only be named when redis-enabled is actually active.
if ! ACTIVE_SERVICES="$("${COMPOSE[@]}" config --services)"; then
  printf 'Could not resolve active Compose services; Omlorix was not stopped.\n' >&2
  exit 1
fi

DEDICATED_WORKERS=(
  operations_worker
  generation_worker
  research_worker
  file_processing_worker
  account_lifecycle_worker
  maintenance_worker
  rendering_worker
  media_worker
  connector_worker
  audit_event_worker
  realtime_gateway
)
SERVICES_TO_STOP=(frontend email_worker "${DEDICATED_WORKERS[@]}" automation_scheduler automation_worker fastapi)
SERVICES_TO_START=(frontend email_worker "${DEDICATED_WORKERS[@]}" fastapi)
if printf '%s\n' "$ACTIVE_SERVICES" | grep -Fqx 'automation_scheduler' \
  && printf '%s\n' "$ACTIVE_SERVICES" | grep -Fqx 'automation_worker'; then
  SERVICES_TO_START=(frontend email_worker "${DEDICATED_WORKERS[@]}" automation_scheduler automation_worker fastapi)
fi

# Allocate every host-side recovery dependency before creating an outage.
# Python parses the backend's structured recovery decision after a failure; if
# either it or temporary storage is unavailable, leave the running stack alone.
if ! command -v python3 >/dev/null 2>&1; then
  printf 'python3 is required to coordinate safe restore recovery; Omlorix was not stopped.\n' >&2
  exit 1
fi
RESTORE_DOCKER_EXECUTABLE="${OMLORIX_RESTORE_DOCKER_BIN:-$(command -v docker || true)}"
if [[ -z "$RESTORE_DOCKER_EXECUTABLE" ]]; then
  printf 'docker is required to fence Compose containers before restore; Omlorix was not stopped.\n' >&2
  exit 1
fi
CAPTURE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/omlorix-restore.XXXXXX")"
trap 'rm -r -- "$CAPTURE_DIR"' EXIT
STDOUT_FILE="$CAPTURE_DIR/stdout"
STDERR_FILE="$CAPTURE_DIR/stderr"
CONTAINER_JSON_FILE="$CAPTURE_DIR/containers.json"
CONTAINER_IDS_FILE="$CAPTURE_DIR/container-ids"

restart_application_services() {
  "${COMPOSE[@]}" up -d --no-deps --force-recreate --remove-orphans \
    "${SERVICES_TO_START[@]}"
}

printf 'Stopping Omlorix application services for the offline restore ...\n'
set +e
"${COMPOSE[@]}" stop "${SERVICES_TO_STOP[@]}"
STOP_STATUS=$?
set -e
if [[ "$STOP_STATUS" -ne 0 ]]; then
  printf 'Could not stop every application service; restore was not started. Attempting recovery restart ...\n' >&2
  if ! restart_application_services; then
    printf 'The stop and recovery restart both failed; inspect the Compose services manually.\n' >&2
  fi
  exit "$STOP_STATUS"
fi

# Named Compose services stop gracefully above. Inventory the complete project
# next so active one-off containers and services removed or renamed by the
# current Compose model cannot write into the restore boundary.
if ! "${COMPOSE[@]}" ps --all --orphans --format json > "$CONTAINER_JSON_FILE"; then
  printf 'Could not inventory every Compose container; restore was not started. Attempting recovery restart ...\n' >&2
  restart_application_services || true
  exit 1
fi
if ! python3 - "$CONTAINER_JSON_FILE" > "$CONTAINER_IDS_FILE" <<'PY'
import json
from pathlib import Path
import re
import sys

raw = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
if not raw:
    rows = []
else:
    try:
        parsed = json.loads(raw)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]

infrastructure = {
    "postgres", "redis", "pgbouncer", "minio", "otel-collector", "jaeger",
    "prometheus", "alertmanager", "postgres-exporter", "redis-exporter",
    "node-exporter", "grafana",
}
one_off_label = "com.docker.compose.oneoff"


def container_is_one_off(row):
    labels = row.get("Labels")
    if isinstance(labels, str):
        values = []
        for label in labels.split(","):
            key, separator, value = label.partition("=")
            if separator and key.strip() == one_off_label:
                values.append(value.strip())
        if len(values) != 1:
            raise ValueError("missing or duplicated Compose one-off label")
        value = values[0]
    elif isinstance(labels, dict):
        if one_off_label not in labels:
            raise ValueError("missing Compose one-off label")
        value = labels[one_off_label]
    else:
        raise ValueError("invalid Compose labels")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError("invalid Compose one-off label")


container_ids = set()
for row in rows:
    if not isinstance(row, dict):
        raise ValueError("invalid Compose container inventory row")
    state_value = row.get("State")
    if not isinstance(state_value, str) or not state_value.strip():
        raise ValueError("invalid Compose container state")
    state = state_value.strip().lower()
    if state not in {"running", "restarting", "paused"}:
        continue
    service_value = row.get("Service")
    if not isinstance(service_value, str):
        raise ValueError("invalid active Compose container service")
    service = service_value.strip().lower()
    if service in infrastructure and not container_is_one_off(row):
        continue
    container_id = row.get("ID")
    if not isinstance(container_id, str) or not re.fullmatch(
        r"[a-fA-F0-9]{12,64}", container_id.strip()
    ):
        raise ValueError("invalid active Compose container ID")
    container_ids.add(container_id.strip())

for container_id in sorted(container_ids):
    print(container_id)
PY
then
  printf 'Compose returned an invalid container inventory; restore was not started. Attempting recovery restart ...\n' >&2
  restart_application_services || true
  exit 1
fi

RESTORE_CONTAINER_IDS=()
while IFS= read -r container_id; do
  [[ -n "$container_id" ]] && RESTORE_CONTAINER_IDS+=("$container_id")
done < "$CONTAINER_IDS_FILE"
if [[ "${#RESTORE_CONTAINER_IDS[@]}" -gt 0 ]] \
  && ! "$RESTORE_DOCKER_EXECUTABLE" stop --time 60 "${RESTORE_CONTAINER_IDS[@]}"; then
  printf 'Could not stop every active application container; restore was not started. Attempting recovery restart ...\n' >&2
  restart_application_services || true
  exit 1
fi

RESTORE_ARGS=(
  run --rm --no-deps --remove-orphans fastapi
  python -m app.backups.cli restore --offline
  "${SOURCE_ARGS[@]}"
  --target "$TARGET"
)
if [[ -n "$CONFIRM" ]]; then
  RESTORE_ARGS+=(--confirm "$CONFIRM")
fi

printf 'Restoring the verified backup ...\n'
set +e
"${COMPOSE[@]}" "${RESTORE_ARGS[@]}" \
  > >(tee "$STDOUT_FILE") \
  2> >(tee "$STDERR_FILE" >&2)
RESTORE_STATUS=$?
# Bash does not guarantee that process-substitution consumers have flushed
# their files when the producer exits. Wait before parsing the authoritative
# terminal JSON from stdout so a safe recovery decision cannot be missed
# because tee was still writing.
wait || true
set -e

restore_is_safe_to_restart() {
  python3 - "$STDOUT_FILE" <<'PY'
import json
import pathlib
import sys


def terminal_json_object(raw: str):
    """Return the terminal top-level JSON object emitted on its own line."""
    candidate = raw.strip()
    root_line = candidate.rfind("\n{")
    if root_line >= 0:
        candidate = candidate[root_line + 1 :]
    if not candidate.startswith("{"):
        return None

    decoder = json.JSONDecoder()
    try:
        payload, end = decoder.raw_decode(candidate)
    except json.JSONDecodeError:
        return None
    if candidate[end:].strip():
        return None
    return payload if isinstance(payload, dict) else None


raw = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
payload = terminal_json_object(raw)
recovery = payload.get("recovery") if isinstance(payload, dict) else None
if isinstance(recovery, dict) and recovery.get("safe_to_restart") is True:
    raise SystemExit(0)

raise SystemExit(1)
PY
}

if [[ "$RESTORE_STATUS" -ne 0 ]]; then
  if restore_is_safe_to_restart; then
    printf 'Restore stopped safely; restarting Omlorix with the existing or recovered data.\n' >&2
    if ! restart_application_services; then
      printf 'Restore failed safely, but Omlorix could not be restarted.\n' >&2
      exit 1
    fi
  else
    printf 'Restore failed and safe recovery was not confirmed; Omlorix remains stopped.\n' >&2
    printf 'Review the restore output before restarting application services.\n' >&2
  fi
  exit "$RESTORE_STATUS"
fi

printf 'Backup data restored successfully; restarting Omlorix ...\n'
if ! restart_application_services; then
  printf 'The restore completed, but Omlorix could not be restarted.\n' >&2
  exit 1
fi

printf 'Omlorix restore completed.\n'
