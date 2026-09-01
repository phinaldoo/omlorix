from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = (
    REPO_ROOT / "third_party_assets_manifest" / "offline-third-party-assets.manifest.json"
)
SUPPORTED_SCHEMA_VERSION = 2
REQUIRED_ASSET_FIELDS = (
    "id",
    "name",
    "version",
    "purpose",
    "license",
    "source_url",
    "license_url",
    "paths",
    "tree_sha256",
    "served_paths",
    "license_paths",
    "referenced_from",
)


def resolve_repo_relative_path(repo_root: Path, path_value: str) -> Path | None:
    """Resolve a manifest path while preventing absolute paths and traversal."""

    candidate = Path(path_value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None

    resolved_root = repo_root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved


def collect_path_files(path: Path) -> list[Path]:
    """Expand a manifest file or directory into a stable list of files."""

    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
    return []


def compute_tree_sha256(repo_root: Path, files: list[Path]) -> str:
    """Hash every relative path and byte in a component deterministically.

    Including repository-relative paths makes renames visible even when the file
    contents do not change. NUL separators keep path/content boundaries
    unambiguous without imposing any text encoding on binary font assets.
    """

    digest = hashlib.sha256()
    resolved_root = repo_root.resolve()
    for path in sorted(files, key=lambda candidate: candidate.relative_to(resolved_root).as_posix()):
        relative_path = path.resolve().relative_to(resolved_root).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _is_lower_hex_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_within_any(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _load_manifest(manifest_path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"Manifest file does not exist: {manifest_path}"]
    except json.JSONDecodeError as exc:
        return None, [f"Manifest file is not valid JSON: {exc}"]

    if not isinstance(manifest, dict):
        return None, ["Manifest root must be an object."]
    return manifest, []


def validate_manifest(manifest_path: Path, repo_root: Path | None = None) -> list[str]:
    """Validate metadata, hashes, references, and exhaustive asset coverage."""

    repo_root = (repo_root or REPO_ROOT).resolve()
    central_license_root = repo_root / "frontend" / "legal" / "third_party_licenses"
    manifest, errors = _load_manifest(manifest_path)
    if manifest is None:
        return errors

    if manifest.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SUPPORTED_SCHEMA_VERSION}; found {manifest.get('schema_version')!r}."
        )

    inventory_root_values = manifest.get("inventory_roots")
    if not isinstance(inventory_root_values, list) or not inventory_root_values:
        errors.append("Manifest must contain a non-empty 'inventory_roots' list.")
        inventory_root_values = []

    inventory_roots: list[Path] = []
    for index, path_value in enumerate(inventory_root_values):
        if not isinstance(path_value, str) or not path_value.strip():
            errors.append(f"inventory_roots[{index}] must be a non-empty string.")
            continue
        resolved = resolve_repo_relative_path(repo_root, path_value)
        if resolved is None:
            errors.append(f"inventory_roots[{index}] must stay within the repository: {path_value}")
        elif not resolved.is_dir():
            errors.append(f"inventory_roots[{index}] is not a directory: {path_value}")
        else:
            inventory_roots.append(resolved)

    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        errors.append("Manifest must contain a non-empty 'assets' list.")
        return errors

    seen_ids: set[str] = set()
    owners_by_file: dict[Path, list[str]] = defaultdict(list)

    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            errors.append(f"assets[{index}] must be an object.")
            continue

        label = asset.get("id") or f"assets[{index}]"
        for field_name in REQUIRED_ASSET_FIELDS:
            if field_name in {"paths", "served_paths", "license_paths", "referenced_from"}:
                if not isinstance(asset.get(field_name), list):
                    errors.append(f"{label}: '{field_name}' must be a list.")
                continue
            value = asset.get(field_name)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}: '{field_name}' must be a non-empty string.")

        asset_id = asset.get("id")
        if isinstance(asset_id, str) and asset_id in seen_ids:
            errors.append(f"{label}: duplicate asset id '{asset_id}'.")
        elif isinstance(asset_id, str):
            seen_ids.add(asset_id)

        for url_field in ("source_url", "license_url"):
            url_value = asset.get(url_field)
            if isinstance(url_value, str) and not url_value.startswith("https://"):
                errors.append(f"{label}: {url_field} must start with 'https://'.")

        served_paths = asset.get("served_paths")
        if isinstance(served_paths, list):
            if not served_paths:
                errors.append(f"{label}: 'served_paths' must not be empty.")
            for served_index, served_path in enumerate(served_paths):
                if not isinstance(served_path, str) or not served_path.startswith("/"):
                    errors.append(f"{label}: served_paths[{served_index}] must start with '/'.")

        tree_hash = asset.get("tree_sha256")
        if isinstance(tree_hash, str) and not _is_lower_hex_digest(tree_hash):
            errors.append(f"{label}: tree_sha256 must be a 64-character lowercase hex string.")

        component_files: list[Path] = []
        component_paths = asset.get("paths")
        if isinstance(component_paths, list):
            if not component_paths:
                errors.append(f"{label}: 'paths' must not be empty.")
            seen_component_files: set[Path] = set()
            for path_index, path_value in enumerate(component_paths):
                if not isinstance(path_value, str) or not path_value.strip():
                    errors.append(f"{label}: paths[{path_index}] must be a non-empty string.")
                    continue
                resolved = resolve_repo_relative_path(repo_root, path_value)
                if resolved is None:
                    errors.append(f"{label}: path must stay within the repository: {path_value}")
                    continue
                expanded_files = collect_path_files(resolved)
                if not expanded_files:
                    errors.append(f"{label}: path does not contain any files: {path_value}")
                    continue
                for file_path in expanded_files:
                    if inventory_roots and not _is_within_any(file_path, inventory_roots):
                        errors.append(f"{label}: file is outside inventory_roots: {file_path.relative_to(repo_root)}")
                    if file_path in seen_component_files:
                        errors.append(
                            f"{label}: file is covered more than once by its paths: "
                            f"{file_path.relative_to(repo_root)}"
                        )
                        continue
                    seen_component_files.add(file_path)
                    component_files.append(file_path)
                    owners_by_file[file_path].append(str(label))

        if component_files and _is_lower_hex_digest(tree_hash):
            actual_hash = compute_tree_sha256(repo_root, component_files)
            if actual_hash != tree_hash:
                errors.append(
                    f"{label}: tree_sha256 mismatch. Expected {tree_hash}, found {actual_hash}."
                )

        license_paths = asset.get("license_paths")
        if isinstance(license_paths, list):
            if not license_paths:
                errors.append(f"{label}: 'license_paths' must not be empty.")
            for license_index, path_value in enumerate(license_paths):
                if not isinstance(path_value, str) or not path_value.strip():
                    errors.append(f"{label}: license_paths[{license_index}] must be a non-empty string.")
                    continue
                resolved = resolve_repo_relative_path(repo_root, path_value)
                if resolved is None:
                    errors.append(f"{label}: license path must stay within the repository: {path_value}")
                elif not resolved.is_file():
                    errors.append(f"{label}: license file does not exist: {path_value}")
                elif resolved.stat().st_size == 0:
                    errors.append(f"{label}: license file must not be empty: {path_value}")
                elif resolved not in component_files and not _is_within_any(
                    resolved,
                    [central_license_root],
                ):
                    errors.append(
                        f"{label}: license file must be covered by the component's paths "
                        f"or stored under frontend/legal/third_party_licenses: {path_value}"
                    )

        referenced_from = asset.get("referenced_from")
        if isinstance(referenced_from, list):
            if not referenced_from:
                errors.append(f"{label}: 'referenced_from' must not be empty.")
            for reference_index, reference in enumerate(referenced_from):
                if not isinstance(reference, dict):
                    errors.append(f"{label}: referenced_from[{reference_index}] must be an object.")
                    continue
                path_value = reference.get("path")
                contains_value = reference.get("contains")
                if not isinstance(path_value, str) or not path_value.strip():
                    errors.append(f"{label}: referenced_from[{reference_index}].path must be a non-empty string.")
                    continue
                if not isinstance(contains_value, str) or not contains_value.strip():
                    errors.append(f"{label}: referenced_from[{reference_index}].contains must be a non-empty string.")
                    continue
                reference_path = resolve_repo_relative_path(repo_root, path_value)
                if reference_path is None:
                    errors.append(f"{label}: reference path must stay within the repository: {path_value}")
                elif not reference_path.is_file():
                    errors.append(f"{label}: reference file does not exist: {path_value}")
                else:
                    source = reference_path.read_text(encoding="utf-8")
                    if contains_value not in source:
                        errors.append(f"{label}: '{contains_value}' was not found in {path_value}.")

    inventory_files: set[Path] = set()
    for inventory_root in inventory_roots:
        inventory_files.update(collect_path_files(inventory_root))

    for file_path in sorted(inventory_files):
        owners = owners_by_file.get(file_path, [])
        relative_path = file_path.relative_to(repo_root).as_posix()
        if not owners:
            errors.append(f"Unlisted third-party asset: {relative_path}")
        elif len(owners) > 1:
            errors.append(f"Third-party asset is owned by multiple components ({', '.join(owners)}): {relative_path}")

    for file_path, owners in sorted(owners_by_file.items()):
        if inventory_roots and file_path not in inventory_files:
            errors.append(
                f"Component path is not captured by inventory_roots ({', '.join(owners)}): "
                f"{file_path.relative_to(repo_root).as_posix()}"
            )

    return errors


