import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.utils import cache_headers, icon_security, pagination


def test_pagination_helpers_report_extra_item_without_returning_it():
    items = ["a", "b", "c", "d"]

    assert pagination.merged_window_limit(limit=2, offset=1) == 4
    assert pagination.page_from_merged_window(items, limit=2, offset=1) == (["b", "c"], True)
    assert pagination.page_from_limited_items(items[:3], limit=2) == (["a", "b"], True)
    assert pagination.page_from_limited_items(items[:2], limit=2) == (["a", "b"], False)


def test_apply_no_store_headers_mutates_and_returns_response():
    response = SimpleNamespace(headers={})

    returned = cache_headers.apply_no_store_headers(response)

    assert returned is response
    assert response.headers == cache_headers.NO_STORE_HEADERS


def test_sanitize_hex_color_accepts_valid_values_and_falls_back():
    assert icon_security.sanitize_hex_color(" #abc ") == "#abc"
    assert icon_security.sanitize_hex_color("#AABBCC") == "#AABBCC"
    assert icon_security.sanitize_hex_color("red", fallback="#000000") == "#000000"
    assert icon_security.sanitize_hex_color(None, fallback="") == ""


def test_sanitize_icon_input_drops_emoji_and_unsafe_json_parts():
    raw = json.dumps(
        {
            "emoji": " ✅ ",
            "svg": "<svg><path d='M0 0'/></svg>",
            "preset": "checklist",
            "image": "javascript:alert(1)",
            "color": "#123abc",
            "ignored": "value",
        }
    )

    sanitized = json.loads(icon_security.sanitize_icon_input(raw))

    assert sanitized == {
        "preset": "checklist",
        "color": "#123abc",
    }
    assert icon_security.sanitize_icon_input("✅", fallback="checklist") == "checklist"


def test_sanitize_icon_input_preserves_compact_preset_icon_payload():
    raw = json.dumps({"preset": "shopping_cart", "color": "#E53935"})

    sanitized = json.loads(icon_security.sanitize_icon_input(raw))

    assert sanitized == {"preset": "shopping_cart", "color": "#E53935"}
    assert len(json.dumps(sanitized, separators=(",", ":"))) < 255


def test_sanitize_icon_input_preserves_inert_inline_svg():
    """Allow path-only logos while still rejecting active SVG features."""

    raw = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none"><path d="M4 4h16v16H4z" stroke="currentColor" '
        'stroke-width="2"/></svg>'
    )

    assert icon_security.sanitize_icon_input(raw) == raw
    assert icon_security.require_safe_icon_input(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "<svg onload='alert(1)'></svg>",
        "<script>alert(1)</script>",
        "<svg><foreignObject></foreignObject></svg>",
        "<!DOCTYPE svg [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><svg>&xxe;</svg>",
        '{"image":"javascript:alert(1)"}',
    ],
)
def test_require_safe_icon_input_rejects_active_or_unknown_markup(raw):
    with pytest.raises(HTTPException) as exc:
        icon_security.require_safe_icon_input(raw)

    assert exc.value.status_code == 400
