from __future__ import annotations

import socket
import ssl
from collections.abc import Iterator

from requests import exceptions as requests_exceptions


class BackupArchivePolicyError(RuntimeError):
    """Raised when a requested archive mode conflicts with server policy."""

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    """Yield wrapped exceptions once so hidden transport failures can be classified.

    webdavclient3 replaces Requests exceptions with its own generic exception.
    Python retains the original exception as ``__context__`` and some client
    wrappers store it in an ``exception`` attribute. Following both forms lets
    the API return an actionable, stable code without exposing raw URLs,
    credentials, response bodies, or provider-specific exception strings.
    """
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current

        for nested in (
            current.__cause__,
            current.__context__,
            getattr(current, "exception", None),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)


def classify_backup_destination_test_error(error: Exception) -> str:
    """Map storage-client failures to stable, non-sensitive frontend codes."""
    chain = tuple(_exception_chain(error))
    message = " ".join(str(item).lower() for item in chain)
    status_codes = {
        int(code)
        for item in chain
        if isinstance((code := getattr(item, "code", None)), int | str)
        and str(code).isdigit()
    }

    # TLS failures are also connection failures, so classify them first to
    # retain the certificate-specific guidance needed by administrators.
    if any(
        isinstance(item, (requests_exceptions.SSLError, ssl.SSLError))
        for item in chain
    ) or any(
        marker in message
        for marker in (
            "certificate verify failed",
            "certificate_verify_failed",
            "hostname mismatch",
            "ip address mismatch",
            "self-signed certificate",
        )
    ):
        return "backup_destination_tls_certificate_invalid"

    if 401 in status_codes:
        return "backup_destination_authentication_failed"
    if 403 in status_codes:
        return "backup_destination_permission_denied"
    if 404 in status_codes or any(
        item.__class__.__name__ in {"RemoteResourceNotFound", "RemoteParentNotFound"}
        for item in chain
    ):
        return "backup_destination_path_not_found"

    if any(
        isinstance(
            item,
            (
                requests_exceptions.Timeout,
                socket.timeout,
                TimeoutError,
            ),
        )
        for item in chain
    ) or "timed out" in message:
        return "backup_destination_connection_timeout"

    if any(
        item.__class__.__name__ == "MethodNotSupported" for item in chain
    ) or "method not supported" in message:
        return "backup_destination_protocol_unsupported"

    if any(
        isinstance(item, requests_exceptions.ConnectionError) for item in chain
    ) or any(
        marker in message
        for marker in (
            "connection refused",
            "network is unreachable",
            "no route to host",
            "name or service not known",
            "nodename nor servname provided",
            "temporary failure in name resolution",
            "no connection with",
        )
    ):
        return "backup_destination_unreachable"

    return "backup_destination_test_failed"
