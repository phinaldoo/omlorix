"""Build-time cache busting for static frontend assets.

Run from the repository root:

    python script/cache_buster.py --frontend-dir frontend --output-dir frontend_dist

The script copies everything from *frontend-dir* into the *output-dir*, bundles
ordered split development assets, then locates every HTML document in that
build directory, computes SHA256 hashes for all local CSS/JS assets, renames
those assets to include a short content hash
(e.g., ``app.3f92ab1c.js``), and rewrites matching `<link rel="stylesheet">`
and `<script src>` tags to reference the new filenames.

Because only the build directory is mutated, the tracked source files remain
unchanged, enabling immutable deployments and reproducible builds. Absolute
references (e.g., `http(s)://`, protocol-relative URLs, `/api`, or `data:`)
are ignored.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
from html.parser import HTMLParser
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import traceback
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, NamedTuple, Tuple

STALE_DIR_TTL_SECONDS = 24 * 60 * 60  # 1 day
WATCH_DEFAULT_INTERVAL_SECONDS = 1.0
BUILD_MARKER_FILENAME = ".cache_buster_frontend_ready"
BUILD_MARKER_META_NAME = "omlorix-build-id"
FILENAME_HASH_LENGTH = 8
# Runtime wrappers load these assets by fixed URLs after the page has loaded.
# Keep them unhashed for the same reason Prism autoloader components are skipped:
# the cache-buster only rewrites static HTML references, not URLs embedded inside
# browser JavaScript. This includes CSS fetched as text for isolated iframe
# documents as well as lazily loaded vendor scripts.
STATIC_HASH_EXCLUDES = {
    "css/chat/visualization-runtime.css",
    "js/vendor/d3.min.js",
    "js/vendor/lucide.min.js",
    "js/vendor/mermaid.min.js",
    "js/vendor/topojson-client.min.js",
    "js/vendor/vega.min.js",
    "js/vendor/vega-lite.min.js",
    "js/vendor/vega-embed.min.js",
    "js/vendor/html2canvas.min.js",
}


class StaticAssetBundle(NamedTuple):
    """A production bundle assembled from ordered development source files."""

    output: str
    sources: Tuple[str, ...]
    html_files: Tuple[str, ...]
    asset_type: str


SPLIT_ASSET_BUNDLES = (
    StaticAssetBundle(
        output="js/admin/helper.js",
        sources=(
            "js/admin/helper/core.js",
            "js/admin/helper/generatedMarkup.js",
            "js/admin/helper/schemaMetadata.js",
            "js/admin/helper/uiState.js",
            "js/admin/helper/fieldLayout.js",
            "js/admin/helper/selectControls.js",
            "js/admin/helper/fieldControls.js",
            "js/admin/helper/api.js",
            "js/admin/helper/settingsController.js",
            "js/admin/helper/fieldValidation.js",
            "js/admin/helper.js",
        ),
        html_files=("admin.html", "index.html", "server_setup.html"),
        asset_type="script",
    ),
    StaticAssetBundle(
        output="js/chat/chatBox.js",
        sources=(
            "js/chat/chatBox/references-and-files.js",
            "js/chat/chatBox/meeting-transcript.js",
            "js/chat/chatBox/composer-controls.js",
            "js/chat/chatBox/attachments-and-generation.js",
            "js/chat/chatBox/event-handlers.js",
            "js/chat/chatBox/mentions.js",
            "js/chat/chatBox.js",
        ),
        html_files=("index.html",),
        asset_type="script",
    ),
    StaticAssetBundle(
        output="js/chat/notes.js",
        sources=(
            "js/chat/notes/state.js",
            "js/chat/notes/api.js",
            "js/chat/notes/dom.js",
            "js/chat/notes/render.js",
            "js/chat/notes/manager.js",
            "js/chat/notes/manager-lifecycle.js",
            "js/chat/notes/manager-history.js",
            "js/chat/notes/sidebar.js",
            "js/chat/notes.js",
        ),
        html_files=("index.html",),
        asset_type="script",
    ),
    StaticAssetBundle(
        output="js/chat/canvas-widget.js",
        sources=(
            "js/chat/canvas-widget/header.js",
            "js/chat/canvas-widget/arguments.js",
            "js/chat/canvas-widget/csv.js",
            "js/chat/canvas-widget/status.js",
            "js/chat/canvas-widget/reference-selection.js",
            "js/chat/canvas-widget/sharing.js",
            "js/chat/canvas-widget/editor-persistence.js",
            "js/chat/canvas-widget/file-loading.js",
            "js/chat/canvas-widget/html-documents.js",
            "js/chat/canvas-widget/pdf-preview.js",
            "js/chat/canvas-widget/rendering.js",
            "js/chat/canvas-widget/lifecycle.js",
            "js/chat/canvas-widget.js",
        ),
        html_files=("index.html",),
        asset_type="script",
    ),
    StaticAssetBundle(
        output="js/chat/splitScreen.js",
        sources=(
            "js/chat/splitScreen/core.js",
            "js/chat/splitScreen/lifecycle.js",
            "js/chat/splitScreen/streaming.js",
            "js/chat/splitScreen/controls.js",
            "js/chat/splitScreen/routing.js",
            "js/chat/splitScreen.js",
        ),
        html_files=("index.html",),
        asset_type="script",
    ),
    StaticAssetBundle(
        output="css/admin/style.css",
        sources=(
            "css/admin/style/base-and-components.css",
            "css/admin/style/stats-and-controls.css",
            "css/admin/style/provider-user-and-icon-management.css",
            "css/admin/style/groups-forms-and-responsive-tables.css",
            "css/admin/style/model-actions-and-local-models.css",
            "css/admin/style/access-rules-and-mobile-layout.css",
        ),
        html_files=("admin.html",),
        asset_type="stylesheet",
    ),
)


def get_file_hash(filepath: Path) -> str:
    """Return the SHA256 hash for the file at *filepath*."""

    hasher = hashlib.sha256()
    with filepath.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def collect_static_files(frontend_dir: Path) -> Dict[Path, str]:
    """Collect hashes for every CSS/JS file below *frontend_dir*."""

    file_hashes: Dict[Path, str] = {}
    root = frontend_dir.resolve()
    for path in root.rglob("*"):
        if not (path.is_file() and path.suffix in {".css", ".js"}):
            continue

        relative_posix = path.resolve().relative_to(root).as_posix()
        if relative_posix.startswith("js/vendor/prism/components/"):
            continue
        if relative_posix in STATIC_HASH_EXCLUDES:
            continue

        file_hashes[path.resolve()] = get_file_hash(path)
    return file_hashes


def collect_html_files(frontend_dir: Path) -> Iterable[Path]:
    return (path for path in frontend_dir.rglob("*.html") if path.is_file())


def _should_skip_asset(url: str) -> bool:
    lowered = url.lower()
    return lowered.startswith(("http://", "https://", "//", "/api")) or lowered.startswith("data:")


class _StaticAssetReferenceScanner(HTMLParser):
    """Collect HTML references to files that must be served from the frontend tree."""

    # ``data-src`` is included because setup pages intentionally lazy-load
    # their illustrations from this attribute after the page is initialized.
    STATIC_REFERENCE_ATTRIBUTES = {"src", "href", "data-src"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.asset_refs: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        self._record_asset_references(attrs)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        self._record_asset_references(attrs)

    def _record_asset_references(self, attrs: List[Tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name and name.lower() in self.STATIC_REFERENCE_ATTRIBUTES and value:
                self.asset_refs.append(value)


def _resolve_case_sensitive_asset_path(frontend_dir: Path, asset_ref: str) -> Path | None:
    """Resolve an ``/assets`` URL using exact directory-entry casing.

    ``Path.exists()`` is not sufficient here: on macOS it can report a match
    for ``dark.png`` when the directory entry is actually ``Dark.png``. The
    published Linux image is case-sensitive, so walk each directory and compare
    names exactly to reproduce the runtime lookup during the build.
    """

    clean_ref = asset_ref.split("?", 1)[0].split("#", 1)[0]
    if not clean_ref.startswith("/assets/"):
        return None

    current = frontend_dir.resolve()
    components = PurePosixPath(clean_ref.lstrip("/")).parts
    for component in components:
        if component in {"", ".", ".."}:
            return None

        try:
            exact_entry = next(
                (entry for entry in current.iterdir() if entry.name == component),
                None,
            )
        except OSError:
            return None

        if exact_entry is None:
            return None
        current = exact_entry

    return current if current.is_file() else None


def validate_case_sensitive_static_asset_references(frontend_dir: Path) -> List[str]:
    """Return HTML references whose local asset filename casing will fail on Linux."""

    frontend_dir = frontend_dir.resolve()
    errors: List[str] = []

    for html_path in collect_html_files(frontend_dir):
        scanner = _StaticAssetReferenceScanner()
        scanner.feed(html_path.read_text(encoding="utf-8"))
        scanner.close()

        for asset_ref in scanner.asset_refs:
            if not asset_ref.startswith("/assets/"):
                continue
            if _resolve_case_sensitive_asset_path(frontend_dir, asset_ref) is not None:
                continue

            relative_html = html_path.relative_to(frontend_dir).as_posix()
            errors.append(
                f"{relative_html} references missing or case-mismatched asset {asset_ref}"
            )

    return errors


def _resolve_asset_path(html_path: Path, asset_ref: str, frontend_dir: Path) -> Path | None:
    target = asset_ref.split("?")[0]
    if not target:
        return None

    candidate: Path
    if target.startswith("/"):
        candidate = frontend_dir / target.lstrip("/")
    else:
        candidate = html_path.parent / target

    candidate = candidate.resolve()

    try:
        candidate.relative_to(frontend_dir.resolve())
    except ValueError:
        return None

    return candidate if candidate.exists() else None


def _build_asset_rename_map(file_hashes: Dict[Path, str], hash_length: int = FILENAME_HASH_LENGTH) -> Dict[Path, Path]:
    renames: Dict[Path, Path] = {}
    for path, digest in file_hashes.items():
        hashed_name = f"{path.stem}.{digest[:hash_length]}{path.suffix}"
        renames[path] = path.with_name(hashed_name)
    return renames


def _format_asset_reference(clean_value: str, hashed_path: Path, html_path: Path, frontend_dir: Path) -> str:
    if clean_value.startswith("/"):
        relative = hashed_path.relative_to(frontend_dir).as_posix()
        return f"/{relative}"

    rel_path = os.path.relpath(hashed_path, html_path.parent)
    normalized = Path(rel_path).as_posix()

    if clean_value.startswith("./") and not normalized.startswith(("./", "../")):
        return f"./{normalized}"

    return normalized


def _build_attr_update(
    attr_name: str,
    value: str,
    asset_renames: Dict[Path, Path],
    html_path: Path,
    frontend_dir: Path,
) -> Tuple[str, str, str] | None:
    if not value or _should_skip_asset(value):
        return None

    asset_path = _resolve_asset_path(html_path, value, frontend_dir)
    if not asset_path:
        return None

    hashed_path = asset_renames.get(asset_path)
    if not hashed_path:
        return None

    clean_value = value.split("?")[0]
    new_value = _format_asset_reference(clean_value, hashed_path, html_path, frontend_dir)
    if new_value == value:
        return None
    return attr_name, value, new_value


def _stylesheet_rel_matches(rel_value: str | None) -> bool:
    if not rel_value:
        return False
    tokens = [token for token in re.split(r"\s+", rel_value.strip().lower()) if token]
    return "stylesheet" in tokens


class _AssetReferenceScanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.asset_refs: List[Tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        self._record_asset_reference(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        self._record_asset_reference(tag, attrs)

    def _record_asset_reference(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value for name, value in attrs if name}

        if tag == "link":
            if not _stylesheet_rel_matches(attr_map.get("rel")):
                return
            href = attr_map.get("href")
            if href:
                self.asset_refs.append(("href", href))
            return

        if tag == "script":
            src = attr_map.get("src")
            if src:
                self.asset_refs.append(("src", src))


def _collect_asset_updates(
    html_text: str,
    asset_renames: Dict[Path, Path],
    html_path: Path,
    frontend_dir: Path,
) -> List[Tuple[str, str, str]]:
    scanner = _AssetReferenceScanner()
    scanner.feed(html_text)
    scanner.close()

    updates: List[Tuple[str, str, str]] = []
    for attr_name, value in scanner.asset_refs:
        update = _build_attr_update(attr_name, value, asset_renames, html_path, frontend_dir)
        if update:
            updates.append(update)

    return updates


def _replace_attr_value(html_text: str, attr_name: str, old_value: str, new_value: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"({re.escape(attr_name)}\s*=\s*)(?P<quote>['\"]){re.escape(old_value)}(?P=quote)",
        flags=re.IGNORECASE,
    )
    updated_text, count = pattern.subn(rf"\1\g<quote>{new_value}\g<quote>", html_text, count=1)
    return updated_text, bool(count)


def _inject_build_marker_meta(html_text: str, build_marker: str) -> tuple[str, bool]:
    meta_pattern = re.compile(
        rf"""<meta(?=[^>]*\bname\s*=\s*["']{re.escape(BUILD_MARKER_META_NAME)}["'])[^>]*>""",
        flags=re.IGNORECASE,
    )
    replacement = f'<meta name="{BUILD_MARKER_META_NAME}" content="{build_marker}">'

    if meta_pattern.search(html_text):
        updated_text, count = meta_pattern.subn(replacement, html_text, count=1)
        return updated_text, bool(count)

    head_close_pattern = re.compile(r"</head\s*>", flags=re.IGNORECASE)
    updated_text, count = head_close_pattern.subn(f"    {replacement}\n</head>", html_text, count=1)
    if count:
        return updated_text, True

    return html_text, False


def process_html_file(
    html_path: Path,
    asset_renames: Dict[Path, Path],
    frontend_dir: Path,
    build_marker: str,
) -> bool:
    """Rewrite asset references inside a single HTML file."""

    html_text = html_path.read_text(encoding="utf-8")
    updates = _collect_asset_updates(html_text, asset_renames, html_path, frontend_dir)

    changed = False
    for attr_name, old_value, new_value in updates:
        html_text, replaced = _replace_attr_value(html_text, attr_name, old_value, new_value)
        if replaced:
            changed = True

    html_text, marker_changed = _inject_build_marker_meta(html_text, build_marker)
    changed = changed or marker_changed

    if changed:
        html_path.write_text(html_text, encoding="utf-8")

    return changed


def _apply_asset_renames(asset_renames: Dict[Path, Path]) -> None:
    for original, hashed in asset_renames.items():
        if original == hashed:
            continue
        if hashed.exists():
            raise SystemExit(f"Hashed asset already exists and would be overwritten: {hashed}")
        original.rename(hashed)


def _ensure_writable(target: Path) -> None:
    try:
        mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        os.chmod(target, mode)
    except OSError:
        pass


def _remove_with_retries(path: Path, attempts: int = 3) -> bool:
    def _onerror(func, value, exc_info):
        exc = exc_info[1]
        if isinstance(exc, PermissionError):
            _ensure_writable(Path(value))
            func(value)
        else:
            raise exc

    for attempt in range(1, attempts + 1):
        try:
            shutil.rmtree(path, onerror=_onerror)
            return True
        except OSError as exc:
            if exc.errno == errno.ENOTEMPTY:
                time.sleep(0.1 * attempt)
                continue
            print(f"Failed to remove '{path}' via shutil.rmtree ({exc}).")
            break
    return not path.exists()


def _rename_stubborn_dir(path: Path) -> Path | None:
    timestamp = int(time.time())
    for offset in range(5):
        candidate = path.with_name(f"{path.name}.stale.{timestamp + offset}")
        if candidate.exists():
            continue
        try:
            path.rename(candidate)
            return candidate
        except OSError:
            continue
    return None


def _cleanup_old_stale_dirs(path: Path, ttl_seconds: int = STALE_DIR_TTL_SECONDS) -> None:
    parent = path.parent
    if not parent.exists() or ttl_seconds <= 0:
        return

    prefix = f"{path.name}.stale."
    cutoff = time.time() - ttl_seconds

    for candidate in parent.iterdir():
        if not candidate.is_dir() or not candidate.name.startswith(prefix):
            continue

        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue

        if mtime > cutoff:
            continue

        if not _remove_with_retries(candidate):
            print(f"Warning: unable to remove stale directory '{candidate}'.")


def _remove_output_dir(path: Path) -> None:
    if not path.exists():
        return

    if _remove_with_retries(path):
        return

    fallback = _rename_stubborn_dir(path)
    if fallback is None:
        raise SystemExit(f"Unable to remove existing output directory: {path}")

    print(
        f"Warning: '{path}' could not be fully removed. Renamed leftover build directory to '{fallback}'."
    )

    # Best-effort cleanup of the renamed directory without blocking the rebuild if it fails again.
    subprocess.run(["rm", "-rf", str(fallback)], check=False)


def _prepare_build_directory(source_dir: Path, output_dir: Path) -> Path:
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()

    if source_dir == output_dir:
        raise SystemExit("Output directory must differ from frontend directory")

    _remove_output_dir(output_dir)
    _cleanup_old_stale_dirs(output_dir)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, output_dir)
    return output_dir


