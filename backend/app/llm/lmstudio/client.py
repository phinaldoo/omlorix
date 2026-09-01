"""LM Studio URL normalization, network access, and client helpers.

Implementations live here to keep the historical facade small. Runtime
dependency synchronization intentionally preserves existing monkeypatch and
extension seams exposed by that facade.
"""

from __future__ import annotations

# The extracted code retains a few intentionally assigned diagnostic values.
# ruff: noqa: F821, F841, F541

from app.llm.lmstudio import utils as _compat_source

# Default expressions are evaluated when this module is imported, before the
# compatibility synchronizer can run for an individual function call.
LMSTUDIO_REQUEST_TIMEOUT = _compat_source.LMSTUDIO_REQUEST_TIMEOUT

_COMPAT_DEPENDENCIES = {
    "normalize_lmstudio_base_url": ("HTTPException", "urlparse"),
    "get_lmstudio_openai_base_url": ("normalize_lmstudio_base_url",),
    "normalize_lmstudio_responses_reasoning_effort": (
        "LMSTUDIO_REASONING_EFFORT_ALIASES",
        "LMSTUDIO_RESPONSES_REASONING_EFFORTS",
    ),
    "_lmstudio_auth_headers": (),
    "_resolve_lmstudio_provider": ("HTTPException", "LLMProvider"),
    "_extract_lmstudio_base_url": ("HTTPException", "normalize_lmstudio_base_url"),
    "_get_lmstudio_credentials": (
        "_extract_lmstudio_base_url",
        "_resolve_lmstudio_provider",
        "normalize_lmstudio_base_url",
    ),
    "_lmstudio_request": (
        "HTTPException",
        "_lmstudio_auth_headers",
        "_lmstudio_error_message",
        "json",
        "requests",
    ),
    "_lmstudio_json_object": ("HTTPException",),
    "_assert_lmstudio_url_allowed": (
        "OutboundRequestBlockedError",
        "assert_url_allowed",
    ),
    "_lmstudio_error_message": ("json",),
    "_coerce_lmstudio_model_entry": (),
    "_lmstudio_reasoning_options": (),
    "lmstudio_capabilities_to_list": (),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


# Populate dependencies before definitions so annotations and defaults retain
# exactly the same evaluation behavior as in the original module.
for _dependency_name in (
    "HTTPException",
    "LLMProvider",
    "LMSTUDIO_REASONING_EFFORT_ALIASES",
    "LMSTUDIO_RESPONSES_REASONING_EFFORTS",
    "OutboundRequestBlockedError",
    "_extract_lmstudio_base_url",
    "_lmstudio_auth_headers",
    "_lmstudio_error_message",
    "_resolve_lmstudio_provider",
    "assert_url_allowed",
    "json",
    "normalize_lmstudio_base_url",
    "requests",
    "urlparse",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_normalize_lmstudio_base_url(value: str | None) -> str:
    """Normalize LM Studio base URLs to the native REST root."""
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        raise HTTPException(status_code=422, detail="LM Studio base_url is required")
    parsed = urlparse(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=422, detail="LM Studio base_url must be a valid http(s) URL"
        )
    if parsed.params or parsed.query or parsed.fragment:
        raise HTTPException(
            status_code=422, detail="LM Studio base_url must be a valid http(s) URL"
        )
    # URL schemes are case-insensitive. Canonicalizing the scheme also avoids a
    # false rejection for otherwise valid URLs such as HTTP://localhost:1234.
    normalized = parsed._replace(scheme=parsed.scheme.lower()).geturl().rstrip("/")
    if normalized.endswith("/v1"):
        normalized = normalized[:-3].rstrip("/")
    return normalized


def _impl_get_lmstudio_openai_base_url(value: str | None) -> str:
    """Return the OpenAI-compatible base URL for LM Studio."""
    return f"{normalize_lmstudio_base_url(value)}/v1"


def _impl_normalize_lmstudio_responses_reasoning_effort(value: Any) -> str | None:
    """Translate native LM Studio reasoning modes to Responses effort values.

    LM Studio's native model metadata describes binary reasoning models with
    ``on`` and ``off``. Those values are not valid for ``reasoning.effort`` on
    ``POST /v1/responses``. Mapping them here also protects requests made with
    model settings saved before the provider schema was corrected.
    """
    normalized = str(value or "").strip().lower()
    normalized = LMSTUDIO_REASONING_EFFORT_ALIASES.get(normalized, normalized)
    if normalized in LMSTUDIO_RESPONSES_REASONING_EFFORTS:
        return normalized
    return None


def _impl__lmstudio_auth_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = str(api_key or "").strip()
    if token:
        headers["Authorization"] = (
            token if token.lower().startswith("bearer ") else f"Bearer {token}"
        )
    return headers


def _impl__resolve_lmstudio_provider(
    db,
    provider_id: str,
    *,
    allow_direct_lookup: bool = True,
) -> LLMProvider:
    if not provider_id:
        raise HTTPException(status_code=422, detail="LM Studio provider_id is required")
    if db is None:
        raise HTTPException(
            status_code=500,
            detail="Database session required to resolve LM Studio provider",
        )

    provider = None
    if allow_direct_lookup:
        provider = db.query(LLMProvider).filter_by(id=provider_id).first()
    if not provider:
        from app.llm.provider_groups import resolve_provider_for_request

        provider = resolve_provider_for_request(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="LLM provider not found")
    if provider.provider != "lmstudio":
        raise HTTPException(
            status_code=422, detail="Resolved provider is not an LM Studio provider"
        )
    return provider


def _impl__extract_lmstudio_base_url(provider: LLMProvider) -> str:
    settings = getattr(provider, "settings", None) or {}
    if not isinstance(settings, dict):
        raise HTTPException(status_code=422, detail="Invalid provider.settings format")
    return normalize_lmstudio_base_url(settings.get("base_url"))


def _impl__get_lmstudio_credentials(
    db,
    lmstudio_provider_id: str | None = None,
    *,
    byok_base_url: str | None = None,
    byok_api_key: str | None = None,
) -> tuple[str, str]:
    if lmstudio_provider_id:
        provider = _resolve_lmstudio_provider(db, lmstudio_provider_id)
        return _extract_lmstudio_base_url(provider), str(provider.api_key or "").strip()
    return normalize_lmstudio_base_url(byok_base_url), str(byok_api_key or "").strip()


def _impl__lmstudio_request(
    method: str,
    url: str,
    *,
    api_key: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int | float = LMSTUDIO_REQUEST_TIMEOUT,
    allow_redirects: bool = False,
) -> requests.Response:
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=_lmstudio_auth_headers(api_key),
            json=payload,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )
        response.raise_for_status()
        return response
    except requests.exceptions.HTTPError as exc:
        detail = ""
        response = exc.response
        if response is not None:
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = _lmstudio_error_message(body, json.dumps(body))
                else:
                    detail = json.dumps(body)
            except Exception:
                detail = response.text or ""
        message = detail or str(exc)
        raise HTTPException(
            status_code=response.status_code if response is not None else 424,
            detail=message,
        ) from exc
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=424, detail=f"LM Studio is not reachable: {exc}"
        ) from exc


