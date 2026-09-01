from __future__ import annotations


class ConnectionRefreshError(ValueError):
    """Base class for refresh failures that need service-level handling."""


class ConnectionRefreshReauthRequiredError(ConnectionRefreshError):
    """Refresh token is no longer usable and the connection must be reauthorized."""


class ConnectionRefreshRetryableError(ConnectionRefreshError):
    """Refresh failed, but stored credentials should be preserved for a later retry."""

    def __init__(self, message: str, *, status_code: int = 503):
        super().__init__(message)
        self.status_code = int(status_code)


def refresh_failure_status_code(upstream_status_code: int | None) -> int:
    if upstream_status_code == 429 or (upstream_status_code is not None and upstream_status_code >= 500):
        return 503
    return 502
