from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import os
from pathlib import Path
import shlex
import subprocess
import socket
import tempfile
from typing import Any, Iterator

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_ssh_private_key,
)


class SshPrivateKeyError(ValueError):
    """Describe a private-key problem with a stable, user-safe error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_ssh_private_key(private_key: Any) -> str:
    """Normalize harmless copy/paste differences without changing key contents.

    Private-key files conventionally end in a newline, while textareas and
    clipboard sources may add several blank lines, Windows line endings, or a
    Unicode byte-order mark. Only those boundary differences are normalized;
    whitespace inside the encoded key remains untouched.
    """
    normalized = str(private_key or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.strip()
    if normalized.startswith("\ufeff"):
        normalized = normalized.removeprefix("\ufeff").strip()
    return normalized


def validate_ssh_private_key(private_key: Any) -> str:
    """Return a normalized, parseable, unencrypted OpenSSH or PEM private key."""
    normalized = normalize_ssh_private_key(private_key)
    if not normalized:
        raise SshPrivateKeyError("ssh_private_key_required")

    first_line = normalized.splitlines()[0] if normalized else ""
    is_openssh = first_line == "-----BEGIN OPENSSH PRIVATE KEY-----"
    is_pem = first_line in {
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
    }
    encrypted_marker = (
        "ENCRYPTED PRIVATE KEY" in first_line
        or "Proc-Type: 4,ENCRYPTED" in normalized
    )
    if encrypted_marker:
        raise SshPrivateKeyError("ssh_private_key_encrypted")
    if not is_openssh and not is_pem:
        raise SshPrivateKeyError("ssh_private_key_invalid")

    try:
        loader = load_ssh_private_key if is_openssh else load_pem_private_key
        loader(normalized.encode("utf-8"), password=None)
    except TypeError as exc:
        # Both cryptography loaders use TypeError when encrypted material is
        # supplied without a password. Omlorix deliberately runs SSH without an
        # interactive passphrase prompt.
        raise SshPrivateKeyError("ssh_private_key_encrypted") from exc
    except (UnsupportedAlgorithm, ValueError, UnicodeError) as exc:
        raise SshPrivateKeyError("ssh_private_key_invalid") from exc
    return normalized


def classify_ssh_connection_error(error: Exception) -> str:
    """Map OpenSSH and network failures to stable, non-sensitive UI codes."""
    message = str(error).lower()
    if isinstance(error, SshPrivateKeyError):
        return error.code
    if isinstance(error, subprocess.TimeoutExpired) or "timed out" in message:
        return "ssh_connection_timeout"
    if "permission denied" in message or "authentication failed" in message:
        return "ssh_authentication_failed"
    if (
        "host key verification failed" in message
        or "remote host identification has changed" in message
    ):
        return "ssh_host_key_verification_failed"
    if (
        "could not resolve hostname" in message
        or "could not be resolved" in message
        or "connection refused" in message
        or "network is unreachable" in message
        or "no route to host" in message
    ):
        return "ssh_connection_unreachable"
    if (
        "error in libcrypto" in message
        or "invalid format" in message
        or "load key" in message
    ):
        return "ssh_private_key_invalid"
    return "ssh_connection_failed"


@dataclass(frozen=True, slots=True)
class SshConnectionSnapshot:
    """Detached SSH values safe to use after releasing an ORM session."""

    id: str
    name: str
    host: str
    port: int
    username: str
    host_key: str
    config: dict[str, Any]
    secrets: dict[str, Any]


def snapshot_ssh_connection(connection) -> SshConnectionSnapshot:
    """Copy every transport field needed by a long-running SSH subprocess."""
    return SshConnectionSnapshot(
        id=str(connection.id),
        name=str(connection.name),
        host=str(connection.host),
        port=int(connection.port or 22),
        username=str(connection.username),
        host_key=str(connection.host_key),
        config=dict(connection.config) if isinstance(connection.config, dict) else {},
        secrets=dict(connection.secrets) if isinstance(connection.secrets, dict) else {},
    )


def _utc_iso() -> str:
    """Return an audit-friendly UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def host_key_fingerprint(host_key: str) -> str:
    """Return the SHA256 fingerprint displayed by OpenSSH for a pinned key."""
    import base64

    parts = str(host_key or "").split()
    if len(parts) < 2:
        return ""
    try:
        decoded = base64.b64decode(parts[1].encode("ascii"), validate=True)
    except Exception:
        return ""
    digest = base64.b64encode(hashlib.sha256(decoded).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def ssh_known_host_name(connection) -> str:
    """Return the exact OpenSSH lookup name for a pinned host and port.

    OpenSSH keys non-default ports as ``[host]:port``. The same value must be
    used both in the generated known-hosts file and as ``HostKeyAlias`` when
    Omlorix connects to a pre-validated numeric destination.
    """
    host = str(connection.host).strip()
    port = int(connection.port or 22)
    return host if port == 22 else f"[{host}]:{port}"


def serialize_ssh_connection(connection) -> dict[str, Any]:
    """Return connection metadata while keeping all credentials server-side."""
    config = connection.config if isinstance(connection.config, dict) else {}
    secrets = connection.secrets if isinstance(connection.secrets, dict) else {}
    return {
        "id": connection.id,
        "name": connection.name,
        "icon": str(connection.icon or "server"),
        "host": connection.host,
        "port": int(connection.port or 22),
        "username": connection.username,
        "host_key": connection.host_key,
        "host_key_fingerprint": host_key_fingerprint(connection.host_key),
        "workspace_root": str(config.get("workspace_root") or "~"),
        "connect_timeout_seconds": int(config.get("connect_timeout_seconds") or 15),
        "enabled": bool(connection.enabled),
        "has_private_key": bool(str(secrets.get("private_key") or "").strip()),
        "status": dict(connection.status) if isinstance(connection.status, dict) else {},
        "created_at": connection.created_at.isoformat() if connection.created_at else None,
        "updated_at": connection.updated_at.isoformat() if connection.updated_at else None,
    }


@contextmanager
def materialize_ssh_credentials(connection) -> Iterator[tuple[str, str]]:
    """Materialize encrypted key material in a private directory for one SSH process.

    OpenSSH requires filesystem paths for identity and known-hosts files. The
    files live only for this context, are mode 0600, and are deleted even when
    the remote command is cancelled or fails.
    """
    secrets = connection.secrets if isinstance(connection.secrets, dict) else {}
    # Validate again at the transport boundary so legacy or externally restored
    # records cannot reach OpenSSH with ambiguous key material.
    private_key = validate_ssh_private_key(secrets.get("private_key"))

    known_host_name = ssh_known_host_name(connection)
    known_hosts_line = f"{known_host_name} {str(connection.host_key).strip()}\n"

    with tempfile.TemporaryDirectory(prefix="omlorix-ssh-") as directory:
        key_path = Path(directory) / "identity"
        known_hosts_path = Path(directory) / "known_hosts"
        key_path.write_text(private_key + "\n", encoding="utf-8")
        known_hosts_path.write_text(known_hosts_line, encoding="utf-8")
        os.chmod(key_path, 0o600)
        os.chmod(known_hosts_path, 0o600)
        yield str(key_path), str(known_hosts_path)


def build_ssh_arguments(
    connection,
    identity_file: str,
    known_hosts_file: str,
    *,
    allocate_tty: bool = False,
    destination_host: str | None = None,
) -> list[str]:
    """Build strict OpenSSH arguments for a saved connection.

    ACP subprocesses use ``-T`` because the protocol is transported over
    stdin/stdout. Interactive terminal sessions instead require ``-tt`` so the
    remote login shell receives a real terminal even though Omlorix itself is
    running without an attached console.
    """
    config = connection.config if isinstance(connection.config, dict) else {}
    timeout = max(3, min(int(config.get("connect_timeout_seconds") or 15), 120))
    host_key_alias = ssh_known_host_name(connection)
    destination = str(destination_host or connection.host).strip()
    return [
        "-F", "/dev/null",
        "-tt" if allocate_tty else "-T",
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts_file}",
        "-o", f"ConnectTimeout={timeout}",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=2",
        "-o", "ForwardAgent=no",
        "-o", "ClearAllForwardings=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", f"HostKeyAlias={host_key_alias}",
        "-i", identity_file,
        "-p", str(int(connection.port or 22)),
        # OpenSSH parses options again after discovering the destination. Keep
        # the terminator before it so both the destination and every remote
        # command remain data, never additional local SSH options.
        "--",
        f"{connection.username}@{destination}",
    ]


