import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from time import sleep

import requests


logger = logging.getLogger(__name__)
OPENWEATHERMAP_ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"
OPENWEATHERMAP_FREE_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
OPENWEATHERMAP_FREE_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
OPENWEATHERMAP_API_MODES = {"free", "onecall_3"}


def _map_openweathermap_to_wmo(condition_id: int | None) -> int | None:
    if condition_id is None:
        return None
    try:
        code = int(condition_id)
    except (TypeError, ValueError):
        return None

    if 200 <= code <= 232:
        return 95
    if 300 <= code <= 321:
        return 51
    if code == 500:
        return 61
    if code == 501:
        return 63
    if 502 <= code <= 504:
        return 65
    if code == 511:
        return 66
    if code == 520:
        return 80
    if code == 521:
        return 81
    if 522 <= code <= 531:
        return 82
    if code == 600:
        return 71
    if code == 601:
        return 73
    if code == 602:
        return 75
    if 611 <= code <= 616:
        return 85
    if code == 620:
        return 85
    if code in (621, 622):
        return 86
    if 701 <= code <= 781:
        if code == 781:
            return 99
        return 45
    if code == 800:
        return 0
    if code == 801:
        return 1
    if code == 802:
        return 2
    if code in (803, 804):
        return 3
    return None


def _format_with_unit(value, unit: str | None):
    if value is None:
        return None
    if unit:
        return f"{value}{unit}"
    return value


def _coerce_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_local_datetime(unix_ts: int | float | None, timezone_offset_seconds: int):
    if unix_ts is None:
        return None
    tz = timezone(timedelta(seconds=timezone_offset_seconds))
    return datetime.fromtimestamp(unix_ts, tz=tz)


def _to_local_iso(unix_ts: int | float | None, timezone_offset_seconds: int) -> str | None:
    dt = _to_local_datetime(unix_ts, timezone_offset_seconds)
    return dt.isoformat() if dt else None


def _get_weather_description(weather_block: list | None) -> str | None:
    if not isinstance(weather_block, list) or not weather_block:
        return None
    first = weather_block[0] or {}
    description = first.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip().capitalize()
    main = first.get("main")
    if isinstance(main, str) and main.strip():
        return main.strip()
    return None


def _get_weather_condition_id(weather_block: list | None) -> int | None:
    if not isinstance(weather_block, list) or not weather_block:
        return None
    first = weather_block[0] or {}
    condition_id = first.get("id")
    try:
        return int(condition_id) if condition_id is not None else None
    except (TypeError, ValueError):
        return None


def _extract_precip_amount(entry: dict, key: str) -> float:
    block = entry.get(key)
    if isinstance(block, dict):
        for amount_key in ("1h", "3h"):
            value = _coerce_float(block.get(amount_key))
            if value is not None:
                return value
    value = _coerce_float(block)
    return value if value is not None else 0.0


def _request_json_with_retries(url: str, params: dict, reject_message: str, label: str) -> dict:
    max_attempts = 3
    delay_seconds = 1.0
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (401, 403):
                raise ValueError(reject_message) from exc
            if status and 500 <= status < 600 and attempt < max_attempts:
                logger.warning(
                    "%s returned %s (attempt %s/%s). Retrying in %.1fs...",
                    label,
                    status,
                    attempt,
                    max_attempts,
                    delay_seconds,
                )
                last_exc = exc
                sleep(delay_seconds)
                delay_seconds *= 2
                continue
            logger.error("%s request failed: %s", label, exc)
            raise
        except requests.RequestException as exc:
            if attempt < max_attempts:
                logger.warning(
                    "%s network error (attempt %s/%s): %s. Retrying in %.1fs...",
                    label,
                    attempt,
                    max_attempts,
                    exc,
                    delay_seconds,
                )
                last_exc = exc
                sleep(delay_seconds)
                delay_seconds *= 2
                continue
            logger.error("%s request error: %s", label, exc)
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError(f"{label} request failed unexpectedly.")


