from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY = "ldap_allow_insecure_plaintext_bind"
LDAP_TRANSPORT_SECURITY_ADMIN_ERROR_DETAIL = (
    "LDAP login requires LDAPS or StartTLS. Enable LDAPS or StartTLS, or explicitly enable the "
    "insecure plaintext LDAP bind override."
)
LDAP_TRANSPORT_SECURITY_RUNTIME_ERROR_DETAIL = (
    "LDAP sign-in is unavailable because the LDAP connection is configured without LDAPS or "
    "StartTLS. An administrator must enable LDAPS or StartTLS, or explicitly enable the insecure "
    "plaintext LDAP bind override."
)


def _as_bool(value: Any, default: bool = False) -> bool:
    """Coerce a value to boolean."""
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


@dataclass(frozen=True)
class LDAPTransportSecurityPolicy:
    ldap_enabled: bool
    transport_scheme: str | None
    allow_insecure_plaintext_bind: bool

    @property
    def has_secure_transport(self) -> bool:
        return self.transport_scheme in {"ldaps", "ldap+starttls"}

    @property
    def allows_bind(self) -> bool:
        return (
            not self.ldap_enabled
            or self.has_secure_transport
            or self.allow_insecure_plaintext_bind
        )

    @property
    def using_insecure_plaintext_bind(self) -> bool:
        return (
            self.ldap_enabled
            and not self.has_secure_transport
            and self.allow_insecure_plaintext_bind
        )


def get_ldap_transport_security_policy(
    settings: Mapping[str, Any] | None,
) -> LDAPTransportSecurityPolicy:
    """Return the effective LDAP transport security policy for a settings payload."""
    payload = settings or {}
    raw_endpoints = payload.get("ldap_server_uris")
    endpoints = raw_endpoints if isinstance(raw_endpoints, list) else []
    first_endpoint = next(
        (str(endpoint).strip() for endpoint in endpoints if str(endpoint).strip()), ""
    )
    transport_scheme = first_endpoint.partition("://")[0].lower() or None
    return LDAPTransportSecurityPolicy(
        ldap_enabled=_as_bool(payload.get("enable_ldap")),
        transport_scheme=transport_scheme,
        allow_insecure_plaintext_bind=_as_bool(
            payload.get(LDAP_ALLOW_INSECURE_PLAINTEXT_BIND_KEY)
        ),
    )
