from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    REPO_ROOT / "third_party_assets_manifest" / "offline-third-party-assets.manifest.json"
)
MODULE_PATH = REPO_ROOT / "dev_scripts" / "verify_frontend_vendor_assets.py"
MODULE_SPEC = importlib.util.spec_from_file_location("verify_frontend_vendor_assets", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
vendor_assets = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(vendor_assets)


def _create_example_manifest(tmp_path: Path) -> tuple[Path, dict]:
    """Create the smallest complete version-2 inventory used by focused tests."""

    inventory_root = tmp_path / "frontend" / "vendor"
    asset_path = inventory_root / "example.min.js"
    reference_path = tmp_path / "frontend" / "index.html"
    license_path = (
        tmp_path / "frontend" / "legal" / "third_party_licenses" / "example.txt"
    )
    manifest_path = tmp_path / "manifest.json"

    inventory_root.mkdir(parents=True)
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    license_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text("console.log('ok');\n", encoding="utf-8")
    license_path.write_text("Copyright (c) Example\n\nMIT License\n", encoding="utf-8")
    reference_path.write_text(
        '<script src="/vendor/example.min.js" defer></script>\n',
        encoding="utf-8",
    )

    # The production verifier owns the tree-hash format, so tests calculate
    # fixture hashes through the same public helper instead of duplicating it.
    tree_hash = vendor_assets.compute_tree_sha256(tmp_path, [asset_path])
    manifest = {
        "schema_version": 2,
        "inventory_roots": ["frontend/vendor"],
        "assets": [
            {
                "id": "example",
                "name": "Example",
                "version": "1.2.3",
                "purpose": "Exercises the test inventory.",
                "license": "MIT",
                "source_url": "https://cdn.example.com/example@1.2.3/example.min.js",
                "license_url": "https://example.com/example-license",
                "paths": ["frontend/vendor/example.min.js"],
                "tree_sha256": tree_hash,
                "served_paths": ["/vendor/example.min.js"],
                "license_paths": [
                    "frontend/legal/third_party_licenses/example.txt"
                ],
                "referenced_from": [
                    {
                        "path": "frontend/index.html",
                        "contains": "/vendor/example.min.js",
                    }
                ],
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, manifest


class FrontendVendorAssetVerificationTests:
    def test_current_repository_manifest_is_valid(self):
        """The committed inventory must cover every vendored frontend file."""

        assert vendor_assets.DEFAULT_MANIFEST_PATH == MANIFEST_PATH
        errors = vendor_assets.validate_manifest(MANIFEST_PATH, REPO_ROOT)

        assert errors == []

    def test_validate_manifest_rejects_tree_hash_mismatch(self, tmp_path):
        """Content changes must not silently retain stale provenance metadata."""

        manifest_path, manifest = _create_example_manifest(tmp_path)
        manifest["assets"][0]["tree_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        errors = vendor_assets.validate_manifest(manifest_path, tmp_path)

        assert len(errors) == 1
        assert "tree_sha256 mismatch" in errors[0]

    def test_validate_manifest_rejects_paths_outside_repository(self, tmp_path):
        """Neither component paths nor application references may traverse upward."""

        manifest_path, manifest = _create_example_manifest(tmp_path)
        manifest["assets"][0]["paths"] = ["../escaped.js"]
        manifest["assets"][0]["referenced_from"] = [
            {"path": "../index.html", "contains": "/vendor/example.min.js"}
        ]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        errors = vendor_assets.validate_manifest(manifest_path, tmp_path)

        assert any("path must stay within the repository" in error for error in errors)
        assert any("reference path must stay within the repository" in error for error in errors)

    def test_validate_manifest_rejects_unlisted_inventory_file(self, tmp_path):
        """Adding any file below an inventory root must require manifest coverage."""

        manifest_path, _ = _create_example_manifest(tmp_path)
        unlisted_path = tmp_path / "frontend" / "vendor" / "forgotten.css"
        unlisted_path.write_text(".forgotten {}\n", encoding="utf-8")

        errors = vendor_assets.validate_manifest(manifest_path, tmp_path)

        assert any("Unlisted third-party asset: frontend/vendor/forgotten.css" in error for error in errors)

    def test_validate_manifest_rejects_missing_license_notice(self, tmp_path):
        """Every redistributed browser component must retain a notice file."""

        manifest_path, manifest = _create_example_manifest(tmp_path)
        manifest["assets"][0]["license_paths"] = []
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        errors = vendor_assets.validate_manifest(manifest_path, tmp_path)

        assert any("'license_paths' must not be empty" in error for error in errors)

    def test_validate_manifest_rejects_multiple_component_owners(self, tmp_path):
        """One vendored file must have exactly one provenance owner."""

        manifest_path, manifest = _create_example_manifest(tmp_path)
        second_asset = deepcopy(manifest["assets"][0])
        second_asset["id"] = "example-copy"
        second_asset["name"] = "Example Copy"
        manifest["assets"].append(second_asset)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        errors = vendor_assets.validate_manifest(manifest_path, tmp_path)

        assert any("owned by multiple components (example, example-copy)" in error for error in errors)