def _build_from_onecall_payload(payload: dict, city: str | None, lat, lon, units: str) -> dict:
    current = payload.get("current") or {}
    hourly_data = payload.get("hourly") or []
    daily_data = payload.get("daily") or []
    timezone_offset_seconds = int(payload.get("timezone_offset") or 0)

    temperature_unit = "°C" if units == "metric" else ("°F" if units == "imperial" else "K")
    windspeed_unit = "m/s" if units != "imperial" else "mph"
    humidity_unit = "%"
    cloud_cover_unit = "%"
    precipitation_unit = "mm" if units != "imperial" else "in"

    current_iso = _to_local_iso(current.get("dt"), timezone_offset_seconds)
    current_date = current_iso.split("T")[0] if current_iso and "T" in current_iso else None
    current_dt = current.get("dt")

    current_condition_id = _get_weather_condition_id(current.get("weather"))
    current_weathercode = _map_openweathermap_to_wmo(current_condition_id)

    hourly_forecast = {
        "time": [],
        "date": [],
        "weather_code": [],
        "description": [],
        "temperature": [],
        "relative_humidity": [],
        "precipitation": [],
        "rain": [],
        "snowfall": [],
        "cloud_cover": [],
    }

    for hour_entry in hourly_data:
        if not isinstance(hour_entry, dict):
            continue
        hour_dt = hour_entry.get("dt")
        hour_iso = _to_local_iso(hour_dt, timezone_offset_seconds)
        if not hour_iso:
            continue
        hour_date = hour_iso.split("T")[0] if "T" in hour_iso else hour_iso

        if current_date and hour_date != current_date:
            continue
        if current_dt is not None and hour_dt is not None and hour_dt < current_dt:
            continue

        rain_value = _extract_precip_amount(hour_entry, "rain")
        snow_value = _extract_precip_amount(hour_entry, "snow")
        precipitation_value = rain_value + snow_value
        condition_id = _get_weather_condition_id(hour_entry.get("weather"))

        hourly_forecast["time"].append(hour_iso)
        hourly_forecast["date"].append(hour_date)
        hourly_forecast["weather_code"].append(_map_openweathermap_to_wmo(condition_id))
        hourly_forecast["description"].append(_get_weather_description(hour_entry.get("weather")))
        hourly_forecast["temperature"].append(
            _format_with_unit(hour_entry.get("temp"), temperature_unit)
        )
        hourly_forecast["relative_humidity"].append(
            _format_with_unit(hour_entry.get("humidity"), humidity_unit)
        )
        hourly_forecast["precipitation"].append(
            _format_with_unit(precipitation_value, precipitation_unit)
        )
        hourly_forecast["rain"].append(_format_with_unit(rain_value, precipitation_unit))
        hourly_forecast["snowfall"].append(_format_with_unit(snow_value, precipitation_unit))
        hourly_forecast["cloud_cover"].append(
            _format_with_unit(hour_entry.get("clouds"), cloud_cover_unit)
        )

    daily_forecast = {
        "date": [],
        "weather_code": [],
        "description": [],
        "temperature_daily_high": [],
        "temperature_daily_low": [],
        "sunrise": [],
        "sunset": [],
        "precipitation_sum": [],
        "rain_sum": [],
        "snowfall_sum": [],
        "windspeed_max": [],
    }

    for day_entry in daily_data:
        if not isinstance(day_entry, dict):
            continue
        day_iso = _to_local_iso(day_entry.get("dt"), timezone_offset_seconds)
        if not day_iso:
            continue
        day_date = day_iso.split("T")[0] if "T" in day_iso else day_iso

        if current_date and day_date == current_date:
            continue

        temp_data = day_entry.get("temp") or {}
        rain_sum = _extract_precip_amount(day_entry, "rain")
        snow_sum = _extract_precip_amount(day_entry, "snow")
        precipitation_sum = rain_sum + snow_sum
        condition_id = _get_weather_condition_id(day_entry.get("weather"))

        daily_forecast["date"].append(day_date)
        daily_forecast["weather_code"].append(_map_openweathermap_to_wmo(condition_id))
        daily_forecast["description"].append(_get_weather_description(day_entry.get("weather")))
        daily_forecast["temperature_daily_high"].append(
            _format_with_unit(temp_data.get("max"), temperature_unit)
        )
        daily_forecast["temperature_daily_low"].append(
            _format_with_unit(temp_data.get("min"), temperature_unit)
        )
        daily_forecast["sunrise"].append(
            _to_local_iso(day_entry.get("sunrise"), timezone_offset_seconds)
        )
        daily_forecast["sunset"].append(
            _to_local_iso(day_entry.get("sunset"), timezone_offset_seconds)
        )
        daily_forecast["precipitation_sum"].append(
            _format_with_unit(precipitation_sum, precipitation_unit)
        )
        daily_forecast["rain_sum"].append(_format_with_unit(rain_sum, precipitation_unit))
        daily_forecast["snowfall_sum"].append(_format_with_unit(snow_sum, precipitation_unit))
        daily_forecast["windspeed_max"].append(
            _format_with_unit(day_entry.get("wind_speed"), windspeed_unit)
        )

    return {
        "city": city or f"{lat},{lon}",
        "date": current_date,
        "time": current_iso,
        "timezone": payload.get("timezone"),
        "current_weather": {
            "description": _get_weather_description(current.get("weather")),
            "temperature": _format_with_unit(current.get("temp"), temperature_unit),
            "windspeed": _format_with_unit(current.get("wind_speed"), windspeed_unit),
            "weathercode": current_weathercode,
            "time": current_iso,
        },
        "forecast": {
            "hourly": hourly_forecast,
            "daily": daily_forecast,
        },
    }


