"""Dependency-free health probe for long-lived worker processes."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time


def _bounded_max_age(prefix: str, default: int) -> int:
    try:
        value = int(os.getenv(f"{prefix}_WORKER_HEALTH_MAX_AGE_SECONDS", str(default)))
    except (TypeError, ValueError):
        return default
    return max(15, min(value, 3600))


def heartbeat_is_fresh(prefix: str, queue: str, default_max_age: int = 120) -> bool:
    """Return whether the worker's atomically written timestamp is recent."""

    env_prefix = prefix.strip().upper().replace("-", "_")
    heartbeat_path = Path(
        os.getenv(
            f"{env_prefix}_WORKER_HEARTBEAT_PATH",
            f"/tmp/omlorix-{queue.strip().lower()}-worker-heartbeat",
        )
    )
    try:
        written_at = float(heartbeat_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    age = time.time() - written_at
    return 0 <= age <= _bounded_max_age(env_prefix, default_max_age)


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    if len(arguments) not in (2, 3):
        print(
            "Usage: python -m app.worker_heartbeat <env-prefix> <queue> [default-max-age]",
            file=sys.stderr,
        )
        return 2
    try:
        default_max_age = int(arguments[2]) if len(arguments) == 3 else 120
    except ValueError:
        return 2
    return 0 if heartbeat_is_fresh(arguments[0], arguments[1], default_max_age) else 1


if __name__ == "__main__":
    raise SystemExit(main())
