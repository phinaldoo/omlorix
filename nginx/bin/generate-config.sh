#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NGINX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${NGINX_DIR}/.." && pwd)"
DEFAULT_ENV_FILE="${PROJECT_ROOT}/.env"

usage() {
  cat <<'EOF'
Usage: ./nginx/bin/generate-config.sh [ENV_FILE]

Generates /nginx/default.conf from the HTTP frontend template.
If ENV_FILE is omitted, the script loads variables from the project root .env file.
EOF
}

arg="${1-}"
if [ "${arg}" = "-h" ] || [ "${arg}" = "--help" ]; then
  usage
  exit 0
fi

ENV_FILE="${DEFAULT_ENV_FILE}"
if [ -n "${arg}" ]; then
  case "${arg}" in
    /*)
      ENV_FILE="${arg}"
      ;;
    *)
      ENV_FILE="${PROJECT_ROOT}/${arg}"
      ;;
  esac
fi

if [ -f "${ENV_FILE}" ]; then
  TMP_ENV_FILE=$(mktemp)
  cleanup_tmp_env() {
    rm -f "${TMP_ENV_FILE}"
  }
  trap cleanup_tmp_env EXIT INT TERM
  sed 's/\r$//' "${ENV_FILE}" > "${TMP_ENV_FILE}"
  # shellcheck disable=SC1090
  set -a
  . "${TMP_ENV_FILE}"
  set +a
  cleanup_tmp_env
  trap - EXIT INT TERM
elif [ -n "${arg}" ]; then
  echo "[generate-config] Provided env file '${ENV_FILE}' not found." >&2
  exit 1
fi

OUTPUT_PATH="${NGINX_DIR}/default.conf"
HTTP_TEMPLATE="${NGINX_DIR}/default.http.conf.template/default.conf"
FORWARDED_FOR_RENDERER="${NGINX_DIR}/bin/render-forwarded-for.sh"

if [ ! -f "${HTTP_TEMPLATE}" ]; then
  echo "[generate-config] HTTP template missing at ${HTTP_TEMPLATE}" >&2
  exit 1
fi

if [ ! -x "${FORWARDED_FOR_RENDERER}" ]; then
  echo "[generate-config] Forwarded-header renderer missing or not executable at ${FORWARDED_FOR_RENDERER}" >&2
  exit 1
fi

"${FORWARDED_FOR_RENDERER}" "${HTTP_TEMPLATE}" "${OUTPUT_PATH}" "${FRONTEND_TRUST_PROXY_HEADERS:-false}"
echo "[generate-config] Generated HTTP nginx config at ${OUTPUT_PATH}"
