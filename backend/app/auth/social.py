"""
Social Authentication Provider Module

Provides an extensible architecture for social login providers (Google, Microsoft, Apple, etc.)
with support for 2FA integration and account linking.
"""

import secrets
import hashlib
import json
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Tuple
from urllib.parse import quote, urlencode
import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.auth.microsoft import microsoft_oauth_endpoints, normalize_microsoft_tenant
from app.auth.github_oauth import (
    DEFAULT_GITHUB_BASE_URL,
    build_github_oauth_endpoints,
)
from app.settings.models import get_settings_page_data
from app.auth.github_email import resolve_github_email_verification
from app.network.policy import assert_http_url_allowed


logger = logging.getLogger(__name__)


def _slack_debug_mask(value: Any, *, keep_start: int = 6, keep_end: int = 4) -> str:
    """Return a short redacted value for temporary Slack OAuth diagnostics.

    OAuth codes, state values, nonces, client secrets, access tokens, and ID
    tokens must never be written to logs. A short prefix/suffix is enough to
    correlate the stages of one request without exposing credentials.
    """
    text = str(value or "")
    if not text:
        return "<empty>"
    if len(text) <= keep_start + keep_end:
        return f"<len={len(text)}>"
    return f"{text[:keep_start]}...{text[-keep_end:]}<len={len(text)}>"


def _slack_debug_fingerprint(value: Any) -> str:
    """Return a non-reversible short fingerprint for correlating secrets."""
    text = str(value or "")
    if not text:
        return "<empty>"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _slack_debug_safe_error(value: Any) -> str:
    """Keep provider error diagnostics bounded and free of line breaks."""
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:200] if text else "<empty>"


def _coerce_setting_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return bool(value)


APPLE_PRIVATE_KEY_ERROR_DETAIL = (
    "Apple private key must be the full .p8 PEM content, including "
    "-----BEGIN PRIVATE KEY----- and -----END PRIVATE KEY----- lines."
)


def normalize_apple_private_key(private_key: Any) -> str:
    """Normalize Apple private key text from admin form and JSON import paths.

    Apple .p8 keys are PEM documents. Admins commonly paste them either as real
    multiline text or with literal ``\\n`` escape sequences copied from JSON or
    environment variables, so normalize that representation before validation or
    JWT signing.
    """
    return str(private_key or "").replace("\\n", "\n").strip()


def validate_apple_private_key(private_key: Any) -> str:
    """Validate and return normalized Apple Sign in private key PEM text.

    PyJWT only fails when it tries to sign the client-secret JWT during login.
    Validating earlier gives admins a clear configuration error and avoids a
    callback traceback for users.
    """
    normalized = normalize_apple_private_key(private_key)
    if not normalized:
        return ""

    try:
        loaded_key = load_pem_private_key(normalized.encode("utf-8"), password=None)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=APPLE_PRIVATE_KEY_ERROR_DETAIL
        ) from exc

    if not isinstance(loaded_key, ec.EllipticCurvePrivateKey) or not isinstance(
        loaded_key.curve, ec.SECP256R1
    ):
        raise HTTPException(status_code=400, detail=APPLE_PRIVATE_KEY_ERROR_DETAIL)

    return normalized


