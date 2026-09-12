"""Focused tests for user-owned SSH connection validation and transport safety."""

from pathlib import Path
import shlex
import subprocess
from types import SimpleNamespace

import pytest
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.remote_connections import ssh as ssh_transport
from app.remote_connections.models import SshConnection
from app.remote_connections.schemas import (
    AcpProfileInput,
    SshConnectionInput,
    SshConnectionUpdate,
    SshConnectionResponse,
    SshHostKeyDiscoveryRequest,
)
from app.remote_connections.service import build_ssh_connection_test_candidate
from app.remote_connections.ssh import (
    SshPrivateKeyError,
    build_acp_remote_command,
    build_ssh_arguments,
    classify_ssh_connection_error,
    materialize_ssh_credentials,
    normalize_ssh_private_key,
    quote_remote_command,
    validate_ssh_private_key,
    validate_ssh_destination,
)
from app.remote_connections.terminal import (
    build_terminal_remote_command,
    clamp_terminal_size,
    resolve_terminal_cwd,
)


HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockHostKeyMaterialForTestsOnly"
PRIVATE_KEY = (
    ed25519.Ed25519PrivateKey.generate()
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    .decode("utf-8")
    .strip()
)


def _connection():
    """Return the smallest connection object accepted by transport helpers."""
    return SimpleNamespace(
        host="mac-mini.example.test",
        port=2222,
        username="omlorix",
        host_key=HOST_KEY,
        config={"connect_timeout_seconds": 9},
        secrets={"private_key": PRIVATE_KEY},
    )


def test_ssh_schema_requires_absolute_workspace_and_safe_host():
    """Reject URL-shaped hosts and workspaces whose meaning depends on a shell."""
    common = dict(name="Mac mini", port=22, username="omlorix", host_key=HOST_KEY, private_key="key")
    with pytest.raises(ValueError):
        SshConnectionInput(**common, host="https://example.test", workspace_root="/srv/omlorix")
    with pytest.raises(ValueError):
        SshConnectionInput(**common, host="example.test", workspace_root="~/projects")
    assert SshHostKeyDiscoveryRequest(host="example.test", port=22).host == "example.test"


def test_edited_ssh_test_candidate_uses_draft_values_and_saved_private_key():
    """Editing tests every current field while retaining an omitted stored key."""
    saved = SimpleNamespace(
        secrets={"private_key": PRIVATE_KEY},
    )
    payload = SshConnectionUpdate(
        name="Changed Mac",
        icon="laptop",
        host="new-host.example.test",
        port=2200,
        username="new-user",
        host_key=HOST_KEY,
        private_key=None,
        workspace_root="/srv/new-workspace",
        connect_timeout_seconds=20,
        enabled=True,
    )

    candidate = build_ssh_connection_test_candidate("user-one", payload, saved)

    assert candidate.host == "new-host.example.test"
    assert candidate.port == 2200
    assert candidate.username == "new-user"
    assert candidate.config["workspace_root"] == "/srv/new-workspace"
    assert candidate.secrets["private_key"] == PRIVATE_KEY


def test_ssh_connections_do_not_expose_a_code_execution_selector():
    """Keep SSH persistence and APIs scoped to ACP and terminal access."""

    assert "use_for_code_execution" not in SshConnectionInput.model_fields
    assert "use_for_code_execution" not in SshConnectionResponse.model_fields
    assert "use_for_code_execution" not in SshConnection.__table__.columns


def test_remote_connection_schemas_support_device_and_safe_custom_acp_icons():
    """Persist SSH presets and permit inert custom SVG only for ACP rendering."""

    ssh = SshConnectionInput(
        name="MacBook",
        icon="laptop",
        host="macbook.example.test",
        port=22,
        username="omlorix",
        host_key=HOST_KEY,
        private_key="key",
        workspace_root="/Users/omlorix/workspace",
    )
    custom_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="currentColor" d="M4 4h16v16H4z"/></svg>'
    )
    acp = AcpProfileInput(
        name="OpenCode",
        icon=custom_svg,
        ssh_connection_id="one",
        arguments=["--flag", "--flag", "value"],
        workspace_root="/srv/code",
    )

    assert ssh.icon == "laptop"
    assert acp.icon == custom_svg
    assert acp.arguments == ["--flag", "--flag", "value"]
    assert "icon" in SshConnection.__table__.columns
    with pytest.raises(ValueError):
        AcpProfileInput(
            name="Unsafe",
            icon='<svg onload="alert(1)"><path d="M0 0"/></svg>',
            ssh_connection_id="one",
            workspace_root="/srv/code",
        )


