"""Schemas for the built-in weather tool settings."""

from typing import Literal

from app.utils.schemas import FieldSchema, Section, Sections
from pydantic import BaseModel, field_validator


class WeatherToolSettings(BaseModel):
    provider: Literal["open_meteo", "openweathermap"] = "open_meteo"
    geocoding_provider: Literal["open_meteo", "api_bdc"] = "open_meteo"
    openweathermap_api_mode: Literal["free", "onecall_3"] = "free"
    api_key: str = ""

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value):
        if value is None:
            return "open_meteo"
        normalized = str(value).strip().lower()
        if normalized not in {"open_meteo", "openweathermap"}:
            return "open_meteo"
        return normalized

    @field_validator("openweathermap_api_mode", mode="before")
    @classmethod
    def _normalize_openweathermap_api_mode(cls, value):
        if value is None:
            return "free"
        normalized = str(value).strip().lower()
        if normalized not in {"free", "onecall_3"}:
            return "free"
        return normalized

    @field_validator("geocoding_provider", mode="before")
    @classmethod
    def _normalize_geocoding_provider(cls, value):
        if value is None:
            return "open_meteo"
        normalized = str(value).strip().lower()
        if normalized not in {"open_meteo", "api_bdc"}:
            return "open_meteo"
        return normalized

    @field_validator("api_key", mode="before")
    @classmethod
    def _normalize_api_key(cls, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()


weather_tool_schema = Sections(
    sections=[
        Section(
            title="Weather Data Provider",
            description="Select which weather data provider to use for fetching weather forecasts.",
            i18n_title="schema_weather_tool_sec0_title",
            i18n_description="schema_weather_tool_sec0_desc",
            fields=[
                FieldSchema(
                    key="provider",
                    label="Weather Provider",
                    description="Choose the provider for fetching weather data.",
                    type="select",
                    options=[
                        {
                            "value": "open_meteo",
                            "label": "Open-Meteo (Free, no API key required)",
                        },
                        {
                            "value": "openweathermap",
                            "label": "OpenWeatherMap (API key required)",
                        },
                    ],
                    i18n_label="schema_weather_tool_provider",
                    i18n_description="schema_weather_tool_provider_desc",
                ),
                FieldSchema(
                    key="geocoding_provider",
                    label="Geocoding Provider",
                    description="Resolve user input or coordinates into weather location coordinates.",
                    type="select",
                    options=[
                        {"value": "open_meteo", "label": "Open-Meteo Geocoding"},
                        {
                            "value": "api_bdc",
                            "label": "api-bdc Reverse Geocoding (coordinates-aware)",
                        },
                    ],
                    i18n_label="schema_weather_tool_geocoding_provider",
                    i18n_description="schema_weather_tool_geocoding_provider_desc",
                ),
                FieldSchema(
                    key="api_key",
                    label="OpenWeatherMap API Key",
                    description="Required when provider is set to OpenWeatherMap.",
                    type="string",
                    input_type="password",
                    placeholder="Enter OpenWeatherMap API key",
                    dependency="provider",
                    dependency_value="openweathermap",
                    i18n_label="schema_weather_tool_api_key",
                    i18n_description="schema_weather_tool_api_key_desc",
                ),
                FieldSchema(
                    key="openweathermap_api_mode",
                    label="OpenWeatherMap API Mode",
                    description="Choose between the free forecast API and the One Call 3.0 API.",
                    type="select",
                    options=[
                        {
                            "value": "free",
                            "label": "Free API (Current + 5-day/3-hour forecast)",
                        },
                        {
                            "value": "onecall_3",
                            "label": "One Call 3.0 (Hourly + Daily)",
                        },
                    ],
                    dependency="provider",
                    dependency_value="openweathermap",
                    i18n_label="schema_weather_tool_openweathermap_api_mode",
                    i18n_description="schema_weather_tool_openweathermap_api_mode_desc",
                ),
            ],
        )
    ]
)
