from __future__ import annotations

import socket
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError
from urllib3.util.connection import allowed_gai_family
from urllib3.util.timeout import _DEFAULT_TIMEOUT

from app.network.policy import (
    OutboundAccessMode,
    OutboundRequestBlockedError,
    _is_public_ip_address,
    assert_http_url_allowed,
    assert_outbound_peer_ip_allowed,
)


PeerIPValidator = Callable[[str, str, int | None], None]


def _target_label(host: str, port: int | None = None, ip: str | None = None) -> str:
    label = host
    if port is not None:
        label = f"{label}:{port}"
    if ip:
        label = f"{label} ({ip})"
    return label


def assert_public_peer_ip_allowed(
    ip: str,
    *,
    host: str,
    port: int | None = None,
    feature: str,
) -> None:
    if _is_public_ip_address(ip):
        return
    raise OutboundRequestBlockedError(
        target=_target_label(host, port, ip),
        feature=feature,
        policy_mode=OutboundAccessMode.allow_all,
        reason="connected peer IP is not publicly routable",
    )


def _resolve_tcp_addresses(
    host: str,
    port: int,
    *,
    feature: str,
    family: int = socket.AF_UNSPEC,
    peer_ip_validator: PeerIPValidator,
) -> list[tuple[int, int, int, str, tuple[Any, ...]]]:
    """Resolve a host once and retain only policy-approved peer addresses."""

    hostname = host.strip("[]")
    try:
        infos = socket.getaddrinfo(
            hostname,
            port,
            family,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
    except OSError:
        raise

    resolved: list[tuple[int, int, int, str, tuple[Any, ...]]] = []
    seen: set[tuple[int, str, int]] = set()
    for af, socktype, proto, canonname, sockaddr in infos:
        if not sockaddr:
            continue
        ip = sockaddr[0]
        peer_ip_validator(ip, hostname, port)
        key = (af, ip, sockaddr[1] if len(sockaddr) > 1 else port)
        if key in seen:
            continue
        seen.add(key)
        resolved.append((af, socktype, proto, canonname, sockaddr))

    if not resolved:
        raise OSError("getaddrinfo returns an empty list")
    return resolved


def resolve_public_tcp_addresses(
    host: str,
    port: int,
    *,
    feature: str,
    family: int = socket.AF_UNSPEC,
) -> list[tuple[int, int, int, str, tuple[Any, ...]]]:
    """Resolve public addresses for callers that never permit private peers."""

    return _resolve_tcp_addresses(
        host,
        port,
        feature=feature,
        family=family,
        peer_ip_validator=lambda ip, hostname, peer_port: assert_public_peer_ip_allowed(
            ip,
            host=hostname,
            port=peer_port,
            feature=feature,
        ),
    )


def _create_validated_web_connection(
    host: str,
    port: int,
    timeout: Any = None,
    *,
    source_address: tuple[str, int] | None = None,
    socket_options: list[tuple[Any, ...]] | tuple[tuple[Any, ...], ...] | None = None,
    feature: str,
    peer_ip_validator: PeerIPValidator,
    family: int | None = None,
) -> socket.socket:
    """Connect to a resolved address and verify the actual peer before use."""

    resolved_family = allowed_gai_family() if family is None else family
    err: OSError | None = None
    for af, socktype, proto, _canonname, sockaddr in _resolve_tcp_addresses(
        host,
        port,
        feature=feature,
        family=resolved_family,
        peer_ip_validator=peer_ip_validator,
    ):
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            for option in socket_options or ():
                sock.setsockopt(*option)
            if timeout is not None:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            peer_ip = sock.getpeername()[0]
            peer_ip_validator(peer_ip, host.strip("[]"), port)
            return sock
        except OutboundRequestBlockedError:
            if sock is not None:
                sock.close()
            raise
        except OSError as exc:
            err = exc
            if sock is not None:
                sock.close()

    if err is not None:
        raise err
    raise OSError("getaddrinfo returns an empty list")


def create_public_web_connection(
    host: str,
    port: int,
    timeout: Any = None,
    *,
    source_address: tuple[str, int] | None = None,
    socket_options: list[tuple[Any, ...]] | tuple[tuple[Any, ...], ...] | None = None,
    feature: str,
    family: int | None = None,
) -> socket.socket:
    """Connect to a public peer using a DNS-rebinding-safe pinned address."""

    return _create_validated_web_connection(
        host,
        port,
        timeout,
        source_address=source_address,
        socket_options=socket_options,
        feature=feature,
        family=family,
        peer_ip_validator=lambda ip, hostname, peer_port: assert_public_peer_ip_allowed(
            ip,
            host=hostname,
            port=peer_port,
            feature=feature,
        ),
    )


def _stream_peer_ip(stream: Any) -> str | None:
    get_extra_info = getattr(stream, "get_extra_info", None)
    if not callable(get_extra_info):
        return None

    peername = get_extra_info("peername")
    if isinstance(peername, tuple) and peername:
        return str(peername[0])

    sock = get_extra_info("socket")
    getpeername = getattr(sock, "getpeername", None)
    if callable(getpeername):
        try:
            sock_peername = getpeername()
        except OSError:
            return None
        if isinstance(sock_peername, tuple) and sock_peername:
            return str(sock_peername[0])
    return None


def assert_public_httpx_stream_peer_allowed(
    stream: Any,
    *,
    host: str,
    port: int,
    feature: str,
) -> None:
    peer_ip = _stream_peer_ip(stream)
    if peer_ip is None:
        raise OutboundRequestBlockedError(
            target=_target_label(host.strip("[]"), port),
            feature=feature,
            policy_mode=OutboundAccessMode.allow_all,
            reason="connected peer IP could not be verified",
        )
    assert_public_peer_ip_allowed(
        peer_ip,
        host=host.strip("[]"),
        port=port,
        feature=feature,
    )


class PublicAsyncNetworkBackend:
    """Network backend that pins outbound connections to public IP addresses.

    Both ``httpx`` and MCP SDK v2's ``httpx2`` expose the same httpcore backend
    contract, but they use distinct ``httpcore`` packages. Keeping the package
    selection explicit prevents accidentally passing an httpcore v1 stream to
    an httpcore2 connection pool.
    """

    def __init__(
        self,
        *,
        feature: str,
        backend: Any | None = None,
        backend_package: str = "httpcore",
    ) -> None:
        self._feature = feature
        self._backend = backend
        if backend_package not in {"httpcore", "httpcore2"}:
            raise ValueError("Unsupported HTTP core backend package.")
        self._backend_package = backend_package

    def _network_backend(self) -> Any:
        if self._backend is None:
            if self._backend_package == "httpcore2":
                from httpcore2._backends.auto import AutoBackend
            else:
                from httpcore._backends.auto import AutoBackend

            self._backend = AutoBackend()
        return self._backend

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any | None = None,
    ) -> Any:
        err: Exception | None = None
        for _af, _socktype, _proto, _canonname, sockaddr in resolve_public_tcp_addresses(
            host,
            port,
            feature=self._feature,
        ):
            ip_address = sockaddr[0]
            stream = None
            try:
                stream = await self._network_backend().connect_tcp(
                    str(ip_address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
                assert_public_httpx_stream_peer_allowed(
                    stream,
                    host=host,
                    port=port,
                    feature=self._feature,
                )
                return stream
            except OutboundRequestBlockedError:
                if stream is not None:
                    await stream.aclose()
                raise
            except Exception as exc:
                err = exc
                if stream is not None:
                    await stream.aclose()

        if err is not None:
            raise err
        raise OSError("getaddrinfo returns an empty list")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any | None = None,
    ) -> Any:
        raise OutboundRequestBlockedError(
            target=path,
            feature=self._feature,
            policy_mode=OutboundAccessMode.allow_all,
            reason="Unix socket connections are not allowed for public web requests",
        )

    async def sleep(self, seconds: float) -> None:
        await self._network_backend().sleep(seconds)


def public_async_httpx_transport(*, feature: str, **kwargs: Any) -> Any:
    """Build Omlorix's DNS-rebinding-safe transport for ordinary httpx."""
    import httpx

    kwargs.setdefault("trust_env", False)
    transport = httpx.AsyncHTTPTransport(**kwargs)
    transport._pool._network_backend = PublicAsyncNetworkBackend(feature=feature)
    return transport


def public_async_httpx2_transport(*, feature: str, **kwargs: Any) -> Any:
    """Build the equivalent safe transport required by MCP Python SDK v2."""
    import httpx2

    kwargs.setdefault("trust_env", False)
    transport = httpx2.AsyncHTTPTransport(**kwargs)
    transport._pool._network_backend = PublicAsyncNetworkBackend(
        feature=feature,
        backend_package="httpcore2",
    )
    return transport


def _build_policy_pool_classes(
    feature: str,
    *,
    peer_ip_validator: PeerIPValidator | None = None,
) -> tuple[type[HTTPConnectionPool], type[HTTPSConnectionPool]]:
    class _PolicyConnectionMixin:
        def _new_conn(self) -> socket.socket:
            try:
                connection_kwargs = {
                    "source_address": self.source_address,
                    "socket_options": self.socket_options,
                    "feature": feature,
                }
                if peer_ip_validator is None:
                    sock = create_public_web_connection(
                        self._dns_host,
                        self.port,
                        None if self.timeout is _DEFAULT_TIMEOUT else self.timeout,
                        **connection_kwargs,
                    )
                else:
                    sock = _create_validated_web_connection(
                        self._dns_host,
                        self.port,
                        None if self.timeout is _DEFAULT_TIMEOUT else self.timeout,
                        peer_ip_validator=peer_ip_validator,
                        **connection_kwargs,
                    )
            except socket.gaierror as exc:
                raise NameResolutionError(self.host, self, exc) from exc
            except TimeoutError as exc:
                raise ConnectTimeoutError(
                    self,
                    f"Connection to {self.host} timed out. (connect timeout={self.timeout})",
                ) from exc
            except OutboundRequestBlockedError:
                raise
            except OSError as exc:
                raise NewConnectionError(
                    self,
                    f"Failed to establish a new connection: {exc}",
                ) from exc

            sys.audit("http.client.connect", self, self.host, self.port)
            return sock

    class PolicyHTTPConnection(_PolicyConnectionMixin, HTTPConnection):
        pass

    class PolicyHTTPSConnection(_PolicyConnectionMixin, HTTPSConnection):
        pass

    class PolicyHTTPConnectionPool(HTTPConnectionPool):
        ConnectionCls = PolicyHTTPConnection

    class PolicyHTTPSConnectionPool(HTTPSConnectionPool):
        ConnectionCls = PolicyHTTPSConnection

    return PolicyHTTPConnectionPool, PolicyHTTPSConnectionPool


class PublicWebHTTPAdapter(HTTPAdapter):
    def __init__(self, *, feature: str, **kwargs: Any) -> None:
        self._feature = feature
        super().__init__(**kwargs)

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        http_pool, https_pool = _build_policy_pool_classes(self._feature)
        manager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )
        manager.pool_classes_by_scheme = {
            "http": http_pool,
            "https": https_pool,
        }
        self.poolmanager = manager


