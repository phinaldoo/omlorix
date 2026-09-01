from fastapi import HTTPException, status, UploadFile, File
from sqlalchemy.engine import make_url
from sqlalchemy.orm.attributes import flag_modified
from tempfile import SpooledTemporaryFile
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from functools import lru_cache
from contextlib import suppress
from typing import Any, Optional, cast
from pathlib import Path
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from copy import deepcopy
import weakref
import uuid
import logging
import hashlib
import os
import re
import base64
import binascii
import xml.etree.ElementTree as ET

from app.auth.ldap_transport import (
    LDAP_TRANSPORT_SECURITY_ADMIN_ERROR_DETAIL,
    get_ldap_transport_security_policy,
)
from app.database import DATABASE_SCHEMA, DATABASE_URL
from app.groups.init import get_user_group_setting_value
from app.paths import DATA_DIR
from app.redis_client import get_redis_client, redis_get_json, redis_set_json
from app.settings.models import (
    SENSITIVE_SETTING_RESPONSE_MASK,
    ensure_sensitive_settings_page_encrypted,
    is_sensitive_setting_key,
    mask_sensitive_settings_page_data,
    preserve_masked_sensitive_settings_page_data,
    Settings,
    get_settings_page_data,
    get_settings_value_from_page_data,
    get_settings_page,
)
from app.settings.schemas import PageSettings
from app.settings.public_urls import normalize_public_url, normalize_public_urls, primary_public_url
from app.settings.validation import validate_settings_page_values
from app.users.models import get_user
from app.users.roles import is_admin_role
from app.utils.svg import rasterize_svg_to_png_bytes



logger = logging.getLogger(__name__)


_ALLOWED_IMAGE_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/svg+xml",
    }
)
_ALLOWED_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "svg"})
_ALLOWED_RASTER_IMAGE_FORMATS = {
    "PNG": "png",
    "JPEG": "jpeg",
    "WEBP": "webp",
}
ET.register_namespace("", "http://www.w3.org/2000/svg")
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_XML_NS = "http://www.w3.org/XML/1998/namespace"

_SAFE_EMBEDDED_SVG_IMAGE_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/webp",
        "image/gif",
        "image/bmp",
    }
)
_SANITIZED_ICON_SVG_BLOCKLIST = re.compile(
    r"<\s*(script|foreignobject|iframe|object|embed|link|meta|base)|"
    r"<!\s*(doctype|entity)|"
    r"\son[a-z0-9_-]+\s*=|"
    r"\b(?:javascript|vbscript)\s*:|"
    r"\bexpression\s*\(|"
    r"<\?\s*xml-stylesheet",
    re.IGNORECASE,
)
_SANITIZED_BRANDING_SVG_ALLOWED_TAGS = frozenset(
    {
        "svg",
        "g",
        "path",
        "circle",
        "rect",
        "line",
        "polyline",
        "polygon",
        "ellipse",
        "title",
        "desc",
        "defs",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "image",
        "use",
    }
)
_SANITIZED_BRANDING_SVG_GLOBAL_ATTRIBUTES = frozenset(
    {
        "id",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-dasharray",
        "stroke-dashoffset",
        "fill-rule",
        "clip-rule",
        "opacity",
        "transform",
        "clip-path",
        "style",
        "display",
        "role",
        "focusable",
        "aria-hidden",
    }
)
_SANITIZED_BRANDING_SVG_TAG_ATTRIBUTES: dict[str, frozenset[str]] = {
    "svg": frozenset({"viewBox", "width", "height", "preserveAspectRatio", "version"}),
    "path": frozenset({"d"}),
    "circle": frozenset({"cx", "cy", "r"}),
    "rect": frozenset({"x", "y", "width", "height", "rx", "ry"}),
    "line": frozenset({"x1", "y1", "x2", "y2"}),
    "polyline": frozenset({"points"}),
    "polygon": frozenset({"points"}),
    "ellipse": frozenset({"cx", "cy", "rx", "ry"}),
    "linearGradient": frozenset({"x1", "x2", "y1", "y2", "gradientUnits"}),
    "radialGradient": frozenset({"cx", "cy", "r", "fx", "fy", "gradientUnits"}),
    "stop": frozenset({"offset", "stop-color", "stop-opacity"}),
    "image": frozenset({"x", "y", "width", "height", "href"}),
    "use": frozenset({"x", "y", "width", "height", "href"}),
    "title": frozenset({"lang"}),
    "desc": frozenset({"lang"}),
}
_SANITIZED_BRANDING_SVG_STYLE_PROPERTIES = frozenset(
    {
        "fill",
        "fill-rule",
        "clip-rule",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-dasharray",
        "stroke-dashoffset",
        "opacity",
        "clip-path",
        "stop-color",
        "stop-opacity",
        "display",
    }
)
_SAFE_SVG_FRAGMENT_REFERENCE_RE = re.compile(r"^#[-_A-Za-z][-\w:.]*$")
_SAFE_SVG_URL_REFERENCE_RE = re.compile(r"^url\(\s*#[-_A-Za-z][-\w:.]*\s*\)$")
_SAFE_DATA_IMAGE_RE = re.compile(
    r"^data:(image/[a-z0-9.+-]+);base64,([a-z0-9+/=\s]+)$",
    re.IGNORECASE | re.DOTALL,
)


def _is_dev_mode() -> bool:
    """Check if the application is running in dev mode."""
    return str(os.getenv("MODE", "production") or "production").strip().lower() == "dev"


def validate_and_normalize_public_url(value: str | None, *, allow_empty: bool = False) -> str:
    """Validate and normalize one public URL for legacy scalar callers."""
    if allow_empty and (value is None or value == ""):
        return ""
    try:
        return normalize_public_url(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def validate_and_normalize_public_urls(value: Any, *, allow_empty: bool = False) -> list[str]:
    """Validate, normalize, and de-duplicate configured public URLs."""
    try:
        return normalize_public_urls(value, allow_empty=allow_empty)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _validate_application_name(value: str | None) -> str:
    """Validate application name."""
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Application name is required")
    if len(normalized) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Application name must be 50 characters or fewer.",
        )
    return normalized


def validate_public_url_requirements(db: Session) -> None:
    """Validate required public URLs against authoritative database values.

    Startup safety checks must not depend on Redis or process-local caches. A
    stale cache entry would otherwise turn a valid database configuration into
    a crash loop before an administrator could reach the settings interface.
    """
    public_url_raw = _load_setting_value(db, "general", "public_url")

    password_reset_enabled = coerce_bool(
        _load_setting_value(db, "login_general", "enable_password_reset"),
        default=False,
    )

    social_data = get_settings_page_data(db, "login_social")
    any_social_provider_enabled = False
    for oauth_key, login_key in (
        ("enable_google_oauth", "enable_google_login"),
        ("enable_github_oauth", "enable_github_login"),
        ("enable_slack_oauth", "enable_slack_login"),
        ("enable_microsoft_oauth", "enable_microsoft_login"),
    ):
        if coerce_bool(social_data.get(oauth_key), default=False) and coerce_bool(social_data.get(login_key), default=False):
            any_social_provider_enabled = True
            break
    if coerce_bool(social_data.get("enable_apple_login"), default=False):
        any_social_provider_enabled = True

    sso_data = get_settings_page_data(db, "login_enterprise_sso")
    any_enterprise_provider_enabled = False
    for key in (
        "enable_saml",
        "enable_oidc",
    ):
        if coerce_bool(sso_data.get(key), default=False):
            any_enterprise_provider_enabled = True
            break

    requires_public_url = bool(
        password_reset_enabled
        or any_social_provider_enabled
        or any_enterprise_provider_enabled
    )

    if not requires_public_url:
        return

    if not public_url_raw:
        raise RuntimeError(
            "general.public_url must be configured when password reset, social login, or enterprise SSO is enabled."
        )

    try:
        validate_and_normalize_public_urls(public_url_raw)
    except HTTPException as exc:
        raise RuntimeError(str(exc.detail)) from exc


def validate_ldap_sync_requirements(db: Session) -> None:
    """Validate LDAP sync configuration requirements."""
    ldap_settings = get_settings_page_data(db, "login_ldap")
    ldap_enabled = coerce_bool(ldap_settings.get("enable_ldap"), default=False)
    group_sync_enabled = coerce_bool(ldap_settings.get("ldap_enable_group_sync"), default=False)
    sync_app_group_on_login = coerce_bool(ldap_settings.get("ldap_sync_app_group_on_login"), default=False)
    sync_role_on_login = coerce_bool(ldap_settings.get("ldap_sync_role_on_login"), default=False)

    if not ldap_enabled:
        if group_sync_enabled or sync_app_group_on_login or sync_role_on_login:
            logger.warning(
                "LDAP group synchronization settings are configured but login_ldap.enable_ldap is disabled; "
                "enable LDAP sign-in before these sync options can apply."
            )
        return

    if not group_sync_enabled and (sync_app_group_on_login or sync_role_on_login):
        logger.warning(
            "login_ldap.ldap_sync_app_group_on_login / login_ldap.ldap_sync_role_on_login are enabled while "
            "login_ldap.ldap_enable_group_sync is disabled; group and role sync will not run until "
            "ldap_enable_group_sync is enabled."
        )

    if group_sync_enabled and (not sync_app_group_on_login or not sync_role_on_login):
        disabled_targets = []
        if not sync_app_group_on_login:
            disabled_targets.append("ldap_sync_app_group_on_login")
        if not sync_role_on_login:
            disabled_targets.append("ldap_sync_role_on_login")
        logger.warning(
            "LDAP group synchronization is enabled, but %s is set to false. Action required: enable these "
            "settings if you expect LDAP login to keep Omlorix groups and roles synchronized.",
            ", ".join(disabled_targets),
        )


def _has_configured_values(raw_values: Any) -> bool:
    if not isinstance(raw_values, (list, tuple, set)):
        raw_values = [raw_values]
    return any(str(value or "").strip() for value in raw_values)


def validate_ip_restriction_requirements(db: Session) -> None:
    """Warn about IP restriction settings that cannot enforce a usable policy."""
    security_settings = get_settings_page_data(db, "security")
    enabled = coerce_bool(security_settings.get("enable_ip_restrictions"), default=False)
    legacy_specific_allow_enabled = coerce_bool(security_settings.get("only_allow_specific_ip"), default=False)
    legacy_country_allow_enabled = coerce_bool(
        security_settings.get("only_allow_ip_from_specific_countries"),
        default=False,
    )
    has_ip_allowlist = _has_configured_values(security_settings.get("allow_specific_ip"))
    has_ip_blocklist = _has_configured_values(security_settings.get("block_specific_ip"))
    has_country_allowlist = _has_configured_values(security_settings.get("allow_country_ip"))
    has_country_blocklist = _has_configured_values(security_settings.get("block_country_ip"))
    legacy_exact_rules_enabled = bool(legacy_specific_allow_enabled or has_ip_allowlist or has_ip_blocklist)
    legacy_country_rules_enabled = bool(legacy_country_allow_enabled or has_country_allowlist or has_country_blocklist)
    exact_rules_enabled = (
        coerce_bool(security_settings.get("enable_ip_address_restrictions"), default=False)
        or legacy_exact_rules_enabled
    )
    country_rules_enabled = (
        coerce_bool(security_settings.get("enable_ip_country_restrictions"), default=False)
        or legacy_country_rules_enabled
    )
    exact_mode = str(security_settings.get("ip_address_restriction_mode") or "").strip().lower()
    country_mode = str(security_settings.get("ip_country_restriction_mode") or "").strip().lower()
    if exact_mode not in {"allowlist", "blocklist"}:
        exact_mode = "allowlist" if legacy_specific_allow_enabled and has_ip_allowlist else "blocklist"
    if country_mode not in {"allowlist", "blocklist"}:
        country_mode = "allowlist" if legacy_country_allow_enabled and has_country_allowlist else "blocklist"
    specific_allow_enabled = exact_rules_enabled and exact_mode == "allowlist"
    country_allow_enabled = country_rules_enabled and country_mode == "allowlist"

    if not enabled:
        return

    if (specific_allow_enabled or legacy_specific_allow_enabled) and not has_ip_allowlist:
        logger.warning(
            "security.only_allow_specific_ip is true but security.allow_specific_ip is empty; "
            "the specific-IP allow policy will not be enforced until at least one IP is configured."
        )

    if (country_allow_enabled or legacy_country_allow_enabled) and not has_country_allowlist:
        logger.warning(
            "security.only_allow_ip_from_specific_countries is true but security.allow_country_ip is empty; "
            "the country allow policy will not be enforced until at least one country is configured."
        )

    has_active_policy = bool(
        (specific_allow_enabled and has_ip_allowlist)
        or (exact_rules_enabled and exact_mode == "blocklist" and has_ip_blocklist)
        or (country_allow_enabled and has_country_allowlist)
        or (country_rules_enabled and country_mode == "blocklist" and has_country_blocklist)
    )
    if not has_active_policy:
        logger.warning(
            "security.enable_ip_restrictions is true, but no IP or country allow/block policy is configured; "
            "requests will not be blocked by IP restrictions until at least one policy list is populated."
        )