# -------------------
# Base Social Provider (Abstract)
# -------------------
class SocialAuthProvider(ABC):
    """Abstract base class for social authentication providers."""

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
    def get_authorization_url(self, redirect_uri: str, state: str, nonce: str) -> str:
        """Generate the OAuth authorization URL."""
        pass

    @abstractmethod
    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> Dict[str, Any]:
        """Exchange authorization code for tokens."""
        pass

    @abstractmethod
    async def get_user_info(
        self, access_token: str, *, tokens: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get user information from the provider."""
        pass

    def validate_domain(self, email: str) -> bool:
        """Validate if email domain is allowed (if restrictions are configured)."""
        return True

    def allows_signup(self) -> bool:
        """Check if new user registration is allowed via this provider."""
        return True

    def validate_identity(self, user_info: Dict[str, Any]) -> bool:
        """Validate provider-specific identity claims after token verification.

        Email-domain policy is shared by all social providers. Providers may
        override this hook when access also depends on signed claims that are
        not represented by the email address, such as a Slack workspace ID.
        """
        return True


# -------------------
# Google OAuth Provider
# -------------------
class GoogleAuthProvider(SocialAuthProvider):
    """Google OAuth 2.0 authentication provider."""

    provider_name: str = "google"

    AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
    JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
    _JWKS_CACHE_TTL_SECONDS = 3600
    _jwks_cache: dict[str, Any] | None = None
    _jwks_cache_fetched_at: float | None = None

    def _load_settings(self) -> None:
        """Load Google OAuth settings from database."""
        self.settings = get_settings_page_data(self.db, "login_social")

    def is_enabled(self) -> bool:
        """Check if Google login is enabled."""
        return (
            _coerce_setting_bool(
                self.settings.get("enable_google_oauth"), default=False
            )
            and _coerce_setting_bool(
                self.settings.get("enable_google_login"), default=False
            )
            and bool(self.settings.get("google_client_id"))
            and bool(self.settings.get("google_client_secret"))
        )

    def get_client_id(self) -> str:
        """Get the Google OAuth client ID."""
        return self.settings.get("google_client_id", "")

    def get_client_secret(self) -> str:
        """Get the Google OAuth client secret."""
        return self.settings.get("google_client_secret", "")

    def get_button_text(self) -> str:
        """Get the button text for Google login."""
        return str(self.settings.get("google_button_text") or "").strip()

    def get_allowed_domains(self) -> list:
        """Get the list of allowed email domains."""
        return self.settings.get("google_allowed_domains", [])

    def allows_signup(self) -> bool:
        """Check if new user registration is allowed via Google."""
        return self.settings.get("google_allow_signup", True)

    def validate_domain(self, email: str) -> bool:
        """Validate if email domain is allowed."""
        allowed_domains = self.get_allowed_domains()
        if not allowed_domains:
            return True

        domain = email.split("@")[-1].lower() if "@" in email else ""
        return domain in [d.lower() for d in allowed_domains]

    def validate_identity(self, user_info: Dict[str, Any]) -> bool:
        """Require Google's signed hosted-domain claim for Workspace policy."""

        allowed_domains = {
            str(value).strip().lower()
            for value in self.get_allowed_domains()
            if str(value).strip()
        }
        if not allowed_domains:
            return True
        hosted_domain = str(user_info.get("hd") or "").strip().lower()
        return hosted_domain in allowed_domains

    def get_authorization_url(self, redirect_uri: str, state: str, nonce: str) -> str:
        """Generate the Google OAuth authorization URL."""
        if not self.is_enabled():
            raise HTTPException(status_code=400, detail="Google login is not enabled")

        params = {
            "client_id": self.get_client_id(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "access_type": "offline",
            "prompt": "select_account",
        }
        allowed_domains = self.get_allowed_domains()
        if len(allowed_domains) == 1:
            # ``hd`` is only a UI/account-selection hint. The signed claim is
            # still enforced after token verification.
            params["hd"] = allowed_domains[0]

        return f"{self.AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> Dict[str, Any]:
        """Exchange authorization code for Google tokens."""
        if not self.is_enabled():
            raise HTTPException(status_code=400, detail="Google login is not enabled")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.get_client_id(),
                    "client_secret": self.get_client_secret(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to exchange code for tokens: {response.text}",
                )

            return response.json()

    async def get_user_info(
        self, access_token: str, *, tokens: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get user information from Google."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=400, detail="Failed to get user info from Google"
                )

            data = response.json()
            id_token = str((tokens or {}).get("id_token") or "").strip()
            if id_token:
                verified_claims = await self.verify_id_token(id_token)
                userinfo_subject = str(data.get("sub") or "").strip()
                token_subject = str(verified_claims.get("sub") or "").strip()
                if (
                    not userinfo_subject
                    or not token_subject
                    or not secrets.compare_digest(userinfo_subject, token_subject)
                ):
                    raise HTTPException(
                        status_code=401,
                        detail="Google UserInfo subject does not match the ID token",
                    )
                # Hosted-domain authorization must come from the signed token,
                # not an email suffix or an unbound profile response.
                data["hd"] = verified_claims.get("hd", "")
                data["email_verified"] = verified_claims.get("email_verified")
                data["nonce"] = verified_claims.get("nonce", "")
            if data.get("picture"):
                data["profile_picture_url"] = data.get("picture")
            return data

    async def verify_id_token(self, id_token: str) -> Dict[str, Any]:
        """Verify a Google ID token from Google Identity Services."""
        try:
            headers = jwt.get_unverified_header(id_token)
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid Google ID token header: {exc}"
            )

        public_key = await self._get_google_public_key(headers.get("kid"))
        if public_key is None:
            raise HTTPException(
                status_code=400, detail="Google signing key not found; try again later"
            )

        try:
            decoded = jwt.decode(
                id_token,
                key=public_key,
                algorithms=["RS256"],
                audience=self.get_client_id(),
                issuer=["accounts.google.com", "https://accounts.google.com"],
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=400, detail="Google ID token has expired")
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid Google ID token: {exc}"
            )

        normalized = {
            "sub": decoded.get("sub", ""),
            "email": str(decoded.get("email") or "").strip(),
            "name": decoded.get("name", ""),
            "given_name": decoded.get("given_name", ""),
            "family_name": decoded.get("family_name", ""),
            "email_verified": decoded.get("email_verified"),
            "hd": str(decoded.get("hd") or "").strip().lower(),
            "nonce": str(decoded.get("nonce") or ""),
        }
        if decoded.get("picture"):
            normalized["profile_picture_url"] = decoded.get("picture")
        return normalized

    @classmethod
    async def _get_google_public_key(cls, kid: Optional[str]):
        if not kid:
            return None

        for force_refresh in (False, True):
            jwks = await cls._get_google_jwks(force_refresh=force_refresh)
            keys = jwks.get("keys", []) if isinstance(jwks, dict) else []
            for key_data in keys:
                if key_data.get("kid") == kid:
                    try:
                        return jwt.algorithms.RSAAlgorithm.from_jwk(
                            json.dumps(key_data)
                        )
                    except Exception:
                        return None
        return None

    @classmethod
    async def _get_google_jwks(cls, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        if (
            not force_refresh
            and cls._jwks_cache is not None
            and cls._jwks_cache_fetched_at is not None
            and now - cls._jwks_cache_fetched_at < cls._JWKS_CACHE_TTL_SECONDS
        ):
            return cls._jwks_cache

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(cls.JWKS_URL)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=400, detail=f"Unable to fetch Google signing keys: {exc}"
            )

        cls._jwks_cache = data if isinstance(data, dict) else {}
        cls._jwks_cache_fetched_at = now
        return cls._jwks_cache


