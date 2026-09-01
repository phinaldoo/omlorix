from __future__ import annotations

from email.headerregistry import Address


def normalize_single_mailbox(value: object) -> str:
    """Return one header-safe mailbox or reject lists and display-name input."""

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("An email address is required")
    try:
        address = Address(addr_spec=raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("A valid single email address is required") from exc
    if not address.username or not address.domain:
        raise ValueError("A valid single email address is required")
    return address.addr_spec
