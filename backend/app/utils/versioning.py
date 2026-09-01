"""Helpers for comparing Omlorix release tags."""

from __future__ import annotations

import re
from dataclasses import dataclass


# Omlorix publishes semantic versions, while Git tags and the version API include
# an optional leading ``v``. Build metadata is accepted but intentionally does
# not participate in precedence, as required by the Semantic Versioning spec.
_SEMANTIC_VERSION_PATTERN = re.compile(
    r"^[vV]?"
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@dataclass(frozen=True)
class _SemanticVersion:
    """Parsed semantic-version components used for precedence comparisons."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]


def _parse_semantic_version(value: str) -> _SemanticVersion | None:
    """Parse a release tag, returning ``None`` for malformed versions."""

    if not isinstance(value, str):
        return None

    match = _SEMANTIC_VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        return None

    major, minor, patch, prerelease_value = match.groups()
    prerelease = tuple(prerelease_value.split(".")) if prerelease_value else ()

    # SemVer forbids leading zeroes in numeric prerelease identifiers. Rejecting
    # them avoids assigning an arbitrary precedence to malformed API values.
    if any(
        identifier.isdigit()
        and len(identifier) > 1
        and identifier.startswith("0")
        for identifier in prerelease
    ):
        return None

    return _SemanticVersion(
        major=int(major),
        minor=int(minor),
        patch=int(patch),
        prerelease=prerelease,
    )


def compare_semantic_versions(left: str, right: str) -> int | None:
    """Compare two release tags using SemVer precedence.

    Returns ``1`` when ``left`` is newer, ``-1`` when it is older, ``0`` when
    both versions have equal precedence, and ``None`` if either value is not a
    valid semantic version.
    """

    left_version = _parse_semantic_version(left)
    right_version = _parse_semantic_version(right)
    if left_version is None or right_version is None:
        return None

    left_core = (left_version.major, left_version.minor, left_version.patch)
    right_core = (right_version.major, right_version.minor, right_version.patch)
    if left_core != right_core:
        return 1 if left_core > right_core else -1

    # A stable version has higher precedence than every prerelease with the
    # same major, minor, and patch components.
    if not left_version.prerelease and not right_version.prerelease:
        return 0
    if not left_version.prerelease:
        return 1
    if not right_version.prerelease:
        return -1

    prerelease_length = max(
        len(left_version.prerelease),
        len(right_version.prerelease),
    )
    for index in range(prerelease_length):
        if index >= len(left_version.prerelease):
            return -1
        if index >= len(right_version.prerelease):
            return 1

        left_identifier = left_version.prerelease[index]
        right_identifier = right_version.prerelease[index]
        if left_identifier == right_identifier:
            continue

        left_is_numeric = left_identifier.isdigit()
        right_is_numeric = right_identifier.isdigit()
        if left_is_numeric and right_is_numeric:
            return 1 if int(left_identifier) > int(right_identifier) else -1
        if left_is_numeric:
            return -1
        if right_is_numeric:
            return 1
        return 1 if left_identifier > right_identifier else -1

    return 0


def is_beta_version(value: str) -> bool:
    """Return whether a version belongs to Omlorix's beta release channel.

    The release workflow publishes beta tags as ``vX.Y.Z-beta.N``. Parsing the
    runtime version with the same SemVer rules keeps channel selection tied to
    the immutable release tag instead of requiring a separate setting that can
    drift out of sync.
    """

    parsed = _parse_semantic_version(value)
    return parsed is not None and parsed.prerelease[:1] == ("beta",)