def quote_remote_command(arguments: list[str]) -> str:
    """Quote an argv list for the remote POSIX login shell used by SSH."""
    if not arguments:
        raise ValueError("Remote command is required")
    return shlex.join([str(argument) for argument in arguments])


def build_acp_remote_command(arguments: list[str]) -> str:
    """Launch a remote ACP process with the user's login environment.

    OpenSSH executes a supplied remote command through a non-interactive shell.
    Those shells commonly omit PATH additions made by Homebrew, nvm, asdf, and
    similar tool managers, so otherwise valid commands such as ``npx`` cannot
    be found even though they work in the user's terminal.

    The shell program is fixed and every user-configured value is passed as a
    positional argument to ``exec``. This loads the login environment without
    interpolating the executable or its arguments into shell source.
    """
    if not arguments:
        raise ValueError("Remote ACP command is required")
    return quote_remote_command(
        [
            "/bin/sh",
            "-lc",
            'exec "$@"',
            "omlorix-acp",
            *[str(argument) for argument in arguments],
        ]
    )


def discover_host_keys(host: str, port: int, *, timeout_seconds: int = 10) -> list[dict[str, str]]:
    """Fetch untrusted host keys for explicit user verification before saving."""
    destination = validate_ssh_destination(host, port)[0]
    try:
        completed = subprocess.run(
            ["ssh-keyscan", "-T", str(timeout_seconds), "-p", str(port), destination],
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 2,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("The Omlorix server does not have ssh-keyscan installed") from exc
    keys: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        public_key = f"{parts[-2]} {parts[-1]}"
        if public_key in seen:
            continue
        seen.add(public_key)
        keys.append(
            {
                "host_key": public_key,
                "key_type": parts[-2],
                "fingerprint": host_key_fingerprint(public_key),
            }
        )
    if not keys:
        detail = completed.stderr.strip() or "No SSH host key was returned"
        raise ConnectionError(detail[:500])
    return keys


def run_ssh_command(
    connection,
    remote_arguments: list[str],
    *,
    stdin_text: str | None = None,
    timeout_seconds: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Execute one remote argv under strict host-key and key-only authentication."""
    destination = validate_ssh_destination(
        connection.host, int(connection.port or 22)
    )[0]
    with materialize_ssh_credentials(connection) as (identity_file, known_hosts_file):
        ssh_arguments = build_ssh_arguments(
            connection,
            identity_file,
            known_hosts_file,
            destination_host=destination,
        )
        try:
            return subprocess.run(
                ["ssh", *ssh_arguments, quote_remote_command(remote_arguments)],
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=max(1, timeout_seconds),
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("The Omlorix server does not have the OpenSSH client installed") from exc


def validate_ssh_destination(host: str, port: int) -> list[str]:
    """Resolve a destination and reject addresses unsafe for server-side SSH.

    Private LAN and Tailscale ranges are intentionally allowed: reaching a
    user's own hardware is the feature. Loopback, link-local, multicast, and
    unspecified addresses are denied to prevent access to the Omlorix host and
    cloud instance metadata endpoints.
    """
    try:
        records = socket.getaddrinfo(str(host), int(port), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConnectionError(f"SSH host could not be resolved: {exc}") from exc
    addresses = sorted({record[4][0] for record in records})
    if not addresses:
        raise ConnectionError("SSH host did not resolve to an address")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ConnectionError("SSH destination resolves to a blocked local or special-use address")
    return addresses


def test_ssh_connection(connection) -> dict[str, Any]:
    """Authenticate and run a side-effect-free capability probe."""
    started_at = datetime.now(timezone.utc)
    completed = run_ssh_command(
        connection,
        ["sh", "-c", "printf '%s\\n' omlorix-ssh-ok && uname -s && uname -m"],
        timeout_seconds=max(5, int((connection.config or {}).get("connect_timeout_seconds") or 15) + 5),
    )
    if completed.returncode != 0:
        raise ConnectionError((completed.stderr or "SSH connection test failed").strip()[:1000])
    lines = completed.stdout.splitlines()
    if not lines or lines[0].strip() != "omlorix-ssh-ok":
        raise ConnectionError("SSH host returned an unexpected test response")
    return {
        "state": "connected",
        "platform": lines[1].strip() if len(lines) > 1 else "",
        "architecture": lines[2].strip() if len(lines) > 2 else "",
        "checked_at": _utc_iso(),
        "latency_ms": max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)),
        "last_error": "",
    }
