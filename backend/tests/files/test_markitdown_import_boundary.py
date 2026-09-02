from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


UTILS_PATH = Path(__file__).resolve().parents[2] / "app" / "files" / "utils.py"


def test_markitdown_is_imported_only_inside_document_conversion():
    module = ast.parse(UTILS_PATH.read_text(encoding="utf-8"))

    top_level_imports = [
        node
        for node in module.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert all(
        not (
            isinstance(node, ast.ImportFrom) and node.module == "markitdown"
        )
        and not (
            isinstance(node, ast.Import)
            and any(alias.name == "markitdown" for alias in node.names)
        )
        for node in top_level_imports
    )

    conversion = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_extract_text_from_path_inline"
    )
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "markitdown"
        for node in ast.walk(conversion)
    )


def test_document_conversion_loads_markitdown_on_demand(monkeypatch, tmp_path):
    from app.files import utils

    source = tmp_path / "document.pdf"
    source.write_bytes(b"placeholder")
    fake_markitdown = ModuleType("markitdown")

    class FakeConverter:
        def __init__(self, *, enable_plugins):
            assert enable_plugins is True

        def convert(self, path):
            assert path == str(source)
            return SimpleNamespace(text_content="converted on demand")

    fake_markitdown.MarkItDown = FakeConverter
    monkeypatch.setitem(sys.modules, "markitdown", fake_markitdown)

    assert utils._extract_text_from_path_inline(
        {"path": str(source), "file_type": "application/pdf"}
    ) == "converted on demand"
