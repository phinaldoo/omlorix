from __future__ import annotations

from ast import literal_eval
import json
import re
from typing import Any

_FALLBACK_MAX_LENGTH = 200
TRANSCRIPTION_NOT_ENABLED_ERROR_CODE = "transcription_not_enabled"
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_LONG_HEX_RE = re.compile(r"\b[a-f0-9]{32,}\b", re.IGNORECASE)
_LONG_BASE64_RE = re.compile(r"\b(?:[A-Za-z0-9+/]{32,}={0,2}|[A-Za-z0-9_-]{32,})\b")
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._-]+\b", re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization)\b\s*[:=]\s*([\"']?)([^\"'\s,}]+)\2"
)


def _clean_string(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())


def _parse_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw.startswith("{") or not raw.endswith("}"):
        return None

    for parser in (json.loads, literal_eval):
        try:
            parsed = parser(raw)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_message_and_status(payload: Any) -> tuple[str, str]:
    if isinstance(payload, str):
        return _clean_string(payload), ""
    if not isinstance(payload, dict):
        return "", ""

    detail = payload.get("detail")
    candidates = [detail, payload]

    for candidate in candidates:
        if isinstance(candidate, str):
            message = _clean_string(candidate)
            if message:
                return message, ""
            continue
        if not isinstance(candidate, dict):
            continue

        status = _clean_string(candidate.get("status") or candidate.get("code"))
        message = _clean_string(
            candidate.get("message")
            or candidate.get("detail")
            or candidate.get("error")
        )
        if message or status:
            return message, status

    return "", ""


def sanitize_fallback(raw_text: Any, max_length: int = _FALLBACK_MAX_LENGTH) -> str:
    text = _clean_string(str(raw_text))
    if not text:
        return ""

    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?<redacted>", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _EMAIL_RE.sub("<redacted-email>", text)
    text = _IP_RE.sub("<redacted-ip>", text)
    text = _LONG_HEX_RE.sub("<redacted>", text)
    text = _LONG_BASE64_RE.sub("<redacted>", text)

    if max_length > 3 and len(text) > max_length:
        return text[: max_length - 3].rstrip() + "..."
    return text[:max_length]


def extract_transcription_error_context(exc: Exception) -> dict[str, Any]:
    raw_text = _clean_string(str(exc))
    provider_status_code = getattr(exc, "status_code", None)
    if not isinstance(provider_status_code, int):
        status_match = re.search(r"status_code:\s*(\d{3})", raw_text)
        provider_status_code = int(status_match.group(1)) if status_match else None

    message = ""
    status = ""

    body_candidates: list[Any] = [
        getattr(exc, "body", None),
        getattr(exc, "detail", None),
    ]

    response = getattr(exc, "response", None)
    if response is not None:
        response_json = getattr(response, "json", None)
        if callable(response_json):
            try:
                body_candidates.append(response_json())
            except Exception:
                pass

    for candidate in body_candidates:
        parsed = _parse_mapping(candidate)
        extracted_message, extracted_status = _extract_message_and_status(parsed or candidate)
        if extracted_message and not message:
            message = extracted_message
        if extracted_status and not status:
            status = extracted_status
        if message and status:
            break

    if not message or not status:
        status_match = re.search(r"['\"]status['\"]\s*:\s*['\"]([^'\"]+)['\"]", raw_text)
        message_match = re.search(r"['\"]message['\"]\s*:\s*['\"]([^'\"]+)['\"]", raw_text)
        if not status and status_match:
            status = _clean_string(status_match.group(1))
        if not message and message_match:
            message = _clean_string(message_match.group(1))

    if not message and raw_text and "headers:" not in raw_text.lower():
        message = sanitize_fallback(raw_text)

    return {
        "message": message,
        "status": status,
        "status_code": provider_status_code,
    }


def build_transcription_error_detail(
    exc: Exception,
    *,
    is_admin: bool,
    fallback_message: str,
) -> dict[str, Any]:
    if not is_admin:
        return {"message": fallback_message}

    context = extract_transcription_error_context(exc)
    detail: dict[str, Any] = {
        "message": context.get("message") or fallback_message,
    }

    if context.get("status"):
        detail["status"] = context["status"]
    if isinstance(context.get("status_code"), int):
        detail["status_code"] = context["status_code"]

    return detail