def validate_ip_address_statistics_requirements(db: Session) -> None:
    """Validate IP address statistics configuration requirements."""
    ip_stats_settings = get_settings_page_data(db, "ip_address_statistics")
    enabled = coerce_bool(ip_stats_settings.get("enabled", False), default=False)
    
    if not enabled:
        return
    
    regulatory_confirmed = coerce_bool(ip_stats_settings.get("regulatory_confirmed", False), default=False)
    regulatory_justification = str(ip_stats_settings.get("regulatory_justification", "")).strip()
    policy_reference = str(ip_stats_settings.get("policy_reference", "")).strip()
    
    if not regulatory_confirmed:
        raise RuntimeError(
            "ip_address_statistics.enabled is true but regulatory_confirmed is false. "
            "Set regulatory_confirmed to true and provide regulatory_justification or policy_reference "
            "to enable IP address statistics collection in compliance with data protection regulations."
        )
    
    if not regulatory_justification and not policy_reference:
        raise RuntimeError(
            "ip_address_statistics.enabled is true but no regulatory documentation is provided. "
            "Provide either regulatory_justification or policy_reference to document the legal basis "
            "for IP address statistics collection."
        )


def is_password_reset_ready(db: Session) -> bool:
    """Check if password reset is properly configured."""
    if not coerce_bool(get_value_by_page_and_key("login_general", "enable_password_reset", db), default=False):
        return False

    public_url_raw = get_value_by_page_and_key("general", "public_url", db)
    try:
        validate_and_normalize_public_urls(public_url_raw)
    except HTTPException:
        return False

    from app.auth.email_delivery import is_email_delivery_config_ready, load_login_email_delivery_config

    return is_email_delivery_config_ready(load_login_email_delivery_config(db))


def is_twofa_email_ready(db: Session) -> bool:
    """Check if 2FA email is properly configured."""
    if not coerce_bool(get_value_by_page_and_key("login_general", "enable_2fa", db), default=True):
        return False
    provider = str(get_value_by_page_and_key("login_general", "twofa_provider", db) or "totp").strip().lower()
    if provider != "email":
        return True

    from app.auth.email_delivery import is_email_delivery_config_ready, load_login_email_delivery_config

    return is_email_delivery_config_ready(load_login_email_delivery_config(db))


def _normalize_public_url(value: Any) -> str:
    """Return the primary sanitized public URL with a development fallback."""
    try:
        return primary_public_url(value)
    except ValueError:
        return _DEFAULT_PUBLIC_URL


def get_public_urls(db: Session) -> list[str]:
    """Fetch every normalized public URL, preserving primary-first ordering."""
    try:
        raw_value = get_value_by_page_and_key("general", "public_url", db)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="general.public_url must be configured.",
        ) from exc

    if _is_dev_mode():
        try:
            return normalize_public_urls(raw_value)
        except ValueError:
            return [_DEFAULT_PUBLIC_URL]

    try:
        return normalize_public_urls(raw_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"general.public_url is misconfigured: {exc}",
        ) from exc


def get_public_url(db: Session) -> str:
    """Return the primary public URL used for server-generated absolute links."""
    return get_public_urls(db)[0]



# Shared filesystem paths and constants
_DATA_DIR = DATA_DIR
_LOGO_DIR = _DATA_DIR / "logo"
_LOGIN_BG_DIR = _DATA_DIR / "login_background"
_LDAP_CERT_DIR = _DATA_DIR / "ldap"
_LDAP_CA_CERT_FILENAME = "ldap_ca_cert.pem"
_FAVICON_SVG_PATH = _LOGO_DIR / "favicon.svg"
_ICON_PNG_PATH = _LOGO_DIR / "icon.png"
_SITE_MANIFEST_TEMPLATE: dict[str, Any] = {
    "name": "Omlorix",
    "short_name": "Omlorix",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#ffffff",
    "theme_color": "#ffffff",
    "icons": [
        {"src": "/api/v1/settings/icon/get?size=32", "sizes": "32x32", "type": "image/png"},
        {"src": "/api/v1/settings/icon/get?size=16", "sizes": "16x16", "type": "image/png"},
        {"src": "/api/v1/settings/icon/get?size=512", "sizes": "512x512", "type": "image/png"},
        {"src": "/api/v1/settings/icon/get?size=180", "sizes": "180x180", "type": "image/png"},
    ],
}

_CACHE_HEADERS = {"Cache-Control": "public, max-age=86400"}
_VERSIONED_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}
_ICON_REDIRECT_CACHE_HEADERS = {"Cache-Control": "public, max-age=300"}
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_VALID_LOGO_THEMES = frozenset({"light", "dark"})
_MAX_LOGO_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB hard ceiling
_MAX_ICON_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB hard ceiling
_MAX_LOGIN_BG_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB hard ceiling for login background
_MAX_LDAP_CA_CERT_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB hard ceiling for LDAP CA cert
_ALLOWED_LDAP_CA_CERT_EXTENSIONS = frozenset({"pem", "crt", "cer"})

_PNG_ICON_SIZES: tuple[tuple[int, str], ...] = (
    (16, "favicon-16x16.png"),
    (32, "favicon-32x32.png"),
    (180, "apple-touch-icon.png"),
    (512, "favicon-512x512.png"),
)
_ICON_FILENAME_MAP = {size: filename for size, filename in _PNG_ICON_SIZES}
_MAX_ICON_PNG_DIMENSION = 512
# Apple applies the Home Screen mask directly to the supplied touch icon. Keep
# the complete uploaded artwork inside the central 80% so details that touch the
# source edges are not crowded or clipped by the iOS rounded-square mask.
_APPLE_TOUCH_ICON_SIZE = 180
_APPLE_TOUCH_ICON_CONTENT_SCALE = 0.80


def _icon_resample_filter():
    """Return the best Pillow resampling filter available in this installation."""
    return getattr(
        getattr(Image, "Resampling", Image),
        "LANCZOS",
        getattr(Image, "BICUBIC", getattr(Image, "NEAREST", 1)),
    )


def _apple_touch_icon_background(image: Image.Image) -> tuple[int, int, int]:
    """Estimate an opaque canvas color from the uploaded icon's corners.

    Apple touch icons should be opaque. Sampling all four corners keeps a flat
    icon background visually continuous after the artwork is inset. Transparent
    corner pixels are composited over white, which is a predictable fallback for
    uploaded logos that do not provide their own background layer.
    """
    rgba_image = image.convert("RGBA")
    try:
        width, height = rgba_image.size
        samples = (
            rgba_image.getpixel((0, 0)),
            rgba_image.getpixel((width - 1, 0)),
            rgba_image.getpixel((0, height - 1)),
            rgba_image.getpixel((width - 1, height - 1)),
        )
    finally:
        rgba_image.close()

    composited_channels: list[tuple[int, int, int]] = []
    for red, green, blue, alpha in samples:
        composited_channels.append(
            (
                round((red * alpha + 255 * (255 - alpha)) / 255),
                round((green * alpha + 255 * (255 - alpha)) / 255),
                round((blue * alpha + 255 * (255 - alpha)) / 255),
            )
        )

    averaged_channels = tuple(
        round(sum(sample[channel] for sample in composited_channels) / len(composited_channels))
        for channel in range(3)
    )
    return averaged_channels[0], averaged_channels[1], averaged_channels[2]


def _create_apple_touch_icon(image: Image.Image) -> Image.Image:
    """Create an opaque 180px iOS Home Screen icon with a platform-safe inset.

    The source aspect ratio is preserved instead of stretching arbitrary uploads
    into a square. Only this Apple-specific derivative is inset; regular favicon
    and manifest variants keep their existing full-bleed rendering behavior.
    """
    source = image.convert("RGBA")
    try:
        content_size = round(_APPLE_TOUCH_ICON_SIZE * _APPLE_TOUCH_ICON_CONTENT_SCALE)
        source.thumbnail((content_size, content_size), _icon_resample_filter())

        background = _apple_touch_icon_background(image)
        canvas = Image.new(
            "RGBA",
            (_APPLE_TOUCH_ICON_SIZE, _APPLE_TOUCH_ICON_SIZE),
            (*background, 255),
        )
        try:
            left = (_APPLE_TOUCH_ICON_SIZE - source.width) // 2
            top = (_APPLE_TOUCH_ICON_SIZE - source.height) // 2
            canvas.alpha_composite(source, dest=(left, top))

            # RGB output guarantees that iOS never has to invent a fill color for alpha.
            return canvas.convert("RGB")
        finally:
            canvas.close()
    finally:
        source.close()


def _save_rasterized_icon_variant(*, png_path: Path, png_bytes: bytes, size: int) -> None:
    """Persist rendered PNG bytes, applying iOS treatment to the touch variant."""
    if size != _APPLE_TOUCH_ICON_SIZE:
        with open(png_path, "wb") as output_file:
            output_file.write(png_bytes)
        return

    # Re-open the renderer output so both eager upload generation and lazy
    # fallback generation always produce the same safe Apple touch icon.
    with Image.open(BytesIO(png_bytes)) as rendered_icon:
        touch_icon = _create_apple_touch_icon(rendered_icon)
        try:
            touch_icon.save(png_path, format="PNG")
        finally:
            touch_icon.close()


def _image_media_type_for_path(path: Path) -> str:
    """Get MIME type for an image file path."""
    ext = path.suffix.lstrip(".").lower()
    mime_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "svg": "image/svg+xml",
    }
    return mime_map.get(ext, "application/octet-stream")


def _get_icon_asset_version() -> str:
    """Return a stable cache-busting value for the currently saved icon files.

    The version is derived from file modification times instead of a database row so
    branding assets can stay filesystem-only while still getting content-addressed
    browser cache behavior. Uploading a new icon rewrites these files and therefore
    changes the version used in generated URLs and redirects.
    """
    candidates = [_FAVICON_SVG_PATH, _ICON_PNG_PATH]
    candidates.extend(_LOGO_DIR / filename for _, filename in _PNG_ICON_SIZES)

    latest_mtime_ns = 0
    for candidate in candidates:
        with suppress(OSError):
            if candidate.is_file():
                latest_mtime_ns = max(latest_mtime_ns, candidate.stat().st_mtime_ns)

    return str(latest_mtime_ns)


