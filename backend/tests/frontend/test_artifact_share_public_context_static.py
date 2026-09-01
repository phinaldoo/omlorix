from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CANVAS_SHARE_HTML = REPO_ROOT / "frontend" / "canvas_share.html"


class CanvasShareParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: dict[str, dict[str, str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value or "" for name, value in attrs}
        node_id = attr_map.get("id")
        if node_id:
            attr_map["tag"] = tag
            self.nodes[node_id] = attr_map


def test_public_canvas_share_page_exposes_recipient_context_controls():
    parser = CanvasShareParser()
    parser.feed(CANVAS_SHARE_HTML.read_text(encoding="utf-8"))

    assert parser.nodes["canvasContent"]["data-i18n-attr"] == "aria-label:canvas_share_content_aria"
    assert "sharedHeader" not in parser.nodes
    assert "copyCanvasBtn" not in parser.nodes
    assert "downloadCanvasBtn" not in parser.nodes
