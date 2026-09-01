from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod
import xml.etree.ElementTree as XMLBuilder

import defusedxml.ElementTree as ET
from sqlalchemy.orm import Session
from fastapi import HTTPException, Request
import secrets
import base64
import hashlib
import httpx
import json
import zlib
import jwt

from app.settings.models import get_settings_page_data
from app.settings.utils import get_public_url
from app.auth.schemas import EnterpriseSSOProviderType
from app.network.policy import assert_http_url_allowed


# -------------------
# Base Enterprise SSO Provider (Abstract)
# -------------------
class SSOSecurityData:
    """Container for protocol security values stored in the SSO flow cookie."""

    def __init__(
        self,
        nonce: Optional[str] = None,
        request_id: Optional[str] = None,
        code_verifier: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ):
        self.nonce = nonce
        self.request_id = request_id
        self.code_verifier = code_verifier
        self.correlation_id = correlation_id

    def to_json(self) -> str:
        return json.dumps(
            {
                "nonce": self.nonce,
                "request_id": self.request_id,
                "code_verifier": self.code_verifier,
                "correlation_id": self.correlation_id,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "SSOSecurityData":
        try:
            parsed = json.loads(data)
            return cls(
                nonce=parsed.get("nonce"),
                request_id=parsed.get("request_id"),
                code_verifier=parsed.get("code_verifier"),
                correlation_id=parsed.get("correlation_id"),
            )
        except (json.JSONDecodeError, TypeError):
            return cls()


_SAML_PERSISTENT_NAMEID_FORMAT = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"
_SAML_TRANSIENT_NAMEID_FORMAT = "urn:oasis:names:tc:SAML:2.0:nameid-format:transient"


def _looks_like_email(value: str) -> bool:
    candidate = str(value or "").strip()
    if "@" not in candidate:
        return False
    local_part, _, domain = candidate.rpartition("@")
    return bool(local_part and "." in domain)


class EnterpriseSSOProvider(ABC):
    """Abstract base class for enterprise SSO providers."""

    provider_type: str = "base"  # "saml" or "oidc"
    provider_name: str = "base"

    def __init__(self, db: Session):
        self.db = db
        self._load_settings()

    @abstractmethod
    def _load_settings(self) -> None:
        """Load provider-specific settings from database."""
        pass

    @abstractmethod
    def is_enabled(self) -> bool:
        """Check if this provider is enabled."""
        pass

    @abstractmethod
    def get_authorization_url(
        self, redirect_uri: str, state: str, relay_state: Optional[str] = None
    ) -> tuple[str, SSOSecurityData]:
        """Generate the SSO authorization URL and security data to store."""
        pass

    @abstractmethod
    async def handle_callback(
        self,
        request_data: Dict[str, Any],
        redirect_uri: str,
        security_data: Optional[SSOSecurityData] = None,
        request: Request | None = None,
    ) -> Dict[str, Any]:
        """Handle SSO callback and extract user information."""
        pass

    def get_config(self) -> Dict[str, Any]:
        """Get the configuration for this SSO provider."""
        return self.settings

    def validate_domain(self, email: str) -> bool:
        """Validate if email domain matches configured domains."""
        allowed_domains = self.settings.get("allowed_domains", [])
        if not allowed_domains:
            return True

        domain = email.split("@")[-1].lower() if "@" in email else ""
        return domain in [d.lower().strip() for d in allowed_domains]

    def allows_jit_provisioning(self) -> bool:
        """Check if JIT (Just-In-Time) user provisioning is enabled."""
        return self.settings.get("enable_jit_provisioning", True)

    def get_default_role(self) -> str:
        """Get default role for JIT provisioned users."""
        return self.settings.get("default_role", "user")

    def get_default_group(self) -> str:
        """Get default group for JIT provisioned users."""
        return self.settings.get("default_group", "default")

    def get_attribute_mapping(self) -> Dict[str, str]:
        """Get attribute mapping configuration."""
        return self.settings.get(
            "attribute_mapping",
            {
                "email": "email",
                "first_name": "given_name",
                "last_name": "family_name",
                "display_name": "name",
            },
        )

    def link_existing_users_by_email(self) -> bool:
        return bool(self.settings.get("link_existing_users_by_email", False))

    def sync_profile_on_login(self) -> bool:
        return bool(self.settings.get("sync_profile_on_login", False))

    def sync_email_on_login(self) -> bool:
        return bool(self.settings.get("sync_email_on_login", False))

    def sync_app_group_on_login(self) -> bool:
        return bool(self.settings.get("sync_app_group_on_login", False))

    def sync_role_on_login(self) -> bool:
        return bool(self.settings.get("sync_role_on_login", False))

    @staticmethod
    def _normalize_group_token(value: Any) -> str:
        """Normalize a group name for case-insensitive policy matching."""

        return str(value or "").strip().lower()

    @classmethod
    def _group_tokens(cls, values: list[str]) -> set[str]:
        """Return normalized upstream group tokens."""

        return {
            token for value in values if (token := cls._normalize_group_token(value))
        }

    @classmethod
    def _parse_group_mappings(cls, entries: Any) -> list[tuple[str, str]]:
        """Parse ordered ``upstream=target`` mapping entries."""

        mappings: list[tuple[str, str]] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, str):
                continue
            source, separator, target = entry.partition("=")
            source = cls._normalize_group_token(source)
            target = target.strip()
            if separator and source and target:
                mappings.append((source, target))
        return mappings

    def _apply_group_policy(self, claims: Dict[str, Any]) -> Dict[str, Any]:
        """Enforce required groups and resolve safe Omlorix group/role targets."""

        if not bool(self.settings.get("enable_group_sync", False)):
            return {}
        claim_name = str(self.settings.get("group_claim") or "groups").strip()
        raw_groups = claims.get(claim_name)
        if isinstance(raw_groups, str):
            separator = str(self.settings.get("groups_separator") or ",")
            upstream_groups = [
                part.strip() for part in raw_groups.split(separator) if part.strip()
            ]
        elif isinstance(raw_groups, (list, tuple, set)):
            upstream_groups = [
                str(value).strip() for value in raw_groups if str(value).strip()
            ]
        else:
            upstream_groups = []

        tokens = self._group_tokens(upstream_groups)
        required = self._group_tokens(self.settings.get("required_groups") or [])
        if required and not required.intersection(tokens):
            raise HTTPException(
                status_code=403,
                detail="SSO user is not a member of any required upstream group",
            )

        result: Dict[str, Any] = {"upstream_groups": upstream_groups}
        from app.groups.models import get_group, get_group_by_name

        for source, target in self._parse_group_mappings(
            self.settings.get("group_to_app_group")
        ):
            if source not in tokens:
                continue
            group = get_group(self.db, target) or get_group_by_name(self.db, target)
            if group:
                result["omlorix_group_id"] = group.id
                break
        for source, target in self._parse_group_mappings(
            self.settings.get("group_to_role")
        ):
            normalized_role = target.lower()
            if source in tokens and normalized_role in {"user", "pending"}:
                result["omlorix_role"] = normalized_role
                break
        return result


# -------------------
# SAML 2.0 Provider
# -------------------
class SAMLSSOProvider(EnterpriseSSOProvider):
    """SAML 2.0 authentication provider for enterprise SSO."""

    provider_type: str = "saml"
    provider_name: str = "saml"

    def _load_settings(self) -> None:
        """Load SAML settings from database."""
        all_settings = get_settings_page_data(self.db, "login_enterprise_sso")
        advanced_settings = all_settings.get("saml_advanced_settings") or {}
        identity_policy = all_settings.get("saml_identity_policy") or {}
        # Legacy SAML installations used the same entity identifier for the SP
        # and IdP sides of the trust. Mirror the stored-settings normalization
        # here because runtime provider loading does not pass through the admin
        # read or merge paths that perform that compatibility upgrade.
        idp_entity_id = advanced_settings.get("idp_entity_id") or all_settings.get(
            "saml_entity_id", ""
        )
        self.settings = {
            "enabled": all_settings.get("enable_saml", False),
            "entity_id": all_settings.get("saml_entity_id", ""),
            "idp_entity_id": idp_entity_id,
            "sso_url": all_settings.get("saml_sso_url", ""),
            "x509_cert": all_settings.get("saml_x509_cert", ""),
            "additional_x509_certs": advanced_settings.get(
                "additional_x509_certs", []
            ),
            "nameid_format": advanced_settings.get(
                "nameid_format", _SAML_PERSISTENT_NAMEID_FORMAT
            ),
            "sign_authn_requests": advanced_settings.get(
                "sign_authn_requests", False
            ),
            "sp_x509_cert": advanced_settings.get("sp_x509_cert", ""),
            "sp_private_key": advanced_settings.get("sp_private_key", ""),
            "allowed_domains": all_settings.get("saml_allowed_domains", []),
            "link_existing_users_by_email": identity_policy.get(
                "link_existing_users_by_email", False
            ),
            "enable_jit_provisioning": all_settings.get(
                "saml_enable_jit_provisioning", True
            ),
            "default_role": all_settings.get("saml_default_role", "user"),
            "default_group": all_settings.get("saml_default_group", "default"),
            "attribute_mapping": all_settings.get("saml_attribute_mapping", {}),
            "button_text": all_settings.get("saml_button_text", ""),
            **identity_policy,
        }

    def is_enabled(self) -> bool:
        """Return whether SAML is both selected and ready for authentication."""

        core_configuration_ready = (
            self.settings.get("enabled", False)
            and bool(self.settings.get("entity_id"))
            and bool(self.settings.get("idp_entity_id"))
            and bool(self.settings.get("sso_url"))
            and bool(self.settings.get("x509_cert"))
        )
        signing_configuration_ready = not self.settings.get(
            "sign_authn_requests", False
        ) or (
            bool(self.settings.get("sp_x509_cert"))
            and bool(self.settings.get("sp_private_key"))
        )
        return core_configuration_ready and signing_configuration_ready

    def get_authorization_url(
        self, redirect_uri: str, state: str, relay_state: Optional[str] = None
    ) -> tuple[str, SSOSecurityData]:
        """Generate SAML authentication request URL using proper XML builder."""
        if not self.is_enabled():
            raise HTTPException(status_code=400, detail="SAML SSO is not enabled")

        if self.settings.get("sign_authn_requests"):
            # python3-saml owns the Redirect-binding signature construction so
            # the exact bytes and SigAlg parameter match its verifier/toolkit.
            from onelogin.saml2.auth import OneLogin_Saml2_Auth

            parsed_uri = urlparse(redirect_uri)
            request_data = {
                "https": "on" if parsed_uri.scheme == "https" else "off",
                "http_host": parsed_uri.netloc,
                "script_name": parsed_uri.path,
                "get_data": {},
                "post_data": {},
            }
            auth = OneLogin_Saml2_Auth(
                request_data, self._build_saml_settings(redirect_uri)
            )
            destination = auth.login(return_to=relay_state or state)
            return destination, SSOSecurityData(request_id=auth.get_last_request_id())

        # NOTE: Manual SAML AuthnRequest construction rationale:
        # We build the AuthnRequest manually rather than using python3-saml's auth.login() because:
        # 1. We need precise control over the HTTP-Redirect binding with custom deflate encoding
        # 2. The library's login() couples request generation with HTTP response handling
        # 3. We integrate with FastAPI's response model, not the library's Flask-oriented design
        # 4. This allows us to inject custom RelayState without library modifications
        # The xml.etree approach is safe for XML construction (no parsing of untrusted input here)

        # Generate SAML AuthnRequest using proper XML construction
        request_id = f"_{secrets.token_hex(16)}"
        issue_instant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build SAML AuthnRequest using xml.etree (safe for construction, no external input parsed)
        samlp_ns = "urn:oasis:names:tc:SAML:2.0:protocol"
        saml_ns = "urn:oasis:names:tc:SAML:2.0:assertion"

        # Register namespaces to avoid ns0/ns1 prefixes
        XMLBuilder.register_namespace("samlp", samlp_ns)
        XMLBuilder.register_namespace("saml", saml_ns)

        # Create AuthnRequest element
        authn_request_elem = XMLBuilder.Element(
            f"{{{samlp_ns}}}AuthnRequest",
            {
                "ID": request_id,
                "Version": "2.0",
                "IssueInstant": issue_instant,
                "AssertionConsumerServiceURL": redirect_uri,
                "Destination": self.settings.get("sso_url", ""),
            },
        )

        # Add Issuer element
        issuer_elem = XMLBuilder.SubElement(
            authn_request_elem, f"{{{saml_ns}}}Issuer"
        )
        issuer_elem.text = self.settings.get("entity_id", "")

        # Serialize to XML string
        authn_request = XMLBuilder.tostring(authn_request_elem, encoding="unicode")

        # Deflate and encode for HTTP-Redirect binding
        compressed = zlib.compress(authn_request.encode("utf-8"))[
            2:-4
        ]  # Remove zlib header/checksum
        encoded_request = base64.b64encode(compressed).decode()

        # Build SSO URL with SAML request
        params = {
            "SAMLRequest": encoded_request,
            "RelayState": relay_state or state,
        }

        # Return URL and security data containing request_id for InResponseTo validation
        security_data = SSOSecurityData(request_id=request_id)
        sso_url = self.settings["sso_url"]
        separator = "&" if "?" in sso_url else "?"
        return f"{sso_url}{separator}{urlencode(params)}", security_data

    def _build_saml_settings(self, redirect_uri: str) -> Dict[str, Any]:
        """Build python3-saml settings dict from stored config."""
        idp_entity_id = self.settings.get("idp_entity_id", "")

        # Use configurable NameID format, defaulting to 'unspecified' for maximum IdP compatibility
        # (e.g., Azure AD, Okta, OneLogin may have different default formats)
        nameid_format = self.settings.get(
            "nameid_format", "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"
        )

        idp: Dict[str, Any] = {
            "entityId": idp_entity_id,
            "singleSignOnService": {
                "url": self.settings.get("sso_url", ""),
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": self.settings.get("x509_cert", ""),
        }
        signing_certificates = [
            certificate
            for certificate in [
                self.settings.get("x509_cert", ""),
                *(self.settings.get("additional_x509_certs") or []),
            ]
            if str(certificate or "").strip()
        ]
        if len(signing_certificates) > 1:
            idp["x509certMulti"] = {"signing": signing_certificates}

        return {
            "strict": True,
            "debug": False,
            "sp": {
                "entityId": self.settings.get("entity_id", ""),
                "assertionConsumerService": {
                    "url": redirect_uri,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
                "NameIDFormat": nameid_format,
                "x509cert": self.settings.get("sp_x509_cert", ""),
                "privateKey": self.settings.get("sp_private_key", ""),
            },
            "idp": idp,
            "security": {
                "authnRequestsSigned": bool(
                    self.settings.get("sign_authn_requests", False)
                ),
                "wantAssertionsSigned": True,
                "wantMessagesSigned": False,
                "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            },
        }

    def _prepare_saml_request(self, http_request: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare request dict for python3-saml."""
        return {
            "https": "on" if http_request.get("https") else "off",
            "http_host": http_request.get("http_host", ""),
            "script_name": http_request.get("script_name", ""),
            "get_data": http_request.get("get_data", {}),
            "post_data": http_request.get("post_data", {}),
        }

    def _resolve_subject_identifier(
        self,
        *,
        nameid: str,
        nameid_format: str,
        get_attr,
        attr_mapping: Dict[str, str],
    ) -> str:
        """Return an immutable SAML subject or raise if the response only exposes email."""
        subject_attr_key = (
            attr_mapping.get("subject")
            or attr_mapping.get("subject_id")
            or attr_mapping.get("user_id")
            or attr_mapping.get("external_id")
        )
        if subject_attr_key:
            mapped_subject = get_attr(subject_attr_key)
            if mapped_subject:
                return mapped_subject

        normalized_nameid = str(nameid or "").strip()
        normalized_format = str(nameid_format or "").strip()
        if normalized_nameid:
            if normalized_format == _SAML_PERSISTENT_NAMEID_FORMAT:
                return normalized_nameid
            if (
                normalized_format != _SAML_TRANSIENT_NAMEID_FORMAT
                and not _looks_like_email(normalized_nameid)
            ):
                return normalized_nameid

        raise HTTPException(
            status_code=400,
            detail=(
                "SAML response missing an immutable subject identifier. "
                "Configure a subject attribute mapping or a persistent NameID."
            ),
        )

    async def handle_callback(
        self,
        request_data: Dict[str, Any],
        redirect_uri: str,
        security_data: Optional[SSOSecurityData] = None,
        request: Request | None = None,
    ) -> Dict[str, Any]:
        """Handle SAML response with full signature validation."""
        saml_response = request_data.get("SAMLResponse")
        if not saml_response:
            raise HTTPException(status_code=400, detail="Missing SAML response")

        try:
            # Build SAML settings
            saml_settings = self._build_saml_settings(redirect_uri)

            # Parse redirect_uri to extract host info
            parsed_uri = urlparse(redirect_uri)

            # Prepare request for python3-saml
            req = {
                "https": "on" if parsed_uri.scheme == "https" else "off",
                "http_host": parsed_uri.netloc,
                "script_name": parsed_uri.path,
                "get_data": {},
                "post_data": {
                    "SAMLResponse": saml_response,
                    "RelayState": request_data.get("RelayState", ""),
                },
            }

            # Import python3-saml only when SAML callbacks need it. This keeps
            # unrelated app startup paths from depending on xmlsec binaries.
            from onelogin.saml2.auth import OneLogin_Saml2_Auth

            # Create SAML auth object and process response
            # Pass request_id to validate InResponseTo attribute for replay protection
            auth = OneLogin_Saml2_Auth(req, saml_settings)
            stored_request_id = security_data.request_id if security_data else None
            auth.process_response(request_id=stored_request_id)

            # Check for errors - this validates the signature
            errors = auth.get_errors()
            if errors:
                error_reason = auth.get_last_error_reason()
                raise HTTPException(
                    status_code=400,
                    detail=f"SAML validation failed: {', '.join(errors)}. {error_reason or ''}",
                )

            # Verify authentication was successful
            if not auth.is_authenticated():
                raise HTTPException(
                    status_code=401, detail="SAML authentication failed"
                )

            # Extract user attributes from validated response
            nameid = str(auth.get_nameid() or "").strip()
            nameid_format = str(
                getattr(auth, "get_nameid_format", lambda: "")() or ""
            ).strip()
            attributes = auth.get_attributes()

            attr_mapping = self.get_attribute_mapping()

            def get_attr(key: str, default: str = "") -> str:
                """Get first value from a validated SAML attribute list."""
                val = attributes.get(key, [])
                return val[0] if val else default

            email = get_attr(attr_mapping.get("email", "email")) or get_attr("Email")
            if not email and _looks_like_email(nameid):
                email = nameid
            if not email:
                raise HTTPException(
                    status_code=400, detail="No email found in SAML response"
                )

            subject = self._resolve_subject_identifier(
                nameid=nameid,
                nameid_format=nameid_format,
                get_attr=get_attr,
                attr_mapping=attr_mapping,
            )

            user_info = {
                "sub": subject,
                "email": email,
                "email_verified": True,
                "name": get_attr(attr_mapping.get("display_name", "displayName")),
                "given_name": get_attr(attr_mapping.get("first_name", "firstName")),
                "family_name": get_attr(attr_mapping.get("last_name", "lastName")),
                "provider": "saml",
                "provider_id": "default",
            }
            user_info.update(self._apply_group_policy(attributes))

            return user_info

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"Failed to process SAML response: {str(e)}"
            )


# -------------------
# Enterprise OIDC Provider
# -------------------
class EnterpriseOIDCProvider(EnterpriseSSOProvider):
    """Enterprise OIDC authentication provider with advanced features."""

    provider_type: str = "oidc"
    provider_name: str = "oidc"

    def _load_settings(self) -> None:
        """Load OIDC settings from database."""
        all_settings = get_settings_page_data(self.db, "login_enterprise_sso")
        advanced_settings = all_settings.get("oidc_advanced_settings") or {}
        identity_policy = all_settings.get("oidc_identity_policy") or {}
        self.settings = {
            "enabled": all_settings.get("enable_oidc", False),
            "client_id": all_settings.get("oidc_client_id", ""),
            "client_secret": all_settings.get("oidc_client_secret", ""),
            "discovery_url": all_settings.get("oidc_discovery_url", ""),
            "issuer": all_settings.get("oidc_issuer", ""),
            "authorization_endpoint": all_settings.get(
                "oidc_authorization_endpoint", ""
            ),
            "token_endpoint": all_settings.get("oidc_token_endpoint", ""),
            "userinfo_endpoint": all_settings.get("oidc_userinfo_endpoint", ""),
            "jwks_uri": all_settings.get("oidc_jwks_uri", ""),
            "scopes": all_settings.get(
                "oidc_scopes", ["openid", "email", "profile"]
            ),
            "allowed_domains": all_settings.get("oidc_allowed_domains", []),
            "link_existing_users_by_email": identity_policy.get(
                "link_existing_users_by_email", False
            ),
            "enable_jit_provisioning": all_settings.get(
                "oidc_enable_jit_provisioning", True
            ),
            "default_role": all_settings.get("oidc_default_role", "user"),
            "default_group": all_settings.get("oidc_default_group", "default"),
            "attribute_mapping": all_settings.get("oidc_attribute_mapping", {}),
            "button_text": all_settings.get("oidc_button_text", ""),
            **advanced_settings,
            **identity_policy,
        }

    def is_enabled(self) -> bool:
        """Check if OIDC SSO is enabled."""
        return (
            self.settings.get("enabled", False)
            and bool(self.settings.get("client_id"))
            and bool(self.settings.get("client_secret"))
            and (
                bool(self.settings.get("discovery_url"))
                or (
                    bool(self.settings.get("authorization_endpoint"))
                    and bool(self.settings.get("token_endpoint"))
                )
            )
        )

    def _apply_profile_attribute_mapping(
        self, user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Map optional profile claims without changing the verified identity binding."""

        mapped_user_info = dict(user_info)
        attribute_mapping = self.get_attribute_mapping()
        profile_fields = {
            "first_name": "given_name",
            "last_name": "family_name",
            "display_name": "name",
        }
        for mapping_key, output_claim in profile_fields.items():
            source_claim = str(attribute_mapping.get(mapping_key) or "").strip()
            if source_claim and source_claim in user_info:
                mapped_user_info[output_claim] = user_info[source_claim]

        # sub and email deliberately remain sourced from the verified OIDC response.
        # Allowing an administrator-provided mapping to replace either value could
        # bind a login to a different account.
        return mapped_user_info

    def _discover_endpoints_sync(self) -> Dict[str, str]:
        """Discover OIDC endpoints synchronously (for auth URL generation)."""
        discovery_url = self.settings.get("discovery_url")
        if not discovery_url:
            return {
                "authorization_endpoint": self.settings.get(
                    "authorization_endpoint", ""
                ),
                "token_endpoint": self.settings.get("token_endpoint", ""),
                "userinfo_endpoint": self.settings.get("userinfo_endpoint", ""),
            }

        try:
            assert_http_url_allowed(
                self.db, url=discovery_url, feature="OIDC discovery"
            )
            response = httpx.get(discovery_url, timeout=10)
            if response.status_code == 200:
                discovery_data = response.json()
                return {
                    "authorization_endpoint": discovery_data.get(
                        "authorization_endpoint", ""
                    ),
                    "token_endpoint": discovery_data.get("token_endpoint", ""),
                    "userinfo_endpoint": discovery_data.get("userinfo_endpoint", ""),
                }
        except Exception:
            pass

        return {
            "authorization_endpoint": self.settings.get("authorization_endpoint", ""),
            "token_endpoint": self.settings.get("token_endpoint", ""),
            "userinfo_endpoint": self.settings.get("userinfo_endpoint", ""),
        }

    def get_end_session_url(self) -> Optional[str]:
        """Return the browser-reachable RP-initiated logout endpoint.

        Docker deployments commonly use a public/browser origin for authorization
        and a different backend-reachable origin for token and discovery calls.
        Discover logout metadata through the backend origin, then map the endpoint
        back onto the configured authorization origin when both metadata and token
        endpoints use the same internal origin.
        """

        if not self.is_enabled():
            return None

        discovery_url = str(self.settings.get("discovery_url") or "").strip()
        if not discovery_url:
            issuer = urlsplit(str(self.settings.get("issuer") or ""))
            token_endpoint = urlsplit(str(self.settings.get("token_endpoint") or ""))
            if issuer.netloc and token_endpoint.scheme and token_endpoint.netloc:
                discovery_url = urlunsplit(
                    (
                        token_endpoint.scheme,
                        token_endpoint.netloc,
                        f"{issuer.path.rstrip('/')}/.well-known/openid-configuration",
                        "",
                        "",
                    )
                )

        if not discovery_url:
            return None

        try:
            assert_http_url_allowed(
                self.db,
                url=discovery_url,
                feature="OIDC RP-initiated logout discovery",
            )
            response = httpx.get(discovery_url, timeout=10, follow_redirects=True)
            if response.status_code != 200:
                return None
            metadata = response.json()
            if not isinstance(metadata, dict):
                return None
            end_session_url = str(metadata.get("end_session_endpoint") or "").strip()
            if not end_session_url:
                return None

            end_session = urlsplit(end_session_url)
            token_endpoint = urlsplit(str(self.settings.get("token_endpoint") or ""))
            authorization_endpoint = urlsplit(
                str(self.settings.get("authorization_endpoint") or "")
            )
            if (
                end_session.scheme
                and end_session.netloc
                and token_endpoint.scheme == end_session.scheme
                and token_endpoint.netloc == end_session.netloc
                and authorization_endpoint.scheme
                and authorization_endpoint.netloc
            ):
                end_session_url = urlunsplit(
                    (
                        authorization_endpoint.scheme,
                        authorization_endpoint.netloc,
                        end_session.path,
                        end_session.query,
                        end_session.fragment,
                    )
                )

            end_session = urlsplit(end_session_url)
            query = dict(parse_qsl(end_session.query, keep_blank_values=True))
            query.update(
                {
                    "client_id": str(self.settings.get("client_id") or ""),
                    "post_logout_redirect_uri": (
                        f"{get_public_url(self.db).rstrip('/')}/login"
                    ),
                }
            )
            end_session_url = urlunsplit(
                (
                    end_session.scheme,
                    end_session.netloc,
                    end_session.path,
                    urlencode(query),
                    end_session.fragment,
                )
            )

            assert_http_url_allowed(
                self.db,
                url=end_session_url,
                feature="OIDC RP-initiated logout",
            )
            return end_session_url
        except Exception:
            return None

    async def _discover_endpoints(self) -> Dict[str, str]:
        """Discover OIDC endpoints from discovery URL."""
        discovery_url = self.settings.get("discovery_url")
        if not discovery_url:
            return {
                "authorization_endpoint": self.settings.get(
                    "authorization_endpoint", ""
                ),
                "token_endpoint": self.settings.get("token_endpoint", ""),
                "userinfo_endpoint": self.settings.get("userinfo_endpoint", ""),
            }

        try:
            assert_http_url_allowed(
                self.db, url=discovery_url, feature="OIDC discovery"
            )
            async with httpx.AsyncClient() as client:
                response = await client.get(discovery_url)
                if response.status_code == 200:
                    discovery_data = response.json()
                    return {
                        "authorization_endpoint": discovery_data.get(
                            "authorization_endpoint", ""
                        ),
                        "token_endpoint": discovery_data.get("token_endpoint", ""),
                        "userinfo_endpoint": discovery_data.get(
                            "userinfo_endpoint", ""
                        ),
                    }
        except Exception:
            pass

        # Fall back to configured endpoints
        return {
            "authorization_endpoint": self.settings.get("authorization_endpoint", ""),
            "token_endpoint": self.settings.get("token_endpoint", ""),
            "userinfo_endpoint": self.settings.get("userinfo_endpoint", ""),
        }

    def get_authorization_url(
        self, redirect_uri: str, state: str, relay_state: Optional[str] = None
    ) -> tuple[str, SSOSecurityData]:
        """Generate OIDC authorization URL."""
        if not self.is_enabled():
            raise HTTPException(status_code=400, detail="OIDC SSO is not enabled")

        # Use configured authorization endpoint
        auth_endpoint = self.settings.get("authorization_endpoint", "")
        if not auth_endpoint and self.settings.get("discovery_url"):
            endpoints = self._discover_endpoints_sync()
            auth_endpoint = endpoints.get("authorization_endpoint", "")
            if auth_endpoint:
                self.settings["authorization_endpoint"] = auth_endpoint
        if not auth_endpoint:
            raise HTTPException(
                status_code=400, detail="OIDC authorization endpoint not configured"
            )

        # Generate nonce for replay protection - must be stored and validated in callback
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode("ascii")
        )

        # Build authorization parameters
        scopes = self.settings.get("scopes", ["openid", "email", "profile"])
        params = {
            "client_id": self.settings.get("client_id", ""),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        prompt = str(self.settings.get("prompt") or "").strip()
        if prompt:
            params["prompt"] = prompt

        # Return URL and security data containing nonce for validation in callback
        security_data = SSOSecurityData(nonce=nonce, code_verifier=code_verifier)
        separator = "&" if "?" in auth_endpoint else "?"
        return f"{auth_endpoint}{separator}{urlencode(params)}", security_data

    async def handle_callback(
        self,
        request_data: Dict[str, Any],
        redirect_uri: str,
        security_data: Optional[SSOSecurityData] = None,
        request: Request | None = None,
    ) -> Dict[str, Any]:
        """Handle OIDC callback and exchange code for tokens."""
        code = request_data.get("code")
        if not code:
            raise HTTPException(status_code=400, detail="Missing authorization code")

        # Get token endpoint
        token_endpoint = self.settings.get("token_endpoint", "")
        if not token_endpoint:
            endpoints = await self._discover_endpoints()
            token_endpoint = endpoints.get("token_endpoint", "")

        if not token_endpoint:
            raise HTTPException(status_code=400, detail="Token endpoint not configured")

        # Exchange code for tokens
        assert_http_url_allowed(
            self.db, url=token_endpoint, feature="OIDC token exchange"
        )
        async with httpx.AsyncClient() as client:
            if not security_data or not security_data.code_verifier:
                raise HTTPException(
                    status_code=401, detail="OIDC PKCE verifier is missing"
                )
            token_data = {
                "client_id": self.settings.get("client_id", ""),
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": security_data.code_verifier,
            }
            token_headers = {"Content-Type": "application/x-www-form-urlencoded"}
            token_auth = None
            if self.settings.get("token_endpoint_auth_method") == "client_secret_post":
                token_data["client_secret"] = self.settings.get("client_secret", "")
            else:
                token_auth = (
                    self.settings.get("client_id", ""),
                    self.settings.get("client_secret", ""),
                )
            response = await client.post(
                token_endpoint,
                data=token_data,
                headers=token_headers,
                auth=token_auth,
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to exchange code for tokens: {response.text}",
                )

            tokens = response.json()
            access_token = tokens.get("access_token")
            id_token = tokens.get("id_token")

            if not access_token:
                raise HTTPException(status_code=400, detail="No access token received")

            if not id_token:
                raise HTTPException(
                    status_code=400, detail="No ID token received from OIDC provider"
                )

            # Always verify ID token first if present (cryptographic proof from IdP)
            id_token_claims = {}
            if id_token:
                stored_nonce = security_data.nonce if security_data else None
                id_token_claims = await self._decode_id_token_verified(
                    id_token, expected_nonce=stored_nonce
                )
            id_token_sub = str(id_token_claims.get("sub") or "").strip()
            if not id_token_sub:
                raise HTTPException(
                    status_code=401,
                    detail="OIDC ID token missing subject",
                )

            # Get user info from userinfo endpoint
            userinfo_endpoint = self.settings.get("userinfo_endpoint", "")
            if not userinfo_endpoint:
                endpoints = await self._discover_endpoints()
                userinfo_endpoint = endpoints.get("userinfo_endpoint", "")

            user_info = {}

            if userinfo_endpoint:
                assert_http_url_allowed(
                    self.db, url=userinfo_endpoint, feature="OIDC UserInfo"
                )
                user_response = await client.get(
                    userinfo_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                )

                if user_response.status_code == 200:
                    user_info = user_response.json()

            # Merge ID token claims with userinfo (userinfo takes precedence for profile data).
            # The authenticated subject must stay bound to the verified ID token.
            if id_token_claims and not user_info:
                user_info = id_token_claims
            elif id_token_claims:
                userinfo_sub = str(user_info.get("sub") or "").strip()
                if userinfo_sub and not secrets.compare_digest(
                    id_token_sub, userinfo_sub
                ):
                    raise HTTPException(
                        status_code=401,
                        detail="OIDC UserInfo subject does not match ID token subject",
                    )

                # Merge: ID token claims as base, userinfo as override, except for sub.
                merged = {**id_token_claims, **user_info}
                merged["sub"] = id_token_sub
                user_info = merged

            if not user_info:
                raise HTTPException(
                    status_code=400, detail="Could not retrieve user info"
                )

            user_info = self._apply_profile_attribute_mapping(user_info)
            user_info.update(self._apply_group_policy(user_info))

            # Normalize user info
            user_info["provider"] = "oidc"
            user_info["provider_id"] = "default"

            return user_info

    async def _fetch_oidc_metadata(self) -> Dict[str, Any]:
        """Fetch OIDC metadata (issuer, jwks_uri) from discovery or config."""
        discovery_url = self.settings.get("discovery_url")
        if not discovery_url:
            return {
                "issuer": self.settings.get("issuer"),
                "jwks_uri": self.settings.get("jwks_uri"),
            }

        try:
            assert_http_url_allowed(
                self.db, url=discovery_url, feature="OIDC discovery"
            )
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(discovery_url)
                if response.status_code == 200:
                    discovery_data = response.json()
                    return {
                        "issuer": discovery_data.get("issuer"),
                        "jwks_uri": discovery_data.get("jwks_uri"),
                    }
        except Exception:
            pass

        return {
            "issuer": self.settings.get("issuer"),
            "jwks_uri": self.settings.get("jwks_uri"),
        }

    async def _fetch_jwks(self, jwks_uri: Optional[str]) -> Dict[str, Any]:
        """Fetch JWKS (JSON Web Key Set) from the OIDC provider."""
        if not jwks_uri:
            return {}

        try:
            assert_http_url_allowed(self.db, url=jwks_uri, feature="OIDC JWKS")
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(jwks_uri)
                if response.status_code == 200:
                    return response.json()
        except Exception:
            pass

        return {}

    async def _decode_id_token_verified(
        self, id_token: str, expected_nonce: Optional[str] = None
    ) -> Dict[str, Any]:
        """Decode ID token with full signature verification using JWKS and nonce validation."""
        try:
            # First, decode header to get the key ID (kid)
            unverified_header = jwt.get_unverified_header(id_token)
            kid = unverified_header.get("kid")
            alg = unverified_header.get("alg", "RS256")
            allowed_algs = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
            if alg not in allowed_algs:
                raise HTTPException(
                    status_code=400, detail=f"Unsupported ID token algorithm: {alg}"
                )

            # Fetch OIDC metadata + JWKS from provider
            metadata = await self._fetch_oidc_metadata()
            expected_issuer = metadata.get("issuer") or self.settings.get("issuer")
            jwks = await self._fetch_jwks(metadata.get("jwks_uri"))
            if not jwks or "keys" not in jwks:
                raise HTTPException(
                    status_code=400, detail="Could not fetch JWKS from OIDC provider"
                )

            # Find the matching key
            public_key = None
            for key_data in jwks.get("keys", []):
                if kid and key_data.get("kid") != kid:
                    continue
                if key_data.get("alg") and key_data.get("alg") != alg:
                    continue

                # Convert JWK to PEM format
                alg_class = jwt.algorithms.get_default_algorithms().get(alg)
                if not alg_class or not hasattr(alg_class, "from_jwk"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unsupported JWK algorithm handler for {alg}",
                    )
                public_key = alg_class.from_jwk(json.dumps(key_data))
                break

            if not public_key:
                raise HTTPException(
                    status_code=400,
                    detail=f"No matching key found in JWKS for kid: {kid}",
                )

            # Verify and decode the token
            decode_kwargs = {
                "audience": self.settings.get("client_id"),
                "options": {
                    "verify_signature": True,
                    "verify_aud": True,
                    "verify_exp": True,
                    "verify_iat": True,
                },
            }
            if expected_issuer:
                decode_kwargs["issuer"] = expected_issuer

            decoded = jwt.decode(
                id_token,
                public_key,
                algorithms=[alg],
                **decode_kwargs,
            )

            # Validate nonce to prevent replay attacks (OIDC Core spec requirement)
            if expected_nonce is None:
                raise HTTPException(
                    status_code=401, detail="Missing nonce for ID token verification"
                )
            if expected_nonce:
                token_nonce = decoded.get("nonce")
                if not token_nonce:
                    raise HTTPException(
                        status_code=401, detail="ID token missing nonce claim"
                    )
                if not secrets.compare_digest(token_nonce, expected_nonce):
                    raise HTTPException(
                        status_code=401,
                        detail="ID token nonce mismatch - possible replay attack",
                    )

            return decoded

        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="ID token has expired")
        except jwt.InvalidAudienceError:
            raise HTTPException(status_code=401, detail="ID token audience mismatch")
        except jwt.InvalidTokenError as e:
            raise HTTPException(status_code=401, detail=f"Invalid ID token: {str(e)}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"Failed to verify ID token: {str(e)}"
            )


# -------------------
# SSO Provider Factory
# -------------------
class EnterpriseSSOProviderFactory:
    """Factory for creating enterprise SSO providers."""

    @classmethod
    def get_provider(
        cls, provider_type: EnterpriseSSOProviderType, db: Session
    ) -> EnterpriseSSOProvider:
        """Get an enterprise SSO provider by type."""
        if provider_type.lower() == "saml":
            return SAMLSSOProvider(db)
        elif provider_type.lower() == "oidc":
            return EnterpriseOIDCProvider(db)
        else:
            raise HTTPException(
                status_code=400, detail=f"Unknown SSO provider type: {provider_type}"
            )

    @classmethod
    def get_enabled_providers(cls, db: Session) -> Dict[str, Any]:
        """Get all enabled enterprise SSO providers."""
        enabled = {}

        # Check SAML
        try:
            saml_provider = SAMLSSOProvider(db)
            if saml_provider.is_enabled():
                enabled["saml"] = {
                    "type": "saml",
                    "name": "saml",
                    "button_text": saml_provider.settings.get("button_text", ""),
                }
        except Exception:
            pass

        # Check OIDC
        try:
            oidc_provider = EnterpriseOIDCProvider(db)
            if oidc_provider.is_enabled():
                enabled["oidc"] = {
                    "type": "oidc",
                    "name": "oidc",
                    "button_text": oidc_provider.settings.get("button_text", ""),
                }
        except Exception:
            pass

        return enabled
