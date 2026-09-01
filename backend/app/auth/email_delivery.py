from __future__ import annotations

from dataclasses import dataclass
from email.headerregistry import Address
import os
from typing import Any

from app.settings.utils import coerce_bool, get_value_by_page_and_key
from app.email.address import normalize_single_mailbox


class EmailDeliveryConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class EmailDeliveryConfig:
    email_from: str
    application_name: str
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    allow_insecure_smtp: bool = False

    @property
    def sender_header(self) -> str:
        display_name = (self.application_name or "").replace("\r", "").replace("\n", "")
        display_name = display_name.replace("<", "").replace(">", "").replace('"', "").strip()
        return str(
            Address(
                display_name=display_name,
                addr_spec=normalize_single_mailbox(self.email_from),
            )
        )


def _coerce_positive_int(value: Any, default: int) -> int:
    """Coerce value to positive integer or return default."""
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def load_login_email_delivery_config(
    db,
    *,
    include_secrets: bool = False,
) -> EmailDeliveryConfig:
    """Load delivery settings, withholding SMTP credentials by default.

    API processes only need a readiness decision before they stage durable
    work. The isolated email worker is the sole caller that opts into loading
    the SMTP password for transport.
    """
    application_name = " ".join(
        str(
            get_value_by_page_and_key("general", "application_name", db)
            or "Omlorix"
        ).splitlines()
    ).strip() or "Omlorix"
    email_from = str(get_value_by_page_and_key("login_general", "email_from_address", db) or "").strip()
    return EmailDeliveryConfig(
        email_from=email_from,
        application_name=application_name,
        smtp_host=str(get_value_by_page_and_key("login_general", "smtp_host", db) or "").strip(),
        smtp_port=_coerce_positive_int(get_value_by_page_and_key("login_general", "smtp_port", db), 587),
        smtp_username=str(get_value_by_page_and_key("login_general", "smtp_username", db) or "").strip(),
        smtp_password=(
            str(
                get_value_by_page_and_key(
                    "login_general",
                    "smtp_password",
                    db,
                )
                or ""
            ).strip()
            if include_secrets
            else ""
        ),
        smtp_use_tls=coerce_bool(get_value_by_page_and_key("login_general", "smtp_use_tls", db), default=True),
        smtp_use_ssl=coerce_bool(get_value_by_page_and_key("login_general", "smtp_use_ssl", db), default=False),
        allow_insecure_smtp=coerce_bool(
            os.getenv("EMAIL_ALLOW_INSECURE_SMTP"),
            default=False,
        ),
    )


def is_email_delivery_config_ready(config: EmailDeliveryConfig) -> bool:
    """Check if email delivery configuration is ready."""
    try:
        validate_email_delivery_config(config)
    except EmailDeliveryConfigurationError:
        return False
    return True


def validate_email_delivery_config(config: EmailDeliveryConfig) -> None:
    """Validate email delivery configuration."""
    if not config.email_from:
        raise EmailDeliveryConfigurationError("Email delivery requires a From address.")

    try:
        normalize_single_mailbox(config.email_from)
    except (TypeError, ValueError) as exc:
        raise EmailDeliveryConfigurationError(
            "Email delivery requires a valid From address."
        ) from exc

    if not config.smtp_host:
        raise EmailDeliveryConfigurationError("SMTP email delivery requires an SMTP host.")
    if not 1 <= int(config.smtp_port) <= 65535:
        raise EmailDeliveryConfigurationError("SMTP port must be between 1 and 65535.")
    if config.smtp_use_tls and config.smtp_use_ssl:
        raise EmailDeliveryConfigurationError(
            "Choose either SMTP STARTTLS or SMTP SSL, not both."
        )
    if not (config.smtp_use_tls or config.smtp_use_ssl):
        if config.smtp_username or config.smtp_password:
            raise EmailDeliveryConfigurationError(
                "Authenticated SMTP always requires TLS or SSL."
            )
        if not config.allow_insecure_smtp:
            raise EmailDeliveryConfigurationError(
                "SMTP requires TLS or SSL unless EMAIL_ALLOW_INSECURE_SMTP is explicitly enabled."
            )