def _versioned_icon_url(size: int | None = None) -> str:
    """Build an icon URL with the current filesystem asset version."""
    params = []
    if size is not None:
        params.append(f"size={size}")
    params.append(f"v={_get_icon_asset_version()}")
    return f"/api/v1/settings/icon/get?{'&'.join(params)}"


def get_branding_assets_overview() -> dict[str, Any]:
    """Return currently saved branding assets without applying theme fallbacks."""

    def _logo_entry(theme: str) -> dict[str, Any] | None:
        candidate = next((p for p in _LOGO_DIR.glob(f"logo_{theme}.*") if p.is_file()), None)
        if candidate is None:
            return None
        return {
            "theme": theme,
            "filename": candidate.name,
            "content_type": _image_media_type_for_path(candidate),
            "url": f"/api/v1/settings/logo/get?theme={theme}",
        }

    def _icon_entry() -> dict[str, Any] | None:
        if _FAVICON_SVG_PATH.exists():
            candidate = _FAVICON_SVG_PATH
        elif _ICON_PNG_PATH.exists():
            candidate = _ICON_PNG_PATH
        else:
            candidate = next(
                (
                    _LOGO_DIR / filename
                    for size, filename in sorted(_PNG_ICON_SIZES, key=lambda item: item[0], reverse=True)
                    if (_LOGO_DIR / filename).exists()
                ),
                None,
            )

        if candidate is None:
            return None

        return {
            "filename": candidate.name,
            "content_type": _image_media_type_for_path(candidate),
            "url": _versioned_icon_url(),
            "sizes": {
                str(size): _versioned_icon_url(size)
                for size, _ in _PNG_ICON_SIZES
            },
            "version": _get_icon_asset_version(),
        }

    return {
        "logos": {
            "light": _logo_entry("light"),
            "dark": _logo_entry("dark"),
        },
        "icon": _icon_entry(),
    }



# -------------------
# Constants & Helpers
# -------------------
# Pages that must never be exposed through the public API or utility layer
_FORBIDDEN_PAGES = {"api_keys", "secret"}

_TRUE_STRINGS = {"true", "1", "yes", "y", "on"}
_FALSE_STRINGS = {"false", "0", "no", "n", "off"}


def coerce_bool(value: Any, default: bool = False) -> bool:
    """Convert loose setting values (e.g., "false") into canonical booleans."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_STRINGS:
            return True
        if normalized in _FALSE_STRINGS:
            return False
    return default


MAX_PINNED_MODELS = 8



# -------------------
# Read helpers with in-process LRU cache (cleared on every update)
# -------------------
def _load_setting_value(db: Session, page_name: str, key_name: str) -> Any:
    """Load a setting value directly from the database-backed settings page."""
    db_settings = get_settings_page(db, page_name)
    if not db_settings:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    return get_settings_value_from_page_data(page_name, db_settings.data, key_name)


@lru_cache(maxsize=1_000)
def _cached_get_value(
    page_name: str,
    key_name: str,
    db_identity: str,
    cache_version: str,
) -> Any:  # noqa: D401
    """Fetch a setting once per database and shared-cache generation.

    ``cache_version`` deliberately participates in the LRU key. Redis
    invalidation is shared by every process, so a process that did not perform
    the update must also stop using values from its previous local generation.
    """
    # The version is consumed by functools.lru_cache even though the database
    # query itself does not need it.
    del cache_version

    session: Session | None = _SESSION_REGISTRY.get(db_identity)  # type: ignore[type-var]
    if session is None:  # session could be GC'ed or closed
        # Fail fast: clear potentially stale cache entry and propagate a sensible error.
        invalidate_settings_cache(db_identity=db_identity)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cached database session is unavailable or closed.",
        )
    return _load_setting_value(session, page_name, key_name)



# Use WeakValueDictionary so entries vanish automatically when Session objects
# are garbage-collected, avoiding an ever-growing in-process registry.
_SESSION_REGISTRY: weakref.WeakValueDictionary[str, Session] = weakref.WeakValueDictionary()
_DEFAULT_PUBLIC_URL = "https://localhost"
_SETTINGS_CACHE_KEY_PREFIX = "omlorix:settings"


def _env_positive_int(name: str, default: int) -> int:
    """Get a positive integer from environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
        if value > 0:
            return value
    except Exception:
        pass
    return default


_SETTINGS_CACHE_TTL_SECONDS = max(30, _env_positive_int("SETTINGS_CACHE_TTL_SECONDS", 600))


def is_sensitive_setting(page_name: str, key_name: str) -> bool:
    """Return whether a setting should bypass shared caches."""
    return page_name in _FORBIDDEN_PAGES or is_sensitive_setting_key(page_name, key_name)


def _cleanup_session_registry(key: str) -> None:
    """Clean up session registry entry."""
    _SESSION_REGISTRY.pop(key, None)


def _remember_session(db_id: str, session: Session) -> None:
    """Remember a session for settings cache."""
    _SESSION_REGISTRY[db_id] = session
    try:
        finalizer = weakref.finalize(session, _cleanup_session_registry, db_id)
        setattr(session, "_omlorix_settings_registry_finalizer", finalizer)
    except Exception:
        # If finalizers cannot be attached, fall back on WeakValueDictionary cleanup.
        pass


def _canonical_database_identity(database_url: str) -> str:
    """Return a stable database identity without retaining credentials."""
    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except Exception:
        # Never retain a password from an unusual URL that SQLAlchemy cannot
        # parse. The digest is only an internal namespace input.
        raw_identity = str(database_url or "unknown-database")
        return f"unparsed:{hashlib.sha256(raw_identity.encode('utf-8')).hexdigest()}"


def _default_database_identity() -> str:
    """Return the configured application database identity."""
    return _canonical_database_identity(DATABASE_URL)


def _settings_cache_namespace(db_identity: str) -> str:
    """Build a non-secret namespace unique to this deployment and database."""
    deployment_namespace = str(os.getenv("SETTINGS_CACHE_NAMESPACE") or "").strip()
    # PostgreSQL schemas can host independent Omlorix installations inside one
    # physical database, so the schema must participate in cache isolation.
    namespace_source = f"{deployment_namespace}\x00{db_identity}\x00{DATABASE_SCHEMA}"
    return hashlib.sha256(namespace_source.encode("utf-8")).hexdigest()[:20]


def _settings_cache_version_key(namespace: str) -> str:
    """Return the Redis generation key for one database namespace."""
    return f"{_SETTINGS_CACHE_KEY_PREFIX}:{namespace}:cache_version"


def _new_settings_cache_version() -> str:
    """Return an opaque generation token that cannot reuse a stale LRU key."""
    return uuid.uuid4().hex


def _get_settings_cache_version(namespace: str, client) -> str | None:
    """Get the current settings cache version for one database namespace."""
    version_key = _settings_cache_version_key(namespace)
    try:
        version = client.get(version_key)
        if not version:
            # Avoid resetting a version another process initialized or bumped
            # while this process was also starting.
            client.set(version_key, _new_settings_cache_version(), nx=True)
            version = client.get(version_key)
            if version:
                return str(version)
            return None
        return str(version)
    except Exception:
        # Returning no generation tells the caller to bypass both cache layers
        # and read PostgreSQL directly until Redis is reliable again.
        return None


def _settings_cache_key(namespace: str, version: str, page_name: str, key_name: str) -> str:
    """Generate a database-scoped Redis cache key for one setting."""
    return f"{_SETTINGS_CACHE_KEY_PREFIX}:{namespace}:cache:{version}:{page_name}:{key_name}"


def invalidate_settings_cache(db: Session | None = None, *, db_identity: str | None = None) -> None:
    """Invalidate local and shared setting caches for one database.

    Callers that already have a database session should pass it so custom and
    test database bindings receive their own namespace. Callers without a
    session safely fall back to Omlorix's configured application database.
    """

    _cached_get_value.cache_clear()
    _SESSION_REGISTRY.clear()
    client = get_redis_client()
    if client is not None:
        resolved_identity = db_identity
        if resolved_identity is None and db is not None:
            resolved_identity = _engine_fingerprint(db.bind)
        if resolved_identity is None:
            resolved_identity = _default_database_identity()
        namespace = _settings_cache_namespace(resolved_identity)
        try:
            # A random token cannot accidentally reuse a generation after a
            # Redis flush or restore, unlike an integer counter restarted at 1.
            client.set(_settings_cache_version_key(namespace), _new_settings_cache_version())
        except Exception:
            logger.warning(
                "Unable to publish settings-cache invalidation for namespace %s; "
                "other processes may retain cached values until Redis recovers or entries expire.",
                namespace,
                exc_info=True,
            )


def _engine_fingerprint(bind) -> str:
    """Return a stable identifier for a session bind.

    Uses the database URL when available; otherwise falls back to a hash of the
    object's repr to reduce the chance of collisions across rebinding in tests.
    """

    if bind is None:
        return "none"

    url = getattr(bind, "url", None)
    if url is not None:
        return _canonical_database_identity(str(url))

    fingerprint = hashlib.sha1(repr(bind).encode("utf-8")).hexdigest()
    return f"anon:{fingerprint}"


def sanitize_pinned_model_ids(raw_value: Any) -> list[str]:
    """Return a canonical, ordered list of pinned model IDs."""

    if isinstance(raw_value, (list, tuple, set)):
        candidates = list(raw_value)
    elif raw_value is None:
        candidates = []
    else:
        candidates = [raw_value]

    sanitized: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        if not isinstance(raw, str):
            continue
        model_id = raw.strip()
        if not model_id or model_id in seen:
            continue
        sanitized.append(model_id)
        seen.add(model_id)
        if len(sanitized) >= MAX_PINNED_MODELS:
            break

    return sanitized


def get_default_pinned_model_ids(db: Session) -> list[str]:
    """Return the admin-configured default pinned models."""

    try:
        raw_value = get_value_by_page_and_key("models", "default_pinned_models", db)
    except HTTPException:
        return []
    return sanitize_pinned_model_ids(raw_value)


def get_effective_pinned_model_ids_for_user(user_id: str, db: Session) -> list[str]:
    """Return the pinned model IDs a user should currently see."""

    from app.users.init import get_user_setting_value

    try:
        customized = coerce_bool(
            get_user_setting_value(user_id, "chat", "pinned_models_customized", db),
            default=False,
        )
    except HTTPException:
        customized = False

    if customized:
        try:
            requested = sanitize_pinned_model_ids(
                get_user_setting_value(user_id, "chat", "pinned_models", db)
            )
        except HTTPException:
            requested = []
    else:
        requested = get_default_pinned_model_ids(db)

    if not requested:
        return []

    try:
        from app.llm.utils import list_user_models

        visible_model_ids = {
            str(item.get("model_id")).strip()
            for item in list_user_models(db, user_id)
            if isinstance(item, dict) and str(item.get("model_id") or "").strip()
        }
    except Exception:
        logger.exception("Failed to filter pinned models against visible models for user %s", user_id)
        return requested

    return [model_id for model_id in requested if model_id in visible_model_ids]