def test_acp_paths_cannot_escape_or_be_absolute():
    """Keep profile directories relative to the explicitly selected workspace."""
    with pytest.raises(ValueError):
        AcpProfileInput(
            name="OpenCode", ssh_connection_id="one", workspace_root="/srv/code",
            cwd="/etc", additional_directories=[],
        )
    with pytest.raises(ValueError):
        AcpProfileInput(
            name="OpenCode", ssh_connection_id="one", workspace_root="/srv/code",
            cwd="../secrets", additional_directories=[],
        )


def test_credentials_are_private_and_removed_after_use():
    """Materialized secrets use 0600 files and never outlive their context."""
    with materialize_ssh_credentials(_connection()) as (identity, known_hosts):
        assert Path(identity).stat().st_mode & 0o777 == 0o600
        assert Path(known_hosts).stat().st_mode & 0o777 == 0o600
        assert Path(identity).read_text() == PRIVATE_KEY + "\n"
        assert "[mac-mini.example.test]:2222" in Path(known_hosts).read_text()
    assert not Path(identity).exists()
    assert not Path(known_hosts).exists()


def test_private_key_copy_paste_whitespace_is_normalized_and_validated():
    """Accept BOMs, Windows line endings, and extra clipboard whitespace."""
    pasted = f"\n\ufeff{PRIVATE_KEY.replace(chr(10), chr(13) + chr(10))}\r\n\r\n"

    assert normalize_ssh_private_key(pasted) == PRIVATE_KEY
    assert validate_ssh_private_key(pasted) == PRIVATE_KEY


def test_private_key_validation_rejects_encrypted_and_malformed_material():
    """Return stable codes for the two key errors users can correct."""
    encrypted = (
        ed25519.Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.BestAvailableEncryption(b"test-passphrase"),
        )
        .decode("utf-8")
    )

    with pytest.raises(SshPrivateKeyError, match="ssh_private_key_encrypted"):
        validate_ssh_private_key(encrypted)
    with pytest.raises(SshPrivateKeyError, match="ssh_private_key_invalid"):
        validate_ssh_private_key(
            "-----BEGIN OPENSSH PRIVATE KEY-----\ninvalid\n-----END OPENSSH PRIVATE KEY-----"
        )


def test_private_key_validation_rejects_unsupported_algorithms(monkeypatch):
    """Unsupported key algorithms use the same safe code as malformed keys."""
    def reject_unsupported_algorithm(*_args, **_kwargs):
        raise UnsupportedAlgorithm("unsupported key algorithm")

    monkeypatch.setattr(ssh_transport, "load_ssh_private_key", reject_unsupported_algorithm)

    with pytest.raises(SshPrivateKeyError, match="ssh_private_key_invalid"):
        validate_ssh_private_key(PRIVATE_KEY)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConnectionError("Permission denied (publickey)."), "ssh_authentication_failed"),
        (ConnectionError("Host key verification failed."), "ssh_host_key_verification_failed"),
        (ConnectionError("Connection refused"), "ssh_connection_unreachable"),
        (subprocess.TimeoutExpired(["ssh"], 5), "ssh_connection_timeout"),
        (ConnectionError("Unexpected SSH failure"), "ssh_connection_failed"),
    ],
)
def test_connection_errors_are_classified_without_returning_raw_stderr(error, expected):
    """Give the browser actionable codes instead of sensitive process output."""
    assert classify_ssh_connection_error(error) == expected


