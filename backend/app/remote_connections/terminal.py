"""Interactive, browser-backed SSH terminal transport."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
from pathlib import PurePosixPath
import pty
import signal
import struct
import termios
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.remote_connections.ssh import (
    build_ssh_arguments,
    materialize_ssh_credentials,
    quote_remote_command,
    validate_ssh_destination,
)


_MAX_INPUT_BYTES = 64 * 1024
_MIN_COLUMNS = 20
_MAX_COLUMNS = 500
_MIN_ROWS = 5
_MAX_ROWS = 200


def clamp_terminal_size(columns: int, rows: int) -> tuple[int, int]:
    """Bound terminal dimensions before passing them to the operating system."""
    return (
        max(_MIN_COLUMNS, min(int(columns), _MAX_COLUMNS)),
        max(_MIN_ROWS, min(int(rows), _MAX_ROWS)),
    )


def resolve_terminal_cwd(workspace_root: str, cwd: str | None) -> str:
    """Resolve an ACP working directory without permitting workspace escape."""
    root = PurePosixPath(str(workspace_root or "").strip())
    if not root.is_absolute():
        raise ValueError("ACP workspace root must be an absolute POSIX path")

    relative = PurePosixPath(str(cwd or ".").strip() or ".")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("ACP working directory must remain inside its workspace")
    return str(root.joinpath(relative))


def build_terminal_remote_command(workspace_root: str, cwd: str | None) -> str:
    """Build a safely quoted login-shell command rooted in the ACP workspace."""
    destination = resolve_terminal_cwd(workspace_root, cwd)
    # The destination is passed as ``$1`` rather than interpolated into shell
    # source, preserving spaces and metacharacters without permitting injection.
    return quote_remote_command(
        [
            "sh",
            "-lc",
            'cd -- "$1" && exec "${SHELL:-/bin/sh}" -l',
            "omlorix-terminal",
            destination,
        ]
    )


def _set_window_size(file_descriptor: int, columns: int, rows: int) -> tuple[int, int]:
    """Resize the local PTY and return the dimensions actually applied."""
    columns, rows = clamp_terminal_size(columns, rows)
    fcntl.ioctl(
        file_descriptor,
        termios.TIOCSWINSZ,
        struct.pack("HHHH", rows, columns, 0, 0),
    )
    return columns, rows


async def _read_pty(websocket: WebSocket, master_fd: int) -> None:
    """Forward raw PTY bytes to xterm.js without decoding terminal sequences."""
    while True:
        try:
            data = await asyncio.to_thread(os.read, master_fd, 64 * 1024)
        except OSError:
            return
        if not data:
            return
        await websocket.send_bytes(data)


async def _receive_browser_input(
    websocket: WebSocket,
    master_fd: int,
    process: asyncio.subprocess.Process,
) -> None:
    """Apply browser input and resize messages to the active terminal PTY."""
    while True:
        raw_message = await websocket.receive_text()
        if len(raw_message.encode("utf-8")) > _MAX_INPUT_BYTES:
            await websocket.send_json({"type": "error", "code": "input_too_large"})
            continue
        try:
            message: dict[str, Any] = json.loads(raw_message)
        except (json.JSONDecodeError, TypeError):
            continue

        message_type = message.get("type")
        if message_type == "input":
            data = message.get("data")
            if isinstance(data, str) and data:
                encoded = data.encode("utf-8")
                if len(encoded) <= _MAX_INPUT_BYTES:
                    await asyncio.to_thread(os.write, master_fd, encoded)
        elif message_type == "resize":
            _set_window_size(
                master_fd,
                message.get("cols", 80),
                message.get("rows", 24),
            )
            # OpenSSH forwards SIGWINCH-triggered PTY changes to the remote
            # terminal. Signalling the local process makes that propagation
            # immediate across supported Unix hosts.
            if process.returncode is None:
                process.send_signal(signal.SIGWINCH)
        elif message_type == "ping":
            await websocket.send_json({"type": "pong"})


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    """Stop the SSH process group and avoid leaving remote shells behind."""
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGHUP)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def run_ssh_terminal(
    websocket: WebSocket,
    connection,
    *,
    workspace_root: str,
    cwd: str | None,
    columns: int,
    rows: int,
) -> int | None:
    """Run one interactive SSH login shell until either endpoint disconnects."""
    destination = validate_ssh_destination(
        connection.host, int(connection.port or 22)
    )[0]
    remote_command = build_terminal_remote_command(workspace_root, cwd)
    master_fd, slave_fd = pty.openpty()
    process: asyncio.subprocess.Process | None = None
    tasks: list[asyncio.Task] = []

    try:
        columns, rows = _set_window_size(master_fd, columns, rows)
        with materialize_ssh_credentials(connection) as (identity_file, known_hosts_file):
            ssh_arguments = build_ssh_arguments(
                connection,
                identity_file,
                known_hosts_file,
                allocate_tty=True,
                destination_host=destination,
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    "ssh",
                    *ssh_arguments,
                    remote_command,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    start_new_session=True,
                    env={**os.environ, "TERM": "xterm-256color"},
                )
            except FileNotFoundError as exc:
                raise RuntimeError("The Omlorix server does not have the OpenSSH client installed") from exc
            finally:
                os.close(slave_fd)
                slave_fd = -1

            await websocket.send_json(
                {
                    "type": "ready",
                    "title": str(connection.name),
                    "cols": columns,
                    "rows": rows,
                }
            )
            reader = asyncio.create_task(_read_pty(websocket, master_fd))
            receiver = asyncio.create_task(_receive_browser_input(websocket, master_fd, process))
            waiter = asyncio.create_task(process.wait())
            tasks = [reader, receiver, waiter]
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            # If the SSH process exited naturally, expose only its numeric exit
            # status. Terminal output already contains any user-facing detail.
            if waiter in done:
                return_code = waiter.result()
                try:
                    await websocket.send_json({"type": "exit", "code": return_code})
                except (RuntimeError, WebSocketDisconnect):
                    pass
                return return_code
            return process.returncode
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if process is not None:
            await _stop_process(process)
        if slave_fd >= 0:
            os.close(slave_fd)
        try:
            os.close(master_fd)
        except OSError:
            pass
