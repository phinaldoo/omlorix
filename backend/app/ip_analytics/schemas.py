import ipaddress
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Literal

from pydantic import BaseModel, ConfigDict, Field, conint, constr, field_validator

# Reuse the application's existing ISO 3166-1 dataset so security policies
# reject well-formed but nonexistent values such as "ZZ", not only values with
# the wrong number of characters.
_ISO_COUNTRY_DATA_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "websearch"
    / "iso_3166_1_countries.json"
)
ISO_3166_1_ALPHA_2_COUNTRY_CODES = frozenset(
    json.loads(_ISO_COUNTRY_DATA_PATH.read_text(encoding="utf-8")).keys()
)


def _normalize_ip_address_value(value: str) -> str:
    """Validate and canonicalize one exact visitor IP address."""
    raw_value = str(value or "").strip()
    if not raw_value:
        raise ValueError("Enter a non-empty IP address.")
    if raw_value.lower() == "localhost":
        return "127.0.0.1"
    if "%" in raw_value:
        # Scoped IPv6 addresses are tied to a local network interface and are not
        # meaningful as remote visitor policy values.
        raise ValueError("IPv6 zone identifiers are not supported.")
    try:
        return ipaddress.ip_address(raw_value).compressed
    except ValueError as exc:
        raise ValueError("Enter a valid IPv4 or IPv6 address.") from exc


def _normalize_ip_address_list(values: Any) -> list[str]:
    """Validate, canonicalize, and de-duplicate an exact-IP policy list."""
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("Expected a list of IP addresses.")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        ip_value = _normalize_ip_address_value(str(item))
        if ip_value in seen:
            continue
        seen.add(ip_value)
        normalized.append(ip_value)
    return normalized


def _normalize_country_code_list(values: Any) -> list[str]:
    """Validate and canonicalize ISO 3166-1 alpha-2 country code lists."""
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("Expected a list of country codes.")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        raw_code = str(item or "").strip()
        code = raw_code.upper()
        if code not in ISO_3166_1_ALPHA_2_COUNTRY_CODES:
            invalid_value = raw_code or "(empty)"
            raise ValueError(
                f"Invalid country code: {invalid_value}. "
                "Use a two-letter ISO 3166-1 code such as DE or US."
            )
        if code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    return normalized


def _normalize_trusted_proxy_list(values: Any) -> list[str]:
    """Validate and canonicalize trusted proxy IP/CIDR entries."""
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("Expected a list of trusted proxy addresses or CIDRs.")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        raw_value = str(item or "").strip()
        if not raw_value:
            raise ValueError("Trusted proxy entries cannot be empty.")
        if "%" in raw_value:
            raise ValueError("IPv6 zone identifiers are not supported.")
        try:
            network = ipaddress.ip_network(raw_value, strict=False)
        except ValueError as exc:
            raise ValueError(
                "Trusted proxies must be valid IP addresses or CIDR ranges."
            ) from exc
        network_value = str(network)
        if network_value in seen:
            continue
        seen.add(network_value)
        normalized.append(network_value)
    return normalized


# -------------------
# Admin: User Identifier
# -------------------
# Block IP
# -------------------
class BlockIP(BaseModel):
    ip_address: constr(min_length=1, max_length=45)
    banned: bool = True
    duration_days: conint(ge=1, le=365) = 30
    reason: constr(min_length=1, max_length=255) = "Banned by admin"

    @field_validator("ip_address")
    @classmethod
    def normalize_ip_address(cls, value: str) -> str:
        """Validate and canonicalize admin-managed blocked IP addresses."""
        return _normalize_ip_address_value(value)


class EditIPBlock(BaseModel):
    ip_address: constr(min_length=1, max_length=45)
    duration_days: conint(ge=1, le=365) = 30
    reason: constr(min_length=1, max_length=255)

    @field_validator("ip_address")
    @classmethod
    def normalize_ip_address(cls, value: str) -> str:
        """Validate and canonicalize an edited admin-managed IP ban address."""
        return _normalize_ip_address_value(value)