class OutboundPolicyHTTPAdapter(PublicWebHTTPAdapter):
    """Requests adapter that pins connections to outbound-policy-approved IPs."""

    def __init__(
        self,
        *,
        feature: str,
        peer_ip_validator: PeerIPValidator,
        **kwargs: Any,
    ) -> None:
        self._peer_ip_validator = peer_ip_validator
        super().__init__(feature=feature, **kwargs)

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        http_pool, https_pool = _build_policy_pool_classes(
            self._feature,
            peer_ip_validator=self._peer_ip_validator,
        )
        manager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )
        manager.pool_classes_by_scheme = {
            "http": http_pool,
            "https": https_pool,
        }
        self.poolmanager = manager


def _attach_session_lifetime(response: requests.Response, session: requests.Session) -> None:
    """Keep a one-request session alive until its returned response is closed."""

    original_close = response.close

    def close_with_session() -> None:
        try:
            original_close()
        finally:
            session.close()

    response.close = close_with_session


def public_web_request(
    method: str,
    url: str,
    *,
    feature: str,
    **kwargs: Any,
) -> requests.Response:
    session = requests.Session()
    session.trust_env = False
    adapter = PublicWebHTTPAdapter(feature=feature)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    try:
        response = session.request(method, url, **kwargs)
    except Exception:
        session.close()
        raise

    _attach_session_lifetime(response, session)
    return response