def _replace_output_dir_atomically(source_dir: Path, output_dir: Path) -> Path:
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()

    if source_dir == output_dir:
        raise SystemExit("Output directory must differ from frontend directory")

    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_dir = parent / f".{output_dir.name}.tmp"

    _remove_output_dir(temp_dir)
    shutil.copytree(source_dir, temp_dir)
    return temp_dir


def _write_build_marker(output_dir: Path, build_marker: str) -> None:
    marker = output_dir / BUILD_MARKER_FILENAME
    marker.write_text(build_marker, encoding="utf-8")


def _bundle_asset_tag(bundle: StaticAssetBundle, source: str) -> str:
    url = f"/{source}"
    if bundle.asset_type == "script":
        return f'<script src="{url}" defer></script>'
    if bundle.asset_type == "stylesheet":
        return f'<link rel="stylesheet" href="{url}">'
    raise ValueError(f"Unsupported bundle asset type: {bundle.asset_type}")


def _collapse_bundle_references(build_path: Path, bundle: StaticAssetBundle) -> bool:
    source_tags = tuple(_bundle_asset_tag(bundle, source) for source in bundle.sources)
    split_tags = source_tags[:-1] if bundle.sources[-1] == bundle.output else source_tags
    replacement_tag = _bundle_asset_tag(bundle, bundle.output)
    referenced = False

    for html_relative in bundle.html_files:
        html_path = build_path / html_relative
        if not html_path.exists():
            continue

        lines = html_path.read_text(encoding="utf-8").splitlines(keepends=True)
        stripped_lines = [line.strip() for line in lines]
        if not any(tag in stripped_lines for tag in split_tags):
            continue

        referenced = True
        try:
            start = stripped_lines.index(source_tags[0])
        except ValueError as exc:
            raise SystemExit(
                f"Split bundle references are incomplete or out of order in {html_relative}: "
                f"{bundle.output}"
            ) from exc

        actual_tags = tuple(stripped_lines[start:start + len(source_tags)])
        if actual_tags != source_tags:
            raise SystemExit(
                f"Split bundle references are incomplete or out of order in {html_relative}: "
                f"{bundle.output}"
            )

        original_line = lines[start]
        indent = original_line[:len(original_line) - len(original_line.lstrip())]
        newline = "\r\n" if original_line.endswith("\r\n") else "\n"
        lines[start:start + len(source_tags)] = [f"{indent}{replacement_tag}{newline}"]
        html_path.write_text("".join(lines), encoding="utf-8")

    return referenced


