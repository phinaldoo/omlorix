import logging
from typing import Any, Dict

import requests

from app.network.policy import OutboundRequestBlockedError, assert_url_allowed
from app.settings.models import get_settings_page_data
from app.tools.weather.open_meteo import get_weather_open_meteo
from app.tools.weather.openweathermap import (
    OPENWEATHERMAP_FREE_CURRENT_URL,
    OPENWEATHERMAP_FREE_FORECAST_URL,
    OPENWEATHERMAP_ONECALL_URL,
    get_weather_openweathermap,
)
from app.users.init import get_user_setting_value


logger = logging.getLogger(__name__)
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
BDC_REVERSE_GEOCODING_URL = "https://api-bdc.io/data/reverse-geocode-client"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_WEATHER_PROVIDER = "open_meteo"
WEATHER_PROVIDERS = {"open_meteo", "openweathermap"}
DEFAULT_GEOCODING_PROVIDER = "open_meteo"
GEOCODING_PROVIDERS = {"open_meteo", "api_bdc"}
DEFAULT_OPENWEATHERMAP_API_MODE = "free"
OPENWEATHERMAP_API_MODES = {"free", "onecall_3"}


def _assert_weather_url_allowed(db, url: str, *, feature: str) -> None:
    try:
        assert_url_allowed(db, url=url, feature=feature)
    except OutboundRequestBlockedError as exc:
        raise ValueError(str(exc)) from exc


