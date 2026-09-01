from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-beta\.[1-9]\d*)?$")
LOCAL_BUILD_IMAGE_RE = re.compile(r"^omlorix-(backend|frontend):\$\{OMLORIX_VERSION:-local\}$")
RELEASE_IMAGE_RE = re.compile(
    r"^ghcr\.io/phinaldoo/omlorix-(backend|frontend)"
    r":\$\{OMLORIX_VERSION:\?OMLORIX_VERSION is required\}$"
)
IMAGE_RE = re.compile(r"^\s*image:\s*(?P<image>\S+)\s*$")


def _read_env_value(path: Path, key: str) -> str:
    """Return a single env value from a checked-in example env file."""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        env_key, value = line.split("=", 1)
        if env_key == key:
            return value.strip().strip('"').strip("'")
    raise AssertionError(f"{key} missing from {path}")


def _read_package_version(path: Path) -> str:
    """Read the package version field without depending on npm tooling."""
    version = json.loads(path.read_text(encoding="utf-8"))["version"]
    assert isinstance(version, str)
    return version


def test_release_image_version_is_pinned_to_app_release_version():
    env_version = _read_env_value(REPO_ROOT / ".env.example", "OMLORIX_VERSION")
    backend_version_source = (REPO_ROOT / "backend/app/version.py").read_text(encoding="utf-8")
    backend_version_match = re.search(r'^APP_VERSION = "([^"]+)"$', backend_version_source, re.MULTILINE)

    assert backend_version_match is not None
    assert SEMVER_RE.fullmatch(env_version)
    assert env_version == backend_version_match.group(1)


def test_launcher_package_version_is_pinned_to_lockfile_version():
    # App releases and launcher releases are versioned independently. The
    # launcher package files still need to agree with each other so release
    # jobs do not publish a package from a stale lockfile.
    package_version = _read_package_version(REPO_ROOT / "package.json")
    lockfile_payload = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))

    assert SEMVER_RE.fullmatch(package_version)
    assert package_version == lockfile_payload["version"]
    assert package_version == lockfile_payload["packages"][""]["version"]


def test_compose_image_references_are_pinned_to_official_repositories():
    compose_files = sorted(REPO_ROOT.glob("docker-compose*.yml"))

    assert compose_files
    for compose_file in compose_files:
        for line in compose_file.read_text(encoding="utf-8").splitlines():
            match = IMAGE_RE.match(line)
            if match is None:
                continue

            image = match.group("image")
            assert (
                "@sha256:" in image
                or LOCAL_BUILD_IMAGE_RE.fullmatch(image)
                or RELEASE_IMAGE_RE.fullmatch(image)
            ), f"{compose_file.name} uses a mutable image reference: {image}"


def test_release_repository_overrides_are_not_exposed_as_environment_configuration():
    """Official release images must not be redirectable through dotenv values."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    release_compose = "\n".join(
        (REPO_ROOT / name).read_text(encoding="utf-8")
        for name in ("docker-compose.server.yml", "docker-compose.managed-cloud.yml")
    )

    assert "OMLORIX_BACKEND_IMAGE_REPOSITORY" not in example
    assert "OMLORIX_FRONTEND_IMAGE_REPOSITORY" not in example
    assert "OMLORIX_BACKEND_IMAGE_REPOSITORY" not in release_compose
    assert "OMLORIX_FRONTEND_IMAGE_REPOSITORY" not in release_compose


def test_host_proxy_management_settings_are_not_exposed_in_example_env():
    """Launcher/CLI host state must not leak into source-checkout dotenv config."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    host_proxy_keys = (
        "ENABLED",
        "AUTOSTART",
        "BIND",
        "PUBLIC_HOSTNAME",
        "HTTP_PORT",
        "HTTPS_ENABLED",
        "HTTPS_PORT",
        "REDIRECT_HTTP_TO_HTTPS",
        "TLS_CERT_PATH",
        "TLS_KEY_PATH",
        "TLS_CA_PATH",
        "TLS_KEY_PASSPHRASE",
    )

    for suffix in host_proxy_keys:
        assert f"OMLORIX_LAUNCHER_PROXY_{suffix}" not in example
    # This credential crosses the host/container boundary and is intentionally
    # the one launcher-proxy value that remains in Compose configuration.
    assert "OMLORIX_LAUNCHER_PROXY_SECRET" in example
