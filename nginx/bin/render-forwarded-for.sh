#!/usr/bin/env sh
set -eu

# Render the one security-sensitive nginx choice that differs between a public
# edge and a frontend hidden behind a trusted upstream proxy. Keeping this in a
# shared helper makes source-generated and prebuilt frontend configurations use
# exactly the same fail-closed default.
INPUT_PATH="${1:?input nginx template path is required}"
OUTPUT_PATH="${2:?output nginx config path is required}"
TRUST_PROXY_HEADERS_VALUE="${3:-${FRONTEND_TRUST_PROXY_HEADERS:-false}}"
LAUNCHER_PROXY_SECRET="${OMLORIX_LAUNCHER_PROXY_SECRET:-}"
TRUSTED_EXTERNAL_UPSTREAMS_RAW="${FRONTEND_TRUSTED_UPSTREAMS:-}"

case "$(printf '%s' "${TRUST_PROXY_HEADERS_VALUE}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    # The launcher secret is deliberately restricted to fixed-length hex. That
    # keeps nginx rendering unambiguous and avoids shell/config injection when a
    # damaged environment file is loaded.
    case "${LAUNCHER_PROXY_SECRET}" in
      *[!0-9A-Fa-f]*|'')
        echo "[render-forwarded-for] Launcher proxy authentication secret is missing or invalid." >&2
        exit 1
        ;;
    esac
    if [ "${#LAUNCHER_PROXY_SECRET}" -ne 64 ]; then
      echo "[render-forwarded-for] Launcher proxy authentication secret must contain 64 hexadecimal characters." >&2
      exit 1
    fi
    LAUNCHER_PROXY_AUTH_VALUE=1
    ;;
  *)
    # An impossible placeholder keeps the map syntactically valid while direct
    # ingress remains fail-closed even if a client invents forwarding headers.
    LAUNCHER_PROXY_SECRET='launcher-proxy-disabled'
    LAUNCHER_PROXY_AUTH_VALUE=0
    # A stale allowlist must not independently re-enable forwarding when the
    # explicit frontend trust switch is off.
    TRUSTED_EXTERNAL_UPSTREAMS_RAW=''
    ;;
esac
X_FORWARDED_FOR_VALUE='$omlorix_client_ip'

TRUSTED_EXTERNAL_UPSTREAMS='# No external trusted upstreams configured.'
if [ -n "${TRUSTED_EXTERNAL_UPSTREAMS_RAW}" ]; then
  TRUSTED_EXTERNAL_UPSTREAMS_RAW="$(printf '%s' "${TRUSTED_EXTERNAL_UPSTREAMS_RAW}" | tr -d ' ')"
  case "${TRUSTED_EXTERNAL_UPSTREAMS_RAW}" in
    *[!0-9A-Fa-f:.,/]*)
      echo "[render-forwarded-for] External trusted upstreams must contain only IP addresses or CIDRs." >&2
      exit 1
      ;;
    ,*|*,|*,,*)
      echo "[render-forwarded-for] External trusted upstreams contains an empty entry." >&2
      exit 1
      ;;
  esac
  TRUSTED_EXTERNAL_UPSTREAMS=''
  OLD_IFS="${IFS}"
  IFS=','
  for upstream in ${TRUSTED_EXTERNAL_UPSTREAMS_RAW}; do
    upstream="$(printf '%s' "${upstream}" | tr -d ' ')"
    if [ -z "${upstream}" ]; then
      echo "[render-forwarded-for] External trusted upstreams contains an empty entry." >&2
      exit 1
    fi
    case "${upstream}" in
      0.0.0.0/0|::/0)
        echo "[render-forwarded-for] External trusted upstreams must not trust the entire Internet." >&2
        exit 1
        ;;
    esac
    TRUSTED_EXTERNAL_UPSTREAMS="${TRUSTED_EXTERNAL_UPSTREAMS}${upstream} 1; "
  done
  IFS="${OLD_IFS}"
fi

OUTPUT_DIR="$(dirname "${OUTPUT_PATH}")"
mkdir -p "${OUTPUT_DIR}"
TMP_OUTPUT="$(mktemp "${OUTPUT_PATH}.tmp.XXXXXX")"
cleanup() {
  rm -f "${TMP_OUTPUT}"
}
trap cleanup EXIT INT TERM

sed \
  -e "s|__X_FORWARDED_FOR_VALUE__|${X_FORWARDED_FOR_VALUE}|g" \
  -e "s|__LAUNCHER_PROXY_SECRET__|${LAUNCHER_PROXY_SECRET}|g" \
  -e "s|__LAUNCHER_PROXY_AUTH_VALUE__|${LAUNCHER_PROXY_AUTH_VALUE}|g" \
  -e "s|__TRUSTED_EXTERNAL_UPSTREAMS__|${TRUSTED_EXTERNAL_UPSTREAMS}|g" \
  "${INPUT_PATH}" > "${TMP_OUTPUT}"

# A leftover token would make nginx fail later with a less actionable message.
# Detect it at render time so image startup and source generation fail clearly.
if grep -Eq '__X_FORWARDED_FOR_VALUE__|__LAUNCHER_PROXY_(SECRET|AUTH_VALUE)__|__TRUSTED_EXTERNAL_UPSTREAMS__' "${TMP_OUTPUT}"; then
  echo "[render-forwarded-for] Failed to render all trusted-ingress placeholders." >&2
  exit 1
fi

mv "${TMP_OUTPUT}" "${OUTPUT_PATH}"
trap - EXIT INT TERM