# -------------------
# Chat Setup
# -------------------
def get_chat_setup(user_id: str, db: Session):
    """Get chat setup configuration for a user."""
    # Import here to avoid circular import
    from app.connections.policy import group_has_enabled_workspace_connections
    from app.users.init import get_user_setting_value
    from app.utils.utils import get_privacy_policy_notice_policy, get_terms_of_service_policy
    from app.users.utils import get_profile_picture_status
    from app.groups.management import has_managed_groups_for_user
    from app.llm.models import has_applicable_rate_limits
    user = get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    color_theme = get_user_setting_value(user_id, "appearance", "color_theme", db)
    theme_mode = get_user_setting_value(user_id, "appearance", "theme", db)
    font_family = get_user_setting_value(user_id, "appearance", "font", db)
    # Keep the chat bootstrap account-aware as a fallback for pages where the
    # auth refresh response is not the first protected payload available.
    language = get_user_setting_value(user_id, "general", "language", db) or ""
    country = get_user_setting_value(user_id, "general", "country", db) or ""
    user_timezone = get_user_setting_value(user_id, "general", "timezone", db) or ""

    speech_playback_speed = get_user_setting_value(user_id, "chat", "speech_playback_speed", db)
    try:
        speech_playback_speed = float(speech_playback_speed) if speech_playback_speed is not None else 1.0
    except (TypeError, ValueError):
        speech_playback_speed = 1.0
    speech_playback_speed = max(0.5, min(2.0, speech_playback_speed))
    render_assistant_messages_markdown = get_user_setting_value(user_id, "chat", "render_assistant_messages_markdown", db)
    
    allow_chat_deletion = get_user_group_setting_value(user_id, "chat", "allow_chat_deletion", db)
    shadow_chat_deletion = get_user_group_setting_value(user_id, "chat", "shadow_chat_deletion", db)
    allow_regenerate_response = get_user_group_setting_value(user_id, "chat", "allow_regenerate_response", db)
    allow_rate_response = get_user_group_setting_value(user_id, "chat", "allow_rate_response", db)
    allow_delete_messages = get_user_group_setting_value(user_id, "chat", "allow_delete_messages", db)
    enable_projects = get_user_group_setting_value(user_id, "projects", "enable_projects", db)
    allow_project_share = get_user_group_setting_value(user_id, "projects", "allow_project_share", db)
    enable_automations = get_user_group_setting_value(user_id, "automations", "enabled_automations", db)
    enable_todo = get_user_group_setting_value(user_id, "todo", "enabled_todo", db)
    allow_todo_list_share = get_user_group_setting_value(user_id, "todo", "allow_todo_list_share", db)
    enable_notes = get_user_group_setting_value(user_id, "notes", "enabled_notes", db)
    allow_notes_share = get_user_group_setting_value(user_id, "notes", "allow_notes_share", db)
    enable_memories = get_user_group_setting_value(user_id, "memories", "enabled_memories", db)
    enable_bookmarks = get_user_group_setting_value(user_id, "bookmarks", "enabled_bookmarks", db)
    allow_bookmark_share = get_user_group_setting_value(user_id, "bookmarks", "allow_bookmark_share", db)
    allow_agents = get_user_group_setting_value(user_id, "agents", "allow_agents", db)
    allow_agent_share = get_user_group_setting_value(user_id, "agents", "allow_agent_share", db)
    enable_artifact_sharing = get_user_group_setting_value(user_id, "sharing", "enable_artifact_sharing", db)
    enable_chat_sharing = get_user_group_setting_value(user_id, "sharing", "enable_chat_sharing", db)
    enable_skills = get_user_group_setting_value(user_id, "skills", "enabled_skills", db)
    allow_skill_share = get_user_group_setting_value(user_id, "skills", "allow_skill_share", db)
    enable_prompts = get_user_group_setting_value(user_id, "prompts", "enabled_prompts", db)
    allow_prompt_share = get_user_group_setting_value(user_id, "prompts", "allow_prompt_share", db)
    allow_file_uploads = get_user_group_setting_value(user_id, "files", "allow_file_uploads", db)
    allow_byok = get_user_group_setting_value(user_id, "chat", "allow_byok", db)
    allow_mcp = get_user_group_setting_value(user_id, "tools_mcp", "enable_mcp", db)
    allow_workspace_connections = group_has_enabled_workspace_connections(user_id, db)
    byok_default_scrape_provider = get_user_group_setting_value(user_id, "chat", "byok_default_scrape_provider", db)
    byok_default_search_provider = get_user_group_setting_value(user_id, "chat", "byok_default_search_provider", db)
    byok_title_generation_model_id = get_user_group_setting_value(
        user_id,
        "chat",
        "byok_title_generation_model_id",
        db,
    )
    chat_full_width = get_user_setting_value(user_id, "chat", "chat_full_width", db)
    show_chat_box_warning = get_user_group_setting_value(user_id, "chat", "show_chat_box_warning", db)
    chat_box_warning_message = get_user_group_setting_value(user_id, "chat", "chat_box_warning_message", db)
    show_message_nav = get_user_setting_value(user_id, "chat", "show_message_nav", db)
    sidebar_button_visibility = get_user_setting_value(user_id, "chat", "sidebar_button_visibility", db)
    pinned_models = get_effective_pinned_model_ids_for_user(user_id, db)
    compliance_enable_watermark = get_user_group_setting_value(user_id, "compliance", "enable_watermark", db)
    compliance_watermark = get_user_group_setting_value(user_id, "compliance", "watermark", db)
    leaderboard_enabled = get_user_group_setting_value(user_id, "leaderboard", "enabled", db)
    artificial_analysis_key = get_user_group_setting_value(
        user_id,
        "leaderboard",
        "artificial_analysis_api_key",
        db,
    )
    has_leaderboard_access = bool(
        leaderboard_enabled
        and isinstance(artificial_analysis_key, str)
        and artificial_analysis_key.strip()
    )
    render_user_messages_markdown = get_user_setting_value(user_id, "chat", "render_user_messages_markdown", db)
    ctrl_enter_to_send = get_user_setting_value(user_id, "chat", "ctrl_enter_to_send", db)
    temporary_chat_allowed = bool(get_user_group_setting_value(user_id, "chat", "allow_temporary_chat", db))
    temporary_chat_persistence_enabled = (
        temporary_chat_allowed
        and bool(get_user_group_setting_value(user_id, "chat", "save_temp_chats", db))
    )
    temporary_chat_saving_enabled = temporary_chat_persistence_enabled
    temporary_chat_retention_enabled = (
        temporary_chat_persistence_enabled
        and bool(get_user_group_setting_value(user_id, "chat", "save_temp_chats_retention_enabled", db))
    )
    temporary_chat_retention_days_raw = get_user_group_setting_value(
        user_id,
        "chat",
        "save_temp_chats_retention_days",
        db,
    )
    try:
        temporary_chat_retention_days = int(temporary_chat_retention_days_raw)
    except (TypeError, ValueError):
        temporary_chat_retention_days = 30
    if temporary_chat_retention_days < 1:
        temporary_chat_retention_days = 1

    always_use_temporary_chat = (
        False
        if not temporary_chat_allowed
        else get_user_setting_value(
            user_id,
            "chat",
            "always_use_temporary_chat",
            db,
        )
    )
    show_model_settings = get_user_setting_value(user_id, "chat", "show_model_settings", db)
    show_assistant_message_metadata = get_user_setting_value(user_id, "chat", "show_assistant_message_metadata", db)
    profile_picture_status = get_profile_picture_status(user_id, db)
    has_custom_profile_picture = bool(profile_picture_status.get("has_custom_profile_picture"))
    has_profile_picture = bool(profile_picture_status.get("has_profile_picture"))
    profile_picture_source = str(profile_picture_status.get("profile_picture_source") or "initials")
    profile_picture_provider = str(profile_picture_status.get("profile_picture_provider") or "")
    has_new_notifications = get_user_setting_value(user_id, "states", "has_new_notifications", db) or False
    welcome_card_dismissed = bool(
        get_user_setting_value(user_id, "states", "welcome_card_dismissed", db)
    )
    personal_info_permissions = get_user_setting_value(
        user_id,
        "security",
        "allow_llm_to_access_personal_information",
        db,
    )
    personal_info_access_enabled = bool(
        isinstance(personal_info_permissions, dict)
        and any(value is True for value in personal_info_permissions.values())
    )
    privacy_policy_notice = get_privacy_policy_notice_policy(db, user_id)
    terms_of_service_policy = get_terms_of_service_policy(db, user_id)

    # Resolve the conditional settings tabs as part of the initial chat
    # bootstrap. Their detailed data remains lazy, but the sidebar can now be
    # laid out completely before the settings view becomes visible.
    user_settings_navigation = {
        "managed_groups": has_managed_groups_for_user(db, user),
        "rate_limits": has_applicable_rate_limits(
            db,
            user.id,
            getattr(user, "group_id", None),
        ),
    }

    # Social login linked status
    google_linked = get_user_setting_value(user_id, "social_login", "google_linked", db) or False
    github_linked = get_user_setting_value(user_id, "social_login", "github_linked", db) or False
    slack_linked = get_user_setting_value(user_id, "social_login", "slack_linked", db) or False
    apple_linked = get_user_setting_value(user_id, "social_login", "apple_linked", db) or False
    microsoft_linked = get_user_setting_value(user_id, "social_login", "microsoft_linked", db) or False

    dictation_settings = get_settings_page(db, "dictation")
    dictation_settings_data = (
        dictation_settings.data
        if dictation_settings and isinstance(dictation_settings.data, dict)
        else {}
    )
    transcription_enabled = bool(dictation_settings_data.get("transcription_enabled"))
    transcription_provider_id = dictation_settings_data.get("transcription_provider_id")
    transcription_model = dictation_settings_data.get("transcription_model")
    live_transcription_enabled = bool(
        dictation_settings_data.get("live_transcription_enabled")
    )
    live_transcription_provider_id = dictation_settings_data.get(
        "live_transcription_provider_id"
    )
    live_transcription_model = dictation_settings_data.get("live_transcription_model")
    realtime_settings = get_settings_page(db, "realtime")
    realtime_settings_data = (
        realtime_settings.data
        if realtime_settings and isinstance(realtime_settings.data, dict)
        else {}
    )
    realtime_enabled = bool(realtime_settings_data.get("realtime_enabled"))
    realtime_provider_id = realtime_settings_data.get("realtime_provider_id")
    realtime_model = realtime_settings_data.get("realtime_model")
    from app.chats.read_aloud import get_read_aloud_runtime_config

    read_aloud_config = get_read_aloud_runtime_config(db)
    transcription_ready = False
    live_transcription_ready = False
    realtime_ready = False
    if transcription_enabled and isinstance(transcription_provider_id, str) and isinstance(transcription_model, str):
        provider_id = transcription_provider_id.strip()
        model_name = transcription_model.strip()
        if provider_id and model_name:
            try:
                from app.llm.models import get_llm_provider
                from app.llm.schemas import ProviderEnum

                provider = get_llm_provider(db, provider_id)
                allowed_types = {
                    ProviderEnum.openai.value,
                    ProviderEnum.openai_responses.value,
                    ProviderEnum.openai_chat_completions.value,
                    ProviderEnum.google_aistudio.value,
                    ProviderEnum.elevenlabs.value,
                    ProviderEnum.xai.value,
                }
                if provider and provider.provider in allowed_types and provider.api_key:
                    transcription_ready = True
            except Exception:
                transcription_ready = False
    if (
        live_transcription_enabled
        and isinstance(live_transcription_provider_id, str)
        and isinstance(live_transcription_model, str)
    ):
        provider_id = live_transcription_provider_id.strip()
        model_name = live_transcription_model.strip()
        if provider_id and model_name:
            try:
                from app.llm.models import get_llm_provider
                from app.llm.openai.model_list import (
                    OPENAI_LIVE_TRANSCRIPTION_MODELS,
                )
                from app.llm.xai.transcription import XAI_TRANSCRIPTION_MODELS
                from app.llm.schemas import (
                    ProviderEnum,
                    provider_api_key_is_optional,
                )

                provider = get_llm_provider(db, provider_id)
                allowed_types = {
                    ProviderEnum.openai.value,
                    ProviderEnum.openai_responses.value,
                    ProviderEnum.openai_chat_completions.value,
                    ProviderEnum.xai.value,
                }
                live_transcription_ready = bool(
                    provider
                    and provider.provider in allowed_types
                    and (
                        provider.api_key
                        or provider_api_key_is_optional(provider.provider)
                    )
                    and model_name
                    in (
                        set(XAI_TRANSCRIPTION_MODELS)
                        if provider.provider == ProviderEnum.xai.value
                        else set(OPENAI_LIVE_TRANSCRIPTION_MODELS)
                    )
                )
            except Exception:
                live_transcription_ready = False
    if realtime_enabled and isinstance(realtime_provider_id, str) and isinstance(realtime_model, str):
        provider_id = realtime_provider_id.strip()
        model_name = realtime_model.strip()
        if provider_id and model_name:
            try:
                from app.llm.models import get_llm_provider
                from app.llm.schemas import ProviderEnum
                from app.llm.openai.realtime import get_openai_realtime_models
                from app.llm.google_aistudio.realtime import get_google_aistudio_live_models
                from app.llm.xai.realtime import get_xai_realtime_models

                provider = get_llm_provider(db, provider_id)
                allowed_types = {
                    ProviderEnum.openai.value,
                    ProviderEnum.openai_responses.value,
                    ProviderEnum.openai_chat_completions.value,
                    ProviderEnum.google_aistudio.value,
                    ProviderEnum.xai.value,
                }
                if provider and provider.provider in allowed_types and provider.api_key:
                    if provider.provider == ProviderEnum.google_aistudio.value:
                        realtime_models = set(
                            get_google_aistudio_live_models(
                                db=db,
                                google_provider_id=provider_id,
                            )
                        )
                    elif provider.provider == ProviderEnum.xai.value:
                        realtime_models = set(
                            get_xai_realtime_models(
                                db=db,
                                provider_id=provider_id,
                            )
                        )
                    else:
                        realtime_models = set(
                            get_openai_realtime_models(
                                db=db,
                                openai_provider_id=provider_id,
                                openai_provider_type=provider.provider,
                            )
                        )
                    realtime_ready = model_name in realtime_models
            except Exception:
                realtime_ready = False
    user_role = getattr(user, "role", "user")
    return {
        "user_id": user.id,
        "user_role": user_role,
        "is_admin": is_admin_role(user_role),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "color_theme": color_theme,
        "theme_mode": theme_mode,
        "font_family": font_family,
        "language": language,
        "country": country,
        "timezone": user_timezone,
        "allow_chat_deletion": allow_chat_deletion,
        "shadow_chat_deletion": bool(shadow_chat_deletion),
        "allow_regenerate_response": bool(allow_regenerate_response),
        "allow_rate_response": bool(allow_rate_response),
        "allow_delete_messages": bool(allow_delete_messages),
        "enable_projects": coerce_bool(enable_projects, default=False),
        "allow_project_share": coerce_bool(allow_project_share, default=True),
        "enable_automations": coerce_bool(enable_automations, default=False),
        "enable_todo": coerce_bool(enable_todo, default=False),
        "allow_todo_list_share": coerce_bool(allow_todo_list_share, default=True),
        "allow_notes_share": coerce_bool(allow_notes_share, default=True),
        "allow_prompt_share": coerce_bool(allow_prompt_share, default=True),
        "enable_bookmarks": coerce_bool(enable_bookmarks, default=False),
        "allow_bookmark_share": coerce_bool(allow_bookmark_share, default=True),
        "allow_agents": coerce_bool(allow_agents, default=False),
        "allow_agent_share": coerce_bool(allow_agent_share, default=True),
        "allow_skill_share": coerce_bool(allow_skill_share, default=True),
        "enable_artifact_sharing": coerce_bool(enable_artifact_sharing, default=True),
        "enable_chat_sharing": coerce_bool(enable_chat_sharing, default=True),
        "allow_file_uploads": coerce_bool(allow_file_uploads, default=False),
        "chat_full_width": chat_full_width,
        "show_chat_box_warning": coerce_bool(show_chat_box_warning, default=False), 
        "chat_box_warning_message": chat_box_warning_message,
        "render_user_messages_markdown": render_user_messages_markdown,
        "ctrl_enter_to_send": ctrl_enter_to_send,
        "always_use_temporary_chat": always_use_temporary_chat,
        "temporary_chat_allowed": temporary_chat_allowed,
        "temporary_chat_saving_enabled": temporary_chat_saving_enabled,
        "temporary_chat_persistence_enabled": temporary_chat_persistence_enabled,
        "temporary_chat_retention_enabled": temporary_chat_retention_enabled,
        "temporary_chat_retention_days": temporary_chat_retention_days,
        "show_model_settings": show_model_settings,
        "show_assistant_message_metadata": show_assistant_message_metadata,
        "has_custom_profile_picture": has_custom_profile_picture,
        "has_profile_picture": has_profile_picture,
        "profile_picture_source": profile_picture_source,
        "profile_picture_provider": profile_picture_provider,
        "show_message_nav": show_message_nav,
        "sidebar_button_visibility": sidebar_button_visibility if isinstance(sidebar_button_visibility, dict) else {},
        "pinned_models": pinned_models if isinstance(pinned_models, list) else [],
        "chat_box_show_call_input": bool(realtime_ready),
        "speech_playback_speed": speech_playback_speed,
        "render_assistant_messages_markdown": render_assistant_messages_markdown,
        "read_aloud_provider_id": read_aloud_config.get("provider_id"),
        "read_aloud_model": read_aloud_config.get("model_name"),
        "read_aloud_voice": read_aloud_config.get("voice"),
        "read_aloud_response_format": read_aloud_config.get("response_format"),
        "read_aloud_provider_type": read_aloud_config.get("provider_type"),
        "read_aloud_ready": bool(read_aloud_config.get("ready")),
        "read_aloud_uses_browser_native": bool(read_aloud_config.get("use_browser_native")),
        "allow_byok": bool(allow_byok),
        "allow_mcp": bool(allow_mcp),
        "allow_workspace_connections": allow_workspace_connections,
        "byok_title_generation_model_id": byok_title_generation_model_id or "",
        "byok_default_scrape_provider": byok_default_scrape_provider or "",
        "byok_default_search_provider": byok_default_search_provider or "",
        "enable_notes": coerce_bool(enable_notes, default=False),
        "enable_memories": coerce_bool(enable_memories, default=False),
        "enable_skills": coerce_bool(enable_skills, default=False),
        "enable_prompts": coerce_bool(enable_prompts, default=False),
        "has_leaderboard_access": has_leaderboard_access,
        "compliance": {
            "enable_watermark": compliance_enable_watermark,
            "watermark": compliance_watermark,
        },
        "has_new_notifications": has_new_notifications,
        "show_welcome_card": not welcome_card_dismissed,
        "personal_info_access_enabled": personal_info_access_enabled,
        "privacy_policy_notice": privacy_policy_notice,
        "terms_of_service_policy": terms_of_service_policy,
        "user_settings_navigation": user_settings_navigation,
        "google_linked": google_linked,
        "github_linked": github_linked,
        "slack_linked": slack_linked,
        "apple_linked": apple_linked,
        "microsoft_linked": microsoft_linked,
        "realtime_call_ready": realtime_ready,
        "file_transcription_ready": transcription_ready,
        "live_transcription_ready": live_transcription_ready,
    }


