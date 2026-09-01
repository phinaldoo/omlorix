import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import HTTPException

from app.network.policy import (
    OutboundAccessMode,
    OutboundRequestBlockedError,
    assert_public_http_url_allowed,
)


class _SettingsPage:
    data = {"external_requests_mode": "allow_all", "external_requests_allowlist": []}


def _allow_all_settings(*_args, **_kwargs):
    return _SettingsPage()


@patch("app.network.policy.get_settings_page", _allow_all_settings)
def test_personal_mcp_url_policy_blocks_loopback_even_when_outbound_is_allow_all():
    with pytest.raises(OutboundRequestBlockedError, match="publicly routable"):
        assert_public_http_url_allowed(
            None,
            url="http://127.0.0.1:8080/mcp",
            feature="MCP HTTP transport",
        )


@patch("app.network.policy.get_settings_page", _allow_all_settings)
def test_personal_mcp_url_policy_blocks_non_http_schemes():
    with pytest.raises(OutboundRequestBlockedError, match="http or https"):
        assert_public_http_url_allowed(
            None,
            url="file:///etc/passwd",
            feature="MCP HTTP transport",
        )


@patch("app.network.policy._resolve_host_ips", return_value=("93.184.216.34",))
@patch("app.network.policy.get_settings_page", _allow_all_settings)
def test_personal_mcp_url_policy_allows_public_http_hosts(_resolve_host_ips):
    assert_public_http_url_allowed(
        None,
        url="https://example.com/mcp",
        feature="MCP HTTP transport",
    )


@patch("app.network.policy._resolve_host_ips", return_value=("93.184.216.34", "127.0.0.1"))
@patch("app.network.policy.get_settings_page", _allow_all_settings)
def test_personal_mcp_url_policy_blocks_hosts_with_any_non_public_resolution(_resolve_host_ips):
    with pytest.raises(OutboundRequestBlockedError, match="publicly routable"):
        assert_public_http_url_allowed(
            None,
            url="https://example.com/mcp",
            feature="MCP HTTP transport",
        )


@patch("app.network.policy.get_settings_page", _allow_all_settings)
def test_personal_mcp_url_policy_blocks_multicast_address():
    with pytest.raises(OutboundRequestBlockedError, match="publicly routable"):
        assert_public_http_url_allowed(
            None,
            url="http://224.0.0.1:8080/mcp",
            feature="MCP HTTP transport",
        )


def test_personal_mcp_runtime_policy_uses_public_url_guard():
    from app.mcp.models import OWNER_USER
    from app.mcp.utils import _assert_mcp_url_allowed

    class _Server:
        owner_type = OWNER_USER
        url = "http://127.0.0.1:8080/mcp"

    class _Session:
        def close(self):
            pass

    with patch("app.mcp.utils.SessionLocal", return_value=_Session()), patch(
        "app.mcp.utils.assert_public_http_url_allowed",
        side_effect=OutboundRequestBlockedError(
            target=_Server.url,
            feature="MCP HTTP transport",
            policy_mode=OutboundAccessMode.allow_all,
            reason="personal MCP servers must use publicly routable destinations",
        ),
    ) as public_guard, patch("app.mcp.utils.assert_url_allowed") as generic_guard, pytest.raises(
        HTTPException
    ):
        _assert_mcp_url_allowed(_Server(), feature="MCP HTTP transport")

    public_guard.assert_called_once()
    generic_guard.assert_not_called()


def test_personal_mcp_httpx2_clients_use_public_transport():
    from app.mcp.models import OWNER_ADMIN, OWNER_USER
    from app.mcp.utils import _mcp_public_httpx2_transport

    class _UserServer:
        owner_type = OWNER_USER

    class _AdminServer:
        owner_type = OWNER_ADMIN

    sentinel = object()
    with patch("app.mcp.utils.public_async_httpx2_transport", return_value=sentinel) as transport_factory:
        assert _mcp_public_httpx2_transport(_UserServer(), feature="MCP HTTP transport") is sentinel
        assert _mcp_public_httpx2_transport(_AdminServer(), feature="MCP HTTP transport") is None

    transport_factory.assert_called_once_with(feature="MCP HTTP transport")
