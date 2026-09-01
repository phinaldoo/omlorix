import json
from typing import Any, NoReturn

from fastapi import HTTPException
import httpx
import requests


def raise_provider_http_error(
    exc: requests.HTTPError | httpx.HTTPStatusError,
    *,
    provider_name: str,
    operation: str,
) -> NoReturn:
    response = getattr(exc, "response", None)
    if response is None:
        raise HTTPException(
            status_code=502,
            detail=f"{provider_name} {operation} failed: {exc}",
        ) from exc

    detail = extract_provider_error_message(response) or str(exc)
    status_code = getattr(response, "status_code", None) or 502
    raise HTTPException(
        status_code=status_code,
        detail=f"{provider_name} {operation} failed: {detail}",
    ) from exc


def extract_provider_error_message(response: Any) -> str | None:
    payload: Any = None
    try:
        payload = response.json()
    except (ValueError, TypeError, json.JSONDecodeError):
        payload = None

    detail = _extract_message_from_payload(payload)
    if detail:
        return detail

    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()[:300]

    reason = getattr(response, "reason", None) or getattr(response, "reason_phrase", None)
    if isinstance(reason, str) and reason.strip():
        return reason.strip()

    return None


def _extract_message_from_payload(payload: Any) -> str | None:
    if isinstance(payload, str) and payload.strip():
        return payload.strip()

    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            detail = _extract_message_from_payload(value)
            if detail:
                return detail
        return None

    if isinstance(payload, list):
        for item in payload:
            detail = _extract_message_from_payload(item)
            if detail:
                return detail
        return None

    return None