# -------------------
# Blocked IP Entry
# -------------------
class AdminBlockedIP(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ip_address: str
    blocked_at: datetime | None = None
    expires_at: datetime | None = None
    reason: str | None = None
    country_code: str | None = None
    blocked_attempt_count: int = 0
    last_blocked_attempt_at: datetime | None = None

class AdminBlockedIPPage(BaseModel):
    items: list[AdminBlockedIP] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    per_page: int = 50
    total_pages: int = 1


class AdminIPAddressStatisticsSettings(BaseModel):
    enabled: bool = False
    regulatory_confirmed: bool = False
    regulatory_justification: str = ""
    policy_reference: str = ""
    retention_policy: str = ""
    retention_days: int = Field(default=90, ge=1, le=3650)
    geo_provider: str | None = None
    geo_provider_configured: bool = False


class AdminIPAddressStatisticsSettingsUpdate(BaseModel):
    enabled: bool | None = None
    regulatory_confirmed: bool | None = None
    regulatory_justification: str | None = None
    policy_reference: str | None = None
    retention_policy: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)


IPAddressEventType = Literal[
    "ban_created",
    "request_denied",
    "ban_removed",
    "rate_limited",
]
IPAddressActivityLevel = Literal["low", "medium", "high"]


class AdminIPAddressStatisticsSummary(BaseModel):
    """Typed headline values for the selected analytics period."""

    active_bans: int = 0
    known_origin_countries: int = 0
    denied_requests: int = 0
    rate_limited_requests: int = 0
    manual_bans_created: int = 0
    automatic_bans_created: int = 0
    unique_ips: int = 0
    denied_distinct_ips: int = 0
    unresolved_ips: int = 0
    top_country_code: str | None = None
    top_country_denied_requests: int = 0
    top_country_distinct_ips: int = 0
    denied_requests_per_ip: float = 0.0


class AdminIPAddressCountrySummary(BaseModel):
    country_code: str | None = None
    denied_requests: int = 0
    rate_limited_requests: int = 0
    manual_bans_created: int = 0
    automatic_bans_created: int = 0
    distinct_ips: int = 0
    denied_distinct_ips: int = 0
    stored_rows: int = 0
    share_of_denied_requests: float = 0.0
    denied_requests_per_ip: float = 0.0
    last_seen_at: datetime | None = None
    activity_level: IPAddressActivityLevel = "low"


class AdminIPAddressSecurityEvent(BaseModel):
    """One lifecycle event or aggregated request-event bucket."""

    id: str
    ip_address: str
    country_code: str | None = None
    event_type: IPAddressEventType
    event_source: str | None = None
    reason_code: str | None = None
    route_category: str | None = None
    reason: str | None = None
    request_count: int = 1
    is_automatic: bool = False
    created_at: datetime
    last_seen_at: datetime
    bucket_start: datetime | None = None
    geo_provider: str | None = None
    geo_lookup_status: str = "pending"
    country_resolved_at: datetime | None = None


class AdminIPAddressSecurityEventPage(BaseModel):
    items: list[AdminIPAddressSecurityEvent] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    per_page: int = 25
    total_pages: int = 1


class AdminIPAddressStatisticsFilterOptions(BaseModel):
    event_types: list[IPAddressEventType] = Field(default_factory=list)
    event_sources: list[str] = Field(default_factory=list)
    country_codes: list[str] = Field(default_factory=list)


class AdminIPAddressStatisticsProvider(BaseModel):
    configured: bool = False
    provider: str | None = None
    status: Literal["configured", "missing", "disabled"] = "missing"
    sends_ip_to_external_provider: bool = False


class AdminIPAddressStatisticsDeleteRequest(BaseModel):
    days: int | None = Field(default=None, ge=1, le=3650)
    ip_address: str | None = Field(default=None, min_length=1, max_length=45)

    @field_validator("ip_address")
    @classmethod
    def normalize_optional_ip_address(cls, value: str | None) -> str | None:
        """Validate an optional targeted deletion address."""
        return _normalize_ip_address_value(value) if value else None


class AdminIPAddressStatisticsMutationResult(BaseModel):
    status: Literal["success"] = "success"
    affected_rows: int = 0


class AdminIPAddressStatisticsImportResult(AdminIPAddressStatisticsMutationResult):
    imported_rows: int = 0
    skipped_rows: int = 0


class AdminIPAddressStatisticsOverview(BaseModel):
    enabled: bool = False
    regulatory_confirmed: bool = False
    period_days: int = 30
    period_start_utc: datetime
    period_end_utc: datetime
    retention_days: int = 90
    retention_cutoff_utc: datetime
    period_truncated_by_retention: bool = False
    countries_truncated: bool = False
    country_total: int = 0
    definitions: Dict[str, str] = Field(default_factory=dict)
    provider: AdminIPAddressStatisticsProvider
    summary: AdminIPAddressStatisticsSummary
    countries: list[AdminIPAddressCountrySummary] = Field(default_factory=list)
    