# -------------------
# Microsoft OAuth Provider
# -------------------
class MicrosoftAuthProvider(SocialAuthProvider):
    """Microsoft OAuth 2.0 authentication provider."""

    provider_name: str = "microsoft"

    AUTHORIZATION_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    USERINFO_URL = "https://graph.microsoft.com/v1.0/me"
    USERPHOTO_URL = "https://graph.microsoft.com/v1.0/me/photo/$value"
    JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
    _JWKS_CACHE_TTL_SECONDS = 3600
    _jwks_cache: dict[str, Any] | None = None
    _jwks_cache_fetched_at: float | None = None

    def _load_settings(self) -> None:
        """Load Microsoft OAuth settings from database."""
        self.settings = get_settings_page_data(self.db, "login_social")
        try:
            self.microsoft_tenant = normalize_microsoft_tenant(
                self.settings.get("microsoft_tenant")
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # These are instance attributes because deployments may choose a
        # single tenant, organizations-only, consumers-only, or ``common``.
        self.AUTHORIZATION_URL, self.TOKEN_URL = microsoft_oauth_endpoints(
            self.microsoft_tenant
        )

    def is_enabled(self) -> bool:
        """Check if Microsoft login is enabled."""
        return (
            _coerce_setting_bool(
                self.settings.get("enable_microsoft_oauth"), default=False
            )
            and _coerce_setting_bool(
                self.settings.get("enable_microsoft_login"), default=False
            )
            and bool(self.settings.get("microsoft_client_id"))
            and bool(self.settings.get("microsoft_client_secret"))
        )

    def get_client_id(self) -> str:
        """Get the Microsoft OAuth client ID."""
        return self.settings.get("microsoft_client_id", "")

    def get_client_secret(self) -> str:
        """Get the Microsoft OAuth client secret."""
        return self.settings.get("microsoft_client_secret", "")

    def get_button_text(self) -> str:
        """Get the button text for Microsoft login."""
        return str(self.settings.get("microsoft_button_text") or "").strip()

    def get_allowed_domains(self) -> list:
        """Get the list of allowed email domains."""
        return self.settings.get("microsoft_allowed_domains", [])

    def allows_signup(self) -> bool:
        """Check if new user registration is allowed via Microsoft."""
        return self.settings.get("microsoft_allow_signup", True)

    def validate_domain(self, email: str) -> bool:
        """Validate if the email domain is allowed for every tenant mode."""
        allowed_domains = self.get_allowed_domains()
        if not allowed_domains:
            return True

        domain = email.split("@")[-1].lower() if "@" in email else ""
        return domain in [str(value).lower() for value in allowed_domains]

    def validate_identity(self, user_info: Dict[str, Any]) -> bool:
        """Enforce the allowlist against the tenant ID from the verified token."""

        allowed_tenants = {
            str(value).strip().lower()
            for value in (self.settings.get("microsoft_allowed_tenant_ids") or [])
            if str(value).strip()
        }
        if not allowed_tenants:
            return True
        tenant_id = str(user_info.get("tenant_id") or "").strip().lower()
        return tenant_id in allowed_tenants

    def get_authorization_url(self, redirect_uri: str, state: str, nonce: str) -> str:
        """Generate the Microsoft OAuth authorization URL."""
        if not self.is_enabled():
            raise HTTPException(
                status_code=400, detail="Microsoft login is not enabled"
            )

        params = {
            "client_id": self.get_client_id(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile User.Read",
            "state": state,
            "nonce": nonce,
            "response_mode": "query",
            "prompt": "select_account",
        }

        return f"{self.AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> Dict[str, Any]:
        """Exchange authorization code for Microsoft tokens."""
        if not self.is_enabled():
            raise HTTPException(
                status_code=400, detail="Microsoft login is not enabled"
            )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.get_client_id(),
                    "client_secret": self.get_client_secret(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to exchange code for tokens: {response.text}",
                )

            return response.json()

    async def get_user_info(
        self, access_token: str, *, tokens: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get user information from Microsoft Graph API."""
        id_token = str((tokens or {}).get("id_token") or "").strip()
        if not id_token:
            raise HTTPException(
                status_code=400, detail="Microsoft ID token is missing."
            )
        id_token_claims = await self._decode_id_token_verified(id_token)
        tenant_id = str(id_token_claims.get("tid") or "").strip()
        if not tenant_id:
            raise HTTPException(
                status_code=400, detail="Microsoft ID token tenant is missing."
            )
        object_id = str(id_token_claims.get("oid") or "").strip()
        if not object_id:
            raise HTTPException(
                status_code=400, detail="Microsoft ID token object ID is missing."
            )

        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=400, detail="Failed to get user info from Microsoft"
                )

            user_data = response.json()
            graph_object_id = str(user_data.get("id") or "").strip()
            if not graph_object_id or not secrets.compare_digest(
                graph_object_id, object_id
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Microsoft profile identity does not match the ID token.",
                )

            # Normalize Microsoft response to match Google format
            # Microsoft returns: id, displayName, givenName, surname, userPrincipalName, mail
            graph_email = str(
                user_data.get("mail") or user_data.get("userPrincipalName") or ""
            ).strip()
            id_token_email = str(
                id_token_claims.get("email")
                or id_token_claims.get("preferred_username")
                or id_token_claims.get("verified_primary_email")
                or id_token_claims.get("verified_secondary_email")
                or ""
            ).strip()
            normalized_email = (graph_email or id_token_email).lower()

            normalized = {
                # Microsoft documents oid + tid as the durable account key.
                # Human-readable email and UPN values remain profile hints and
                # are never used to bind an existing Omlorix account.
                "sub": object_id,
                "email": normalized_email,
                "name": user_data.get("displayName", ""),
                "given_name": user_data.get("givenName", ""),
                "family_name": user_data.get("surname", ""),
                # Microsoft object IDs are tenant-scoped identifiers. Preserve
                # the verified tenant claim so account linking can scope the
                # subject to its actual OpenID issuer.
                "tenant_id": tenant_id,
                "nonce": str(id_token_claims.get("nonce") or ""),
                "microsoft_identity_verified": True,
            }
            email_verified = self._resolve_email_verification(
                normalized_email, id_token_claims
            )
            if email_verified is not None:
                normalized["email_verified"] = email_verified

            if _coerce_setting_bool(
                self.settings.get("import_microsoft_oauth_profile_picture"),
                default=False,
            ):
                photo_response = await client.get(
                    self.USERPHOTO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if photo_response.status_code == 200:
                    content_type = (
                        str(photo_response.headers.get("content-type") or "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if content_type.startswith("image/") and photo_response.content:
                        normalized["profile_picture_bytes"] = photo_response.content
                        normalized["profile_picture_content_type"] = content_type

            return normalized

    @classmethod
    async def _get_microsoft_jwks(
        cls, *, force_refresh: bool = False
    ) -> dict[str, Any]:
        now = time.time()
        if (
            not force_refresh
            and cls._jwks_cache is not None
            and cls._jwks_cache_fetched_at is not None
            and now - cls._jwks_cache_fetched_at < cls._JWKS_CACHE_TTL_SECONDS
        ):
            return cls._jwks_cache

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(cls.JWKS_URL)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=400, detail=f"Unable to fetch Microsoft signing keys: {exc}"
            )

        cls._jwks_cache = data if isinstance(data, dict) else {}
        cls._jwks_cache_fetched_at = now
        return cls._jwks_cache

    @classmethod
    async def _get_microsoft_public_key(cls, kid: Optional[str]):
        if not kid:
            return None

        for force_refresh in (False, True):
            jwks = await cls._get_microsoft_jwks(force_refresh=force_refresh)
            keys = jwks.get("keys", []) if isinstance(jwks, dict) else []
            for key_data in keys:
                if key_data.get("kid") == kid:
                    try:
                        return jwt.algorithms.RSAAlgorithm.from_jwk(
                            json.dumps(key_data)
                        )
                    except Exception:
                        return None
        return None

    async def _decode_id_token_verified(self, id_token: str) -> Dict[str, Any]:
        try:
            headers = jwt.get_unverified_header(id_token)
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid Microsoft ID token header: {exc}"
            )

        public_key = await self._get_microsoft_public_key(headers.get("kid"))
        if public_key is None:
            raise HTTPException(
                status_code=400,
                detail="Microsoft signing key not found; try again later",
            )

        try:
            decoded = jwt.decode(
                id_token,
                key=public_key,
                algorithms=["RS256"],
                audience=self.get_client_id(),
                options={
                    "verify_signature": True,
                    "verify_aud": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "require": ["iss", "aud", "exp", "iat", "nonce", "oid", "tid"],
                },
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=400, detail="Microsoft ID token has expired"
            )
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid Microsoft ID token: {exc}"
            )

        issuer = str(decoded.get("iss") or "").strip()
        tenant_id = str(decoded.get("tid") or "").strip()
        expected_issuer = (
            f"https://login.microsoftonline.com/{tenant_id}/v2.0" if tenant_id else ""
        )
        if (
            not issuer
            or not expected_issuer
            or not secrets.compare_digest(issuer, expected_issuer)
        ):
            raise HTTPException(
                status_code=400, detail="Microsoft ID token issuer mismatch"
            )

        return decoded

    @staticmethod
    def _resolve_email_verification(
        email: str, id_token_claims: Dict[str, Any]
    ) -> Optional[bool]:
        """Return only a real email-verification claim when Microsoft emits it.

        ``preferred_username`` and ``email`` are mutable profile hints, while
        ``xms_edov`` describes domain-owner verification rather than ownership
        of one mailbox.  None of those claims is promoted to email verification.
        """

        if not id_token_claims:
            return None

        if "email_verified" in id_token_claims:
            return _coerce_setting_bool(
                id_token_claims.get("email_verified"), default=False
            )

        return None


# -------------------
# GitHub OAuth Provider
# -------------------
class GitHubAuthProvider(SocialAuthProvider):
    """OAuth provider for GitHub.com or one GitHub Enterprise Server."""

    provider_name: str = "github"

    AUTHORIZATION_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    USERINFO_URL = "https://api.github.com/user"
    USER_EMAILS_URL = "https://api.github.com/user/emails"
    USER_ORGS_URL = "https://api.github.com/user/orgs"

    def _load_settings(self) -> None:
        """Load settings and derive every endpoint from one server origin."""
        self.settings = get_settings_page_data(self.db, "login_social")
        self.endpoints = build_github_oauth_endpoints(
            self.settings.get("github_base_url", DEFAULT_GITHUB_BASE_URL)
        )

        # The surrounding social-auth router inspects these instance attributes
        # before starting a flow.  Assigning all of them here preserves that
        # security check while allowing a self-hosted GitHub origin.
        self.AUTHORIZATION_URL = self.endpoints.authorization_url
        self.TOKEN_URL = self.endpoints.token_url
        self.USERINFO_URL = self.endpoints.user_url
        self.USER_EMAILS_URL = self.endpoints.user_emails_url
        self.USER_ORGS_URL = self.endpoints.user_organizations_url

    def is_enabled(self) -> bool:
        """Check if GitHub login is enabled."""
        return (
            _coerce_setting_bool(
                self.settings.get("enable_github_oauth"), default=False
            )
            and _coerce_setting_bool(
                self.settings.get("enable_github_login"), default=False
            )
            and bool(self.settings.get("github_client_id"))
            and bool(self.settings.get("github_client_secret"))
        )

    def get_client_id(self) -> str:
        """Get the GitHub OAuth client ID."""
        return self.settings.get("github_client_id", "")

    def get_client_secret(self) -> str:
        """Get the GitHub OAuth client secret."""
        return self.settings.get("github_client_secret", "")

    def get_button_text(self) -> str:
        """Get the button text for GitHub login."""
        return str(self.settings.get("github_button_text") or "").strip()

    def get_allowed_domains(self) -> list:
        """Get the list of allowed email domains."""
        return self.settings.get("github_allowed_domains", [])

    def allows_signup(self) -> bool:
        """Check if new user registration is allowed via GitHub."""
        return self.settings.get("github_allow_signup", True)

    def validate_domain(self, email: str) -> bool:
        """Validate if email domain is allowed."""
        allowed_domains = self.get_allowed_domains()
        if not allowed_domains:
            return True

        domain = email.split("@")[-1].lower() if "@" in email else ""
        return domain in [d.lower() for d in allowed_domains]

    def validate_identity(self, user_info: Dict[str, Any]) -> bool:
        """Enforce GitHub organization membership fetched with the login token."""

        allowed = {
            str(value).strip().lower()
            for value in (self.settings.get("github_allowed_organizations") or [])
            if str(value).strip()
        }
        if not allowed:
            return True
        organizations = {
            str(value).strip().lower()
            for value in (user_info.get("organizations") or [])
            if str(value).strip()
        }
        return bool(allowed.intersection(organizations))

    def _get_allowed_organizations(self) -> set[str]:
        """Return the normalized organization allowlist for this provider."""

        return {
            str(value).strip().lower()
            for value in (self.settings.get("github_allowed_organizations") or [])
            if str(value).strip()
        }

    def _assert_endpoint_allowed(self, url: str, *, feature: str) -> None:
        """Apply outbound policy whenever the provider has runtime DB context.

        Production providers are always constructed with a database session.
        The small guard preserves isolated provider-method tests that allocate
        the class with ``__new__`` and never perform a real outbound request.
        """

        db = getattr(self, "db", None)
        if db is not None:
            assert_http_url_allowed(db, url=url, feature=feature)

    def get_authorization_url(self, redirect_uri: str, state: str, nonce: str) -> str:
        """Generate the GitHub OAuth authorization URL."""
        if not self.is_enabled():
            raise HTTPException(status_code=400, detail="GitHub login is not enabled")

        scopes = ["read:user", "user:email"]
        if self.settings.get("github_allowed_organizations"):
            scopes.append("read:org")
        params = {
            "client_id": self.get_client_id(),
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "allow_signup": "true",
        }
        return f"{self.AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> Dict[str, Any]:
        """Exchange authorization code for GitHub tokens."""
        if not self.is_enabled():
            raise HTTPException(status_code=400, detail="GitHub login is not enabled")

        self._assert_endpoint_allowed(
            self.TOKEN_URL,
            feature="GitHub OAuth token exchange",
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.get_client_id(),
                    "client_secret": self.get_client_secret(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )

            token_data = (
                response.json()
                if response.headers.get("content-type", "").startswith(
                    "application/json"
                )
                else {}
            )
            if response.status_code != 200 or not token_data.get("access_token"):
                error_message = (
                    token_data.get("error_description")
                    or token_data.get("error")
                    or response.text
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to exchange code for tokens: {error_message}",
                )

            return token_data

    async def _get_primary_email(
        self,
        access_token: str,
        preferred_email: str | None = None,
    ) -> tuple[str, Optional[bool]]:
        """Fetch GitHub email verification details from the email API."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._assert_endpoint_allowed(
            self.USER_EMAILS_URL,
            feature="GitHub OAuth email lookup",
        )
        async with httpx.AsyncClient() as client:
            response = await client.get(self.USER_EMAILS_URL, headers=headers)
            if response.status_code != 200:
                return "", None

            try:
                emails = response.json()
            except ValueError:
                return "", None

            return resolve_github_email_verification(emails, preferred_email)

    async def _get_allowed_organization_memberships(
        self,
        client: httpx.AsyncClient,
        access_token: str,
    ) -> list[str]:
        """Return allowed organizations in which the user is an active member.

        Checking each configured organization directly avoids false denials from
        GitHub's paginated ``/user/orgs`` collection and includes private
        memberships when the OAuth app received ``read:org``.
        """

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        memberships: list[str] = []
        for organization in sorted(self._get_allowed_organizations()):
            membership_url = (
                f"{self.endpoints.api_base_url}/user/memberships/orgs/"
                f"{quote(organization, safe='')}"
            )
            self._assert_endpoint_allowed(
                membership_url,
                feature="GitHub OAuth organization membership",
            )
            response = await client.get(membership_url, headers=headers)
            if response.status_code != 200:
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("state") or "").strip().lower() == "active":
                memberships.append(organization)
        return memberships

    async def get_user_info(
        self, access_token: str, *, tokens: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get user information from GitHub."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        self._assert_endpoint_allowed(
            self.USERINFO_URL,
            feature="GitHub OAuth profile lookup",
        )
        async with httpx.AsyncClient() as client:
            response = await client.get(self.USERINFO_URL, headers=headers)
            if response.status_code != 200:
                raise HTTPException(
                    status_code=400, detail="Failed to get user info from GitHub"
                )
            user_data = response.json()

            organizations = (
                await self._get_allowed_organization_memberships(client, access_token)
                if self._get_allowed_organizations()
                else []
            )

        email = (user_data.get("email") or "").strip().lower()
        email, email_verified = await self._get_primary_email(
            access_token, preferred_email=email
        )
        full_name = (user_data.get("name") or "").strip()
        given_name = ""
        family_name = ""
        if full_name:
            parts = full_name.split()
            given_name = parts[0]
            family_name = " ".join(parts[1:]) if len(parts) > 1 else ""

        normalized = {
            "sub": str(user_data.get("id") or user_data.get("login") or ""),
            "email": email,
            "name": full_name or user_data.get("login", ""),
            "given_name": given_name,
            "family_name": family_name,
            "profile_picture_url": user_data.get("avatar_url", ""),
            "organizations": organizations,
        }
        if email_verified is not None:
            normalized["email_verified"] = email_verified
        return normalized


# -------------------
# Slack OpenID Connect Provider
# -------------------
class SlackAuthProvider(SocialAuthProvider):
    """Slack OpenID Connect provider used exclusively for social sign-in.

    Slack's Sign in with Slack scopes cannot be combined with regular Slack API
    scopes in one authorization request. This provider therefore uses Slack's
    dedicated OpenID endpoints and remains separate from the managed workspace
    connection flow in :mod:`app.connections.slack`.
    """

    provider_name: str = "slack"

    AUTHORIZATION_URL = "https://slack.com/openid/connect/authorize"
    TOKEN_URL = "https://slack.com/api/openid.connect.token"
    JWKS_URL = "https://slack.com/openid/connect/keys"
    ISSUER = "https://slack.com"
    _JWKS_CACHE_TTL_SECONDS = 3600
    _jwks_cache: dict[str, Any] | None = None
    _jwks_cache_fetched_at: float | None = None

    def _load_settings(self) -> None:
        """Load Slack OAuth and sign-in policy settings."""
        self.settings = get_settings_page_data(self.db, "login_social")
        logger.debug(
            "SLACK_DEBUG settings_loaded enable_oauth=%s enable_login=%s client_id=%s "
            "client_secret_present=%s "
            "scope_tier=%s allowed_domains_count=%s allowed_workspaces_count=%s",
            _coerce_setting_bool(
                self.settings.get("enable_slack_oauth"), default=False
            ),
            _coerce_setting_bool(
                self.settings.get("enable_slack_login"), default=False
            ),
            _slack_debug_mask(self.settings.get("slack_client_id")),
            bool(str(self.settings.get("slack_client_secret") or "").strip()),
            str(self.settings.get("slack_connection_scope_tier") or "<default>"),
            len(self.settings.get("slack_allowed_domains") or []),
            len(self.settings.get("slack_allowed_workspace_ids") or []),
        )

    def is_enabled(self) -> bool:
        """Return whether Slack OAuth credentials and login are enabled."""
        enabled = (
            _coerce_setting_bool(self.settings.get("enable_slack_oauth"), default=False)
            and _coerce_setting_bool(
                self.settings.get("enable_slack_login"), default=False
            )
            and bool(self.get_client_id())
            and bool(self.get_client_secret())
        )
        logger.debug(
            "SLACK_DEBUG provider_enabled=%s client_id=%s",
            enabled,
            _slack_debug_mask(self.get_client_id()),
        )
        return enabled

    def get_client_id(self) -> str:
        """Return the configured Slack app client ID."""
        return str(self.settings.get("slack_client_id") or "").strip()

    def get_client_secret(self) -> str:
        """Return the configured Slack app client secret."""
        return str(self.settings.get("slack_client_secret") or "").strip()

    def get_button_text(self) -> str:
        """Return custom button text, or empty to preserve the translated label."""
        return str(self.settings.get("slack_button_text") or "").strip()

    def get_allowed_domains(self) -> list[str]:
        """Return email domains allowed to authenticate through Slack."""
        return [
            str(domain).strip().lower()
            for domain in (self.settings.get("slack_allowed_domains") or [])
            if str(domain).strip()
        ]

    def get_allowed_workspace_ids(self) -> list[str]:
        """Return Slack workspace IDs allowed to authenticate, if restricted."""
        return [
            str(workspace_id).strip().upper()
            for workspace_id in (self.settings.get("slack_allowed_workspace_ids") or [])
            if str(workspace_id).strip()
        ]

    def allows_signup(self) -> bool:
        """Return whether Slack may create new Omlorix users."""
        return _coerce_setting_bool(
            self.settings.get("slack_allow_signup"), default=True
        )

    def validate_domain(self, email: str) -> bool:
        """Apply the configured email-domain allowlist."""
        allowed_domains = self.get_allowed_domains()
        if not allowed_domains:
            return True
        domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        return domain in allowed_domains

    def validate_identity(self, user_info: Dict[str, Any]) -> bool:
        """Apply the workspace allowlist to Slack's verified team claim."""
        allowed_workspace_ids = self.get_allowed_workspace_ids()
        if not allowed_workspace_ids:
            return True
        workspace_id = str(user_info.get("workspace_id") or "").strip().upper()
        return workspace_id in allowed_workspace_ids

    def get_authorization_url(self, redirect_uri: str, state: str, nonce: str) -> str:
        """Build a Slack OpenID authorization URL with identity-only scopes."""
        if not self.is_enabled():
            raise HTTPException(status_code=400, detail="Slack login is not enabled")

        params = {
            "client_id": self.get_client_id(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "form_post",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
        }
        authorization_url = f"{self.AUTHORIZATION_URL}?{urlencode(params)}"
        logger.info(
            "SLACK_DEBUG authorization_url_built endpoint=%s client_id=%s redirect_uri=%s "
            "response_type=%s response_mode=%s scope=%s state_fp=%s nonce_fp=%s url_length=%s",
            self.AUTHORIZATION_URL,
            _slack_debug_mask(self.get_client_id()),
            redirect_uri,
            params["response_type"],
            params["response_mode"],
            params["scope"],
            _slack_debug_fingerprint(state),
            _slack_debug_fingerprint(nonce),
            len(authorization_url),
        )
        return authorization_url

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> Dict[str, Any]:
        """Exchange the Slack authorization code at the OpenID token endpoint."""
        if not self.is_enabled():
            raise HTTPException(status_code=400, detail="Slack login is not enabled")

        logger.debug(
            "SLACK_DEBUG token_exchange_start endpoint=%s client_id=%s redirect_uri=%s "
            "code_fp=%s code_length=%s grant_type=authorization_code",
            self.TOKEN_URL,
            _slack_debug_mask(self.get_client_id()),
            redirect_uri,
            _slack_debug_fingerprint(code),
            len(str(code or "")),
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.get_client_id(),
                    "client_secret": self.get_client_secret(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        try:
            token_data = response.json()
        except ValueError:
            token_data = {}

        safe_payload = token_data if isinstance(token_data, dict) else {}
        logger.debug(
            "SLACK_DEBUG token_exchange_response http_status=%s content_type=%s "
            "payload_keys=%s ok=%s error=%s error_description=%s "
            "access_token_present=%s id_token_present=%s refresh_token_present=%s",
            response.status_code,
            getattr(response, "headers", {}).get("content-type", "<missing>"),
            sorted(str(key) for key in safe_payload.keys()),
            safe_payload.get("ok", "<missing>"),
            _slack_debug_safe_error(safe_payload.get("error")),
            _slack_debug_safe_error(safe_payload.get("error_description")),
            bool(safe_payload.get("access_token")),
            bool(safe_payload.get("id_token")),
            bool(safe_payload.get("refresh_token")),
        )
        if (
            response.status_code != 200
            or token_data.get("ok") is False
            or not token_data.get("id_token")
        ):
            logger.warning(
                "SLACK_DEBUG token_exchange_failed http_status=%s slack_error=%s "
                "slack_error_description=%s code_fp=%s redirect_uri=%s",
                response.status_code,
                _slack_debug_safe_error(safe_payload.get("error")),
                _slack_debug_safe_error(safe_payload.get("error_description")),
                _slack_debug_fingerprint(code),
                redirect_uri,
            )
            raise HTTPException(
                status_code=400, detail="Failed to exchange Slack authorization code"
            )
        logger.debug(
            "SLACK_DEBUG token_exchange_success code_fp=%s id_token_length=%s access_token_present=%s",
            _slack_debug_fingerprint(code),
            len(str(token_data.get("id_token") or "")),
            bool(token_data.get("access_token")),
        )
        return token_data

    async def get_user_info(
        self,
        access_token: str,
        *,
        tokens: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Verify Slack's ID token and normalize its identity claims.

        ``access_token`` is the ID token for this provider. The access token is
        deliberately unused because authentication depends only on verified,
        signed OpenID claims and must not become a Slack data connection.
        """
        logger.debug(
            "SLACK_DEBUG id_token_verification_start id_token_fp=%s id_token_length=%s",
            _slack_debug_fingerprint(access_token),
            len(str(access_token or "")),
        )
        try:
            headers = jwt.get_unverified_header(access_token)
        except jwt.InvalidTokenError as exc:
            logger.warning(
                "SLACK_DEBUG id_token_header_invalid error=%s",
                _slack_debug_safe_error(exc),
            )
            raise HTTPException(
                status_code=400, detail="Invalid Slack ID token header"
            ) from exc

        logger.debug(
            "SLACK_DEBUG id_token_header alg=%s kid=%s typ=%s",
            headers.get("alg", "<missing>"),
            _slack_debug_mask(headers.get("kid")),
            headers.get("typ", "<missing>"),
        )

        public_key = await self._get_slack_public_key(headers.get("kid"))
        if public_key is None:
            logger.warning(
                "SLACK_DEBUG id_token_signing_key_missing kid=%s",
                _slack_debug_mask(headers.get("kid")),
            )
            raise HTTPException(
                status_code=400, detail="Slack signing key not found; try again later"
            )

        try:
            decoded = jwt.decode(
                access_token,
                key=public_key,
                algorithms=["RS256"],
                audience=self.get_client_id(),
                issuer=self.ISSUER,
                options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce"]},
            )
        except jwt.ExpiredSignatureError as exc:
            logger.warning(
                "SLACK_DEBUG id_token_expired error=%s", _slack_debug_safe_error(exc)
            )
            raise HTTPException(
                status_code=400, detail="Slack ID token has expired"
            ) from exc
        except jwt.InvalidTokenError as exc:
            logger.warning(
                "SLACK_DEBUG id_token_invalid error=%s", _slack_debug_safe_error(exc)
            )
            raise HTTPException(
                status_code=400, detail="Invalid Slack ID token"
            ) from exc

        subject = str(decoded.get("sub") or "").strip()
        claimed_user_id = str(
            decoded.get("https://slack.com/user_id") or subject
        ).strip()
        workspace_id = str(decoded.get("https://slack.com/team_id") or "").strip()
        logger.debug(
            "SLACK_DEBUG id_token_claims_verified sub=%s user_id=%s workspace_id=%s "
            "email_present=%s email_verified=%s name_present=%s picture_present=%s nonce_fp=%s claims=%s",
            _slack_debug_mask(subject),
            _slack_debug_mask(claimed_user_id),
            _slack_debug_mask(workspace_id),
            bool(str(decoded.get("email") or "").strip()),
            decoded.get("email_verified", "<missing>"),
            bool(str(decoded.get("name") or "").strip()),
            bool(str(decoded.get("picture") or "").strip()),
            _slack_debug_fingerprint(decoded.get("nonce")),
            sorted(str(key) for key in decoded.keys()),
        )
        if (
            not workspace_id
            or not claimed_user_id
            or not secrets.compare_digest(subject, claimed_user_id)
        ):
            logger.warning(
                "SLACK_DEBUG id_token_identity_claims_inconsistent sub=%s user_id=%s workspace_id=%s",
                _slack_debug_mask(subject),
                _slack_debug_mask(claimed_user_id),
                _slack_debug_mask(workspace_id),
            )
            raise HTTPException(
                status_code=400,
                detail="Slack ID token identity claims are inconsistent",
            )

        return {
            "sub": subject,
            "email": str(decoded.get("email") or "").strip().lower(),
            "email_verified": decoded.get("email_verified"),
            "name": str(decoded.get("name") or "").strip(),
            "given_name": str(decoded.get("given_name") or "").strip(),
            "family_name": str(decoded.get("family_name") or "").strip(),
            "profile_picture_url": str(decoded.get("picture") or "").strip(),
            "workspace_id": workspace_id,
            "nonce": str(decoded.get("nonce") or ""),
        }

    @classmethod
    async def _get_slack_public_key(cls, kid: Optional[str]):
        """Resolve a Slack signing key, refreshing JWKS once for key rotation."""
        if not kid:
            return None
        for force_refresh in (False, True):
            logger.info(
                "SLACK_DEBUG jwks_key_lookup kid=%s force_refresh=%s",
                _slack_debug_mask(kid),
                force_refresh,
            )
            jwks = await cls._get_slack_jwks(force_refresh=force_refresh)
            keys = jwks.get("keys", []) if isinstance(jwks, dict) else []
            for key_data in keys:
                if key_data.get("kid") == kid:
                    logger.info(
                        "SLACK_DEBUG jwks_key_found kid=%s key_count=%s",
                        _slack_debug_mask(kid),
                        len(keys),
                    )
                    try:
                        return jwt.algorithms.RSAAlgorithm.from_jwk(
                            json.dumps(key_data)
                        )
                    except Exception as exc:
                        logger.warning(
                            "SLACK_DEBUG jwks_key_parse_failed kid=%s error=%s",
                            _slack_debug_mask(kid),
                            _slack_debug_safe_error(exc),
                        )
                        return None
            logger.info(
                "SLACK_DEBUG jwks_key_not_found kid=%s key_count=%s force_refresh=%s",
                _slack_debug_mask(kid),
                len(keys),
                force_refresh,
            )
        return None

    @classmethod
    async def _get_slack_jwks(cls, *, force_refresh: bool = False) -> dict[str, Any]:
        """Fetch and cache Slack's OpenID signing keys for a bounded period."""
        now = time.time()
        if (
            not force_refresh
            and cls._jwks_cache is not None
            and cls._jwks_cache_fetched_at is not None
            and now - cls._jwks_cache_fetched_at < cls._JWKS_CACHE_TTL_SECONDS
        ):
            logger.info(
                "SLACK_DEBUG jwks_cache_hit age_seconds=%.2f key_count=%s",
                now - cls._jwks_cache_fetched_at,
                len(cls._jwks_cache.get("keys", []))
                if isinstance(cls._jwks_cache, dict)
                else 0,
            )
            return cls._jwks_cache

        logger.info(
            "SLACK_DEBUG jwks_fetch_start endpoint=%s force_refresh=%s",
            cls.JWKS_URL,
            force_refresh,
        )
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(cls.JWKS_URL)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "SLACK_DEBUG jwks_fetch_failed error=%s", _slack_debug_safe_error(exc)
            )
            raise HTTPException(
                status_code=400, detail="Unable to fetch Slack signing keys"
            ) from exc

        cls._jwks_cache = data if isinstance(data, dict) else {}
        cls._jwks_cache_fetched_at = now
        logger.info(
            "SLACK_DEBUG jwks_fetch_success http_status=%s key_count=%s payload_keys=%s",
            response.status_code,
            len(cls._jwks_cache.get("keys", [])),
            sorted(str(key) for key in cls._jwks_cache.keys()),
        )
        return cls._jwks_cache


# Apple OAuth Provider
# -------------------
class AppleAuthProvider(SocialAuthProvider):
    """Apple Sign In authentication provider."""

    provider_name: str = "apple"

    AUTHORIZATION_URL = "https://appleid.apple.com/auth/authorize"
    TOKEN_URL = "https://appleid.apple.com/auth/token"
    USERINFO_URL = None
    JWKS_URL = "https://appleid.apple.com/auth/keys"
    _JWKS_CACHE_TTL_SECONDS = 3600
    _jwks_cache: dict[str, Any] | None = None
    _jwks_cache_fetched_at: float | None = None

    def _load_settings(self) -> None:
        """Load Apple OAuth settings from database."""
        self.settings = get_settings_page_data(self.db, "login_social")

    def is_enabled(self) -> bool:
        """Check if Apple login is enabled."""
        return (
            _coerce_setting_bool(self.settings.get("enable_apple_login"), default=False)
            and bool(self.settings.get("apple_client_id"))
            and bool(self.settings.get("apple_team_id"))
            and bool(self.settings.get("apple_key_id"))
            and bool(self.settings.get("apple_private_key"))
        )

    def get_client_id(self) -> str:
        """Get the Apple OAuth client ID (Service ID)."""
        return self.settings.get("apple_client_id", "")

    def get_team_id(self) -> str:
        """Get the Apple Team ID."""
        return self.settings.get("apple_team_id", "")

    def get_key_id(self) -> str:
        """Get the Apple Key ID."""
        return self.settings.get("apple_key_id", "")

    def get_private_key(self) -> str:
        """Get the Apple Private Key."""
        return normalize_apple_private_key(self.settings.get("apple_private_key", ""))

    def get_button_text(self) -> str:
        """Get the button text for Apple login."""
        return str(self.settings.get("apple_button_text") or "").strip()

    def get_allowed_domains(self) -> list:
        """Get the list of allowed email domains."""
        return self.settings.get("apple_allowed_domains", [])

    def allows_signup(self) -> bool:
        """Check if new user registration is allowed via Apple."""
        return self.settings.get("apple_allow_signup", True)

    def validate_domain(self, email: str) -> bool:
        """Validate if email domain is allowed."""
        allowed_domains = self.get_allowed_domains()
        if not allowed_domains:
            return True

        domain = email.split("@")[-1].lower() if "@" in email else ""
        return domain in [d.lower() for d in allowed_domains]

    def _generate_client_secret(self) -> str:
        """Generate JWT client secret for Apple authentication."""
        import jwt
        import time

        headers = {"kid": self.get_key_id(), "alg": "ES256"}

        claims = {
            "iss": self.get_team_id(),
            "iat": int(time.time()),
            "exp": int(time.time()) + 86400 * 180,  # 180 days
            "aud": "https://appleid.apple.com",
            "sub": self.get_client_id(),
        }

        try:
            client_secret = jwt.encode(
                claims,
                validate_apple_private_key(self.get_private_key()),
                algorithm="ES256",
                headers=headers,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=APPLE_PRIVATE_KEY_ERROR_DETAIL
            ) from exc

        return client_secret

    def get_authorization_url(self, redirect_uri: str, state: str, nonce: str) -> str:
        """Generate the Apple OAuth authorization URL."""
        if not self.is_enabled():
            raise HTTPException(status_code=400, detail="Apple login is not enabled")

        params = {
            "client_id": self.get_client_id(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "form_post",
            "scope": "name email",
            "state": state,
            "nonce": nonce,
        }

        return f"{self.AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> Dict[str, Any]:
        """Exchange authorization code for Apple tokens."""
        if not self.is_enabled():
            raise HTTPException(status_code=400, detail="Apple login is not enabled")

        client_secret = self._generate_client_secret()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.get_client_id(),
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to exchange code for tokens: {response.text}",
                )

            return response.json()

    async def get_user_info(
        self, access_token: str, *, tokens: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get user information from Apple ID token.

        Apple doesn't provide a userinfo endpoint, so we decode the id_token JWT.
        """
        # The access_token parameter will actually be the id_token for Apple
        try:
            headers = jwt.get_unverified_header(access_token)
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid Apple ID token header: {exc}"
            )

        public_key = await self._get_apple_public_key(headers.get("kid"))
        if public_key is None:
            raise HTTPException(
                status_code=400, detail="Apple signing key not found; try again later"
            )

        try:
            decoded = jwt.decode(
                access_token,
                key=public_key,
                algorithms=["RS256"],
                audience=self.get_client_id(),
                issuer="https://appleid.apple.com",
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=400, detail="Apple ID token has expired")
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid Apple ID token: {exc}"
            )

        normalized = {
            "sub": decoded.get("sub", ""),
            "email": decoded.get("email", ""),
            "name": "",  # Name must be captured from form_post if available
            "given_name": "",
            "family_name": "",
            "email_verified": decoded.get("email_verified"),
            "nonce": decoded.get("nonce", ""),
        }

        return normalized

    @classmethod
    async def _get_apple_public_key(cls, kid: Optional[str]):
        if not kid:
            return None

        for force_refresh in (False, True):
            jwks = await cls._get_apple_jwks(force_refresh=force_refresh)
            keys = jwks.get("keys", []) if isinstance(jwks, dict) else []
            for key_data in keys:
                if key_data.get("kid") == kid:
                    try:
                        return jwt.algorithms.RSAAlgorithm.from_jwk(
                            json.dumps(key_data)
                        )
                    except Exception:
                        return None
        return None

    @classmethod
    async def _get_apple_jwks(cls, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        if (
            not force_refresh
            and cls._jwks_cache is not None
            and cls._jwks_cache_fetched_at is not None
            and now - cls._jwks_cache_fetched_at < cls._JWKS_CACHE_TTL_SECONDS
        ):
            return cls._jwks_cache

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(cls.JWKS_URL)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=400, detail=f"Unable to fetch Apple signing keys: {exc}"
            )

        cls._jwks_cache = data if isinstance(data, dict) else {}
        cls._jwks_cache_fetched_at = now
        return cls._jwks_cache


# -------------------
# Provider Factory
# -------------------
class SocialAuthProviderFactory:
    """Factory for creating social auth providers."""

    PROVIDERS = {
        "google": GoogleAuthProvider,
        "microsoft": MicrosoftAuthProvider,
        "github": GitHubAuthProvider,
        "slack": SlackAuthProvider,
        "apple": AppleAuthProvider,
    }

    @classmethod
    def get_provider(cls, provider_name: str, db: Session) -> SocialAuthProvider:
        """Get a social auth provider by name."""
        provider_class = cls.PROVIDERS.get(provider_name.lower())
        if not provider_class:
            raise HTTPException(
                status_code=400, detail=f"Unknown provider: {provider_name}"
            )
        return provider_class(db)

    @classmethod
    def get_enabled_providers(cls, db: Session) -> Dict[str, Dict[str, Any]]:
        """Get all enabled social auth providers with their configuration."""
        enabled = {}
        for name, provider_class in cls.PROVIDERS.items():
            try:
                provider = provider_class(db)
                if provider.is_enabled():
                    provider_config = {
                        "name": name,
                        "button_text": getattr(
                            provider, "get_button_text", lambda: ""
                        )(),
                    }
                    if name == "google":
                        provider_config["client_id"] = getattr(
                            provider, "get_client_id", lambda: ""
                        )()
                    enabled[name] = provider_config
            except Exception:
                continue
        return enabled


# -------------------
# OAuth State Management
# -------------------
def generate_oauth_state() -> Tuple[str, str]:
    """Generate a secure state parameter for OAuth.

    Returns:
        Tuple of (state_token, state_hash) - store the hash server-side, send token to client
    """
    state_token = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(state_token.encode()).hexdigest()
    return state_token, state_hash


# -------------------
# Generate OAuth Nonce
# -------------------
def generate_oauth_nonce() -> str:
    """Generate a nonce for OAuth flows."""
    return secrets.token_urlsafe(16)


# -------------------
# Social Login Result
# -------------------
class SocialLoginResult:
    """Result of a social login attempt."""

    def __init__(
        self,
        success: bool,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        provider: Optional[str] = None,
        provider_user_id: Optional[str] = None,
        profile_picture_url: Optional[str] = None,
        error: Optional[str] = None,
        requires_2fa_setup: bool = False,
        requires_2fa_verify: bool = False,
        is_new_user: bool = False,
        needs_password_setup: bool = False,
    ):
        self.success = success
        self.email = email
        self.first_name = first_name
        self.last_name = last_name
        self.provider = provider
        self.provider_user_id = provider_user_id
        self.profile_picture_url = profile_picture_url
        self.error = error
        self.requires_2fa_setup = requires_2fa_setup
        self.requires_2fa_verify = requires_2fa_verify
        self.is_new_user = is_new_user
        self.needs_password_setup = needs_password_setup

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "provider": self.provider,
            "provider_user_id": self.provider_user_id,
            "profile_picture_url": self.profile_picture_url,
            "error": self.error,
            "requires_2fa_setup": self.requires_2fa_setup,
            "requires_2fa_verify": self.requires_2fa_verify,
            "is_new_user": self.is_new_user,
            "needs_password_setup": self.needs_password_setup,
        }