def get_default_model_id(db: Session) -> str | None:
    """Return the configured default model ID, or None if not configured."""
    try:
        raw_value = get_value_by_page_and_key("models", "default_model", db)
    except HTTPException:
        return None
    if isinstance(raw_value, str):
        normalized = raw_value.strip()
        return normalized or None
    return None


# -------------------
# Page Settings
# -------------------
def get_page_settings_by_page(page_name: str, db: Session) -> PageSettings:
    """Get page settings by page name."""
    if page_name in _FORBIDDEN_PAGES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This page is not accessible")
    db_settings = get_settings_page(db, page_name)
    if not db_settings:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    raw_data = get_settings_page_data(db, db_settings.page_name, decrypt_sensitive_values=False)
    canonical_data = _canonicalize_settings_page_data(db_settings.page_name, raw_data)
    return PageSettings(
        page_name=db_settings.page_name,
        data=mask_sensitive_settings_page_data(db_settings.page_name, canonical_data),
    )


def _canonicalize_settings_page_data(page_name: str, raw_data: dict[str, Any] | None) -> dict[str, Any]:
    """Drop obsolete keys from stored settings before returning them to callers."""

    page_defaults = _load_canonical_defaults().get(page_name)
    if not isinstance(page_defaults, dict):
        return deepcopy(raw_data) if isinstance(raw_data, dict) else {}
    try:
        return validate_settings_page_values(
            page_name,
            {},
            current_values=raw_data if isinstance(raw_data, dict) else {},
        )
    except HTTPException:
        # Some callers pass partial page data, and older rows may fail full
        # schema validation even though individual current keys are still
        # meaningful. Preserve only keys that still exist in the canonical page
        # defaults, then layer them over defaults so obsolete fields do not leak
        # back to the admin UI or exports.
        canonical_data = deepcopy(page_defaults)
        if isinstance(raw_data, dict):
            for key_name, value in raw_data.items():
                if key_name in page_defaults:
                    canonical_data[key_name] = deepcopy(value)
        return canonical_data


# -------------------
# Get Value By Page And Key
# -------------------
def get_value_by_page_and_key(page_name: str, key_name: str, db: Session) -> Any:
    """Get a setting value by page name and key name."""
    if is_sensitive_setting(page_name, key_name):
        return _load_setting_value(db, page_name, key_name)

    db_id = _engine_fingerprint(db.bind)
    cache_namespace = _settings_cache_namespace(db_id)
    client = get_redis_client()
    if client is None:
        cache_version = "local"
        cache_key = None
    else:
        cache_version = _get_settings_cache_version(cache_namespace, client)
        if cache_version is None:
            return _load_setting_value(db, page_name, key_name)
        cache_key = _settings_cache_key(cache_namespace, cache_version, page_name, key_name)

    if client is not None:
        sentinel = object()
        cached = redis_get_json(client, cache_key, default=sentinel)
        if cached is not sentinel:
            return cached

    _remember_session(db_id, db)
    value = _cached_get_value(page_name, key_name, db_id, cache_version)
    if client is not None and cache_key is not None:
        redis_set_json(client, cache_key, value, ttl_seconds=_SETTINGS_CACHE_TTL_SECONDS)
    return value


