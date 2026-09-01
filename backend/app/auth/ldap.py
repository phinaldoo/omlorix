from __future__ import annotations

from dataclasses import dataclass
import binascii
import logging
import ssl
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.ldap_transport import (
    LDAP_TRANSPORT_SECURITY_RUNTIME_ERROR_DETAIL,
    get_ldap_transport_security_policy,
)
from app.database import SessionLocal
from app.logging.models import create_admin_notification
from app.settings.models import get_settings_page_data

logger = logging.getLogger(__name__)

_LDAP_UNAVAILABLE_UNTIL = 0.0
_LDAP_UNAVAILABLE_COOLDOWN_SECONDS = 30.0
_LDAP_NOTIFICATION_SENT_UNTIL = 0.0
_LDAP_NOTIFICATION_COOLDOWN_SECONDS = 300.0
_LDAP_NOTIFICATION_LOCK = threading.Lock()

try:
    from ldap3 import BASE, FIRST, LEVEL, SUBTREE, Connection, Server, ServerPool, Tls
    from ldap3.core.exceptions import LDAPException
    from ldap3.utils.conv import escape_filter_chars
except Exception:  # pragma: no cover - exercised only when dependency is absent
    BASE = LEVEL = SUBTREE = None
    Connection = Server = ServerPool = Tls = None
    FIRST = None
    LDAPException = Exception

    def escape_filter_chars(value: str) -> str:
        return (
            str(value)
            .replace("\\", r"\5c")
            .replace("*", r"\2a")
            .replace("(", r"\28")
            .replace(")", r"\29")
            .replace("\x00", r"\00")
        )


@dataclass
class LDAPGroup:
    dn: str
    name: str


@dataclass
class LDAPAuthenticatedUser:
    identifier: str
    dn: str
    directory_user_id: str
    email: str
    first_name: str
    last_name: str
    display_name: str
    username: str
    groups: list[LDAPGroup]
    raw_attributes: dict[str, Any]


def _as_bool(value: Any, default: bool = False) -> bool:
    """Coerce value to boolean."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _normalize_text(value: Any) -> str:
    """Normalize LDAP attribute value to string."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return binascii.hexlify(value).decode("ascii")
    return str(value).strip()


def _first_text(value: Any) -> str:
    """Get first non-empty text value from attribute."""
    if isinstance(value, (list, tuple, set)):
        for item in value:
            normalized = _normalize_text(item)
            if normalized:
                return normalized
        return ""
    return _normalize_text(value)