def test_ssh_arguments_disable_interactive_auth_and_forwarding():
    """Saved connections cannot prompt for passwords or open SSH forwards."""
    arguments = build_ssh_arguments(_connection(), "/tmp/key", "/tmp/hosts")
    rendered = " ".join(arguments)
    assert "BatchMode=yes" in rendered
    assert "PasswordAuthentication=no" in rendered
    assert "ClearAllForwardings=yes" in rendered
    assert arguments[-1] == "omlorix@mac-mini.example.test"
    assert quote_remote_command(["printf", "%s", "hello world"]) == "printf %s 'hello world'"

    terminal_arguments = build_ssh_arguments(
        _connection(),
        "/tmp/key",
        "/tmp/hosts",
        allocate_tty=True,
    )
    assert "-tt" in terminal_arguments
    assert "-T" not in terminal_arguments

    pinned_arguments = build_ssh_arguments(
        _connection(),
        "/tmp/key",
        "/tmp/hosts",
        destination_host="192.0.2.10",
    )
    assert "HostKeyAlias=[mac-mini.example.test]:2222" in pinned_arguments
    assert pinned_arguments[-1] == "omlorix@192.0.2.10"

    default_port_connection = _connection()
    default_port_connection.port = 22
    default_port_arguments = build_ssh_arguments(
        default_port_connection,
        "/tmp/key",
        "/tmp/hosts",
    )
    assert "HostKeyAlias=mac-mini.example.test" in default_port_arguments


def test_acp_remote_command_loads_login_path_without_interpolating_arguments():
    """Find user-installed runtimes while keeping profile values as shell data."""
    remote_command = build_acp_remote_command(
        [
            "npx",
            "-y",
            "@agentclientprotocol/codex-acp",
            "argument; touch /tmp/not-created",
        ]
    )

    # Parsing the final SSH command must recover a fixed shell program and
    # fixed source. Metacharacters from profile arguments remain one inert
    # positional value consumed by `exec "$@"`.
    assert shlex.split(remote_command) == [
        "/bin/sh",
        "-lc",
        'exec "$@"',
        "omlorix-acp",
        "npx",
        "-y",
        "@agentclientprotocol/codex-acp",
        "argument; touch /tmp/not-created",
    ]
    with pytest.raises(ValueError, match="Remote ACP command is required"):
        build_acp_remote_command([])


def test_run_ssh_command_terminates_options_before_destination(monkeypatch):
    """Protect the destination and remote command from local option parsing."""
    recorded_argv = []

    def record_subprocess(argv, **_kwargs):
        """Capture the final OpenSSH argv without contacting a real SSH server."""
        recorded_argv.extend(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    # The transport deliberately connects to a resolved numeric address while
    # retaining the configured hostname as its host-key alias.
    monkeypatch.setattr(
        ssh_transport,
        "validate_ssh_destination",
        lambda _host, _port: ["192.0.2.10"],
    )
    monkeypatch.setattr(ssh_transport.subprocess, "run", record_subprocess)

    # A dash-prefixed executable proves the terminator remains active for the
    # remote command instead of allowing OpenSSH to interpret ``-V`` locally.
    remote_arguments = ["-V"]
    ssh_transport.run_ssh_command(_connection(), remote_arguments)

    assert recorded_argv[-3] == "--"
    assert recorded_argv[-2] == "omlorix@192.0.2.10"
    assert recorded_argv[-1] == quote_remote_command(remote_arguments)


def test_terminal_starts_in_profile_workspace_without_shell_injection():
    """Resolve the ACP cwd as data and quote it separately from shell source."""
    assert resolve_terminal_cwd("/srv/source trees", "omlorix") == "/srv/source trees/omlorix"
    command = build_terminal_remote_command("/srv/source trees", "omlorix; touch /tmp/nope")
    assert "'/srv/source trees/omlorix; touch /tmp/nope'" in command
    assert 'cd -- "$1"' in command
    with pytest.raises(ValueError):
        resolve_terminal_cwd("/srv/source", "../secrets")
    assert clamp_terminal_size(1, 999) == (20, 200)


def test_destination_policy_allows_private_devices_but_blocks_loopback(monkeypatch):
    """Home/Tailscale addresses work while the Omlorix server loopback does not."""
    monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: [(2, 1, 6, "", ("192.168.1.20", 22))])
    assert validate_ssh_destination("mac-mini.home", 22) == ["192.168.1.20"]
    monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 22))])
    with pytest.raises(ConnectionError):
        validate_ssh_destination("localhost", 22)
