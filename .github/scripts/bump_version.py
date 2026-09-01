"""Bump Omlorix application or server launcher release version files."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(beta)\.([1-9]\d*))?$"
)


@dataclass(frozen=True)
class VersionFile:
    path: str
    label: str


APP_VERSION_FILES = (
    VersionFile("backend/app/version.py", "backend runtime version"),
    VersionFile(".env.example", "release image version"),
)

LAUNCHER_VERSION_FILES = (
    VersionFile("package.json", "desktop package version"),
    VersionFile("package-lock.json", "desktop lockfile version"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bump", choices=("patch", "minor", "major"))
    parser.add_argument(
        "--channel",
        choices=("stable", "beta"),
        default="stable",
        help="Release channel to prepare. Beta creates/increments beta prereleases.",
    )
    parser.add_argument(
        "--scope",
        choices=("app", "launcher", "all"),
        default="app",
        help=(
            "Version file group to bump. App releases intentionally do not bump "
            "the desktop launcher package version."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--base-tag-prefix",
        default="",
        help=(
            "Use the newest matching Git tag as an additional version baseline. "
            "For example, app releases pass 'v' so a release remains monotonic "
            "when the release tag exists but main has not yet synchronized its "
            "version files."
        ),
    )
    return parser.parse_args()


def parse_semver(value: str) -> tuple[int, int, int, str, int]:
    match = SEMVER_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Unsupported version '{value}'. Expected semver like 1.2.3 or 1.2.3-beta.1.")
    major, minor, patch, prerelease_label, prerelease_number = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        prerelease_label or "",
        int(prerelease_number or "0"),
    )


def bump_core(major: int, minor: int, patch: int, bump: str) -> tuple[int, int, int]:
    if bump == "patch":
        patch += 1
    elif bump == "minor":
        minor += 1
        patch = 0
    else:
        major += 1
        minor = 0
        patch = 0
    return major, minor, patch


def bump_semver(current: str, bump: str, channel: str) -> str:
    major, minor, patch, prerelease_label, prerelease_number = parse_semver(current)
    if channel == "stable":
        if prerelease_label:
            return f"{major}.{minor}.{patch}"
        major, minor, patch = bump_core(major, minor, patch, bump)
        return f"{major}.{minor}.{patch}"

    if prerelease_label == "beta":
        return f"{major}.{minor}.{patch}-beta.{prerelease_number + 1}"

    major, minor, patch = bump_core(major, minor, patch, bump)
    return f"{major}.{minor}.{patch}-beta.1"


def semver_sort_key(value: str) -> tuple[int, int, int, int]:
    """Return a comparison key where stable follows betas of the same core."""
    major, minor, patch, prerelease_label, prerelease_number = parse_semver(value)
    prerelease_rank = prerelease_number if prerelease_label else sys.maxsize
    return major, minor, patch, prerelease_rank


def latest_tagged_version(root: Path, prefix: str) -> str | None:
    """Return the newest valid semantic version found below a Git tag prefix."""
    if not prefix:
        return None

    result = subprocess.run(
        ["git", "tag", "--list", f"{prefix}*"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    versions: list[str] = []
    for tag in result.stdout.splitlines():
        if not tag.startswith(prefix):
            continue
        candidate = tag.removeprefix(prefix)
        if SEMVER_RE.fullmatch(candidate):
            versions.append(candidate)

    if not versions:
        return None
    return max(versions, key=semver_sort_key)


def resolve_bump_base(current: str, tagged: str | None) -> str:
    """Choose the newest known version without allowing stale files to regress."""
    if tagged is None:
        return current
    return max((current, tagged), key=semver_sort_key)


def read_package_version(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    value = payload.get("version")
    if not isinstance(value, str):
        raise ValueError(f"{path} is missing a string 'version' field.")
    return value


def write_package_version(path: Path, version: str) -> None:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["version"] = version
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def read_package_lock_version(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    value = payload.get("version")
    package_value = ((payload.get("packages") or {}).get("") or {}).get("version")
    if not isinstance(value, str):
        raise ValueError(f"{path} is missing a string top-level 'version' field.")
    if not isinstance(package_value, str):
        raise ValueError(f"{path} is missing packages[''].version.")
    if value != package_value:
        raise ValueError(
            f"{path} has mismatched versions: top-level={value!r}, packages['']={package_value!r}."
        )
    return value


def write_package_lock_version(path: Path, version: str) -> None:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    packages = payload.get("packages")
    if not isinstance(packages, dict) or not isinstance(packages.get(""), dict):
        raise ValueError(f"{path} is missing packages[''].")
    payload["version"] = version
    packages[""]["version"] = version
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def read_version_py(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION = "([^"]+)"$', content, re.MULTILINE)
    if not match:
        raise ValueError(f"{path} is missing APP_VERSION.")
    return match.group(1)


def write_version_py(path: Path, version: str) -> None:
    content = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^APP_VERSION = "[^"]+"$',
        f'APP_VERSION = "{version}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(f"Failed to update APP_VERSION in {path}.")
    path.write_text(updated, encoding="utf-8")


def read_env_example_version(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    match = re.search(r'^OMLORIX_VERSION="?([^"\n]+)"?$', content, re.MULTILINE)
    if not match:
        raise ValueError(f"{path} is missing OMLORIX_VERSION.")
    return match.group(1)


def write_env_example_version(path: Path, version: str) -> None:
    content = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^OMLORIX_VERSION="?[^"\n]+"?$',
        f"OMLORIX_VERSION={version}",
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(f"Failed to update OMLORIX_VERSION in {path}.")
    path.write_text(updated, encoding="utf-8")


READERS = {
    "package.json": read_package_version,
    "package-lock.json": read_package_lock_version,
    "backend/app/version.py": read_version_py,
    ".env.example": read_env_example_version,
}

WRITERS = {
    "package.json": write_package_version,
    "package-lock.json": write_package_lock_version,
    "backend/app/version.py": write_version_py,
    ".env.example": write_env_example_version,
}


def resolve_root(root: Path) -> Path:
    resolved = root.resolve()
    missing = [item.path for item in APP_VERSION_FILES if not (resolved / item.path).exists()]
    if missing:
        missing_list = ", ".join(missing)
        raise FileNotFoundError(f"Repository root '{resolved}' is missing expected files: {missing_list}")
    return resolved


def list_version_files(root: Path, scope: str) -> tuple[VersionFile, ...]:
    """Return the version files for the requested independently released scope."""
    if scope == "app":
        return APP_VERSION_FILES
    if scope == "launcher":
        files = tuple(item for item in LAUNCHER_VERSION_FILES if (root / item.path).exists())
        missing = [item.path for item in LAUNCHER_VERSION_FILES if not (root / item.path).exists()]
        if missing:
            missing_list = ", ".join(missing)
            raise FileNotFoundError(f"Repository root '{root}' is missing launcher version files: {missing_list}")
        return files
    return (*APP_VERSION_FILES, *LAUNCHER_VERSION_FILES)


def read_versions(root: Path, version_files: tuple[VersionFile, ...]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for item in version_files:
        version = READERS[item.path](root / item.path)
        parse_semver(version)
        versions[item.path] = version
    return versions


def ensure_synced(versions: dict[str, str]) -> str:
    unique_versions = sorted(set(versions.values()))
    if len(unique_versions) != 1:
        details = "\n".join(f"- {path}: {version}" for path, version in versions.items())
        raise ValueError(f"Version files are out of sync:\n{details}")
    return unique_versions[0]


def write_versions(root: Path, version_files: tuple[VersionFile, ...], version: str) -> None:
    for item in version_files:
        WRITERS[item.path](root / item.path, version)


def main() -> int:
    args = parse_args()
    try:
        root = resolve_root(args.root)
        version_files = list_version_files(root, args.scope)
        current_versions = read_versions(root, version_files)
        current_version = ensure_synced(current_versions)
        tagged_version = latest_tagged_version(root, args.base_tag_prefix)
        base_version = resolve_bump_base(current_version, tagged_version)
        next_version = bump_semver(base_version, args.bump, args.channel)
        write_versions(root, version_files, next_version)
    except Exception as exc:  # pragma: no cover - exercised via subprocess test
        print(str(exc), file=sys.stderr)
        return 1

    print(f"base_version={base_version}")
    print(f"version={next_version}")
    print(f"tag=v{next_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
