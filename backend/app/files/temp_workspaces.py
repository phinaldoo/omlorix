"""Process-held leases for temporary workspaces shared with file maintenance."""

from contextlib import contextmanager
import fcntl
from pathlib import Path
import tempfile


_LEASE_FILE = ".omlorix-workspace.lock"


@contextmanager
def temporary_workspace(*, parent: Path, prefix: str):
    """Keep a workspace alive until exit; process death releases its lease."""
    with tempfile.TemporaryDirectory(prefix=prefix, dir=parent) as directory:
        workspace = Path(directory)
        with (workspace / _LEASE_FILE).open("xb") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            yield workspace


def workspace_is_active(directory: Path) -> bool:
    """Check a lease across worker processes without waiting for its owner."""
    try:
        lease = (directory / _LEASE_FILE).open("rb")
    except FileNotFoundError:
        return False
    with lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
