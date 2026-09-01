import hashlib
import json
import logging
import math
import threading
import time
from typing import Any
import re
import requests
from fastapi import HTTPException

from app.groups.init import get_group_page_settings, get_user, get_user_group_setting_value
from app.llm.models import get_model
from app.llm.utils import list_user_models

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
# Tool categories mapping
TOOL_CATEGORIES: dict[str, list[str]] = {
    "websearch": ["web_search"],
    "todo_management": ["todos"],
    "notes_management": ["notes"],
    "automations_management": ["automations"],
    "skills_management": ["skills"],
    "memory_management": ["memories"],
    "information": ["weather"],
    "education": ["quiz", "flashcards"],
    "media_generation": ["image_generation", "video_generation", "audio_generation", "music_generation"],
    "presentations": ["slide_presentation"],
    "research": ["deep_research"],
    "canvas": ["canvas"],
    "code_execution": ["code_execution"],
}

# Reverse mapping: tool -> category
TOOL_TO_CATEGORY: dict[str, str] = {}
for cat, tools in TOOL_CATEGORIES.items():
    for tool in tools:
        TOOL_TO_CATEGORY[tool] = cat


def _compute_tool_categories(tool_list: list[str]) -> dict:
    """Compute tool category statuses and uncategorized tools.
    
    Returns:
        {
            "categories": {
                "websearch": "full" | "partial" | "none",
                "todo_management": "full" | "partial" | "none",
                "notes_management": "full" | "partial" | "none",
            },
            "uncategorized": ["tool1", "tool2", ...]
        }
    """
    if not tool_list:
        return {
            "categories": {cat: "none" for cat in TOOL_CATEGORIES},
            "uncategorized": [],
        }
    
    tool_set = set(tool_list)
    categories_status = {}
    
    for cat, cat_tools in TOOL_CATEGORIES.items():
        cat_tool_set = set(cat_tools)
        present = tool_set & cat_tool_set
        
        if len(present) == 0:
            categories_status[cat] = "none"
        elif len(present) == len(cat_tool_set):
            categories_status[cat] = "full"
        else:
            categories_status[cat] = "partial"
    
    # Find uncategorized tools
    categorized = set(TOOL_TO_CATEGORY.keys())
    uncategorized = [t for t in tool_list if t not in categorized]
    
    return {
        "categories": categories_status,
        "uncategorized": uncategorized,
    }


