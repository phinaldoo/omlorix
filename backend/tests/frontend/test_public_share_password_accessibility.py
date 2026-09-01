from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inputs: dict[str, dict[str, str]] = {}
        self.labels: dict[str, dict[str, str]] = {}
        self.nodes: dict[str, dict[str, str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value or "" for name, value in attrs}
        node_id = attr_map.get("id")
        if node_id:
            self.nodes[node_id] = attr_map
        if tag == "input" and node_id:
            self.inputs[node_id] = attr_map
        if tag == "label" and attr_map.get("for"):
            self.labels[attr_map["for"]] = attr_map


def _parse(path: Path) -> FormParser:
    parser = FormParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def test_public_share_password_forms_have_accessible_non_account_password_fields():
    cases = (
        (REPO_ROOT / "frontend" / "chat_share.html", "sharedPasswordInput", "sharedPasswordError"),
        (REPO_ROOT / "frontend" / "canvas_share.html", "passwordInput", "passwordError"),
    )

    for path, input_id, error_id in cases:
        parser = _parse(path)
        password_input = parser.inputs[input_id]

        assert parser.labels[input_id]["for"] == input_id
        assert password_input["autocomplete"] == "off"
        assert password_input["aria-invalid"] == "false"
        assert error_id in password_input["aria-describedby"].split()
        assert parser.nodes[error_id]["role"] == "alert"
        assert parser.nodes[error_id]["aria-live"] == "polite"