def _build_from_free_payload(
    current_payload: dict,
    forecast_payload: dict,
    city: str | None,
    lat,
    lon,
    units: str,
) -> dict:
    current = current_payload or {}
    forecast = forecast_payload or {}
    forecast_list = forecast.get("list") or []
    city_meta = forecast.get("city") or {}

    timezone_offset_seconds = int(
        current.get("timezone")
        or city_meta.get("timezone")
        or 0
    )

    temperature_unit = "°C" if units == "metric" else ("°F" if units == "imperial" else "K")
    windspeed_unit = "m/s" if units != "imperial" else "mph"
    humidity_unit = "%"
    cloud_cover_unit = "%"
    precipitation_unit = "mm" if units != "imperial" else "in"

    current_dt = current.get("dt")
    current_iso = _to_local_iso(current_dt, timezone_offset_seconds)
    current_date = current_iso.split("T")[0] if current_iso and "T" in current_iso else None
    current_condition_id = _get_weather_condition_id(current.get("weather"))
    current_weathercode = _map_openweathermap_to_wmo(current_condition_id)

    hourly_forecast = {
        "time": [],
        "date": [],
        "weather_code": [],
        "description": [],
        "temperature": [],
        "relative_humidity": [],
        "precipitation": [],
        "rain": [],
        "snowfall": [],
        "cloud_cover": [],
    }

    for item in forecast_list:
        if not isinstance(item, dict):
            continue
        item_dt = item.get("dt")
        item_iso = _to_local_iso(item_dt, timezone_offset_seconds)
        if not item_iso:
            continue
        item_date = item_iso.split("T")[0] if "T" in item_iso else item_iso

        if current_date and item_date != current_date:
            continue
        if current_dt is not None and item_dt is not None and item_dt < current_dt:
            continue

        main = item.get("main") or {}
        rain_value = _extract_precip_amount(item, "rain")
        snow_value = _extract_precip_amount(item, "snow")
        precipitation_value = rain_value + snow_value
        condition_id = _get_weather_condition_id(item.get("weather"))

        hourly_forecast["time"].append(item_iso)
        hourly_forecast["date"].append(item_date)
        hourly_forecast["weather_code"].append(_map_openweathermap_to_wmo(condition_id))
        hourly_forecast["description"].append(_get_weather_description(item.get("weather")))
        hourly_forecast["temperature"].append(_format_with_unit(main.get("temp"), temperature_unit))
        hourly_forecast["relative_humidity"].append(
            _format_with_unit(main.get("humidity"), humidity_unit)
        )
        hourly_forecast["precipitation"].append(
            _format_with_unit(precipitation_value, precipitation_unit)
        )
        hourly_forecast["rain"].append(_format_with_unit(rain_value, precipitation_unit))
        hourly_forecast["snowfall"].append(_format_with_unit(snow_value, precipitation_unit))
        hourly_forecast["cloud_cover"].append(
            _format_with_unit((item.get("clouds") or {}).get("all"), cloud_cover_unit)
        )

    if not hourly_forecast["time"] and current_iso:
        main = current.get("main") or {}
        rain_value = _extract_precip_amount(current, "rain")
        snow_value = _extract_precip_amount(current, "snow")
        precipitation_value = rain_value + snow_value
        hourly_forecast["time"].append(current_iso)
        hourly_forecast["date"].append(current_date)
        hourly_forecast["weather_code"].append(current_weathercode)
        hourly_forecast["description"].append(_get_weather_description(current.get("weather")))
        hourly_forecast["temperature"].append(_format_with_unit(main.get("temp"), temperature_unit))
        hourly_forecast["relative_humidity"].append(
            _format_with_unit(main.get("humidity"), humidity_unit)
        )
        hourly_forecast["precipitation"].append(
            _format_with_unit(precipitation_value, precipitation_unit)
        )
        hourly_forecast["rain"].append(_format_with_unit(rain_value, precipitation_unit))
        hourly_forecast["snowfall"].append(_format_with_unit(snow_value, precipitation_unit))
        hourly_forecast["cloud_cover"].append(
            _format_with_unit((current.get("clouds") or {}).get("all"), cloud_cover_unit)
        )

    daily_groups = defaultdict(list)
    for item in forecast_list:
        if not isinstance(item, dict):
            continue
        item_dt = item.get("dt")
        local_dt = _to_local_datetime(item_dt, timezone_offset_seconds)
        if not local_dt:
            continue
        date_key = local_dt.date().isoformat()
        if current_date and date_key <= current_date:
            continue
        daily_groups[date_key].append(item)

    daily_forecast = {
        "date": [],
        "weather_code": [],
        "description": [],
        "temperature_daily_high": [],
        "temperature_daily_low": [],
        "sunrise": [],
        "sunset": [],
        "precipitation_sum": [],
        "rain_sum": [],
        "snowfall_sum": [],
        "windspeed_max": [],
    }

    for date_key in sorted(daily_groups.keys())[:7]:
        entries = sorted(
            daily_groups[date_key],
            key=lambda entry: entry.get("dt") or 0,
        )
        if not entries:
            continue

        high_candidates = []
        low_candidates = []
        rain_sum = 0.0
        snow_sum = 0.0
        wind_candidates = []

        for entry in entries:
            main = entry.get("main") or {}
            for key in ("temp_max", "temp"):
                value = _coerce_float(main.get(key))
                if value is not None:
                    high_candidates.append(value)
            for key in ("temp_min", "temp"):
                value = _coerce_float(main.get(key))
                if value is not None:
                    low_candidates.append(value)
            rain_sum += _extract_precip_amount(entry, "rain")
            snow_sum += _extract_precip_amount(entry, "snow")
            wind_value = _coerce_float((entry.get("wind") or {}).get("speed"))
            if wind_value is not None:
                wind_candidates.append(wind_value)

        selected_entry = min(
            entries,
            key=lambda entry: abs(
                (_to_local_datetime(entry.get("dt"), timezone_offset_seconds) or datetime.min).hour - 12
            ),
        )
        condition_id = _get_weather_condition_id(selected_entry.get("weather"))
        precipitation_sum = rain_sum + snow_sum
        temp_high = max(high_candidates) if high_candidates else None
        temp_low = min(low_candidates) if low_candidates else None
        wind_max = max(wind_candidates) if wind_candidates else None

        daily_forecast["date"].append(date_key)
        daily_forecast["weather_code"].append(_map_openweathermap_to_wmo(condition_id))
        daily_forecast["description"].append(_get_weather_description(selected_entry.get("weather")))
        daily_forecast["temperature_daily_high"].append(
            _format_with_unit(temp_high, temperature_unit)
        )
        daily_forecast["temperature_daily_low"].append(
            _format_with_unit(temp_low, temperature_unit)
        )
        daily_forecast["sunrise"].append(None)
        daily_forecast["sunset"].append(None)
        daily_forecast["precipitation_sum"].append(
            _format_with_unit(precipitation_sum, precipitation_unit)
        )
        daily_forecast["rain_sum"].append(_format_with_unit(rain_sum, precipitation_unit))
        daily_forecast["snowfall_sum"].append(_format_with_unit(snow_sum, precipitation_unit))
        daily_forecast["windspeed_max"].append(_format_with_unit(wind_max, windspeed_unit))

    return {
        "city": city or f"{lat},{lon}",
        "date": current_date,
        "time": current_iso,
        "timezone": city_meta.get("timezone"),
        "current_weather": {
            "description": _get_weather_description(current.get("weather")),
            "temperature": _format_with_unit((current.get("main") or {}).get("temp"), temperature_unit),
            "windspeed": _format_with_unit((current.get("wind") or {}).get("speed"), windspeed_unit),
            "weathercode": current_weathercode,
            "time": current_iso,
        },
        "forecast": {
            "hourly": hourly_forecast,
            "daily": daily_forecast,
        },
    }


