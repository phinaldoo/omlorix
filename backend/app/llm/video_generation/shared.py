import base64
import time
from typing import Any

import requests


VIDEO_URL_KEYS = {
    "video_url",
    "video_uri",
    "download_url",
    "download_uri",
    "unsigned_url",
    "unsigned_urls",
    "uri",
    "url",
    "file_url",
}

VIDEO_BASE64_KEYS = {
    "b64_json",
    "video_b64",
    "base64",
    "data",
}

VIDEO_STATUS_DONE = {"succeeded", "completed", "done", "success"}
VIDEO_STATUS_FAILED = {"failed", "error", "cancelled", "canceled", "expired"}
VIDEO_STATUS_PENDING = {"queued", "pending", "running", "processing", "in_progress", "created"}


def to_plain_data(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): to_plain_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [to_plain_data(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return to_plain_data(model_dump())
        except Exception:
            pass

    as_dict = getattr(value, "to_dict", None)
    if callable(as_dict):
        try:
            return to_plain_data(as_dict())
        except Exception:
            pass

    try:
        raw_dict = vars(value)
    except Exception:
        raw_dict = None
    if isinstance(raw_dict, dict):
        return {
            str(key): to_plain_data(val)
            for key, val in raw_dict.items()
            if not str(key).startswith("_")
        }
    return str(value)


def _get_nested_value(payload: Any, keys: list[str]) -> Any:
    node = payload
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def extract_status(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    candidates = [
        payload.get("status"),
        payload.get("state"),
        payload.get("job_status"),
        _get_nested_value(payload, ["operation", "status"]),
        _get_nested_value(payload, ["metadata", "status"]),
    ]
    done = payload.get("done")
    if isinstance(done, bool):
        if done:
            return "done"
        if not any(candidates):
            return "running"
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip().lower()
        if text:
            return text
    return ""


def extract_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("error", "message", "detail", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = extract_error_message(value)
            if nested:
                return nested
    return None


def _parse_data_uri_video(value: str) -> tuple[bytes, str] | None:
    if not isinstance(value, str):
        return None
    if not value.startswith("data:video/"):
        return None
    header, _, data = value.partition(",")
    if not data:
        return None
    mime = header[5:].split(";")[0] if header.startswith("data:") else "video/mp4"
    try:
        return base64.b64decode(data), mime
    except Exception:
        return None


def collect_video_candidates(payload: Any) -> tuple[list[str], list[tuple[bytes, str]]]:
    urls: list[str] = []
    inline_videos: list[tuple[bytes, str]] = []

    def _visit(node: Any, key_hint: str | None = None) -> None:
        if node is None:
            return
        if isinstance(node, dict):
            mime_hint = str(node.get("mime_type") or node.get("mimeType") or "").lower()
            data_hint = node.get("data")
            if mime_hint.startswith("video/") and isinstance(data_hint, str):
                parsed_inline = _parse_data_uri_video(f"data:{mime_hint};base64,{data_hint}")
                if parsed_inline:
                    inline_videos.append(parsed_inline)
            for key, value in node.items():
                key_lower = str(key).lower()
                if isinstance(value, str):
                    stripped = value.strip()
                    parsed_inline = _parse_data_uri_video(stripped)
                    if parsed_inline:
                        inline_videos.append(parsed_inline)
                    elif key_lower in VIDEO_URL_KEYS and stripped.lower().startswith(("http://", "https://")):
                        urls.append(stripped)
                    elif key_lower in VIDEO_BASE64_KEYS and (key_hint or "").lower().startswith("video"):
                        try:
                            inline_videos.append((base64.b64decode(stripped), "video/mp4"))
                        except Exception:
                            pass
                _visit(value, key_lower)
            return
        if isinstance(node, list):
            for item in node:
                _visit(item, key_hint)
            return
        if isinstance(node, str):
            stripped = node.strip()
            parsed_inline = _parse_data_uri_video(stripped)
            if parsed_inline:
                inline_videos.append(parsed_inline)
            elif (key_hint or "").lower() in VIDEO_URL_KEYS and stripped.lower().startswith(("http://", "https://")):
                urls.append(stripped)

    _visit(payload)
    deduped_urls = list(dict.fromkeys(urls))
    return deduped_urls, inline_videos


def extract_job_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload.get("id"),
        payload.get("request_id"),
        payload.get("job_id"),
        payload.get("video_id"),
        payload.get("generation_id"),
        payload.get("name"),
    ]
    operation_name = _get_nested_value(payload, ["operation", "name"])
    if operation_name:
        candidates.append(operation_name)
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def request_with_retries(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_payload: dict[str, Any] | None = None,
    data_payload: dict[str, Any] | None = None,
    files_payload: Any | None = None,
    timeout_seconds: int = 60,
    max_retries: int = 0,
) -> requests.Response:
    last_error: Exception | None = None
    retries = max(0, int(max_retries or 0))
    attempts = retries + 1

    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=json_payload,
                data=data_payload,
                files=files_payload,
                timeout=timeout_seconds,
            )
            if response.status_code >= 500 and attempt < attempts:
                time.sleep(min(2.5 * attempt, 8.0))
                continue
            if response.status_code >= 400:
                detail = response.text.strip()[:1500]
                raise RuntimeError(
                    f"Provider request failed ({response.status_code}) for {url}: {detail}"
                )
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(min(2.5 * attempt, 8.0))
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(min(2.5 * attempt, 8.0))

    raise RuntimeError(f"Provider request failed for {url}: {last_error}")


def wait_for_job_result(
    fetch_status_fn,
    *,
    timeout_seconds: int,
    poll_interval_seconds: int,
    provider_name: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    poll_interval = max(1, int(poll_interval_seconds))
    last_payload: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        payload_raw = fetch_status_fn()
        payload = to_plain_data(payload_raw)
        last_payload = payload if isinstance(payload, dict) else {}
        status = extract_status(last_payload)

        if status in VIDEO_STATUS_DONE:
            return last_payload
        if status in VIDEO_STATUS_FAILED:
            error_detail = extract_error_message(last_payload) or "The provider reported a failed video job."
            raise RuntimeError(error_detail)
        if status == "done":
            return last_payload
        if not status and isinstance(last_payload, dict):
            done_value = last_payload.get("done")
            if done_value is True:
                return last_payload

        time.sleep(poll_interval)

    raise TimeoutError(
        f"{provider_name} video generation timed out after {int(timeout_seconds)} seconds."
    )
