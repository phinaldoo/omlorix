from types import SimpleNamespace
import json

import pytest
import requests

from app.tools.weather import utils as weather_utils
from app.tools import helper as tool_helper


def test_parse_coordinates_from_location_accepts_trimmed_ranges():
    assert weather_utils._parse_coordinates_from_location(" 52.52, 13.405 ") == (52.52, 13.405)
    assert weather_utils._parse_coordinates_from_location("-90,180") == (-90.0, 180.0)


@pytest.mark.parametrize("value", [None, "", "Berlin", "1", "91,0", "0,181", "north,east"])
def test_parse_coordinates_from_location_rejects_non_coordinates(value):
    assert weather_utils._parse_coordinates_from_location(value) is None


def test_geocode_location_uses_explicit_coordinates_without_network(monkeypatch):
    monkeypatch.setattr(
        weather_utils,
        "_geocode_open_meteo",
        lambda *args, **kwargs: pytest.fail("coordinates should not call forward geocoding"),
    )

    result = weather_utils.geocode_location(object(), "user-1", "48.1, 11.6")

    assert result == {
        "status": "success",
        "latitude": 48.1,
        "longitude": 11.6,
        "city_display": "48.1, 11.6",
    }


def test_geocode_location_uses_user_location_when_argument_missing(monkeypatch):
    monkeypatch.setattr(weather_utils, "get_user_setting_value", lambda user_id, page, key, db: "Paris, France")
    monkeypatch.setattr(
        weather_utils,
        "_geocode_open_meteo",
        lambda db, search, original: {
            "latitude": 48.8566,
            "longitude": 2.3522,
            "city_display": f"{search}/{original}",
        },
    )

    result = weather_utils.geocode_location(object(), "user-1", None)

    assert result["latitude"] == 48.8566
    assert result["longitude"] == 2.3522
    assert result["city_display"] == "Paris/Paris, France"


def test_geocode_location_reports_missing_user_location(monkeypatch):
    monkeypatch.setattr(weather_utils, "get_user_setting_value", lambda *args: "")

    with pytest.raises(ValueError, match="no location set up"):
        weather_utils.geocode_location(object(), "user-1", None)


def test_get_weather_settings_normalizes_invalid_admin_values(monkeypatch):
    monkeypatch.setattr(
        weather_utils,
        "get_settings_page_data",
        lambda db, page: {
            "provider": "not-real",
            "geocoding_provider": "nope",
            "openweathermap_api_mode": "bad-mode",
            "api_key": "  key-123  ",
        },
    )

    assert weather_utils._get_weather_settings(object()) == {
        "provider": "open_meteo",
        "geocoding_provider": "open_meteo",
        "openweathermap_api_mode": "free",
        "api_key": "key-123",
    }


def test_get_weather_calls_open_meteo_with_resolved_location(monkeypatch):
    checked_urls: list[str] = []

    monkeypatch.setattr(weather_utils, "_get_weather_settings", lambda db: {"provider": "open_meteo"})
    monkeypatch.setattr(
        weather_utils,
        "geocode_location",
        lambda db, user_id, location, geocoding_provider=None: {
            "status": "success",
            "latitude": 51.5,
            "longitude": -0.12,
            "city_display": "London, United Kingdom",
        },
    )
    monkeypatch.setattr(weather_utils, "_assert_weather_url_allowed", lambda db, url, *, feature: checked_urls.append(url))
    monkeypatch.setattr(
        weather_utils,
        "get_weather_open_meteo",
        lambda lat, lon, city=None: {"lat": lat, "lon": lon, "city": city},
    )

    assert weather_utils.get_weather(object(), "user-1", "London") == {
        "lat": 51.5,
        "lon": -0.12,
        "city": "London, United Kingdom",
    }
    assert checked_urls == [weather_utils.OPEN_METEO_FORECAST_URL]


def test_get_weather_requires_openweathermap_api_key(monkeypatch):
    monkeypatch.setattr(
        weather_utils,
        "_get_weather_settings",
        lambda db: {"provider": "openweathermap", "api_key": "", "openweathermap_api_mode": "free"},
    )
    monkeypatch.setattr(
        weather_utils,
        "geocode_location",
        lambda *args, **kwargs: {"status": "success", "latitude": 1, "longitude": 2, "city_display": "Somewhere"},
    )

    with pytest.raises(ValueError, match="requires an API key"):
        weather_utils.get_weather(object(), "user-1", "Somewhere")


def test_get_weather_maps_openweathermap_auth_errors(monkeypatch):
    response = SimpleNamespace(status_code=401)
    http_error = requests.HTTPError("unauthorized", response=response)

    monkeypatch.setattr(
        weather_utils,
        "_get_weather_settings",
        lambda db: {"provider": "openweathermap", "api_key": "key", "openweathermap_api_mode": "onecall_3"},
    )
    monkeypatch.setattr(
        weather_utils,
        "geocode_location",
        lambda *args, **kwargs: {"status": "success", "latitude": 1, "longitude": 2, "city_display": "Somewhere"},
    )
    monkeypatch.setattr(weather_utils, "_assert_weather_url_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(weather_utils, "get_weather_openweathermap", lambda *args, **kwargs: (_ for _ in ()).throw(http_error))

    with pytest.raises(ValueError, match="One Call API access"):
        weather_utils.get_weather(object(), "user-1", "Somewhere")


def test_weather_widget_payload_is_frontend_json():
    weather = {
            "city": '<img src=x onerror=alert(1)>, <script>alert(1)</script>',
            "time": "09:00",
            "date": "2026-05-31",
            "current_weather": {
                "temperature": "<b>22</b>",
                "description": "<strong>Clear</strong> ☀️",
                "weathercode": 0,
                "windspeed": '<svg onload="alert(1)">',
            },
            "forecast": {
                "hourly": {
                    "time": ['<img src=x onerror="alert(1)">'],
                    "temperature": ["20<script>"],
                    "weather_code": [0],
                    "relative_humidity": ['50" onmouseover="alert(1)'],
                },
                "daily": {
                    "date": ["not-a-date<script>"],
                    "weather_code": [0],
                    "temperature_daily_high": ["25<script>"],
                    "temperature_daily_low": ["12<script>"],
                },
            },
        }
    widget = tool_helper._build_frontend_widget_payload("weather", weather)

    assert widget["render_mode"] == "frontend"
    assert json.loads(widget["html"]) == weather