def _coerce_to_list(value):
    """Coerce a value to a list."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return list(parsed)
        except ValueError:
            return [value]
        return [value]
    return []


def _extract_capabilities(raw_value):
    """Extract capabilities from a raw value."""
    if isinstance(raw_value, list):
        return list(raw_value)
    if isinstance(raw_value, (tuple, set)):
        return list(raw_value)
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                return list(parsed)
            if isinstance(parsed, str):
                return [parsed]
        except ValueError:
            return [raw_value]
    return []


def _coerce_tools(value) -> list | dict | str | None:
    """Coerce tools to appropriate type based on input.
    
    Args:
        value: Input value to coerce. Can be a list, tuple, set, dict, or string.
    
    Returns:
        - list: When input is a list, tuple, or set.
        - dict: When input is a mapping/dict.
        - str: When input is a string that cannot be parsed as JSON.
        - None: When no tools are provided or input is empty.
    """
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, dict):
        return {k: v for k, v in value.items()}
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (list, dict)):
                return parsed
        except ValueError:
            return value
        return value
    return None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# Artificial Analysis exposes a deliberately smaller response from the Free
# endpoint. Keep these values stable because they are persisted in group
# settings and are also returned to the frontend as a rendering profile.
LEADERBOARD_DATA_LEVEL_FREE = "free"
LEADERBOARD_DATA_LEVEL_FULL = "full"
LEADERBOARD_DATA_LEVELS = {
    LEADERBOARD_DATA_LEVEL_FREE,
    LEADERBOARD_DATA_LEVEL_FULL,
}

LEADERBOARD_API_URLS = {
    LEADERBOARD_DATA_LEVEL_FREE: (
        "https://artificialanalysis.ai/api/v2/language/models/free"
    ),
    LEADERBOARD_DATA_LEVEL_FULL: (
        "https://artificialanalysis.ai/api/v2/language/models"
    ),
}

# A defensive upper bound prevents an invalid or malicious pagination envelope
# from keeping a request worker in an unbounded loop.
LEADERBOARD_MAX_API_PAGES = 100

# Data retention policy:
#   - Leaderboard data is fetched from the Artificial Analysis API and held
#     in an in-memory cache for up to the configured per-level TTL.
#   - Cache entries are partitioned by a non-reversible hash of the caller's
#     Artificial Analysis API key and by the configured data level.
#   - The cache is automatically cleared when models, providers, or leaderboard
#     group settings are updated to ensure data consistency.
#   - Cache entries contain no personally identifiable information (PII); only
#     public model names, benchmark scores, and provider metadata are stored.
#   - Data is discarded on process restart (volatile, non-persistent storage).
#   - Maximum retention period: 6 hours for Free data and 1 hour for Full data.
#     Cache invalidation is triggered on relevant configuration changes.
# ---------------------------------------------------------------------------
LEADERBOARD_CACHE_TTLS = {
    # The Free API is limited to 100 requests per day and currently spans more
    # than one page. A longer TTL keeps multi-worker deployments comfortably
    # inside that quota while retaining sufficiently fresh benchmark data.
    LEADERBOARD_DATA_LEVEL_FREE: 6 * 3600,
    LEADERBOARD_DATA_LEVEL_FULL: 3600,
}
# Avoid retrying a temporarily unavailable provider on every request while
# still keeping the retry interval much shorter than either normal cache TTL.
LEADERBOARD_STALE_RETRY_COOLDOWN = 60
_MODEL_CACHE: dict[str, dict[str, Any]] = {}
_MODEL_CACHE_LOCK = threading.Lock()
# Cold-cache callers cannot be served stale data while the first provider
# request is running. The condition lets them share that in-flight refresh
# instead of independently consuming the provider's limited request quota.
_MODEL_CACHE_CONDITION = threading.Condition(_MODEL_CACHE_LOCK)


def normalize_leaderboard_data_level(value: Any) -> str:
    """Return a supported persisted data level, defaulting safely to Free."""
    normalized = str(value or "").strip().lower()
    if normalized in LEADERBOARD_DATA_LEVELS:
        return normalized
    return LEADERBOARD_DATA_LEVEL_FREE


def _leaderboard_cache_key(api_key: str, data_level: str) -> str:
    """Build a non-reversible cache key for one key/data-level combination."""
    cache_material = f"{data_level}\0{api_key.strip()}"
    return hashlib.sha256(cache_material.encode("utf-8")).hexdigest()


def clear_llm_model_leaderboard_cache(api_key: str | None = None) -> None:
    """Clear all cached data levels associated with an API key."""
    with _MODEL_CACHE_CONDITION:
        if api_key is None:
            _MODEL_CACHE.clear()
            _MODEL_CACHE_CONDITION.notify_all()
            return

        # A group can switch between Free and Full while keeping the same key.
        # Removing both variants avoids leaving stale tier-specific data behind.
        for data_level in LEADERBOARD_DATA_LEVELS:
            _MODEL_CACHE.pop(_leaderboard_cache_key(api_key, data_level), None)
        _MODEL_CACHE_CONDITION.notify_all()


def _provider_error(error_type: str) -> HTTPException:
    """Build a stable, frontend-translatable provider error response."""
    return HTTPException(status_code=424, detail={"type": error_type})


def _raise_for_provider_status(status_code: int, data_level: str) -> None:
    """Translate Artificial Analysis HTTP statuses into stable error types."""
    if status_code == 401:
        raise _provider_error("leaderboard_provider_api_key_invalid")
    if status_code == 403 and data_level == LEADERBOARD_DATA_LEVEL_FULL:
        raise _provider_error("leaderboard_provider_full_tier_required")
    if status_code == 429:
        raise _provider_error("leaderboard_provider_rate_limited")
    if status_code >= 500:
        raise _provider_error("leaderboard_provider_unavailable")
    raise _provider_error("leaderboard_provider_unexpected_response")


def _parse_provider_page(
    payload: Any,
    *,
    requested_page: int,
    data_level: str,
) -> tuple[list[dict[str, Any]], str, float, bool]:
    """Validate one list page and return its data plus pagination metadata.

    Artificial Analysis now uses one consistent envelope for list endpoints.
    Validating the envelope here prevents a partial or malformed response from
    silently replacing a previously complete cache entry.
    """
    if not isinstance(payload, dict):
        raise _provider_error("leaderboard_provider_invalid_data")

    data = payload.get("data")
    pagination = payload.get("pagination")
    provider_tier = payload.get("tier")
    index_version = payload.get("intelligence_index_version")

    malformed_model = any(
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not isinstance(item.get("name"), str)
        or not isinstance(item.get("slug"), str)
        or not isinstance(item.get("evaluations"), dict)
        for item in data
    ) if isinstance(data, list) else True

    if (
        not isinstance(data, list)
        or malformed_model
        or not isinstance(pagination, dict)
        or provider_tier not in {"free", "pro", "commercial"}
        or isinstance(index_version, bool)
        or not isinstance(index_version, (int, float))
    ):
        raise _provider_error("leaderboard_provider_invalid_data")

    page = pagination.get("page")
    has_more = pagination.get("has_more")
    if page != requested_page or not isinstance(has_more, bool):
        raise _provider_error("leaderboard_provider_invalid_data")

    # A Full request should never succeed with a Free response shape. Treating
    # this as a configuration error gives administrators a useful next action.
    if (
        data_level == LEADERBOARD_DATA_LEVEL_FULL
        and provider_tier not in {"pro", "commercial"}
    ):
        raise _provider_error("leaderboard_provider_full_tier_required")

    return data, provider_tier, float(index_version), has_more


def _fetch_all_leaderboard_pages(api_key: str, data_level: str) -> dict[str, Any]:
    """Fetch and combine every page from the selected Artificial Analysis API."""
    url = LEADERBOARD_API_URLS[data_level]
    headers = {"x-api-key": api_key}
    combined_data: list[dict[str, Any]] = []
    provider_tier: str | None = None
    index_version: float | None = None

    for page in range(1, LEADERBOARD_MAX_API_PAGES + 1):
        logger.info(
            "Fetching Artificial Analysis leaderboard page %s (%s data)",
            page,
            data_level,
        )
        response = requests.get(
            url,
            headers=headers,
            params={"page": page},
            timeout=(5, 15),
        )

        if response.status_code != 200:
            logger.warning(
                "Artificial Analysis API returned status %s for %s data",
                response.status_code,
                data_level,
            )
            _raise_for_provider_status(response.status_code, data_level)

        try:
            payload = response.json()
        except ValueError as exc:
            raise _provider_error("leaderboard_provider_invalid_data") from exc

        page_data, page_tier, page_version, has_more = _parse_provider_page(
            payload,
            requested_page=page,
            data_level=data_level,
        )

        # Tier and benchmark-version changes in the middle of pagination would
        # produce an internally inconsistent leaderboard, so reject them.
        if provider_tier is not None and provider_tier != page_tier:
            raise _provider_error("leaderboard_provider_invalid_data")
        if index_version is not None and index_version != page_version:
            raise _provider_error("leaderboard_provider_invalid_data")

        provider_tier = page_tier
        index_version = page_version
        combined_data.extend(page_data)

        if not has_more:
            return {
                "data": combined_data,
                "data_level": data_level,
                "provider_tier": provider_tier,
                "intelligence_index_version": index_version,
            }

    raise _provider_error("leaderboard_provider_invalid_data")


def _finish_failed_refresh(
    cache_key: str,
    stale_result: dict[str, Any] | None,
    exc: HTTPException,
) -> dict[str, Any]:
    """Release the refresh marker and use stale data for transient failures."""
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error_type = detail.get("type")
    serve_stale = stale_result is not None and error_type in {
        "leaderboard_provider_rate_limited",
        "leaderboard_provider_unavailable",
    }

    with _MODEL_CACHE_CONDITION:
        if cache_key in _MODEL_CACHE:
            cache_entry = _MODEL_CACHE[cache_key]
            cache_entry["refreshing"] = False
            if serve_stale:
                cache_entry["retry_after"] = (
                    time.time() + LEADERBOARD_STALE_RETRY_COOLDOWN
                )
            elif stale_result is None and isinstance(error_type, str):
                # Cold-cache followers should observe the owner's translated
                # failure instead of serially repeating the same failed fetch.
                cache_entry["last_error_type"] = error_type
                cache_entry["retry_after"] = (
                    time.time() + LEADERBOARD_STALE_RETRY_COOLDOWN
                )
        _MODEL_CACHE_CONDITION.notify_all()

    if serve_stale:
        logger.warning(
            "Returning stale Artificial Analysis data after transient error: %s",
            error_type,
        )
        return stale_result
    raise exc


def get_leaderboard_data(api_key: str, data_level: str = LEADERBOARD_DATA_LEVEL_FREE):
    """Get a complete, tier-aware leaderboard response with per-level caching."""
    normalized_level = normalize_leaderboard_data_level(data_level)
    cache_key = _leaderboard_cache_key(api_key, normalized_level)
    cache_ttl = LEADERBOARD_CACHE_TTLS[normalized_level]

    # Recheck after every condition wake-up because cache invalidation can race
    # with a provider refresh. Exactly one caller claims an empty or expired
    # cache entry; cold-cache followers wait for that caller's result.
    while True:
        now = time.time()
        with _MODEL_CACHE_CONDITION:
            cache_entry = _MODEL_CACHE.setdefault(
                cache_key,
                {
                    "result": None,
                    "timestamp": 0,
                    "refreshing": False,
                },
            )
            cached_result = cache_entry["result"]
            if cached_result is not None and (now - cache_entry["timestamp"]) < cache_ttl:
                return cached_result

            # A transient refresh failure may serve stale data briefly without
            # extending the entry's normal freshness timestamp.
            if cached_result is not None and now < cache_entry.get("retry_after", 0):
                return cached_result

            cached_error_type = cache_entry.get("last_error_type")
            if (
                cached_result is None
                and isinstance(cached_error_type, str)
                and now < cache_entry.get("retry_after", 0)
            ):
                raise _provider_error(cached_error_type)

            # Expired data remains useful while another caller refreshes it.
            if cache_entry["refreshing"] and cached_result is not None:
                return cached_result

            if cache_entry["refreshing"]:
                _MODEL_CACHE_CONDITION.wait()
                continue

            cache_entry["refreshing"] = True
            break

    try:
        result = _fetch_all_leaderboard_pages(api_key, normalized_level)
    except requests.RequestException as exc:
        logger.warning("Failed to reach Artificial Analysis API: %s", exc)
        return _finish_failed_refresh(
            cache_key,
            cached_result,
            _provider_error("leaderboard_provider_unavailable"),
        )
    except HTTPException as exc:
        return _finish_failed_refresh(cache_key, cached_result, exc)
    except BaseException:
        # Never strand cold-cache waiters if an unexpected provider/parsing
        # failure escapes the normal translated exception paths.
        with _MODEL_CACHE_CONDITION:
            cache_entry = _MODEL_CACHE.get(cache_key)
            if cache_entry is not None:
                cache_entry["refreshing"] = False
            _MODEL_CACHE_CONDITION.notify_all()
        raise

    # Only a fully fetched and validated multi-page response replaces the cache.
    with _MODEL_CACHE_CONDITION:
        _MODEL_CACHE[cache_key] = {
            "result": result,
            "timestamp": time.time(),
            "refreshing": False,
        }
        _MODEL_CACHE_CONDITION.notify_all()

    return result


def _build_model_entry(api_model: dict, db_model):
    """Build a model entry from API and DB data."""
    model_creator = api_model.get("model_creator")
    provider_name = None
    if isinstance(model_creator, dict):
        raw_provider = model_creator.get("name") or model_creator.get("slug")
        if isinstance(raw_provider, str):
            provider_name = raw_provider

    evaluations = api_model.get("evaluations")
    if not isinstance(evaluations, dict):
        evaluations = {}
    else:
        normalized_evaluations = {}
        for evaluation_name, evaluation_value in evaluations.items():
            if isinstance(evaluation_value, str) and evaluation_value.strip():
                try:
                    numeric_value = float(evaluation_value)
                    if math.isfinite(numeric_value):
                        evaluation_value = numeric_value
                except ValueError:
                    # Providers may add non-numeric evaluation metadata. Keep
                    # it intact instead of failing response validation.
                    pass
            normalized_evaluations[evaluation_name] = evaluation_value
        evaluations = normalized_evaluations

    api_model_name = api_model.get("name") or api_model.get("slug") or api_model.get("id")
    cleaned_model_name = None
    if isinstance(api_model_name, str):
        cleaned_model_name = api_model_name.strip()
        if cleaned_model_name.lower().endswith(":free"):
            cleaned_model_name = cleaned_model_name[:-5]
        cleaned_model_name = cleaned_model_name.replace(":", "-").replace(".", "-")

    model_name = cleaned_model_name
    settings_dict = {}
    model_capabilities = []
    tools_supported = None
    training_data_state = None

    if db_model is not None:
        db_model_name = getattr(db_model, "name", None)
        if isinstance(db_model_name, str) and db_model_name.strip():
            model_name = db_model_name.strip()

        settings_dict = getattr(db_model, "settings", None) or {}
        if not isinstance(settings_dict, dict):
            settings_dict = {}

        input_capabilities = []
        for key in ("input_capabilities", "input_formats"):
            if not input_capabilities and key in settings_dict:
                input_capabilities = _coerce_to_list(settings_dict.get(key))

        output_capabilities = []
        for key in ("output_capabilities", "output_formats"):
            if not output_capabilities and key in settings_dict:
                output_capabilities = _coerce_to_list(settings_dict.get(key))

        model_capabilities = _extract_capabilities(getattr(db_model, "capabilities", None))
        if "tools" in model_capabilities:
            tools_supported = _coerce_tools(getattr(db_model, "tools", None))

        training_data_state = settings_dict.get("training_data")
    else:
        input_capabilities = []
        output_capabilities = []

    model_entry = {
        "model_name": model_name,
        "provider_name": provider_name,
        "evaluations": evaluations,
        "input_capabilities": input_capabilities,
        "output_capabilities": output_capabilities,
        "capabilities": model_capabilities,
        "training_data": training_data_state,
    }

    if tools_supported is not None:
        model_entry["tools"] = tools_supported
        if isinstance(tools_supported, list):
            model_entry["tool_categories"] = _compute_tool_categories(tools_supported)
        elif isinstance(tools_supported, dict):
            tool_names = list(tools_supported.keys())
            model_entry["tool_categories"] = _compute_tool_categories(tool_names)

    return model_entry



def _resolve_user_model_settings(raw_settings: Any) -> dict:
    """Resolve user model settings."""
    if isinstance(raw_settings, dict):
        return raw_settings
    if isinstance(raw_settings, str) and raw_settings.strip():
        try:
            parsed = json.loads(raw_settings)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            return {}
    return {}


def _extract_model_identifier(user_model: dict[str, Any]) -> str | None:
    """Extract model identifier from user model."""
    for key in ("model_name", "name", "model_id", "id"):
        value = user_model.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _get_db_model(db, user_model: dict[str, Any]):
    """Get database model from user model."""
    model_id = user_model.get("model_id") or user_model.get("id")
    if not model_id:
        return None
    try:
        return get_model(db, model_id)
    except HTTPException:
        return None


def _get_accessible_agent_base_models(db, user_id: str) -> dict[str, Any]:
    """Map accessible agent wrapper IDs to their backing database models.

    The public model-selector response intentionally omits ``base_model_id``
    and provider model names. Re-resolving the access-filtered agents here
    keeps those private fields off the wire while still matching leaderboard
    entries against the real provider model rather than the agent's display
    name or wrapper ID.
    """
    try:
        from app.agents.utils import list_accessible_agents

        agents = list_accessible_agents(db, user_id)
    except HTTPException:
        return {}

    resolved: dict[str, Any] = {}
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("model_id") or agent.get("id") or "").strip()
        base_model_id = str(agent.get("base_model_id") or "").strip()
        if not agent_id or not base_model_id:
            continue
        try:
            resolved[agent_id] = get_model(db, base_model_id)
        except HTTPException:
            continue
    return resolved


def get_llm_model_leaderboard(db, user_id: str):
    """Get the LLM model leaderboard."""
    user_models = list_user_models(db, user_id) or []
    settings = get_group_page_settings(get_user(db, user_id, None).group_id, "leaderboard", db)
    enabled = settings.get("enabled") if isinstance(settings, dict) else False
    api_key = settings.get("artificial_analysis_api_key") if isinstance(settings, dict) else None
    data_level = normalize_leaderboard_data_level(
        settings.get("artificial_analysis_data_level")
        if isinstance(settings, dict)
        else None
    )
    if not enabled or not isinstance(api_key, str) or not api_key.strip():
        raise HTTPException(
            status_code=403,
            detail={
                "type": "leaderboard_access_denied",
                "message": "Your group does not have access to the LLM model leaderboard.",
            },
        )
    # 1. Get the leaderboard data
    provider_result = get_leaderboard_data(api_key, data_level)
    data = provider_result["data"]

    result_leaderboard_models: list[dict] = []
    has_agent_summaries = any(
        isinstance(model, dict)
        and (model.get("model_kind") == "agent" or model.get("is_custom_agent") is True)
        for model in user_models
    )
    accessible_agent_base_models = (
        _get_accessible_agent_base_models(db, user_id) if has_agent_summaries else {}
    )

    # 2. For each model get the data
    for user_model in user_models:
        if not isinstance(user_model, dict):
            continue

        # The user-model API intentionally exposes only a public Omlorix ID.
        # Resolve provider identifiers and reasoning settings on the server
        # after the access-filtered model has been selected.
        public_model_id = str(user_model.get("model_id") or user_model.get("id") or "").strip()
        is_agent_summary = (
            user_model.get("model_kind") == "agent"
            or user_model.get("is_custom_agent") is True
        )
        db_model = (
            accessible_agent_base_models.get(public_model_id)
            if is_agent_summary
            else _get_db_model(db, user_model)
        )
        # Never fall back to an agent display name or wrapper ID. Besides
        # producing false matches, doing so would make results depend on a
        # user-editable label rather than the accessible backing model.
        if is_agent_summary and db_model is None:
            continue
        raw_model_identifier = (
            getattr(db_model, "model_name", None)
            if db_model is not None
            else _extract_model_identifier(user_model)
        )
        if not raw_model_identifier:
            continue

        model_name = normalize_model_name(raw_model_identifier)

        # Get the model's reasoning settings
        model_settings = _resolve_user_model_settings(
            getattr(db_model, "settings", None) if db_model is not None else None
        )
        reasoning_enabled = bool(model_settings.get("reasoning_enabled", False))
        reasoning_effort = model_settings.get("reasoning_effort") or model_settings.get("thinking_level")
        if reasoning_effort and not reasoning_enabled:
            reasoning_enabled = True

        filtered_models = give_models_type(data, model_name)
        if not filtered_models:
            continue
        if not check_multiple_models(filtered_models):
            # Only one model found
            selected_models = filtered_models
        else:
            selected_models = filter_multiple_models(filtered_models, model_name, reasoning_enabled, reasoning_effort)

        if not selected_models:
            continue

        if not isinstance(selected_models, list):
            selected_models = [selected_models]

        for api_model in selected_models:
            if not isinstance(api_model, dict):
                continue
            result_leaderboard_models.append(_build_model_entry(api_model, db_model))

    return {
        "status": "ok",
        "data_level": provider_result["data_level"],
        "provider_tier": provider_result["provider_tier"],
        "intelligence_index_version": provider_result[
            "intelligence_index_version"
        ],
        "models": result_leaderboard_models,
    }






def give_models_type(models, model_name: str):
    """Return models whose name or slug matches the provided model_name.
    
    Args:
        models: List of model dictionaries to filter.
        model_name: The model name to match against model['name'] and model['slug'].
    
    Returns:
        Filtered list of models where model_name matches the name or slug field.
    """
    if not model_name:
        return models

    needle = model_name.lower()
    filtered = []

    for model in models:
        if not isinstance(model, dict):
            continue

        name = str(model.get("name")).lower()
        slug = str(model.get("slug")).lower()

        if needle in name or needle in slug:
            filtered.append(model)

    return filtered


def check_multiple_models(filtered_models):
    """Check for multiple models."""
    # If there are mutliple models in the array
    if len(filtered_models) > 1:
        return True
    return False



def filter_multiple_models(filtered_models: str, model_name: str, reasoning_enabled: bool, reasoning_effort: str):
    """Filter multiple models."""
    def _exclude_by_slug_keyword(models, keyword, label):
        def _keyword_matches(slug):
            if keyword == "mini":
                return re.search(r"(?<![a-z0-9])mini(?![a-z0-9])", slug) is not None
            return keyword in slug

        kept = []
        for model in models:
            slug = str(model.get("slug", "")).lower()
            if _keyword_matches(slug):
                continue
            kept.append(model)
        return kept

    def _exclude_non_reasoning(models):
        kept = []
        for model in models:
            slug = str(model.get("slug", "")).lower()
            name = str(model.get("name", "")).lower()
            if "non-reasoning" in slug or "non-reasoning" in name:
                continue
            kept.append(model)
        return kept

    def _filter_by_reasoning_effort(models, effort):
        def _normalized(label):
            return label.strip().lower() if label else ""

        def _effort_order():
            default_order = ["minimal", "low", "medium", "high", "xhigh"]
            configured = globals().get("POSSIBLE_REASONING_EFFORTS")
            if isinstance(configured, list) and configured:
                return [str(item).lower() for item in configured]
            return default_order

        def _effort_index(order, label):
            try:
                return order.index(label)
            except ValueError:
                return None

        def _annotate_models(order):
            annotated = []
            for model in models:
                slug_lower = str(model.get("slug", "")).lower()
                name_lower = str(model.get("name", "")).lower()

                detected = None

                paren_levels = re.findall(r"\(([^)]+)\)", str(model.get("name", "")))
                if paren_levels:
                    paren_level = paren_levels[-1].strip().lower()
                    if paren_level in order:
                        detected = paren_level

                if not detected:
                    slug_tokens = [
                        token
                        for token in re.split(r"[^a-z0-9]+", slug_lower)
                        if token
                    ]
                    name_tokens = [
                        token
                        for token in re.split(r"[^a-z0-9]+", name_lower)
                        if token
                    ]

                    for option in order:
                        if option in slug_tokens or option in name_tokens:
                            detected = option
                            break

                annotated.append((model, detected))
            return annotated

        def _collect_for_effort(annotated, effort_label):
            subset = [model for model, detected in annotated if detected == effort_label]
            return subset

        def _ensure_single(subset, label):
            if not subset:
                return subset
            if len(subset) == 1:
                return subset

            chosen = sorted(
                subset,
                key=lambda model: (
                    str(model.get("slug", "")),
                    str(model.get("name", "")),
                ),
            )[0]
            return [chosen]

        order = _effort_order()
        normalized_effort = _normalized(effort)
        annotated_models = _annotate_models(order)

        if not normalized_effort:
            return _ensure_single([model for model, _ in annotated_models], "any")

        exact_subset = _collect_for_effort(annotated_models, normalized_effort)
        if exact_subset:
            return _ensure_single(exact_subset, normalized_effort)

        desired_index = _effort_index(order, normalized_effort)
        available_efforts = sorted(
            {detected for _, detected in annotated_models if detected},
            key=lambda label: _effort_index(order, label) if _effort_index(order, label) is not None else float("inf"),
        )

        fallback_effort = None
        if available_efforts:
            if desired_index is None:
                fallback_effort = available_efforts[0]
            else:
                fallback_effort = min(
                    available_efforts,
                    key=lambda label: abs(_effort_index(order, label) - desired_index),
                )

        if fallback_effort:
            fallback_subset = _collect_for_effort(annotated_models, fallback_effort)
            return _ensure_single(fallback_subset, fallback_effort)

        return _ensure_single([model for model, _ in annotated_models], normalized_effort)

    def _exclude_numeric_sub_versions(models, base_slug):
        kept = []
        base_slug = base_slug.lower()

        for model in models:
            slug = str(model.get("slug", "")).lower()
            suffix = ""
            if slug.startswith(base_slug):
                suffix = slug[len(base_slug):]

            if suffix.startswith("-") and len(suffix) > 1 and suffix[1].isdigit():
                continue

            kept.append(model)

        return kept

    # Sort out nano models, if the model is not a nano model
    if "nano" not in model_name:
        filtered_models = _exclude_by_slug_keyword(filtered_models, "nano", "nano")
    # Sort out mini models, if the model is not a mini model
    if "mini" not in model_name:
        filtered_models = _exclude_by_slug_keyword(filtered_models, "mini", "mini")
    # Sort out codex models, if the model is not a codex model
    if "codex" not in model_name:
        filtered_models = _exclude_by_slug_keyword(filtered_models, "codex", "codex")
    if "chatgpt" not in model_name:
        filtered_models = _exclude_by_slug_keyword(filtered_models, "chatgpt", "chatgpt")
    # Check if still more than one model
    if not check_multiple_models(filtered_models):
        return filtered_models

    filtered_models = _exclude_numeric_sub_versions(
        filtered_models, model_name
    )
    if reasoning_enabled: 
        # Sort out non reasoning models (slug or name)
        filtered_models = _exclude_non_reasoning(filtered_models)
        if not check_multiple_models(filtered_models):
            return filtered_models
        # Now there might be multiple models with differnt reasoning levels
        # Check if the current reasoning level is in the model slug or model name, return then this one
        filtered_models = _filter_by_reasoning_effort(
            filtered_models, reasoning_effort
        )
        if not check_multiple_models(filtered_models):
            return filtered_models

    else:
        non_reasoning_named_models = [
            model
            for model in filtered_models
            if "non-reasoning" in str(model.get("name", "")).lower()
        ]
        if non_reasoning_named_models:
            chosen = non_reasoning_named_models[0]
            return [chosen]

        # Filter out all reasoning models
        # Which have "-minimal", "-low", "-medium", "-high" in their model slug
        removal_clauses = [
            ("-minimal", "minimal"),
            ("-low", "low"),
            ("-medium", "medium"),
            ("-high", "high"),
            ("-xhigh", "xhigh")
        ]

        kept_models = []
        for model in filtered_models:
            slug_lower = model.get("slug", "")
            name = model.get("name", "")

            slug_lower = slug_lower.lower() if isinstance(slug_lower, str) else ""
            name_lower = name.lower() if isinstance(name, str) else ""

            skip_model = False
            for marker, label in removal_clauses:
                if marker in slug_lower or marker in name_lower:
                    skip_model = True
                    break

            if not skip_model:
                kept_models.append(model)

        filtered_models = kept_models


        
    return filtered_models


def normalize_model_name(model_name: str):
    """Normalize a model name."""
    # Drop any prefix before "/"
    model_name = model_name.split("/")[-1]
    # Remove trailing preview suffixes like "-preview-02-05"
    model_name = re.sub(r"-preview(?:-\d{2}-\d{2})?$", "", model_name, flags=re.IGNORECASE)
    # Remove trailing date fragments like "-2025-08-07"
    model_name = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model_name)
    # Remove any ":free" in the name
    model_name = model_name.replace(":free", "")
    # Replae any ":latest" in the name
    model_name = model_name.replace(":latest", "")
    # Remove any "-preview" in the name
    model_name = model_name.replace("-preview", "")
    # Replace any ":" with "-"
    model_name = model_name.replace(":", "-")
    # replace any "." with a "-"
    model_name = model_name.replace(".", "-")
    return model_name
