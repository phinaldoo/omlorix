from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


MAX_FLASHCARDS = 40


def _normalize_required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _normalize_optional_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(parts)
    return str(value).strip()


def _resolve_card_field(card: dict[str, Any], keys: tuple[str, ...], field_name: str, card_number: int) -> str:
    for key in keys:
        if key in card and card.get(key) not in (None, ""):
            return _normalize_required_text(card.get(key), f"cards[{card_number}].{field_name}")
    raise ValueError(f"cards[{card_number}].{field_name} is required")


def create_flashcards(title: Any, cards: Any, description: Any = None) -> dict[str, Any]:
    deck_title = _normalize_required_text(title, "title")
    deck_description = _normalize_optional_text(description)

    if not isinstance(cards, list) or not cards:
        raise ValueError("cards must be a non-empty list")
    if len(cards) > MAX_FLASHCARDS:
        raise ValueError(f"cards supports at most {MAX_FLASHCARDS} items")

    normalized_cards: list[dict[str, Any]] = []
    for card_index, raw_card in enumerate(cards):
        if not isinstance(raw_card, dict):
            raise ValueError(f"cards[{card_index}] must be an object")

        front = _resolve_card_field(
            raw_card,
            ("front", "term", "prompt", "question"),
            "front",
            card_index,
        )
        back = _resolve_card_field(
            raw_card,
            ("back", "definition", "translation", "answer"),
            "back",
            card_index,
        )

        normalized_cards.append(
            {
                "id": f"c{card_index + 1}",
                "front": front,
                "back": back,
                "hint": _normalize_optional_text(raw_card.get("hint") or raw_card.get("clue")),
                "example": _normalize_optional_text(
                    raw_card.get("example") or raw_card.get("example_sentence") or raw_card.get("usage")
                ),
                "pronunciation": _normalize_optional_text(raw_card.get("pronunciation")),
                "category": _normalize_optional_text(raw_card.get("category") or raw_card.get("topic") or raw_card.get("tag")),
                "note": _normalize_optional_text(raw_card.get("note") or raw_card.get("notes") or raw_card.get("mnemonic")),
            }
        )

    return {
        "deck_id": str(uuid4()),
        "title": deck_title,
        "description": deck_description,
        "card_count": len(normalized_cards),
        "cards": normalized_cards,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