def _list_text(value: Any) -> list[str]:
    """Convert attribute value to list of strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    result: list[str] = []
    for item in items:
        normalized = _normalize_text(item)
        if normalized:
            result.append(normalized)
    return result


def _dn_attribute_value(dn: str, attribute: str) -> str:
    """Extract attribute value from DN string."""
    if not dn or not attribute:
        return ""
    prefix = f"{attribute.strip().lower()}="
    for segment in dn.split(","):
        part = segment.strip()
        lowered = part.lower()
        if lowered.startswith(prefix):
            return part[len(prefix) :].strip()
    return ""


def _scope_from_setting(value: Any):
    """Convert LDAP search scope setting to ldap3 constant."""
    normalized = str(value or "subtree").strip().lower()
    mapping = {
        "base": BASE,
        "level": LEVEL,
        "onelevel": LEVEL,
        "subtree": SUBTREE,
    }
    return mapping.get(normalized, SUBTREE)


def _ldap_temporarily_unavailable() -> bool:
    """Check if LDAP is temporarily unavailable."""
    return time.monotonic() < _LDAP_UNAVAILABLE_UNTIL


def _mark_ldap_temporarily_unavailable() -> None:
    """Mark LDAP as temporarily unavailable."""
    global _LDAP_UNAVAILABLE_UNTIL
    _LDAP_UNAVAILABLE_UNTIL = time.monotonic() + _LDAP_UNAVAILABLE_COOLDOWN_SECONDS


def _send_ldap_connection_error_notification(
    error_message: str, server_uri: str
) -> None:
    """Send admin notification for LDAP connection error."""
    global _LDAP_NOTIFICATION_SENT_UNTIL
    with _LDAP_NOTIFICATION_LOCK:
        if time.monotonic() < _LDAP_NOTIFICATION_SENT_UNTIL:
            return
        _LDAP_NOTIFICATION_SENT_UNTIL = (
            time.monotonic() + _LDAP_NOTIFICATION_COOLDOWN_SECONDS
        )

    try:
        with SessionLocal() as db:
            create_admin_notification(
                db,
                category="ldap",
                message=f"LDAP connection failed: {error_message}",
                notification_type="error",
                details={
                    "server_uri": server_uri,
                    "error": error_message,
                    "timestamp": time.time(),
                },
            )
        logger.info("LDAP connection error notification sent to admins")
    except Exception as exc:
        logger.exception("Failed to send LDAP connection error notification: %s", exc)


class LDAPAuthProvider:
    """LDAP authentication provider."""

    def __init__(self, db: Session):
        """Initialize LDAP authentication provider."""
        self.db = db
        self._load_settings()

    def _load_settings(self) -> None:
        """Load LDAP settings from database."""
        self.settings = get_settings_page_data(self.db, "login_ldap")

    def is_enabled(self) -> bool:
        """Check if LDAP authentication is enabled."""
        return (
            _as_bool(self.settings.get("enable_ldap"))
            and bool(self._server_uris())
            and bool(_normalize_text(self.settings.get("ldap_user_base_dn")))
        )

    def _server_uris(self) -> list[str]:
        """Return the validated, ordered LDAP endpoint pool."""

        raw_value = self.settings.get("ldap_server_uris")
        if not isinstance(raw_value, list):
            return []
        return [endpoint for value in raw_value if (endpoint := _normalize_text(value))]

    def _uses_start_tls(self) -> bool:
        """Return whether the endpoint pool requires a StartTLS upgrade."""

        endpoints = self._server_uris()
        return bool(
            endpoints and urlsplit(endpoints[0]).scheme.lower() == "ldap+starttls"
        )

    def get_public_status(self) -> dict[str, Any]:
        """Get public status of LDAP authentication."""
        return {
            "enabled": self.is_enabled(),
            "label": _normalize_text(self.settings.get("ldap_label"))
            or "Directory Sign-In",
            "identifier_hint": _normalize_text(
                self.settings.get("ldap_identifier_hint")
            )
            or "Email or directory login",
        }

    def authenticate(
        self, identifier: str, password: str
    ) -> LDAPAuthenticatedUser | None:
        """Authenticate user against LDAP directory."""
        if not self.is_enabled():
            return None
        if not Connection or not Server:
            raise HTTPException(
                status_code=500, detail="LDAP support is not installed on this server."
            )

        normalized_identifier = _normalize_text(identifier)
        if not normalized_identifier or not password:
            return None

        search_conn = self._open_search_connection()
        try:
            user_entry = self._find_user(search_conn, normalized_identifier)
            if user_entry is None:
                return None
            user_dn = _normalize_text(getattr(user_entry, "entry_dn", ""))
            if not user_dn:
                logger.warning(
                    "LDAP search returned an entry without a DN for identifier %s",
                    normalized_identifier,
                )
                return None

            if not self._verify_user_bind(user_dn, password):
                return None

            attributes = getattr(user_entry, "entry_attributes_as_dict", {}) or {}
            groups = self._resolve_groups(
                search_conn, user_entry, normalized_identifier
            )

            email_attribute = (
                _normalize_text(self.settings.get("ldap_email_attribute")) or "mail"
            )
            email = _first_text(attributes.get(email_attribute))
            if not email and "@" in normalized_identifier:
                email = normalized_identifier
            email = email.lower().strip()

            display_name_attribute = (
                _normalize_text(self.settings.get("ldap_display_name_attribute"))
                or "displayName"
            )
            display_name = _first_text(attributes.get(display_name_attribute))
            first_name = _first_text(
                attributes.get(
                    _normalize_text(self.settings.get("ldap_first_name_attribute"))
                    or "givenName"
                )
            )
            last_name = _first_text(
                attributes.get(
                    _normalize_text(self.settings.get("ldap_last_name_attribute"))
                    or "sn"
                )
            )
            if not first_name and display_name:
                first_name = display_name.split()[0]
            if not last_name and display_name and len(display_name.split()) > 1:
                last_name = " ".join(display_name.split()[1:])

            username = _first_text(
                attributes.get(
                    _normalize_text(self.settings.get("ldap_username_attribute"))
                    or "uid"
                )
            )

            directory_user_id = self._extract_directory_user_id(attributes)

            if not email:
                raise HTTPException(
                    status_code=400,
                    detail="LDAP user entry is missing an email address.",
                )

            return LDAPAuthenticatedUser(
                identifier=normalized_identifier,
                dn=user_dn,
                directory_user_id=directory_user_id,
                email=email,
                first_name=first_name or "User",
                last_name=last_name,
                display_name=display_name,
                username=username,
                groups=groups,
                raw_attributes=attributes,
            )
        finally:
            self._close_connection(search_conn)

    def _build_server(self) -> Any:
        """Build LDAP server configuration."""
        transport_policy = get_ldap_transport_security_policy(self.settings)
        if not transport_policy.allows_bind:
            raise HTTPException(
                status_code=503, detail=LDAP_TRANSPORT_SECURITY_RUNTIME_ERROR_DETAIL
            )

        if transport_policy.using_insecure_plaintext_bind:
            logger.warning(
                "LDAP bind is proceeding without LDAPS or StartTLS because the insecure plaintext "
                "override is enabled for server %s",
                ", ".join(self._server_uris()),
            )

        tls = None
        endpoints = self._server_uris()
        use_ssl = bool(endpoints and urlsplit(endpoints[0]).scheme.lower() == "ldaps")
        validate_cert = _as_bool(self.settings.get("ldap_validate_cert"), True)
        ca_cert_file = _normalize_text(self.settings.get("ldap_ca_cert_file"))
        if Tls and (use_ssl or self._uses_start_tls()):
            tls = Tls(
                validate=ssl.CERT_REQUIRED if validate_cert else ssl.CERT_NONE,
                ca_certs_file=ca_cert_file or None,
            )

        servers = []
        for endpoint in endpoints:
            parsed = urlsplit(endpoint)
            servers.append(
                Server(
                    parsed.hostname,
                    port=parsed.port or (636 if use_ssl else 389),
                    use_ssl=use_ssl,
                    tls=tls,
                    connect_timeout=int(
                        self.settings.get("ldap_connect_timeout_seconds") or 10
                    ),
                )
            )
        if len(servers) == 1:
            return servers[0]
        # Preserve administrator-defined priority while allowing ldap3 to
        # advance through unavailable endpoints in the pool.
        return ServerPool(servers, pool_strategy=FIRST, active=True, exhaust=True)

    def _open_search_connection(self):
        """Open LDAP connection for search operations."""
        if _ldap_temporarily_unavailable():
            raise HTTPException(
                status_code=503, detail="LDAP server is temporarily unavailable."
            )

        server = self._build_server()
        server_uri = ", ".join(self._server_uris())
        bind_dn = _normalize_text(self.settings.get("ldap_bind_dn"))
        bind_password = _normalize_text(self.settings.get("ldap_bind_password"))
        receive_timeout = int(self.settings.get("ldap_receive_timeout_seconds") or 10)

        try:
            conn = Connection(
                server,
                user=bind_dn or None,
                password=bind_password or None,
                auto_bind=False,
                receive_timeout=receive_timeout,
                raise_exceptions=True,
            )
            conn.open()
            if self._uses_start_tls():
                conn.start_tls()
            conn.bind()
            return conn
        except LDAPException as exc:
            _mark_ldap_temporarily_unavailable()
            error_message = str(exc)
            logger.exception("Failed to connect or bind to LDAP search account")
            _send_ldap_connection_error_notification(error_message, server_uri)
            raise HTTPException(
                status_code=500,
                detail="Unable to connect to the configured LDAP server.",
            ) from exc

    def _verify_user_bind(self, user_dn: str, password: str) -> bool:
        """Verify user credentials via LDAP bind."""
        server = self._build_server()
        receive_timeout = int(self.settings.get("ldap_receive_timeout_seconds") or 10)
        try:
            conn = Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=False,
                receive_timeout=receive_timeout,
                raise_exceptions=True,
            )
            conn.open()
            if self._uses_start_tls():
                conn.start_tls()
            conn.bind()
            self._close_connection(conn)
            return True
        except LDAPException as exc:
            error_message = str(exc)
            logger.info("LDAP bind failed for DN %s: %s", user_dn, error_message)
            return False

    def _find_user(self, conn, identifier: str):
        """Find user in LDAP directory."""
        attrs = {
            _normalize_text(self.settings.get("ldap_email_attribute")) or "mail",
            _normalize_text(self.settings.get("ldap_first_name_attribute"))
            or "givenName",
            _normalize_text(self.settings.get("ldap_last_name_attribute")) or "sn",
            _normalize_text(self.settings.get("ldap_display_name_attribute"))
            or "displayName",
            _normalize_text(self.settings.get("ldap_username_attribute")) or "uid",
            _normalize_text(self.settings.get("ldap_user_id_attribute")) or "entryUUID",
        }
        # Only fetch group attributes if group sync is actually enabled
        group_sync_enabled = _as_bool(self.settings.get("ldap_enable_group_sync")) and (
            _as_bool(self.settings.get("ldap_sync_app_group_on_login"))
            or _as_bool(self.settings.get("ldap_sync_role_on_login"))
            or bool(self.settings.get("ldap_required_groups"))
            or bool(self.settings.get("ldap_group_to_app_group"))
            or bool(self.settings.get("ldap_group_to_role"))
        )
        if (
            group_sync_enabled
            and _normalize_text(self.settings.get("ldap_group_source")) == "memberOf"
        ):
            attrs.add(
                _normalize_text(self.settings.get("ldap_group_attribute")) or "memberOf"
            )
        filter_text = self._format_filter(
            _normalize_text(self.settings.get("ldap_user_filter")),
            identifier=identifier,
            email=identifier if "@" in identifier else "",
            username=identifier.split("@", 1)[0],
            user_dn="",
        )
        if not filter_text:
            raise HTTPException(
                status_code=500, detail="LDAP user filter is not configured."
            )

        try:
            conn.search(
                _normalize_text(self.settings.get("ldap_user_base_dn")),
                filter_text,
                search_scope=_scope_from_setting(
                    self.settings.get("ldap_user_search_scope")
                ),
                attributes=sorted(attr for attr in attrs if attr),
                size_limit=2,
            )
        except LDAPException as exc:
            error_text = str(exc)
            error_lower = error_text.lower()
            group_attr = (
                _normalize_text(self.settings.get("ldap_group_attribute")) or "memberOf"
            )
            requested_group_attr = (
                group_sync_enabled
                and _normalize_text(self.settings.get("ldap_group_source"))
                == "memberOf"
            )

            is_attribute_error = (
                "invalid attribute" in error_lower or "attributeerror" in error_lower
            )
            error_mentions_group_attr = (
                group_attr.lower() in error_lower or "memberof" in error_lower
            )

            if (
                is_attribute_error
                and requested_group_attr
                and error_mentions_group_attr
            ):
                logger.warning(
                    "LDAP server does not support group attribute, retrying without it: %s",
                    exc,
                )
                attrs_without_group = {
                    attr for attr in attrs if attr not in {"memberOf", group_attr}
                }
                conn.search(
                    _normalize_text(self.settings.get("ldap_user_base_dn")),
                    filter_text,
                    search_scope=_scope_from_setting(
                        self.settings.get("ldap_user_search_scope")
                    ),
                    attributes=sorted(attr for attr in attrs_without_group if attr),
                    size_limit=2,
                )
            else:
                raise

        if not conn.entries:
            return None
        return conn.entries[0]

    def _resolve_groups(self, conn, user_entry, identifier: str) -> list[LDAPGroup]:
        """Resolve user groups from LDAP."""
        if not _as_bool(self.settings.get("ldap_enable_group_sync")):
            return []
        group_source = (
            _normalize_text(self.settings.get("ldap_group_source")) or "memberOf"
        )
        group_name_attribute = (
            _normalize_text(self.settings.get("ldap_group_name_attribute")) or "cn"
        )
        if group_source == "search":
            group_filter = self._format_filter(
                _normalize_text(self.settings.get("ldap_group_filter")),
                identifier=identifier,
                email=identifier if "@" in identifier else "",
                username=identifier.split("@", 1)[0],
                user_dn=_normalize_text(getattr(user_entry, "entry_dn", "")),
            )
            base_dn = _normalize_text(self.settings.get("ldap_group_base_dn"))
            if not group_filter or not base_dn:
                return []
            conn.search(
                base_dn,
                group_filter,
                search_scope=_scope_from_setting(
                    self.settings.get("ldap_group_search_scope")
                ),
                attributes=[group_name_attribute],
            )
            groups: list[LDAPGroup] = []
            for entry in conn.entries:
                attributes = getattr(entry, "entry_attributes_as_dict", {}) or {}
                name = _first_text(
                    attributes.get(group_name_attribute)
                ) or _dn_attribute_value(
                    _normalize_text(getattr(entry, "entry_dn", "")),
                    group_name_attribute,
                )
                groups.append(
                    LDAPGroup(
                        dn=_normalize_text(getattr(entry, "entry_dn", "")),
                        name=name,
                    )
                )
            return groups

        attributes = getattr(user_entry, "entry_attributes_as_dict", {}) or {}
        membership_attribute = (
            _normalize_text(self.settings.get("ldap_group_attribute")) or "memberOf"
        )
        values = _list_text(attributes.get(membership_attribute))
        return [
            LDAPGroup(
                dn=value,
                name=_dn_attribute_value(value, group_name_attribute)
                or _dn_attribute_value(value, "cn"),
            )
            for value in values
        ]

    def _format_filter(
        self,
        template: str,
        *,
        identifier: str,
        email: str,
        username: str,
        user_dn: str,
    ) -> str:
        """Format LDAP filter template with parameters."""
        safe = {
            "identifier": escape_filter_chars(identifier or ""),
            "email": escape_filter_chars(email or ""),
            "username": escape_filter_chars(username or ""),
            "user_dn": escape_filter_chars(user_dn or ""),
        }
        try:
            return (template or "").format_map(safe).strip()
        except Exception as exc:
            logger.exception("Invalid LDAP filter template %r", template)
            raise HTTPException(
                status_code=500, detail="Invalid LDAP filter template configured."
            ) from exc

    def _extract_directory_user_id(self, attributes: dict[str, Any]) -> str:
        """Extract stable directory user ID from attributes."""
        preferred = (
            _normalize_text(self.settings.get("ldap_user_id_attribute")) or "entryUUID"
        )
        candidates = []
        for attr_name in (
            preferred,
            "entryUUID",
            "objectGUID",
            "objectSid",
            "nsUniqueId",
            "ipaUniqueID",
            "uidNumber",
        ):
            normalized = _normalize_text(attr_name)
            if normalized and normalized not in candidates:
                candidates.append(normalized)

        for attr_name in candidates:
            value = _first_text(attributes.get(attr_name))
            if value:
                return value

        raise HTTPException(
            status_code=400,
            detail=(
                "LDAP user entry is missing a stable directory identifier. "
                "Configure ldap_user_id_attribute to an immutable attribute such as entryUUID or objectGUID."
            ),
        )

    @staticmethod
    def _close_connection(conn) -> None:
        """Close LDAP connection safely."""
        if conn is None:
            return
        try:
            conn.unbind()
        except Exception:
            pass


def get_ldap_provider(db: Session) -> LDAPAuthProvider:
    """Get LDAP authentication provider instance."""
    return LDAPAuthProvider(db)