def bundle_split_assets(
    build_path: Path,
    bundles: Tuple[StaticAssetBundle, ...] = SPLIT_ASSET_BUNDLES,
) -> int:
    """Concatenate split development assets and collapse their production tags."""

    bundled_count = 0
    removable_parents: set[Path] = set()

    for bundle in bundles:
        if not _collapse_bundle_references(build_path, bundle):
            continue

        source_paths = tuple(build_path / source for source in bundle.sources)
        missing = [path for path in source_paths if not path.is_file()]
        if missing:
            missing_list = ", ".join(path.relative_to(build_path).as_posix() for path in missing)
            raise SystemExit(f"Missing split bundle sources for {bundle.output}: {missing_list}")

        contents = [path.read_text(encoding="utf-8") for path in source_paths]
        bundled_source = "".join(
            content if content.endswith("\n") else f"{content}\n"
            for content in contents
        )
        output_path = build_path / bundle.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(bundled_source, encoding="utf-8")

        for source_path in source_paths:
            if source_path == output_path:
                continue
            source_path.unlink()
            removable_parents.add(source_path.parent)

        bundled_count += 1

    for directory in sorted(removable_parents, key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass

    return bundled_count


def _run_cache_buster(frontend_path: Path, output_path: Path) -> None:
    asset_reference_errors = validate_case_sensitive_static_asset_references(frontend_path)
    if asset_reference_errors:
        details = "\n".join(f"- {error}" for error in asset_reference_errors)
        raise SystemExit(
            "Static frontend asset validation failed. "
            "Asset paths must match their filenames exactly:\n"
            f"{details}"
        )

    build_path = _replace_output_dir_atomically(frontend_path, output_path)
    build_marker = _compute_tree_fingerprint(frontend_path)[:12]

    bundled_count = bundle_split_assets(build_path)
    if bundled_count:
        print(f"Bundled {bundled_count} split frontend assets for production.")

    file_hashes = collect_static_files(build_path)
    if not file_hashes:
        print("No CSS/JS assets found; nothing to update.")
        _write_build_marker(build_path, build_marker)
        return

    asset_renames = _build_asset_rename_map(file_hashes)

    html_files = list(collect_html_files(build_path))
    if not html_files:
        print("No HTML files found under frontend directory.")
        _write_build_marker(build_path, build_marker)
        return

    updated_count = 0
    for html_path in html_files:
        if process_html_file(html_path, asset_renames, build_path, build_marker):
            updated_count += 1
            print(f"Processed: {html_path.relative_to(build_path)}")

    _apply_asset_renames(asset_renames)

    print(
        f"\nCache-busting complete! Updated {updated_count}/{len(html_files)} HTML files. "
        f"Build output written to {output_path}."
    )
    _write_build_marker(build_path, build_marker)

    previous_dir = output_path.with_name(f".{output_path.name}.previous")
    _remove_output_dir(previous_dir)

    if output_path.exists():
        output_path.rename(previous_dir)

    build_path.rename(output_path)
    _remove_output_dir(previous_dir)


def _compute_tree_fingerprint(frontend_path: Path) -> str:
    entries: List[str] = []
    for path in frontend_path.rglob("*"):
        if not path.is_file():
            continue
        stat_result = path.stat()
        relative = path.relative_to(frontend_path).as_posix()
        entries.append(f"{relative}:{stat_result.st_mtime_ns}:{stat_result.st_size}")

    digest = hashlib.sha256("\n".join(sorted(entries)).encode("utf-8"))
    return digest.hexdigest()


def _watch_for_changes(frontend_path: Path, output_path: Path, interval: float) -> None:
    interval = interval if interval > 0 else WATCH_DEFAULT_INTERVAL_SECONDS
    last_fingerprint: str | None = None

    print(
        f"[cache_buster] Watching '{frontend_path}' for changes (interval {interval:.2f}s)."
    )

    while True:
        fingerprint = _compute_tree_fingerprint(frontend_path)
        if fingerprint != last_fingerprint:
            if last_fingerprint is None:
                print("[cache_buster] Initial build starting...")
            else:
                print("[cache_buster] Changes detected. Rebuilding...")

            try:
                _run_cache_buster(frontend_path, output_path)
                last_fingerprint = fingerprint
                print("[cache_buster] Build complete.")
            except Exception as exc:  # pragma: no cover - diagnostic path
                print(f"[cache_buster] Build failed: {exc}", file=sys.stderr)
                traceback.print_exc()

        time.sleep(interval)


def main(frontend_dir: str, output_dir: str, watch: bool, watch_interval: float) -> None:
    frontend_path = Path(frontend_dir).resolve()
    if not frontend_path.exists():
        raise SystemExit(f"Frontend directory not found: {frontend_dir}")

    output_path = Path(output_dir).resolve()

    if watch:
        try:
            _watch_for_changes(frontend_path, output_path, watch_interval)
        except KeyboardInterrupt:
            print("\n[cache_buster] Watch mode interrupted. Exiting.")
        return

    _run_cache_buster(frontend_path, output_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Append cache-busting hashes to HTML asset references.")
    parser.add_argument(
        "--frontend-dir",
        default="frontend",
        help="Path to the frontend directory containing HTML/CSS/JS (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        default="frontend_dist",
        help="Directory where processed assets should be written (default: %(default)s)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously watch the frontend directory and rebuild on changes.",
    )
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=WATCH_DEFAULT_INTERVAL_SECONDS,
        help="Polling interval (in seconds) used in --watch mode (default: %(default)s)",
    )
    args = parser.parse_args()
    main(args.frontend_dir, args.output_dir, args.watch, args.watch_interval)