def _parse_coordinates_from_location(value: str | None) -> tuple[float, float] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    text = text.replace(" ", "")
    if "," not in text:
        return None
    parts = text.split(",", 1)
    if len(parts) != 2:
        return None
    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _geocode_open_meteo(db, search_location: str, original_location: str) -> Dict[str, Any]:
    _assert_weather_url_allowed(db, GEOCODING_URL, feature="Weather geocoding request")
    params = {
        "name": search_location,
        "count": 1,
        "language": "en",
        "format": "json",
    }

    response = requests.get(GEOCODING_URL, params=params, timeout=10)
    response.raise_for_status()
    payload: Dict[str, Any] = response.json()
    results = payload.get("results") or []

    if not results:
        raise LookupError(f"Could not resolve location '{original_location}'")

    result = results[0]
    try:
        latitude = float(result["latitude"])
        longitude = float(result["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LookupError(f"Incomplete geocoding data for '{original_location}'") from exc

    name = result.get("name") or original_location
    country = result.get("country")
    city_display = f"{name}, {country}" if country else name
    return {"latitude": latitude, "longitude": longitude, "city_display": city_display}


def _reverse_geocode_bdc(db, lat: float, lon: float, fallback_label: str | None = None) -> Dict[str, Any]:
    _assert_weather_url_allowed(
        db,
        BDC_REVERSE_GEOCODING_URL,
        feature="Weather reverse geocoding request",
    )
    params = {
        "latitude": lat,
        "longitude": lon,
        "localityLanguage": "en",
    }
    response = requests.get(BDC_REVERSE_GEOCODING_URL, params=params, timeout=10)
    response.raise_for_status()
    payload: Dict[str, Any] = response.json() or {}

    city = (
        payload.get("city")
        or payload.get("locality")
        or payload.get("principalSubdivision")
        or payload.get("countryName")
    )
    country = payload.get("countryName")
    if city and country and str(city).strip().lower() != str(country).strip().lower():
        city_display = f"{city}, {country}"
    elif city:
        city_display = str(city)
    else:
        city_display = fallback_label or f"{lat},{lon}"

    return {"latitude": float(lat), "longitude": float(lon), "city_display": city_display}


def geocode_location(
    db,
    user_id: str,
    location: str | None,
    geocoding_provider: str | None = None,
) -> Dict[str, Any]:
    """Resolve a location string to latitude, longitude, and display name."""

    if not location:
        location = get_user_setting_value(user_id, "general", "location", db)
        if not location:
            raise ValueError("The user has no location set up. Either set a location in the settings or provide a location.")


    if not location or not location.strip():
        raise ValueError("Location must be a non-empty string")

    location = location.strip()
    selected_provider = (geocoding_provider or DEFAULT_GEOCODING_PROVIDER).strip().lower()
    if selected_provider not in GEOCODING_PROVIDERS:
        selected_provider = DEFAULT_GEOCODING_PROVIDER

    parsed_coords = _parse_coordinates_from_location(location)
    if parsed_coords is not None:
        lat, lon = parsed_coords
        if selected_provider == "api_bdc":
            resolved = _reverse_geocode_bdc(db, lat, lon, fallback_label=location)
        else:
            resolved = {"latitude": lat, "longitude": lon, "city_display": location}
        return {"status": "success", **resolved}

    search_location = location.split(",", 1)[0].strip() or location
    resolved = _geocode_open_meteo(db, search_location, location)
    if selected_provider == "api_bdc":
        try:
            resolved = _reverse_geocode_bdc(
                db,
                resolved["latitude"],
                resolved["longitude"],
                fallback_label=resolved.get("city_display"),
            )
        except Exception:
            # Keep forward geocoding result if reverse provider is temporarily unavailable.
            pass
    return {"status": "success", **resolved}


def _get_weather_settings(db) -> Dict[str, str]:
    """Return normalized weather settings from admin config."""
    provider = DEFAULT_WEATHER_PROVIDER
    geocoding_provider = DEFAULT_GEOCODING_PROVIDER
    openweathermap_api_mode = DEFAULT_OPENWEATHERMAP_API_MODE
    api_key = ""
    try:
        weather_settings = get_settings_page_data(db, "weather_tool")
        configured_provider = weather_settings.get("provider")
        if isinstance(configured_provider, str) and configured_provider.strip():
            provider = configured_provider.strip().lower()
        configured_geocoding_provider = weather_settings.get("geocoding_provider")
        if isinstance(configured_geocoding_provider, str) and configured_geocoding_provider.strip():
            geocoding_provider = configured_geocoding_provider.strip().lower()
        configured_mode = weather_settings.get("openweathermap_api_mode")
        if isinstance(configured_mode, str) and configured_mode.strip():
            openweathermap_api_mode = configured_mode.strip().lower()
        configured_api_key = weather_settings.get("api_key")
        if isinstance(configured_api_key, str):
            api_key = configured_api_key.strip()
    except Exception:
        logger.debug("Failed to read weather_tool settings, falling back to open_meteo")
    if provider not in WEATHER_PROVIDERS:
        provider = DEFAULT_WEATHER_PROVIDER
    if geocoding_provider not in GEOCODING_PROVIDERS:
        geocoding_provider = DEFAULT_GEOCODING_PROVIDER
    if openweathermap_api_mode not in OPENWEATHERMAP_API_MODES:
        openweathermap_api_mode = DEFAULT_OPENWEATHERMAP_API_MODE
    return {
        "provider": provider,
        "geocoding_provider": geocoding_provider,
        "openweathermap_api_mode": openweathermap_api_mode,
        "api_key": api_key,
    }


def get_weather(db, user_id: str, location: str | None = None) -> Dict[str, Any]:
    """Fetch weather data for a human-readable location string."""
    weather_settings = _get_weather_settings(db)
    geocoding_provider = weather_settings.get("geocoding_provider", DEFAULT_GEOCODING_PROVIDER)
    result = geocode_location(db, user_id, location, geocoding_provider=geocoding_provider)
    if result.get("status") == "error":
        raise ValueError(str(result.get("message") or "Failed to resolve weather location."))

    lat = result.get("latitude")
    lon = result.get("longitude")
    city_display = result.get("city_display")

    provider = weather_settings.get("provider", DEFAULT_WEATHER_PROVIDER)
    openweathermap_api_mode = weather_settings.get(
        "openweathermap_api_mode",
        DEFAULT_OPENWEATHERMAP_API_MODE,
    )
    api_key = weather_settings.get("api_key", "")

    try:
        if provider == "open_meteo":
            _assert_weather_url_allowed(
                db,
                OPEN_METEO_FORECAST_URL,
                feature="Weather provider request",
            )
            weather = get_weather_open_meteo(lat, lon, city=city_display)
        elif provider == "openweathermap":
            if not api_key:
                raise ValueError(
                    "OpenWeatherMap requires an API key. "
                    "Configure it in Admin > Tools > Weather settings."
                )
            if openweathermap_api_mode == "onecall_3":
                _assert_weather_url_allowed(
                    db,
                    OPENWEATHERMAP_ONECALL_URL,
                    feature="Weather provider request",
                )
            else:
                _assert_weather_url_allowed(
                    db,
                    OPENWEATHERMAP_FREE_CURRENT_URL,
                    feature="Weather provider request",
                )
                _assert_weather_url_allowed(
                    db,
                    OPENWEATHERMAP_FREE_FORECAST_URL,
                    feature="Weather provider request",
                )
            weather = get_weather_openweathermap(
                lat,
                lon,
                api_key=api_key,
                city=city_display,
                api_mode=openweathermap_api_mode,
            )
        else:
            logger.warning("Unknown weather provider '%s', falling back to open_meteo", provider)
            weather = get_weather_open_meteo(lat, lon, city=city_display)
            provider = DEFAULT_WEATHER_PROVIDER
    except ValueError:
        raise
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if provider == "openweathermap" and status in (401, 403):
            mode_hint = (
                "Check API key and One Call API access."
                if openweathermap_api_mode == "onecall_3"
                else "Check API key."
            )
            raise ValueError(f"OpenWeatherMap request was rejected. {mode_hint}") from exc
        raise
    except requests.RequestException as exc:
        raise ValueError(str(exc)) from exc

    return weather
