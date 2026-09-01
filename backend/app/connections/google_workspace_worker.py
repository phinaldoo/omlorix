"""Bundled single-user Gmail and Google Calendar MCP worker.

The Omlorix connection service already owns the Google OAuth lifecycle and keeps
refresh tokens encrypted. This worker deliberately accepts those credentials
only through its private subprocess environment, avoiding a second browser
OAuth flow and avoiding plaintext credential files inside the container.
"""

from __future__ import annotations

import os
import base64
from email.message import EmailMessage
from typing import Any, Callable

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from app.connections.google import (
    GOOGLE_PROVIDER_CAPABILITIES,
    GOOGLE_WORKSPACE_TOOL_CAPABILITIES_META_KEY,
)


def _required_env(name: str) -> str:
    """Return a required environment value or fail with a useful startup error."""
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required Google Workspace worker setting: {name}")
    return value


def _credentials() -> Credentials:
    """Build refreshable Google credentials from Omlorix-managed OAuth secrets."""
    return Credentials(
        token=None,
        refresh_token=_required_env("GOOGLE_WORKSPACE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=_required_env("GOOGLE_WORKSPACE_CLIENT_ID"),
        client_secret=_required_env("GOOGLE_WORKSPACE_CLIENT_SECRET"),
    )


def _service(api: str, version: str):
    """Create a discovery client without writing a discovery cache to disk."""
    return build(api, version, credentials=_credentials(), cache_discovery=False)


def _known_capabilities() -> set[str]:
    """Return all capabilities supported by the managed Google catalog."""
    return {
        str(capability or "").strip().lower()
        for capabilities in GOOGLE_PROVIDER_CAPABILITIES.values()
        for capability in capabilities
        if str(capability or "").strip()
    }


def _enabled_capabilities() -> set[str]:
    """Return exact capabilities from the comma-delimited worker allowlist."""
    raw = str(os.getenv("GOOGLE_WORKSPACE_ENABLED_CAPABILITIES") or "")
    allowed = _known_capabilities()
    return {
        token
        for item in raw.split(",")
        if (token := item.strip().lower()) in allowed
    }


class _CapabilityAwareMCPServer(MCPServer):
    """Expose only the Google capabilities granted to this worker process.

    Gmail and Google Calendar intentionally share one executable so Omlorix can
    reuse its credential and refresh lifecycle.  MCP discovery is separate
    from tool execution, however, so the normal ``@server.tool`` registration
    would otherwise advertise both products for every connection.  Keeping
    capability ownership on the server lets future tools opt into a capability
    without maintaining a second hardcoded allowlist in Omlorix.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tool_capabilities: dict[str, frozenset[str]] = {}

    def register_tool_capabilities(self, tool_name: str, capabilities: set[str]) -> None:
        """Record the Google API capabilities required by a registered tool."""
        normalized_name = str(tool_name or "").strip()
        normalized_capabilities = frozenset(
            str(capability or "").strip().lower()
            for capability in capabilities
            if str(capability or "").strip().lower() in _known_capabilities()
        )
        if not normalized_name or not normalized_capabilities:
            raise ValueError("Google Workspace tools must declare a valid capability.")
        self._tool_capabilities[normalized_name] = normalized_capabilities

    async def list_tools(self) -> list[Any]:
        """Return only tools compatible with this worker's enabled capabilities."""
        enabled_capabilities = _enabled_capabilities()
        tools = await super().list_tools()
        return [
            tool
            for tool in tools
            if self._tool_capabilities.get(tool.name, frozenset()) & enabled_capabilities
        ]


mcp = _CapabilityAwareMCPServer("Omlorix Google Workspace")


def _workspace_tool(
    *capabilities: str,
    **tool_options: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a tool and declare the Google capability it requires.

    The capability declaration is attached to MCP metadata so Omlorix can apply
    a second, provider-aware filter even if an older worker or a stale discovery
    response returns a broader tool list than expected.
    """
    normalized_capabilities = {
        str(capability or "").strip().lower()
        for capability in capabilities
        if str(capability or "").strip().lower() in _known_capabilities()
    }
    if not normalized_capabilities:
        raise ValueError("Google Workspace tools must declare a valid capability.")

    metadata = dict(tool_options.get("meta") or {})
    metadata[GOOGLE_WORKSPACE_TOOL_CAPABILITIES_META_KEY] = sorted(normalized_capabilities)
    tool_options["meta"] = metadata

    def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = str(tool_options.get("name") or function.__name__).strip()
        mcp.register_tool_capabilities(tool_name, normalized_capabilities)
        return mcp.tool(**tool_options)(function)

    return decorator


READ_ONLY_TOOL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
WRITE_TOOL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
DESTRUCTIVE_TOOL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)


@_workspace_tool("gmail", annotations=READ_ONLY_TOOL)
def search_gmail_messages(query: str, max_results: int = 20) -> dict[str, Any]:
    """Search Gmail using standard Gmail query syntax."""
    if "gmail" not in _enabled_capabilities():
        raise ValueError("Gmail is not enabled for this connection.")
    gmail = _service("gmail", "v1")
    result = gmail.users().messages().list(
        userId="me",
        q=query,
        maxResults=max(1, min(int(max_results), 100)),
    ).execute()
    return {"messages": result.get("messages", []), "resultSizeEstimate": result.get("resultSizeEstimate", 0)}


@_workspace_tool("gmail", annotations=READ_ONLY_TOOL)
def get_gmail_message(message_id: str, format: str = "metadata") -> dict[str, Any]:
    """Read a Gmail message by ID using metadata, full, minimal, or raw format."""
    if "gmail" not in _enabled_capabilities():
        raise ValueError("Gmail is not enabled for this connection.")
    safe_format = format if format in {"metadata", "full", "minimal", "raw"} else "metadata"
    return _service("gmail", "v1").users().messages().get(
        userId="me",
        id=message_id,
        format=safe_format,
    ).execute()


def _gmail_raw_message(to: list[str], subject: str, body: str, cc: list[str] | None = None) -> str:
    """Build the URL-safe RFC 2822 payload required by the Gmail API."""
    message = EmailMessage()
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


@_workspace_tool("gmail", annotations=WRITE_TOOL)
def create_gmail_draft(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
) -> dict[str, Any]:
    """Create a Gmail draft without sending it."""
    if "gmail" not in _enabled_capabilities():
        raise ValueError("Gmail is not enabled for this connection.")
    raw = _gmail_raw_message(to, subject, body, cc)
    return _service("gmail", "v1").users().drafts().create(
        userId="me",
        body={"message": {"raw": raw}},
    ).execute()


@_workspace_tool("gmail", annotations=DESTRUCTIVE_TOOL)
def send_gmail_message(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
) -> dict[str, Any]:
    """Send an email through Gmail; callers should obtain user confirmation first."""
    if "gmail" not in _enabled_capabilities():
        raise ValueError("Gmail is not enabled for this connection.")
    raw = _gmail_raw_message(to, subject, body, cc)
    return _service("gmail", "v1").users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()


@_workspace_tool("calendar", annotations=READ_ONLY_TOOL)
def list_calendar_events(
    calendar_id: str = "primary",
    time_min: str | None = None,
    time_max: str | None = None,
    max_results: int = 50,
) -> dict[str, Any]:
    """List Google Calendar events in an optional RFC3339 time range."""
    if "calendar" not in _enabled_capabilities():
        raise ValueError("Google Calendar is not enabled for this connection.")
    params: dict[str, Any] = {
        "calendarId": calendar_id,
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": max(1, min(int(max_results), 250)),
    }
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    return _service("calendar", "v3").events().list(**params).execute()


@_workspace_tool("calendar", annotations=WRITE_TOOL)
def create_calendar_event(
    summary: str,
    start: dict[str, Any],
    end: dict[str, Any],
    calendar_id: str = "primary",
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
) -> dict[str, Any]:
    """Create a Google Calendar event."""
    if "calendar" not in _enabled_capabilities():
        raise ValueError("Google Calendar is not enabled for this connection.")
    body: dict[str, Any] = {"summary": summary, "start": start, "end": end}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": email} for email in attendees]
    return _service("calendar", "v3").events().insert(calendarId=calendar_id, body=body).execute()


@_workspace_tool("calendar", annotations=DESTRUCTIVE_TOOL)
def update_calendar_event(
    event_id: str,
    changes: dict[str, Any],
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """Patch selected fields on an existing Google Calendar event."""
    if "calendar" not in _enabled_capabilities():
        raise ValueError("Google Calendar is not enabled for this connection.")
    return _service("calendar", "v3").events().patch(
        calendarId=calendar_id,
        eventId=event_id,
        body=changes,
    ).execute()


@_workspace_tool("calendar", annotations=DESTRUCTIVE_TOOL)
def delete_calendar_event(event_id: str, calendar_id: str = "primary") -> dict[str, bool]:
    """Delete a Google Calendar event."""
    if "calendar" not in _enabled_capabilities():
        raise ValueError("Google Calendar is not enabled for this connection.")
    _service("calendar", "v3").events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return {"deleted": True}


def main() -> None:
    """Run the worker over stdio for Omlorix's MCP client."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