# -------------------
# Update Page Key Value
# -------------------
def update_page_key_value_by_page_and_key(page_name: str, key_name: str, value: Any, db: Session) -> PageSettings:
    """Update a setting value by page name and key name."""
    if page_name in _FORBIDDEN_PAGES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access to this page is forbidden")

    db_settings = get_settings_page(db, page_name)
    if not db_settings:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")

    if (
        is_sensitive_setting_key(page_name, key_name)
        and value == SENSITIVE_SETTING_RESPONSE_MASK
    ):
        return PageSettings(
            page_name=page_name,
            data=mask_sensitive_settings_page_data(page_name, db_settings.data),
        )

    next_value = value
    current_data = deepcopy(_load_canonical_defaults().get(page_name, {}))
    if isinstance(db_settings.data, dict):
        current_data.update(db_settings.data)

    validated_data = validate_settings_page_values(
        page_name,
        {key_name: next_value},
        current_values=current_data,
    )

    if page_name == "login_ldap":
        transport_policy = get_ldap_transport_security_policy(validated_data)
        if not transport_policy.allows_bind:
            raise HTTPException(status_code=400, detail=LDAP_TRANSPORT_SECURITY_ADMIN_ERROR_DETAIL)

    if page_name == "login_social" and key_name in {"enable_apple_login", "apple_private_key"}:
        from app.auth.social import APPLE_PRIVATE_KEY_ERROR_DETAIL, validate_apple_private_key

        decrypted_current_data = get_settings_page_data(db, page_name)
        merged_login_social = {**decrypted_current_data, key_name: next_value}
        if key_name == "apple_private_key":
            next_value_text = str(next_value or "")
            if next_value_text.strip():
                next_value = validate_apple_private_key(next_value)
                validated_data[key_name] = next_value
                merged_login_social[key_name] = next_value

        apple_login_active = coerce_bool(merged_login_social.get("enable_apple_login"))

        if apple_login_active and not str(merged_login_social.get("apple_private_key") or "").strip():
            raise HTTPException(status_code=400, detail=APPLE_PRIVATE_KEY_ERROR_DETAIL)

        if apple_login_active:
            validate_apple_private_key(merged_login_social.get("apple_private_key"))

    validated_data = preserve_masked_sensitive_settings_page_data(
        page_name,
        db_settings.data if isinstance(db_settings.data, dict) else {},
        validated_data,
    )

    _, storage_data = ensure_sensitive_settings_page_encrypted(
        page_name,
        validated_data,
        treat_values_as_plaintext=False,
    )

    if isinstance(db_settings.data, dict) and db_settings.data == storage_data:
        return PageSettings(
            page_name=page_name,
            data=mask_sensitive_settings_page_data(page_name, db_settings.data),
        )

    db_settings.data = storage_data
    flag_modified(db_settings, "data")
    db.commit()
    db.refresh(db_settings)

    # Clear cache after update
    invalidate_settings_cache()

    if page_name == "general" and key_name in {
        "offline_mode",
        "internet_connectivity_check_enabled",
        "external_requests_mode",
        "external_requests_allowlist",
    }:
        _sync_internet_connectivity_worker_state(db)
        try:
            _refresh_llm_provider_worker()
        except Exception as exc:
            logger = logging.getLogger(__name__)
            logger.exception("[Settings] Failed to refresh LLM provider worker after settings update: %s", exc)

    return PageSettings(
        page_name=db_settings.page_name,
        data=mask_sensitive_settings_page_data(db_settings.page_name, db_settings.data),
    )


def _sync_internet_connectivity_worker_state(db: Session) -> None:
    """Keep the connectivity worker aligned with general connectivity settings."""

    from app.utils.utils import (
        start_internet_connectivity_checker_worker,
        stop_internet_connectivity_checker_worker,
    )
    from app.utils.background import worker_manager

    offline_mode = coerce_bool(get_value_by_page_and_key("general", "offline_mode", db), default=False)
    connectivity_enabled = coerce_bool(
        get_value_by_page_and_key("general", "internet_connectivity_check_enabled", db),
        default=True,
    )

    if offline_mode or not connectivity_enabled:
        if worker_manager.is_running("internet_connectivity_checker"):
            stop_internet_connectivity_checker_worker()
        return

    if not worker_manager.is_running("internet_connectivity_checker"):
        start_internet_connectivity_checker_worker()


def _refresh_llm_provider_worker() -> None:
    """Refresh the LLM provider worker."""
    from app.llm.worker import start_llm_provider_worker

    start_llm_provider_worker(restart=True)


# -------------------
# Login Settings
# -------------------
def get_login_passkey_policy(db):
    """Get login passkey policy with legacy fallback."""
    general_data = get_settings_page_data(db, "login_general")
    legacy_data = get_settings_page_data(db, "login_passkeys")

    def _resolve_bool(key: str, default: bool = False) -> bool:
        value = general_data.get(key)
        if value is None and key not in general_data:
            value = legacy_data.get(key)
        return coerce_bool(value, default=default)

    return {
        "enable_passkeys": _resolve_bool("enable_passkeys", default=True),
    }


def get_login_settings(db):
    """Get login settings."""
    from app.utils.utils import get_terms_of_service_policy

    login_customization = get_page_settings_by_page("login_customization", db)

    general_data = get_settings_page_data(db, "login_general")
    if not general_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")

    enable_signin = general_data.get("enable_signin")
    enable_signup = general_data.get("enable_signup")
    enable_password_reset = general_data.get("enable_password_reset")
    password_reset_ready = is_password_reset_ready(db)
    contact_support_email = general_data.get("contact_support_email")
    show_privacy_notice_link = general_data.get("show_privacy_notice_link", False)
    show_terms_of_service_link = coerce_bool(general_data.get("show_terms_of_service_link"), default=False)
    enforce_terms_of_service_signup_acceptance = coerce_bool(
        general_data.get("enforce_terms_of_service_signup_acceptance"),
        default=False,
    )
    enforce_terms_of_service_access_acceptance = coerce_bool(
        general_data.get("enforce_terms_of_service_access_acceptance"),
        default=False,
    )
    application_name = get_value_by_page_and_key("general", "application_name", db) or "Omlorix"
    passkey_policy = get_login_passkey_policy(db)
    terms_of_service_policy = get_terms_of_service_policy(db)
    # The sign-up entry point is controlled only by the explicit administrator
    # setting. Terms policy independently tells the client whether consent is
    # required and must never override the administrator's registration switch.
    effective_enable_signup = coerce_bool(enable_signup, default=False)

    return {
        "login_customization": login_customization,
        "enable_signin": enable_signin,
        "enable_signup": effective_enable_signup,
        "enable_password_reset": enable_password_reset,
        "password_reset_ready": password_reset_ready,
        "enable_2fa": coerce_bool(general_data.get("enable_2fa"), default=True),
        "twofa_provider": str(general_data.get("twofa_provider") or "totp").strip().lower() or "totp",
        "twofa_email_ready": is_twofa_email_ready(db),
        "contact_support_email": contact_support_email,
        "show_privacy_notice_link": show_privacy_notice_link,
        "show_terms_of_service_link": show_terms_of_service_link,
        "enforce_terms_of_service_signup_acceptance": enforce_terms_of_service_signup_acceptance,
        "enforce_terms_of_service_access_acceptance": enforce_terms_of_service_access_acceptance,
        "terms_of_service_policy": terms_of_service_policy,
        "application_name": application_name,
        "enable_passkeys": passkey_policy["enable_passkeys"],
    }


# -------------------
# Logo
# -------------------
def upload_logo(logo: UploadFile = File(...), theme: str = "light"):
    """Upload a logo image for a theme."""
    # Validate theme variant
    if theme not in _VALID_LOGO_THEMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid theme. Allowed values are 'light' or 'dark'.")

    parsed_upload: dict[str, Any] | None = None
    try:
        parsed_upload = _parse_image_upload(
            logo,
            kind="Logo",
            limit_bytes=_MAX_LOGO_UPLOAD_BYTES,
            allow_svg=True,
        )

        file_extension = str(parsed_upload["extension"])

        # Build path relative to project root (app/data/logo)
        _LOGO_DIR.mkdir(parents=True, exist_ok=True)

        # Remove any pre-existing logo for the same theme (regardless of extension) to avoid stale variants
        for existing in _LOGO_DIR.glob(f"logo_{theme}.*"):
            with suppress(Exception):
                existing.unlink()

        logo_path = _LOGO_DIR / f"logo_{theme}.{file_extension}"

        with open(logo_path, "wb") as f:
            f.write(cast(bytes, parsed_upload["payload"]))
    finally:
        if parsed_upload and parsed_upload.get("image") is not None:
            with suppress(Exception):
                parsed_upload["image"].close()
        with suppress(Exception):
            logo.file.close()

    return {"status": "success", "theme": theme}


# -------------------
# Logo
# -------------------
def get_logo(theme: str = "light"):
    """Return the logo image for the requested theme (light or dark)."""
    from fastapi.responses import FileResponse

    if theme not in _VALID_LOGO_THEMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid theme. Allowed values are 'light' or 'dark'.")

    # Look for a theme-specific logo first (e.g. logo_light.png / logo_dark.svg)
    candidate = next((p for p in _LOGO_DIR.glob(f"logo_{theme}.*") if p.is_file()), None)

    # Fallback order: generic light logo, any generic "logo.*" (legacy support)
    if candidate is None and theme == "dark":
        candidate = next((p for p in _LOGO_DIR.glob("logo_light.*") if p.is_file()), None)
    if candidate is None:
        candidate = next((p for p in _LOGO_DIR.glob("logo.*") if p.is_file()), None)

    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logo not found")

    # Basic mime-type map; fall back to octet-stream
    _mime_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "svg": "image/svg+xml",
    }
    ext = candidate.suffix.lstrip(".").lower()
    media_type = _mime_map.get(ext, "application/octet-stream")

    return FileResponse(
        path=candidate,
        media_type=media_type,
        filename=candidate.name,
        headers=_NO_STORE_HEADERS,
    )


# -------------------
# Login Background Image
# -------------------
def upload_login_background(image: UploadFile = File(...)):
    """Upload a background image for the login page branding panel."""
    parsed_upload: dict[str, Any] | None = None
    try:
        parsed_upload = _parse_image_upload(
            image,
            kind="Login background",
            limit_bytes=_MAX_LOGIN_BG_UPLOAD_BYTES,
            allow_svg=False,  # SVG not recommended for backgrounds
        )

        file_extension = str(parsed_upload["extension"])

        # Build path relative to project root (app/data/login_background)
        _LOGIN_BG_DIR.mkdir(parents=True, exist_ok=True)

        # Remove any pre-existing background images to avoid stale variants
        for existing in _LOGIN_BG_DIR.glob("background.*"):
            with suppress(Exception):
                existing.unlink()

        bg_path = _LOGIN_BG_DIR / f"background.{file_extension}"

        with open(bg_path, "wb") as f:
            f.write(cast(bytes, parsed_upload["payload"]))
    finally:
        if parsed_upload and parsed_upload.get("image") is not None:
            with suppress(Exception):
                parsed_upload["image"].close()
        with suppress(Exception):
            image.file.close()

    return {"status": "success"}


def get_login_background():
    """Return the login background image if it exists."""
    from fastapi.responses import FileResponse

    # Look for any background image file
    candidate = next((p for p in _LOGIN_BG_DIR.glob("background.*") if p.is_file()), None)

    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Login background not found")

    # Basic mime-type map; fall back to octet-stream
    _mime_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }
    ext = candidate.suffix.lstrip(".").lower()
    media_type = _mime_map.get(ext, "application/octet-stream")

    return FileResponse(
        path=candidate,
        media_type=media_type,
        filename=candidate.name,
        headers=_NO_STORE_HEADERS,
    )


def delete_login_background():
    """Delete the login background image."""
    deleted = False
    for existing in _LOGIN_BG_DIR.glob("background.*"):
        with suppress(Exception):
            existing.unlink()
            deleted = True

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Login background not found")

    return {"status": "success"}


# -------------------
# LDAP CA Certificate
# -------------------
def _managed_ldap_ca_cert_path() -> Path:
    """Get the path for the managed LDAP CA certificate."""
    return _LDAP_CERT_DIR / _LDAP_CA_CERT_FILENAME


def _parse_ldap_ca_certificate_upload(certificate: UploadFile) -> bytes:
    """Parse and validate LDAP CA certificate upload."""
    payload = _read_upload_bytes(
        certificate,
        limit_bytes=_MAX_LDAP_CA_CERT_UPLOAD_BYTES,
        kind="LDAP CA certificate",
    )

    extension = Path(certificate.filename or "").suffix.lower().lstrip(".")
    if extension and extension not in _ALLOWED_LDAP_CA_CERT_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LDAP CA certificate must be uploaded as PEM, CRT, or CER.",
        )

    try:
        cert_text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LDAP CA certificate must be UTF-8 PEM text.",
        ) from exc

    normalized = cert_text.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LDAP CA certificate upload is empty.",
        )

    if "-----BEGIN CERTIFICATE-----" not in normalized or "-----END CERTIFICATE-----" not in normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LDAP CA certificate must include BEGIN/END CERTIFICATE PEM blocks.",
        )

    if not normalized.endswith("\n"):
        normalized = f"{normalized}\n"

    return normalized.encode("utf-8")


