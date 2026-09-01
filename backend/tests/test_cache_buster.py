from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "script" / "cache_buster.py"
MODULE_SPEC = importlib.util.spec_from_file_location("cache_buster", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
cache_buster = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(cache_buster)


class CacheBusterTests:
    def test_repository_static_asset_references_match_case_sensitive_paths(self):
        """Ensure local HTML asset URLs also work in Linux-based frontend images."""
        errors = cache_buster.validate_case_sensitive_static_asset_references(REPO_ROOT / "frontend")

        assert errors == []

    def test_static_asset_validation_does_not_trust_case_insensitive_filesystems(self, tmp_path):
        """Catch a casing mismatch even when the test host filesystem ignores case."""
        frontend_dir = tmp_path / "frontend"
        theme_dir = frontend_dir / "assets" / "theme"
        theme_dir.mkdir(parents=True)
        (theme_dir / "Dark.png").write_bytes(b"not-a-real-png")
        (frontend_dir / "index.html").write_text(
            '<img src="/assets/theme/dark.png" alt="Dark">\n',
            encoding="utf-8",
        )

        errors = cache_buster.validate_case_sensitive_static_asset_references(frontend_dir)

        assert errors == [
            "index.html references missing or case-mismatched asset /assets/theme/dark.png"
        ]

    def test_split_assets_are_bundled_and_html_references_are_collapsed(self, tmp_path):
        frontend_dir = tmp_path / "frontend"
        script_parts = frontend_dir / "js" / "feature"
        style_parts = frontend_dir / "css" / "theme"
        script_parts.mkdir(parents=True)
        style_parts.mkdir(parents=True)

        (script_parts / "state.js").write_text("const state = {};\n", encoding="utf-8")
        (frontend_dir / "js" / "feature.js").write_text(
            "window.feature = state;\n",
            encoding="utf-8",
        )
        (style_parts / "base.css").write_text("body { color: black; }\n", encoding="utf-8")
        (style_parts / "layout.css").write_text("main { display: grid; }\n", encoding="utf-8")
        (frontend_dir / "index.html").write_text(
            """<!DOCTYPE html>
<html>
<head>
    <link rel="stylesheet" href="/css/theme/base.css">
    <link rel="stylesheet" href="/css/theme/layout.css">
    <script src="/js/feature/state.js" defer></script>
    <script src="/js/feature.js" defer></script>
</head>
</html>
""",
            encoding="utf-8",
        )

        bundles = (
            cache_buster.StaticAssetBundle(
                output="js/feature.js",
                sources=("js/feature/state.js", "js/feature.js"),
                html_files=("index.html",),
                asset_type="script",
            ),
            cache_buster.StaticAssetBundle(
                output="css/theme.css",
                sources=("css/theme/base.css", "css/theme/layout.css"),
                html_files=("index.html",),
                asset_type="stylesheet",
            ),
        )

        bundled_count = cache_buster.bundle_split_assets(frontend_dir, bundles)

        assert bundled_count == 2
        assert (frontend_dir / "js" / "feature.js").read_text(encoding="utf-8") == (
            "const state = {};\nwindow.feature = state;\n"
        )
        assert (frontend_dir / "css" / "theme.css").read_text(encoding="utf-8") == (
            "body { color: black; }\nmain { display: grid; }\n"
        )
        assert not script_parts.exists()
        assert not style_parts.exists()
        output_html = (frontend_dir / "index.html").read_text(encoding="utf-8")
        assert output_html.count('<script src="/js/feature.js" defer></script>') == 1
        assert output_html.count('<link rel="stylesheet" href="/css/theme.css">') == 1
        assert "/js/feature/state.js" not in output_html
        assert "/css/theme/base.css" not in output_html

    def test_run_cache_buster_rewrites_local_assets_and_injects_build_marker(self, tmp_path):
        """Test that cache buster rewrites local assets and injects build marker."""
        frontend_dir = tmp_path / "frontend"
        output_dir = tmp_path / "frontend_dist"
        (frontend_dir / "css").mkdir(parents=True)
        (frontend_dir / "js").mkdir(parents=True)

        css_file = frontend_dir / "css" / "app.css"
        js_file = frontend_dir / "js" / "app.js"
        css_file.write_text("body { color: #123456; }\n", encoding="utf-8")
        js_file.write_text("console.log('ready');\n", encoding="utf-8")
        (frontend_dir / "index.html").write_text(
            """<!DOCTYPE html>
<html>
<head>
    <link rel="preload stylesheet" href="/css/app.css?v=1">
    <link rel="icon" href="/css/app.css?v=1">
    <script src="./js/app.js?cache=1" defer></script>
    <script src="https://cdn.example.com/app.js" defer></script>
</head>
</html>
""",
            encoding="utf-8",
        )

        cache_buster._run_cache_buster(frontend_dir, output_dir)

        css_hash = cache_buster.get_file_hash(css_file)[: cache_buster.FILENAME_HASH_LENGTH]
        js_hash = cache_buster.get_file_hash(js_file)[: cache_buster.FILENAME_HASH_LENGTH]
        output_html = (output_dir / "index.html").read_text(encoding="utf-8")

        assert f'/css/app.{css_hash}.css' in output_html
        assert f'./js/app.{js_hash}.js' in output_html
        assert 'href="/css/app.css?v=1"' in output_html
        assert 'src="https://cdn.example.com/app.js"' in output_html
        assert output_html.count(f'name="{cache_buster.BUILD_MARKER_META_NAME}"') == 1
        assert (output_dir / f"css/app.{css_hash}.css").exists()
        assert (output_dir / f"js/app.{js_hash}.js").exists()
        assert (output_dir / cache_buster.BUILD_MARKER_FILENAME).exists()

    def test_run_cache_buster_keeps_lazy_runtime_assets_unhashed(self, tmp_path):
        """Test that every runtime-loaded asset keeps its fixed public URL."""
        frontend_dir = tmp_path / "frontend"
        output_dir = tmp_path / "frontend_dist"
        (frontend_dir / "css" / "chat").mkdir(parents=True)
        (frontend_dir / "js" / "common").mkdir(parents=True)
        (frontend_dir / "js" / "vendor").mkdir(parents=True)

        runtime_file = frontend_dir / "js" / "common" / "mermaidRuntime.js"
        visualization_css_file = (
            frontend_dir / "css" / "chat" / "visualization-runtime.css"
        )
        d3_file = frontend_dir / "js" / "vendor" / "d3.min.js"
        lucide_file = frontend_dir / "js" / "vendor" / "lucide.min.js"
        mermaid_file = frontend_dir / "js" / "vendor" / "mermaid.min.js"
        topojson_file = frontend_dir / "js" / "vendor" / "topojson-client.min.js"
        vega_file = frontend_dir / "js" / "vendor" / "vega.min.js"
        vega_lite_file = frontend_dir / "js" / "vendor" / "vega-lite.min.js"
        vega_embed_file = frontend_dir / "js" / "vendor" / "vega-embed.min.js"
        html2canvas_file = frontend_dir / "js" / "vendor" / "html2canvas.min.js"
        runtime_file.write_text(
            "const MERMAID_SCRIPT_URL = '/js/vendor/mermaid.min.js?v=1';\n"
            "const VEGA_SCRIPT_URL = '/js/vendor/vega.min.js?v=1';\n",
            encoding="utf-8",
        )
        visualization_css_file.write_text(":root { color-scheme: light dark; }\n", encoding="utf-8")
        d3_file.write_text("window.d3 = {};\n", encoding="utf-8")
        lucide_file.write_text("window.lucide = {};\n", encoding="utf-8")
        mermaid_file.write_text("window.mermaid = { initialize() {} };\n", encoding="utf-8")
        topojson_file.write_text("window.topojson = {};\n", encoding="utf-8")
        vega_file.write_text("window.vega = { parse() {} };\n", encoding="utf-8")
        vega_lite_file.write_text("window.vegaLite = { compile() {} };\n", encoding="utf-8")
        vega_embed_file.write_text("window.vegaEmbed = async () => {};\n", encoding="utf-8")
        html2canvas_file.write_text("window.html2canvas = async () => {};\n", encoding="utf-8")
        (frontend_dir / "index.html").write_text(
            """<!DOCTYPE html>
<html>
<head>
    <script src="/js/common/mermaidRuntime.js?v=1" defer></script>
</head>
</html>
""",
            encoding="utf-8",
        )

        cache_buster._run_cache_buster(frontend_dir, output_dir)

        runtime_hash = cache_buster.get_file_hash(runtime_file)[: cache_buster.FILENAME_HASH_LENGTH]
        output_html = (output_dir / "index.html").read_text(encoding="utf-8")
        output_runtime = (output_dir / f"js/common/mermaidRuntime.{runtime_hash}.js").read_text(
            encoding="utf-8"
        )

        assert f"/js/common/mermaidRuntime.{runtime_hash}.js" in output_html
        assert "/js/vendor/mermaid.min.js?v=1" in output_runtime
        assert "/js/vendor/vega.min.js?v=1" in output_runtime

        # Visualization previews fetch these exact URLs from a hashed page
        # bundle, so both the CSS contract and optional script libraries must
        # survive the production build without filename rewriting.
        fixed_runtime_assets = (
            "css/chat/visualization-runtime.css",
            "js/vendor/d3.min.js",
            "js/vendor/lucide.min.js",
            "js/vendor/mermaid.min.js",
            "js/vendor/topojson-client.min.js",
            "js/vendor/vega.min.js",
            "js/vendor/vega-lite.min.js",
            "js/vendor/vega-embed.min.js",
            "js/vendor/html2canvas.min.js",
        )
        for relative_path in fixed_runtime_assets:
            output_path = output_dir / relative_path
            assert output_path.exists(), f"missing fixed runtime asset: {relative_path}"
            assert not list(
                output_path.parent.glob(f"{output_path.stem}.*{output_path.suffix}")
            ), f"runtime asset was unexpectedly hashed: {relative_path}"

    def test_process_html_file_replaces_existing_marker_and_skips_api_assets(self, tmp_path):
        """Test that HTML processing replaces marker and skips API assets."""
        frontend_dir = tmp_path / "frontend"
        html_dir = frontend_dir / "nested"
        (frontend_dir / "css").mkdir(parents=True)
        (html_dir / "js").mkdir(parents=True)

        css_file = frontend_dir / "css" / "page.css"
        js_file = html_dir / "js" / "page.js"
        css_file.write_text("main { display: grid; }\n", encoding="utf-8")
        js_file.write_text("window.pageLoaded = true;\n", encoding="utf-8")

        html_path = html_dir / "page.html"
        html_path.write_text(
            f"""<!DOCTYPE html>
<html>
<head>
    <meta name="{cache_buster.BUILD_MARKER_META_NAME}" content="old-marker">
    <LINK REL="stylesheet" HREF="../css/page.css?v=2">
    <script src="./js/page.js"></script>
    <script src="/api/runtime-config.js"></script>
</head>
</html>
""",
            encoding="utf-8",
        )

        asset_renames = {
            css_file.resolve(): css_file.resolve().with_name(
                f"page.{cache_buster.get_file_hash(css_file)[:cache_buster.FILENAME_HASH_LENGTH]}.css"
            ),
            js_file.resolve(): js_file.resolve().with_name(
                f"page.{cache_buster.get_file_hash(js_file)[:cache_buster.FILENAME_HASH_LENGTH]}.js"
            ),
        }

        changed = cache_buster.process_html_file(
            html_path=html_path,
            asset_renames=asset_renames,
            frontend_dir=frontend_dir,
            build_marker="fresh-marker",
        )

        output_html = html_path.read_text(encoding="utf-8")

        assert changed is True
        assert 'content="fresh-marker"' in output_html
        assert output_html.count(f'name="{cache_buster.BUILD_MARKER_META_NAME}"') == 1
        assert '../css/page.' in output_html
        assert './js/page.' in output_html
        assert 'src="/api/runtime-config.js"' in output_html

    def test_run_cache_buster_replaces_output_directory_atomically(self, tmp_path):
        """Test that rebuilding replaces the output directory with a fresh build."""
        frontend_dir = tmp_path / "frontend"
        output_dir = tmp_path / "frontend_dist"
        (frontend_dir / "css").mkdir(parents=True)
        (frontend_dir / "js").mkdir(parents=True)

        css_file = frontend_dir / "css" / "app.css"
        js_file = frontend_dir / "js" / "app.js"
        css_file.write_text("body { color: #111; }\n", encoding="utf-8")
        js_file.write_text("console.log('v1');\n", encoding="utf-8")
        (frontend_dir / "index.html").write_text(
            """<!DOCTYPE html>
<html>
<head>
    <link rel="stylesheet" href="/css/app.css">
    <script src="/js/app.js" defer></script>
</head>
</html>
""",
            encoding="utf-8",
        )

        cache_buster._run_cache_buster(frontend_dir, output_dir)
        inode_before = output_dir.stat().st_ino

        js_file.write_text("console.log('v2');\n", encoding="utf-8")
        cache_buster._run_cache_buster(frontend_dir, output_dir)

        inode_after = output_dir.stat().st_ino

        assert inode_after != inode_before
        assert output_dir.is_dir()
        output_html = (output_dir / "index.html").read_text(encoding="utf-8")
        assert "app.css" not in output_html
        assert "app.js" not in output_html
