"""Helpers for validating numeric JSON export format versions."""

from __future__ import annotations

from typing import Any


def matches_export_version(value: Any, expected: int | float) -> bool:
    """Return whether a decoded JSON value identifies ``expected``.

    JSON clients commonly normalize ``1.0`` to ``1`` while parsing and
    serializing a document. Python then decodes those equivalent JSON numbers
    as different runtime types, so version checks must compare their numeric
    value rather than require ``float`` identity. Booleans are excluded
    explicitly because ``bool`` is an ``int`` subclass in Python.
    """

    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and value == expected
    )