def _impl__lmstudio_json_object(
    response: requests.Response, *, operation: str
) -> dict[str, Any]:
    """Decode an LM Studio response and require the documented object shape."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=424,
            detail=f"LM Studio returned invalid JSON while {operation}",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=424,
            detail=f"LM Studio returned an invalid response while {operation}",
        )
    return payload


def _impl__assert_lmstudio_url_allowed(db, *, base_url: str, feature: str) -> None:
    """Apply the outbound-request policy to every native LM Studio operation."""
    try:
        assert_url_allowed(db, url=base_url, feature=feature)
    except OutboundRequestBlockedError as exc:
        raise exc.to_http_exception() from exc


def _impl__lmstudio_error_message(payload: dict[str, Any], default: str) -> str:
    """Extract a readable error from native API string or object error fields."""
    raw_error = payload.get("message") or payload.get("error") or payload.get("detail")
    if isinstance(raw_error, dict):
        raw_error = (
            raw_error.get("message") or raw_error.get("type") or json.dumps(raw_error)
        )
    return str(raw_error or default).strip() or default


def _impl__coerce_lmstudio_model_entry(entry: dict[str, Any]) -> dict[str, Any]:
    loaded_instances = entry.get("loaded_instances")
    if not isinstance(loaded_instances, list):
        loaded_instances = []
    capabilities = entry.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = {}
    quantization_info = entry.get("quantization")
    if isinstance(quantization_info, dict):
        quantization = str(quantization_info.get("name") or "").strip() or None
    elif quantization_info in (None, ""):
        quantization = None
        quantization_info = None
    else:
        # Retain compatibility with pre-v1 servers that returned a string.
        quantization = str(quantization_info).strip() or None
    key = str(entry.get("key") or "").strip()
    display_name = str(entry.get("display_name") or key).strip() or key
    return {
        "id": key,
        "key": key,
        "model": key,
        "name": display_name,
        "description": str(entry.get("description") or display_name).strip()
        or display_name,
        "type": entry.get("type"),
        "publisher": entry.get("publisher"),
        "architecture": entry.get("architecture"),
        "quantization": quantization,
        "quantization_info": quantization_info,
        "max_context_length": entry.get("max_context_length"),
        "size_bytes": entry.get("size_bytes"),
        "params_string": entry.get("params_string"),
        "format": entry.get("format"),
        "path": entry.get("path"),
        "compatibility_type": entry.get("compatibility_type"),
        "capabilities": capabilities,
        "loaded_instances": loaded_instances,
        "variants": entry.get("variants")
        if isinstance(entry.get("variants"), list)
        else [],
        "selected_variant": entry.get("selected_variant"),
        "raw": entry,
    }


def _impl__lmstudio_reasoning_options(entry: dict[str, Any] | None) -> list[str]:
    caps = (entry or {}).get("capabilities") if isinstance(entry, dict) else {}
    reasoning = caps.get("reasoning") if isinstance(caps, dict) else {}
    options = reasoning.get("allowed_options") if isinstance(reasoning, dict) else []
    result: list[str] = []
    seen: set[str] = set()
    for option in options or []:
        value = str(option or "").strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _impl_lmstudio_capabilities_to_list(entry: dict[str, Any] | None) -> list[str]:
    caps = (entry or {}).get("capabilities") if isinstance(entry, dict) else {}
    result = ["completion"]
    if isinstance(caps, dict):
        if caps.get("vision"):
            result.append("vision")
        if caps.get("reasoning"):
            result.append("thinking")
    # LM Studio provides a default tool-use format for every LLM, while the
    # trained_for_tool_use flag only identifies models with native templates.
    if str((entry or {}).get("type") or "").strip().lower() == "llm":
        result.append("tools")
    return result
