OPENROUTER_BASE_URL = "https://openrouter.ai"
OPENROUTER_EU_BASE_URL = "https://eu.openrouter.ai"
OPENROUTER_API_BASE_PATH = "/api/v1"
OPENROUTER_DEFAULT_RANKING_URL = "https://github.com/phinaldoo/omlorix"
OPENROUTER_DEFAULT_RANKING_TITLE = "Omlorix"


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value) if value is not None else False


def is_openrouter_eu_routing_enabled(settings: dict | None = None) -> bool:
    if not isinstance(settings, dict):
        return False
    return _coerce_bool(settings.get("eu_routing"))


def get_openrouter_base_url(settings: dict | None = None) -> str:
    if is_openrouter_eu_routing_enabled(settings):
        return OPENROUTER_EU_BASE_URL
    return OPENROUTER_BASE_URL


def get_openrouter_api_base_url(settings: dict | None = None) -> str:
    return f"{get_openrouter_base_url(settings)}{OPENROUTER_API_BASE_PATH}"


def build_openrouter_api_url(path: str, settings: dict | None = None) -> str:
    normalized_path = f"/{str(path or '').lstrip('/')}"
    return f"{get_openrouter_api_base_url(settings)}{normalized_path}"


def resolve_openrouter_attribution(
    settings: dict | None = None,
    *,
    ranking_url: str | None = None,
    ranking_title: str | None = None,
) -> tuple[str, str]:
    """Resolve OpenRouter app attribution with stable Omlorix defaults.

    Explicit function arguments take precedence over stored provider settings.
    Blank values intentionally fall back because OpenRouter requires a referer
    to associate usage with an application.
    """
    normalized_settings = settings if isinstance(settings, dict) else {}
    resolved_url = str(
        ranking_url or normalized_settings.get("ranking_url") or OPENROUTER_DEFAULT_RANKING_URL
    ).strip()
    resolved_title = str(
        ranking_title
        or normalized_settings.get("ranking_title")
        or OPENROUTER_DEFAULT_RANKING_TITLE
    ).strip()
    return (
        resolved_url or OPENROUTER_DEFAULT_RANKING_URL,
        resolved_title or OPENROUTER_DEFAULT_RANKING_TITLE,
    )


def get_openrouter_attribution_headers(
    settings: dict | None = None,
    *,
    ranking_url: str | None = None,
    ranking_title: str | None = None,
) -> dict[str, str]:
    """Return OpenRouter's current application-attribution headers."""
    resolved_url, resolved_title = resolve_openrouter_attribution(
        settings,
        ranking_url=ranking_url,
        ranking_title=ranking_title,
    )
    return {
        "HTTP-Referer": resolved_url,
        "X-OpenRouter-Title": resolved_title,
    }


def build_openrouter_headers(
    api_key: str | None,
    settings: dict | None = None,
    *,
    include_content_type: bool = True,
    ranking_url: str | None = None,
    ranking_title: str | None = None,
) -> dict[str, str]:
    """Build authenticated OpenRouter headers with consistent attribution."""
    headers = get_openrouter_attribution_headers(
        settings,
        ranking_url=ranking_url,
        ranking_title=ranking_title,
    )
    normalized_api_key = str(api_key or "").strip()
    if normalized_api_key:
        headers["Authorization"] = f"Bearer {normalized_api_key}"
    if include_content_type:
        headers["Content-Type"] = "application/json"
    return headers


def is_openrouter_api_base_url(base_url: str | None) -> bool:
    normalized = str(base_url or "").strip().rstrip("/")
    return normalized in {
        f"{OPENROUTER_BASE_URL}{OPENROUTER_API_BASE_PATH}",
        f"{OPENROUTER_EU_BASE_URL}{OPENROUTER_API_BASE_PATH}",
    }