def upload_ldap_ca_certificate(certificate: UploadFile = File(...), db: Optional[Session] = None):
    """Upload an LDAP CA certificate."""
    cert_payload: bytes | None = None
    try:
        cert_payload = _parse_ldap_ca_certificate_upload(certificate)

        _LDAP_CERT_DIR.mkdir(parents=True, exist_ok=True)

        for existing in _LDAP_CERT_DIR.glob("ldap_ca_cert.*"):
            with suppress(Exception):
                existing.unlink()

        cert_path = _managed_ldap_ca_cert_path()
        with open(cert_path, "wb") as cert_file:
            cert_file.write(cert_payload)

        if db is not None:
            update_page_key_value_by_page_and_key("login_ldap", "ldap_ca_cert_file", str(cert_path), db)
    finally:
        with suppress(Exception):
            certificate.file.close()

    return {
        "status": "success",
        "filename": _LDAP_CA_CERT_FILENAME,
        "path": str(_managed_ldap_ca_cert_path()),
    }


def get_ldap_ca_certificate_status(db: Optional[Session] = None):
    """Get LDAP CA certificate status."""
    managed_path = _managed_ldap_ca_cert_path()
    managed_path_str = str(managed_path)

    ldap_settings = get_settings_page_data(db, "login_ldap") if db is not None else {}
    configured_path = str(ldap_settings.get("ldap_ca_cert_file") or "").strip()

    using_managed_path = False
    if configured_path:
        try:
            using_managed_path = Path(configured_path).resolve() == managed_path.resolve()
        except Exception:
            using_managed_path = configured_path == managed_path_str

    uploaded = False
    size_bytes = None
    updated_at = None
    try:
        managed_stat = managed_path.stat()
        uploaded = True
        size_bytes = managed_stat.st_size
        updated_at = datetime.fromtimestamp(managed_stat.st_mtime, tz=timezone.utc).isoformat()
    except FileNotFoundError:
        uploaded = False

    return {
        "status": "success",
        "uploaded": uploaded,
        "filename": _LDAP_CA_CERT_FILENAME if uploaded else None,
        "managed_path": managed_path_str,
        "configured_path": configured_path or None,
        "using_managed_path": using_managed_path,
        "size_bytes": size_bytes,
        "updated_at": updated_at,
    }


def delete_ldap_ca_certificate(db: Optional[Session] = None):
    """Delete the LDAP CA certificate."""
    ldap_settings = get_settings_page_data(db, "login_ldap") if db is not None else {}
    configured_path = str(ldap_settings.get("ldap_ca_cert_file") or "").strip()

    deleted = False
    for existing in _LDAP_CERT_DIR.glob("ldap_ca_cert.*"):
        with suppress(Exception):
            existing.unlink()
            deleted = True

    if configured_path and db is not None:
        update_page_key_value_by_page_and_key("login_ldap", "ldap_ca_cert_file", "", db)
        return {"status": "success"}

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LDAP CA certificate not found")

    return {"status": "success"}


# -------------------
# Favicon
# -------------------
def _enforce_upload_size(upload: UploadFile, limit_bytes: int = _MAX_ICON_UPLOAD_BYTES) -> int:
    """Ensure the uploaded file does not exceed limit bytes.

    Returns the total file size in bytes. Raises HTTPException if the limit is
    exceeded or the size cannot be determined.
    """

    file_obj = upload.file
    if isinstance(file_obj, SpooledTemporaryFile):
        max_size = getattr(file_obj, "max_size", None)
        if max_size and max_size > limit_bytes:
            # Force rollover to disk to measure accurately
            file_obj.rollover()

    current_pos = file_obj.tell()
    file_obj.seek(0, os.SEEK_END)
    size = file_obj.tell()
    file_obj.seek(0)
    if size > limit_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Upload exceeds the allowed size of {limit_bytes // (1024 * 1024)} MB.",
        )
    return size


def _read_upload_bytes(upload: UploadFile, *, limit_bytes: int, kind: str) -> bytes:
    """Read bytes from an upload file with size validation."""
    _enforce_upload_size(upload, limit_bytes)
    upload.file.seek(0)
    payload = upload.file.read()
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} upload is empty.",
        )
    return payload


def _svg_local_name(name: str) -> str:
    """Return the local part of an XML tag or attribute name."""
    if name.startswith("{"):
        return name.split("}", 1)[1]
    return name


def _is_safe_svg_data_image(value: str) -> bool:
    """Allow only embedded raster data URIs inside uploaded SVG branding assets."""
    match = _SAFE_DATA_IMAGE_RE.fullmatch(str(value or "").strip())
    if not match:
        return False

    mime_type = match.group(1).lower()
    if mime_type not in _SAFE_EMBEDDED_SVG_IMAGE_MIME_TYPES:
        return False

    try:
        # Validate the base64 payload so malformed or partially injected URIs are rejected.
        base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except (binascii.Error, ValueError):
        return False

    return True


def _is_safe_svg_presentation_value(value: str) -> bool:
    """Reject presentation values that try to smuggle active content into SVG."""
    normalized = str(value or "").strip()
    lowered = normalized.lower()
    if not normalized:
        return False
    if any(token in lowered for token in ("javascript:", "vbscript:", "expression(", "@import", "<", ">")):
        return False
    if "url(" in lowered and not _SAFE_SVG_URL_REFERENCE_RE.fullmatch(normalized):
        return False
    return True


def _sanitize_svg_style_value(style_value: str) -> str:
    """Keep only safe style declarations needed by uploaded branding SVGs."""
    sanitized_parts: list[str] = []
    for chunk in str(style_value or "").split(";"):
        if ":" not in chunk:
            continue
        property_name, raw_value = chunk.split(":", 1)
        normalized_property = property_name.strip()
        normalized_value = raw_value.strip()
        if normalized_property not in _SANITIZED_BRANDING_SVG_STYLE_PROPERTIES:
            continue
        if not _is_safe_svg_presentation_value(normalized_value):
            continue
        sanitized_parts.append(f"{normalized_property}:{normalized_value}")
    return ";".join(sanitized_parts)


def _sanitize_svg_href_value(tag_name: str, value: str) -> str | None:
    """Validate href/xlink:href values according to the SVG element using them."""
    normalized = str(value or "").strip()
    if not normalized:
        return None

    if tag_name == "use":
        return normalized if _SAFE_SVG_FRAGMENT_REFERENCE_RE.fullmatch(normalized) else None

    if tag_name == "image":
        return normalized if _is_safe_svg_data_image(normalized) else None

    return None


def _sanitize_branding_svg_node(node: ET.Element) -> ET.Element | None:
    """Recursively sanitize an SVG node while preserving safe vector features."""
    tag_name = _svg_local_name(str(node.tag))
    if tag_name not in _SANITIZED_BRANDING_SVG_ALLOWED_TAGS:
        return None

    safe_node = ET.Element(node.tag)
    allowed_attributes = set(_SANITIZED_BRANDING_SVG_GLOBAL_ATTRIBUTES)
    allowed_attributes.update(_SANITIZED_BRANDING_SVG_TAG_ATTRIBUTES.get(tag_name, frozenset()))

    for attribute_name, raw_value in node.attrib.items():
        local_attribute = _svg_local_name(attribute_name)
        normalized_value = str(raw_value or "").strip()

        # Strip event handlers and inline document sources entirely.
        if local_attribute.lower().startswith("on") or local_attribute.lower() == "srcdoc":
            continue

        # Only preserve hrefs that stay within the document or embed raster data safely.
        if attribute_name == f"{{{_XLINK_NS}}}href" or local_attribute == "href":
            sanitized_href = _sanitize_svg_href_value(tag_name, normalized_value)
            if sanitized_href:
                safe_node.set(attribute_name, sanitized_href)
            continue

        # xml:space can be preserved on the root SVG without affecting safety.
        if attribute_name == f"{{{_XML_NS}}}space" and tag_name == "svg":
            safe_node.set(attribute_name, normalized_value)
            continue

        if local_attribute not in allowed_attributes or not normalized_value:
            continue

        if local_attribute == "style":
            sanitized_style = _sanitize_svg_style_value(normalized_value)
            if sanitized_style:
                safe_node.set("style", sanitized_style)
            continue

        if not _is_safe_svg_presentation_value(normalized_value):
            continue

        safe_node.set(attribute_name, normalized_value)

    if tag_name in {"title", "desc"}:
        safe_node.text = str(node.text or "").strip()

    for child in list(node):
        sanitized_child = _sanitize_branding_svg_node(child)
        if sanitized_child is not None:
            safe_node.append(sanitized_child)

    return safe_node


def _sanitize_svg_payload(payload: bytes, *, kind: str) -> bytes:
    """Sanitize SVG payload while preserving safe branding SVG features."""
    try:
        svg_text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} must be UTF-8 encoded SVG.",
        ) from exc

    if "<svg" not in svg_text.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} must be a valid SVG image.",
        )

    # Reject XML features that can expand entities or import external stylesheets before parsing.
    if _SANITIZED_ICON_SVG_BLOCKLIST.search(svg_text):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} contains unsafe SVG content.",
        )

    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} must be a valid SVG image.",
        ) from exc

    if _svg_local_name(str(root.tag)) != "svg":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} must be a valid SVG image.",
        )

    sanitized_root = _sanitize_branding_svg_node(root)
    if sanitized_root is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} contains unsupported SVG content.",
        )

    sanitized_bytes = ET.tostring(sanitized_root, encoding="utf-8", xml_declaration=True)
    if b"<svg" not in sanitized_bytes.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} contains unsupported SVG content.",
        )

    return sanitized_bytes


def _load_validated_raster_image(payload: bytes, *, kind: str) -> tuple[Image.Image, str]:
    """Load and validate a raster image (PNG, JPEG, WEBP)."""
    try:
        with Image.open(BytesIO(payload)) as probe:
            probe.verify()
        image = Image.open(BytesIO(payload))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} must be a supported PNG, JPEG, or WEBP image.",
        ) from exc

    detected_format = str(image.format or "").upper()
    extension = _ALLOWED_RASTER_IMAGE_FORMATS.get(detected_format)
    if extension is None:
        image.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} must be a supported PNG, JPEG, or WEBP image.",
        )

    return image, extension


def _parse_image_upload(
    upload: UploadFile,
    *,
    kind: str,
    limit_bytes: int,
    allow_svg: bool,
) -> dict[str, Any]:
    """Parse and validate an image upload."""
    payload = _read_upload_bytes(upload, limit_bytes=limit_bytes, kind=kind)
    extension = Path(upload.filename or "").suffix.lower().lstrip(".")
    content_type = str(upload.content_type or "").strip().lower()

    if extension and extension not in _ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} must be uploaded as PNG, JPEG, WEBP, or SVG.",
        )

    if content_type and content_type not in _ALLOWED_IMAGE_CONTENT_TYPES and not content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} must be uploaded as an image.",
        )

    if allow_svg and (extension == "svg" or content_type == "image/svg+xml"):
        sanitized_payload = _sanitize_svg_payload(payload, kind=kind)
        return {"kind": "svg", "payload": sanitized_payload, "extension": "svg"}

    image, raster_extension = _load_validated_raster_image(payload, kind=kind)
    return {
        "kind": "raster",
        "payload": payload,
        "extension": raster_extension,
        "image": image,
    }


