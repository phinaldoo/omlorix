from __future__ import annotations

from typing import Any

import requests


DEEPGRAM_BASE_URL = "https://api.deepgram.com"
DEEPGRAM_DEFAULT_TIMEOUT_SECONDS = 120
DEEPGRAM_DEFAULT_LOOKUP_TIMEOUT_SECONDS = 20
DEEPGRAM_PRIVACY_OPT_OUT_QUERY_PARAM = "mip_opt_out"
DEEPGRAM_PRIVACY_OPT_OUT_QUERY_VALUE = "true"


def normalize_deepgram_timeout(value: Any, default: int = DEEPGRAM_DEFAULT_TIMEOUT_SECONDS) -> int:
    try:
        timeout = int(value)
    except Exception:
        timeout = default
    return max(1, timeout)


def deepgram_auth_headers(
    api_key: str,
    *,
    accept: str | None = None,
    content_type: str | None = None,
) -> dict[str, str]:
    token = str(api_key or "").strip()
    if not token:
        raise ValueError("Deepgram api_key is required")

    headers = {
        "Authorization": f"Token {token}",
    }
    if accept:
        headers["Accept"] = accept
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def extract_deepgram_error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return response.text.strip() or f"HTTP {response.status_code}"

    if isinstance(payload, dict):
        for key in ("err_msg", "error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return response.text.strip() or f"HTTP {response.status_code}"


def list_deepgram_models_payload(
    api_key: str,
    *,
    base_url: str | None = None,
    timeout: int = DEEPGRAM_DEFAULT_LOOKUP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    endpoint = f"{(base_url or DEEPGRAM_BASE_URL).rstrip('/')}/v1/models"

    try:
        response = requests.get(
            endpoint,
            headers=deepgram_auth_headers(api_key, accept="application/json"),
            timeout=normalize_deepgram_timeout(timeout, DEEPGRAM_DEFAULT_LOOKUP_TIMEOUT_SECONDS),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to reach Deepgram API: {exc}") from exc

    if not response.ok:
        detail = extract_deepgram_error_detail(response)
        raise RuntimeError(f"Failed to list Deepgram models: {detail}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Deepgram models response was not a JSON object")
    return payload
