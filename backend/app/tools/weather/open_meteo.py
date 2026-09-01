import logging
from datetime import datetime
from time import sleep

import requests


logger = logging.getLogger(__name__)

def get_weather_open_meteo(lat, lon, timezone="auto", city=None):
    if timezone and timezone != "auto" and "/" not in timezone:
        if city is None:
            city = timezone
        timezone = "auto"
    url = "https://api.open-meteo.com/v1/forecast"

    hourly_vars = [
        "temperature_2m", "relative_humidity_2m",
        "precipitation", "rain", "snowfall", "weather_code",
        "cloud_cover",
    ]

    daily_vars = [
        "weather_code", "temperature_2m_max", "temperature_2m_min",
        "sunrise", "sunset",
        "precipitation_sum", "rain_sum", "snowfall_sum", "windspeed_10m_max",
    ]

    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        "hourly": ",".join(hourly_vars),
        "daily": ",".join(daily_vars),
        "timezone": timezone
    }

    max_attempts = 3
    delay_seconds = 1.0
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            break
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status and 500 <= status < 600 and attempt < max_attempts:
                logger.warning(
                    "Open-Meteo %s for %s,%s (attempt %s/%s). Retrying in %.1fs...",
                    status,
                    lat,
                    lon,
                    attempt,
                    max_attempts,
                    delay_seconds,
                )
                last_exc = exc
                sleep(delay_seconds)
                delay_seconds *= 2
                continue
            logger.error("Open-Meteo request failed: %s", exc)
            raise
        except requests.RequestException as exc:
            if attempt < max_attempts:
                logger.warning(
                    "Open-Meteo network error for %s,%s (attempt %s/%s): %s. Retrying in %.1fs...",
                    lat,
                    lon,
                    attempt,
                    max_attempts,
                    exc,
                    delay_seconds,
                )
                last_exc = exc
                sleep(delay_seconds)
                delay_seconds *= 2
                continue
            logger.error("Open-Meteo request error: %s", exc)
            raise
    else:
        # Should not reach here because loop breaks on success or raises on final failure
        if last_exc:
            raise last_exc

    payload = response.json()

    def format_with_unit(value, unit):
        if value is None:
            return None
        if unit:
            return f"{value}{unit}"
        return value

    current_weather = payload.get("current_weather", {})
    current_units = payload.get("current_weather_units", {})
    current_time_iso = current_weather.get("time")

    current_date = None
    current_time = None
    if current_time_iso:
        try:
            dt = datetime.fromisoformat(current_time_iso)
            current_date = dt.date().isoformat()
            current_time = dt.time().isoformat(timespec="minutes")
        except ValueError:
            if "T" in current_time_iso:
                current_date, current_time = current_time_iso.split("T", 1)
            else:
                current_date = current_time_iso

    def parse_iso(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None

    current_dt = parse_iso(current_time_iso)

    hourly_units = payload.get("hourly_units", {})
    hourly_data = payload.get("hourly", {})
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

    hourly_iterables = [
        hourly_data.get("time", []),
        hourly_data.get("temperature_2m", []),
        hourly_data.get("relative_humidity_2m", []),
        hourly_data.get("precipitation", []),
        hourly_data.get("rain", []),
        hourly_data.get("snowfall", []),
        hourly_data.get("weather_code", []),
        hourly_data.get("cloud_cover", []),
    ]

    for items in zip(*hourly_iterables):
        (
            timestamp,
            temp,
            humidity,
            precipitation,
            rain,
            snowfall,
            weather_code,
            cloud_cover,
        ) = items

        description = wmo_code_label(weather_code) if weather_code is not None else None
        date_entry = timestamp.split("T")[0] if isinstance(timestamp, str) and "T" in timestamp else timestamp

        timestamp_dt = parse_iso(timestamp)
        if current_date and date_entry and date_entry != current_date:
            continue
        if current_dt and timestamp_dt and timestamp_dt < current_dt:
            continue

        hourly_forecast["time"].append(timestamp)
        hourly_forecast["date"].append(date_entry)
        hourly_forecast["weather_code"].append(weather_code)
        hourly_forecast["description"].append(description)
        hourly_forecast["temperature"].append(format_with_unit(temp, hourly_units.get("temperature_2m")))
        hourly_forecast["relative_humidity"].append(format_with_unit(humidity, hourly_units.get("relative_humidity_2m")))
        hourly_forecast["precipitation"].append(format_with_unit(precipitation, hourly_units.get("precipitation")))
        hourly_forecast["rain"].append(format_with_unit(rain, hourly_units.get("rain")))
        hourly_forecast["snowfall"].append(format_with_unit(snowfall, hourly_units.get("snowfall")))
        hourly_forecast["cloud_cover"].append(format_with_unit(cloud_cover, hourly_units.get("cloud_cover")))

    daily_units = payload.get("daily_units", {})
    daily_data = payload.get("daily", {})
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

    daily_iterables = [
        daily_data.get("time", []),
        daily_data.get("weather_code", []),
        daily_data.get("temperature_2m_max", []),
        daily_data.get("temperature_2m_min", []),
        daily_data.get("sunrise", []),
        daily_data.get("sunset", []),
        daily_data.get("precipitation_sum", []),
        daily_data.get("rain_sum", []),
        daily_data.get("snowfall_sum", []),
        daily_data.get("windspeed_10m_max", []),
    ]

    for items in zip(*daily_iterables):
        (
            date_entry,
            weather_code,
            temp_high,
            temp_low,
            sunrise,
            sunset,
            precipitation_sum,
            rain_sum,
            snowfall_sum,
            windspeed_max,
        ) = items

        description = wmo_code_label(weather_code) if weather_code is not None else None

        if current_date and date_entry == current_date:
            continue

        daily_forecast["date"].append(date_entry)
        daily_forecast["weather_code"].append(weather_code)
        daily_forecast["description"].append(description)
        daily_forecast["temperature_daily_high"].append(format_with_unit(temp_high, daily_units.get("temperature_2m_max")))
        daily_forecast["temperature_daily_low"].append(format_with_unit(temp_low, daily_units.get("temperature_2m_min")))
        daily_forecast["sunrise"].append(sunrise)
        daily_forecast["sunset"].append(sunset)
        daily_forecast["precipitation_sum"].append(format_with_unit(precipitation_sum, daily_units.get("precipitation_sum")))
        daily_forecast["rain_sum"].append(format_with_unit(rain_sum, daily_units.get("rain_sum")))
        daily_forecast["snowfall_sum"].append(format_with_unit(snowfall_sum, daily_units.get("snowfall_sum")))
        daily_forecast["windspeed_max"].append(format_with_unit(windspeed_max, daily_units.get("windspeed_10m_max")))

    weather_code_current = current_weather.get("weathercode")
    structured = {
        "city": city or f"{lat},{lon}",
        "date": current_date,
        "time": current_time_iso,
        "timezone": payload.get("timezone", timezone),
        "current_weather": {
            "description": wmo_code_label(weather_code_current) if weather_code_current is not None else None,
            "temperature": format_with_unit(current_weather.get("temperature"), current_units.get("temperature")),
            "windspeed": format_with_unit(current_weather.get("windspeed"), current_units.get("windspeed")),
            "weathercode": weather_code_current,
            "time": current_time_iso,
        },
        "forecast": {
            "hourly": hourly_forecast,
            "daily": daily_forecast,
        },
    }

    return structured



def wmo_code_label(code: int) -> str:
    mapping = {
        0: "Clear sky ☀️",
        1: "Mainly clear 🌤️",
        2: "Partly cloudy ⛅",
        3: "Overcast ☁️",
        45: "Fog 🌫️",
        48: "Depositing rime fog 🌫️",
        51: "Light drizzle 🌦️",
        53: "Moderate drizzle 🌦️",
        55: "Dense drizzle 🌧️",
        61: "Slight rain 🌧️",
        63: "Moderate rain 🌧️",
        65: "Heavy rain 🌧️",
        66: "Light freezing rain 🥶🌧️",
        67: "Heavy freezing rain 🥶🌧️",
        71: "Slight snow fall ❄️",
        73: "Moderate snow fall ❄️",
        75: "Heavy snow fall ❄️",
        77: "Snow grains ❄️",
        80: "Rain showers 🌦️",
        81: "Moderate rain showers 🌦️",
        82: "Violent rain showers 🌧️",
        85: "Slight snow showers ❄️",
        86: "Heavy snow showers ❄️",
        95: "Thunderstorm ⛈️",
        96: "Thunderstorm with slight hail ⛈️",
        99: "Thunderstorm with heavy hail ⛈️",
    }
    return mapping.get(int(code), f"Code {code}")