def outbound_policy_web_request(
    db,
    method: str,
    url: str,
    *,
    feature: str,
    max_redirects: int = 10,
    **kwargs: Any,
) -> requests.Response:
    """Send an HTTP request through the active policy and validate redirects.

    The session ignores proxy environment variables. Every redirect URL is
    checked before it is requested, and the adapter connects directly to a DNS
    result that was validated again as the actual socket peer.
    """

    session = requests.Session()
    session.trust_env = False

    def validate_peer(ip_address: str, host: str, port: int | None) -> None:
        assert_outbound_peer_ip_allowed(
            db,
            host=host,
            ip_address=ip_address,
            port=port,
            feature=feature,
        )

    adapter = OutboundPolicyHTTPAdapter(
        feature=feature,
        peer_ip_validator=validate_peer,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    current_url = url
    request_kwargs = dict(kwargs)
    request_kwargs.pop("allow_redirects", None)
    try:
        for _redirect_count in range(max_redirects + 1):
            assert_http_url_allowed(db, url=current_url, feature=feature)
            response = session.request(
                method,
                current_url,
                allow_redirects=False,
                **request_kwargs,
            )
            if not response.is_redirect and not response.is_permanent_redirect:
                _attach_session_lifetime(response, session)
                return response

            location = response.headers.get("Location")
            if not location:
                _attach_session_lifetime(response, session)
                return response
            response.close()
            current_url = urljoin(current_url, location)

            # Query parameters belong to the initial request URL. A redirect's
            # Location header defines the complete next URL, matching Requests'
            # normal redirect behavior instead of appending the query twice.
            request_kwargs.pop("params", None)
    except Exception:
        session.close()
        raise

    session.close()
    raise requests.TooManyRedirects(f"Exceeded {max_redirects} redirects.")