def _print_tree_hashes(manifest_path: Path, repo_root: Path) -> int:
    """Print current component hashes for intentional manifest refreshes."""

    manifest, errors = _load_manifest(manifest_path)
    if manifest is None:
        for error in errors:
            print(error)
        return 1

    assets = manifest.get("assets")
    if not isinstance(assets, list):
        print("Manifest has no assets list.")
        return 1

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        files: list[Path] = []
        for path_value in asset.get("paths", []):
            if not isinstance(path_value, str):
                continue
            resolved = resolve_repo_relative_path(repo_root, path_value)
            if resolved is not None:
                files.extend(collect_path_files(resolved))
        if files:
            print(f"{asset.get('id', '<missing-id>')} {compute_tree_sha256(repo_root, files)} {len(files)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the complete machine-readable inventory for vendored frontend browser assets."
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
        help=f"Path to the manifest file. Defaults to {DEFAULT_MANIFEST_PATH}.",
    )
    parser.add_argument(
        "--print-tree-hashes",
        action="store_true",
        help="Print current component tree hashes and file counts without validating stored hashes.",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    if args.print_tree_hashes:
        return _print_tree_hashes(manifest_path, REPO_ROOT)

    errors = validate_manifest(manifest_path)
    if errors:
        print("Vendored frontend asset verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file_count = 0
    for asset in manifest["assets"]:
        for path_value in asset["paths"]:
            resolved = resolve_repo_relative_path(REPO_ROOT, path_value)
            if resolved is not None:
                file_count += len(collect_path_files(resolved))
    print(
        f"Vendored frontend asset verification passed for {len(manifest['assets'])} components "
        f"and {file_count} files in {manifest_path}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
