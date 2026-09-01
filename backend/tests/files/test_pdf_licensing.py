from hashlib import sha256
from importlib.metadata import distribution
from pathlib import Path

from pypdfium2 import version as pdfium_version


FONT_HASHES = {
    "NotoSans-wdth-wght.ttf": "bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d",
    "NotoSans-Italic-wdth-wght.ttf": "58e6e0ebd1931b29a365aa2d3e2ee9a9e831a3af7cf3ad1462d4e72154f0b291",
    "NotoSansArabic-wdth-wght.ttf": "63111b5b2e074dd48cc67692e0a2726d86ee94c1c37fe8598257b7b4e87e869e",
    "NotoSansDevanagari-wdth-wght.ttf": "9ce7b04f60e363d8870e5997744cf85cf69d38a4d7d129d364d92a3b14b461d7",
    "NotoSansSC-wght.ttf": "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da",
}


def _installed_paths(package_name: str) -> list[str]:
    return [str(path).replace("\\", "/") for path in distribution(package_name).files or []]


def test_pdfium_wheel_retains_wrapper_and_binary_license_payloads():
    paths = _installed_paths("pypdfium2")

    assert any(path.endswith("/licenses/LICENSES/Apache-2.0.txt") for path in paths)
    assert any(path.endswith("/licenses/LICENSES/BSD-3-Clause.txt") for path in paths)
    assert any(path.endswith("/licenses/LICENSES/CC-BY-4.0.txt") for path in paths)
    build_licenses = [path for path in paths if "/BUILD_LICENSES/" in path]
    assert any(path.endswith("/BUILD_LICENSES/pdfium.txt") for path in build_licenses)
    assert any(path.endswith("/BUILD_LICENSES/pdfium-binaries.txt") for path in build_licenses)
    assert len(build_licenses) >= 10


def test_pdfium_release_binary_excludes_javascript_and_xfa_engines():
    assert "V8" not in pdfium_version.PDFIUM_INFO.flags
    assert "XFA" not in pdfium_version.PDFIUM_INFO.flags


def test_pdf_export_dependencies_retain_their_license_files():
    assert any(
        path.endswith("/licenses/LICENSE")
        for path in _installed_paths("reportlab")
    )
    assert any(
        path.endswith("/licenses/LICENSE")
        for path in _installed_paths("uharfbuzz")
    )


def test_bundled_harfbuzz_engine_notice_is_retained():
    notice = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "assets"
        / "licenses"
        / "HARFBUZZ-COPYING.txt"
    ).read_text(encoding="utf-8")

    assert 'HarfBuzz is licensed under the so-called "Old MIT" license' in notice
    assert "Permission is hereby granted" in notice


def test_bundled_noto_fonts_match_reviewed_ofl_sources():
    font_dir = Path(__file__).resolve().parents[2] / "app" / "assets" / "fonts"

    assert (font_dir / "OFL.txt").is_file()
    for filename, expected_hash in FONT_HASHES.items():
        assert sha256((font_dir / filename).read_bytes()).hexdigest() == expected_hash