def get_weather_openweathermap(
    lat,
    lon,
    api_key: str,
    city: str | None = None,
    units: str = "metric",
    language: str = "en",
    api_mode: str = "free",
):
    if not api_key or not str(api_key).strip():
        raise ValueError("OpenWeatherMap API key is required.")

    normalized_mode = (api_mode or "free").strip().lower()
    if normalized_mode not in OPENWEATHERMAP_API_MODES:
        normalized_mode = "free"

    common_params = {
        "lat": lat,
        "lon": lon,
        "appid": str(api_key).strip(),
        "units": units,
        "lang": language,
    }

    if normalized_mode == "onecall_3":
        onecall_params = {
            **common_params,
            "exclude": "minutely,alerts",
        }
        payload = _request_json_with_retries(
            OPENWEATHERMAP_ONECALL_URL,
            onecall_params,
            "OpenWeatherMap request was rejected. Check API key and One Call API access.",
            "OpenWeatherMap One Call 3.0",
        )
        structured = _build_from_onecall_payload(payload, city=city, lat=lat, lon=lon, units=units)
        return structured

    current_payload = _request_json_with_retries(
        OPENWEATHERMAP_FREE_CURRENT_URL,
        common_params,
        "OpenWeatherMap request was rejected. Check API key.",
        "OpenWeatherMap Current Weather",
    )
    forecast_payload = _request_json_with_retries(
        OPENWEATHERMAP_FREE_FORECAST_URL,
        common_params,
        "OpenWeatherMap request was rejected. Check API key.",
        "OpenWeatherMap 5-day Forecast",
    )
    structured = _build_from_free_payload(
        current_payload=current_payload,
        forecast_payload=forecast_payload,
        city=city,
        lat=lat,
        lon=lon,
        units=units,
    )
    return structured