def upload_icon(icon: UploadFile = File(...)):
    """Upload an icon image and generate favicon set."""
    # Save the icon in the data folder under /logo/ and generate the favicon set.
    parsed_upload: dict[str, Any] | None = None
    try:
        parsed_upload = _parse_image_upload(
            icon,
            kind="Icon",
            limit_bytes=_MAX_ICON_UPLOAD_BYTES,
            allow_svg=True,
        )

        file_extension = str(parsed_upload["extension"])

        # Build directory path relative to the project root (app/data/logo)
        _LOGO_DIR.mkdir(parents=True, exist_ok=True)

        if file_extension == "svg":
            # Read SVG bytes once for both storage and rasterization
            svg_bytes = cast(bytes, parsed_upload["payload"])

            # Remove any existing PNG icons (stale fallbacks) since SVG becomes authoritative
            try:
                patterns = (
                    "favicon-*.png",
                    "apple-touch-icon.png",
                    "icon.png",
                )
                for pattern in patterns:
                    for stale in _LOGO_DIR.glob(pattern):
                        with suppress(Exception):
                            stale.unlink()
            except Exception:
                # Best-effort cleanup; continue even if some files cannot be deleted
                pass

            # Persist the original SVG
            svg_path = _FAVICON_SVG_PATH
            with open(svg_path, "wb") as f:
                f.write(svg_bytes)

            # Attempt to generate PNG fallbacks for browsers that do not support
            # SVG favicons (for example, older Safari versions).
            try:
                for size, filename in _PNG_ICON_SIZES:
                    png_path = _LOGO_DIR / filename
                    png_bytes = rasterize_svg_to_png_bytes(
                        svg_bytes=svg_bytes,
                        output_width=size,
                        output_height=size,
                    )
                    _save_rasterized_icon_variant(
                        png_path=png_path,
                        png_bytes=cast(bytes, png_bytes),
                        size=size,
                    )

                # Additionally create a bounded square PNG icon (icon.png) for
                # legacy callers that do not ask for a specific favicon size.
                high_res = _MAX_ICON_PNG_DIMENSION
                icon_png_path = _ICON_PNG_PATH
                icon_png_bytes = rasterize_svg_to_png_bytes(
                    svg_bytes=svg_bytes,
                    output_width=high_res,
                    output_height=high_res,
                )
                with open(icon_png_path, "wb") as out:
                    out.write(cast(bytes, icon_png_bytes))
            except Exception:  # noqa: BLE001
                # Covers missing optional dependencies as well as rasterization
                # failures from malformed or unsupported SVG input.
                logger.warning("SVG rasterizer unavailable; SVG stored without PNG fallbacks.")

            return {"status": "success", "format": "svg+png"}

        # Remove any existing vector favicon so that new PNG set becomes authoritative
        svg_path = _FAVICON_SVG_PATH
        if svg_path.exists():
            with suppress(Exception):
                svg_path.unlink()

        image = cast(Image.Image, parsed_upload["image"])

        for size, filename in _PNG_ICON_SIZES:
            resample_filter = _icon_resample_filter()
            if size == _APPLE_TOUCH_ICON_SIZE:
                # iOS does not add its own optical padding before applying the
                # Home Screen mask, so generate a dedicated safe-area variant.
                img_resized = _create_apple_touch_icon(image)
            else:
                img_resized = image.resize((size, size), resample_filter)
            buf = BytesIO()
            img_resized.save(buf, format="PNG")
            img_resized.close()
            buf.seek(0)

            file_path = _LOGO_DIR / filename

            # Persist the resized icon
            with open(file_path, "wb") as f:
                f.write(buf.read())

        # Create a high-resolution square icon.png without upscaling
        try:
            # Ensure we work with RGBA to preserve transparency if present
            if image.mode not in ("RGBA", "LA"):
                image = image.convert("RGBA")

            width, height = image.size
            # Determine the largest possible square crop centered
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            right = left + side
            bottom = top + side
            square = image.crop((left, top, right, bottom))
            if square.width > _MAX_ICON_PNG_DIMENSION or square.height > _MAX_ICON_PNG_DIMENSION:
                # Keep the generic icon endpoint reasonably small even when an
                # admin uploads a very large source image.
                square.thumbnail(
                    (_MAX_ICON_PNG_DIMENSION, _MAX_ICON_PNG_DIMENSION),
                    resample_filter,
                )

            icon_png_path = _ICON_PNG_PATH
            square.save(icon_png_path, format="PNG")
        except Exception:
            # If anything goes wrong, skip icon.png creation silently to not break upload
            pass

        return {"status": "success", "format": "png"}
    finally:
        if parsed_upload and parsed_upload.get("image") is not None:
            with suppress(Exception):
                parsed_upload["image"].close()
        with suppress(Exception):
            icon.file.close()


# -------------------
# Favicon
# -------------------
def get_icon(size: int | None = None, v: str | None = None):
    """Return a favicon image of the requested size.

    Parameters
    ----------
    size: int | None, optional
        Desired square size in pixels. Supported values: None (default), 16, 32, 180, 512.
        If None, returns the high-resolution square icon (icon.png).
    v: str | None, optional
        Cache-busting asset version. Versioned requests are safe to cache for a long time.
    """
    from fastapi.responses import FileResponse
    from fastapi.responses import RedirectResponse

    current_version = _get_icon_asset_version()
    if v is None and current_version != "0":
        # Static HTML cannot know the latest filesystem version. Redirecting the
        # stable URL keeps old callers working while letting the browser cache
        # the final icon asset as immutable.
        return RedirectResponse(
            url=_versioned_icon_url(size),
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers=_ICON_REDIRECT_CACHE_HEADERS,
        )

    cache_headers = _VERSIONED_CACHE_HEADERS if v else _CACHE_HEADERS

    # If size is None, prioritize returning the vector favicon.svg; otherwise return icon.png
    if size is None:
        if _FAVICON_SVG_PATH.exists():
            return FileResponse(
                path=_FAVICON_SVG_PATH,
                media_type="image/svg+xml",
                headers=cache_headers,
            )

        if _ICON_PNG_PATH.exists():
            return FileResponse(
                path=_ICON_PNG_PATH,
                media_type="image/png",
                headers=cache_headers,
            )
        # Fallback to the largest available favicon
        size = 512

    filename = _ICON_FILENAME_MAP.get(size)
    # If size provided is not one of the supported keys, fall back to icon.png
    if filename is None:
        if _ICON_PNG_PATH.exists():
            return FileResponse(
                path=_ICON_PNG_PATH,
                media_type="image/png",
                headers=cache_headers,
            )
        # If even icon.png is missing, try largest favicon
        filename = _ICON_FILENAME_MAP[512]

    icon_path = _LOGO_DIR / filename

    if not icon_path.exists():
        if _FAVICON_SVG_PATH.exists():
            # Attempt to rasterize SVG into the requested size on-the-fly.
            try:
                png_bytes = rasterize_svg_to_png_bytes(
                    svg_path=_FAVICON_SVG_PATH,
                    output_width=size,
                    output_height=size,
                )
                # Persist the generated PNG so repeated requests stay cheap.
                _save_rasterized_icon_variant(
                    png_path=icon_path,
                    png_bytes=cast(bytes, png_bytes),
                    size=size,
                )

                return FileResponse(
                    path=icon_path,
                    media_type="image/png",
                    headers=cache_headers,
                )
            except Exception:  # noqa: BLE001
                # Any failure here should still leave the SVG favicon usable.
                return FileResponse(
                    path=_FAVICON_SVG_PATH,
                    media_type="image/svg+xml",
                    headers=cache_headers,
                )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Icon not found")

    return FileResponse(
        path=icon_path,
        media_type="image/png",
        headers=cache_headers,
    )


# -------------------
# Site Manifest for PWA
# -------------------
def get_site_manifest(db: Session):
    """Return the inlined Web App Manifest, overriding name fields with application_name."""
    from fastapi.responses import JSONResponse

    manifest_data = deepcopy(_SITE_MANIFEST_TEMPLATE)
    manifest_data["icons"] = [
        {**icon, "src": _versioned_icon_url(int(str(icon["sizes"]).split("x", maxsplit=1)[0]))}
        for icon in manifest_data["icons"]
    ]

    application_name = "Omlorix"
    if db is not None:
        try:
            value = get_value_by_page_and_key("general", "application_name", db)
            if isinstance(value, str) and value.strip():
                application_name = value.strip()
        except Exception:
            # Non-fatal: fall back to default name if settings are unavailable
            pass

    manifest_data["name"] = application_name
    manifest_data["short_name"] = application_name

    return JSONResponse(
        content=manifest_data,
        media_type="application/manifest+json",
        headers=_CACHE_HEADERS,
    )


# -------------------
# Reset Settings To Default
# -------------------
def _load_canonical_defaults() -> dict[str, dict]:
    """Load and normalize the canonical defaults from app.settings.defaults."""
    # Local import to avoid import cycles at module level
    try:
        from app.settings.defaults import DEFAULT_SETTINGS  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to load default settings: {e}")

    normalized = DEFAULT_SETTINGS
    if not isinstance(normalized, dict):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="DEFAULT_SETTINGS must be a dict")
    # Ensure page->dict shape
    for page, data in normalized.items():
        if not isinstance(data, dict):
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"DEFAULT_SETTINGS[{page!r}] must be a dict")
    return normalized  # type: ignore[return-value]

# -------------------
# Complete Server Setup
# -------------------
def complete_server_setup(
    application_name: str,
    public_urls: list[str],
    default_user_role: str,
    db: Session,
):
    """Complete the initial server setup.
    Sets application name, public URLs, default user role, and marks server_setup state as True.
    """
    from sqlalchemy.orm.attributes import flag_modified
    
    # Validate inputs
    allowed_roles = {"user", "pending"}
    if default_user_role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid default user role. Allowed values are: {', '.join(sorted(allowed_roles))}",
        )
    application_name = _validate_application_name(application_name)
    public_urls = validate_and_normalize_public_urls(public_urls)
    now = datetime.now(timezone.utc)
    
    # Update general settings (application_name and public_url)
    general_settings = get_settings_page(db, "general")
    if not general_settings:
        # Create general settings if they don't exist
        defaults = _load_canonical_defaults()
        general_data = deepcopy(defaults.get("general", {}))
        general_data["application_name"] = application_name
        general_data["public_url"] = public_urls
        general_settings = Settings(page_name="general", data=general_data, updated_at=now)
        db.add(general_settings)
    else:
        general_settings.data["application_name"] = application_name
        general_settings.data["public_url"] = public_urls
        general_settings.updated_at = now
        flag_modified(general_settings, "data")
    
    # Update login_general settings
    login_general_settings = get_settings_page(db, "login_general")
    if not login_general_settings:
        defaults = _load_canonical_defaults()
        login_general_data = deepcopy(defaults.get("login_general", {}))
        login_general_data["default_user_role"] = default_user_role
        login_general_settings = Settings(page_name="login_general", data=login_general_data, updated_at=now)
        db.add(login_general_settings)
    else:
        login_general_settings.data["default_user_role"] = default_user_role
        login_general_settings.updated_at = now
        flag_modified(login_general_settings, "data")
    
    # Update states settings (server_setup = True)
    states_settings = get_settings_page(db, "states")
    if not states_settings:
        # Create states settings if they don't exist
        defaults = _load_canonical_defaults()
        states_data = deepcopy(defaults.get("states", {}))
        states_data["server_setup"] = True
        states_settings = Settings(page_name="states", data=states_data, updated_at=now)
        db.add(states_settings)
    else:
        states_settings.data["server_setup"] = True
        states_settings.updated_at = now
        flag_modified(states_settings, "data")
    
    db.commit()
    
    # Clear cache so fresh values are read
    invalidate_settings_cache()
    
    return {
        "status": "success",
        "public_urls": public_urls,
        "primary_public_url": public_urls[0],
    }
