from __future__ import annotations

from email.message import EmailMessage
import smtplib
import ssl

from app.auth.email_delivery import (
    EmailDeliveryConfig,
    validate_email_delivery_config,
)
from app.email.address import normalize_single_mailbox


_SMTP_TIMEOUT = 20.0


class EmailDeliverySendError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        error_type: str = "smtp_error",
    ):
        super().__init__(message)
        self.retryable = bool(retryable)
        self.error_type = str(error_type or "smtp_error")


class SMTPDeliveryClient:
    """Reuse one authenticated SMTP connection for a bounded worker batch."""

    def __init__(self, config: EmailDeliveryConfig):
        validate_email_delivery_config(config)
        self.config = config
        self.server = None

    def __enter__(self):
        self.server = _open_smtp_connection(self.config)
        return self

    def send_message(self, message: EmailMessage) -> None:
        if self.server is None:
            raise EmailDeliverySendError(
                "SMTP connection is closed",
                error_type="connection",
            )
        try:
            recipient_header = message["To"]
            recipients = list(getattr(recipient_header, "addresses", ()) or ())
            if len(recipients) != 1:
                raise EmailDeliverySendError(
                    "Email message must have exactly one recipient",
                    retryable=False,
                    error_type="invalid_recipient",
                )
            envelope_recipient = normalize_single_mailbox(recipients[0].addr_spec)
            self.server.send_message(
                message,
                from_addr=normalize_single_mailbox(self.config.email_from),
                to_addrs=[envelope_recipient],
            )
        except EmailDeliverySendError:
            raise
        except Exception as exc:
            raise _classify_smtp_exception(exc) from exc

    def __exit__(self, _exc_type, _exc, _traceback):
        server, self.server = self.server, None
        if server is None:
            return
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass


def _classify_smtp_exception(exc: Exception) -> EmailDeliverySendError:
    """Return a bounded error category without persisting provider text."""

    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return EmailDeliverySendError(
            "SMTP authentication failed",
            # Credentials are operator-managed configuration and can be fixed
            # while the worker is running. Preserve durable jobs for retry.
            retryable=True,
            error_type="authentication",
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        refused = list((getattr(exc, "recipients", None) or {}).values())
        codes = [item[0] for item in refused if isinstance(item, tuple) and item]
        retryable = bool(codes) and all(400 <= int(code) < 500 for code in codes)
        return EmailDeliverySendError(
            "SMTP recipient rejected",
            retryable=retryable,
            error_type="recipient_rejected",
        )
    if isinstance(exc, smtplib.SMTPResponseException):
        code = int(getattr(exc, "smtp_code", 0) or 0)
        return EmailDeliverySendError(
            "SMTP provider rejected the message",
            retryable=400 <= code < 500,
            error_type=(
                "provider_temporary"
                if 400 <= code < 500
                else "provider_permanent"
            ),
        )
    if isinstance(
        exc,
        (TimeoutError, ConnectionError, OSError, smtplib.SMTPServerDisconnected),
    ):
        return EmailDeliverySendError(
            "SMTP connection failed",
            retryable=True,
            error_type="connection",
        )
    return EmailDeliverySendError(
        "SMTP delivery failed",
        retryable=True,
        error_type="smtp_error",
    )


def _open_smtp_connection(config: EmailDeliveryConfig):
    server = None
    tls_context = ssl.create_default_context()
    try:
        if config.smtp_use_ssl:
            server = smtplib.SMTP_SSL(
                config.smtp_host,
                config.smtp_port,
                timeout=_SMTP_TIMEOUT,
                context=tls_context,
            )
        else:
            server = smtplib.SMTP(
                config.smtp_host,
                config.smtp_port,
                timeout=_SMTP_TIMEOUT,
            )
            if config.smtp_use_tls:
                server.starttls(context=tls_context)
        if config.smtp_username:
            server.login(config.smtp_username, config.smtp_password)
        return server
    except Exception as exc:
        if server is not None:
            try:
                server.close()
            except Exception:
                pass
        raise _classify_smtp_exception(exc) from exc
