import json

import pytest

from app.tools.flashcards import utils as flashcard_utils
from app.tools.quiz import utils as quiz_utils
from app.tools import helper as tool_helper


def test_create_flashcards_normalizes_alias_fields_and_optional_lists():
    deck = flashcard_utils.create_flashcards(
        "  Vocabulary  ",
        [
            {
                "term": "Haus",
                "definition": "house",
                "clue": "Building",
                "usage": "Das Haus ist groß.",
                "tag": ["German", "A1"],
                "notes": "Common noun",
            },
            {
                "prompt": "bonjour",
                "translation": "hello",
                "pronunciation": "bohn-zhoor",
            },
        ],
        description=["Languages", "Starter deck"],
    )

    assert deck["title"] == "Vocabulary"
    assert deck["description"] == "Languages, Starter deck"
    assert deck["card_count"] == 2
    assert deck["cards"][0] == {
        "id": "c1",
        "front": "Haus",
        "back": "house",
        "hint": "Building",
        "example": "Das Haus ist groß.",
        "pronunciation": "",
        "category": "German, A1",
        "note": "Common noun",
    }
    assert deck["cards"][1]["id"] == "c2"
    assert deck["created_at"].endswith("+00:00")


@pytest.mark.parametrize(
    ("title", "cards", "match"),
    [
        ("", [{"front": "A", "back": "B"}], "title is required"),
        ("Deck", [], "cards must be a non-empty list"),
        ("Deck", ["not-object"], r"cards\[0\] must be an object"),
        ("Deck", [{"front": "A"}], r"cards\[0\]\.back is required"),
    ],
)
def test_create_flashcards_rejects_invalid_payloads(title, cards, match):
    with pytest.raises(ValueError, match=match):
        flashcard_utils.create_flashcards(title, cards)


def test_create_flashcards_enforces_card_limit():
    cards = [{"front": f"front-{index}", "back": f"back-{index}"} for index in range(flashcard_utils.MAX_FLASHCARDS + 1)]

    with pytest.raises(ValueError, match=f"at most {flashcard_utils.MAX_FLASHCARDS}"):
        flashcard_utils.create_flashcards("Too many", cards)


def test_flashcards_widget_payload_is_structured_frontend_data():
    deck = {
        "deck_id": 'deck-1" onclick="alert(1)',
        "title": "<b>Unsafe</b>",
        "description": '<img src=x onerror="alert(1)">',
        "card_count": 1,
        "cards": [{"front": "</script><script>alert(1)</script>", "back": "Answer"}],
    }

    widget = tool_helper._build_frontend_widget_payload("flashcards", deck)

    assert widget["render_mode"] == "frontend"
    assert "allow_scripts" not in widget
    assert json.loads(widget["html"]) == deck


def test_create_quiz_normalizes_dict_options_and_answer_text():
    quiz = quiz_utils.create_quiz(
        "  Safety check  ",
        [
            {
                "prompt": "Pick the safe option",
                "choices": [
                    {"label": "Escape HTML"},
                    {"text": "Trust strings"},
                    {"value": "Run scripts"},
                    "Skip validation",
                ],
                "answer": "Escape HTML",
                "explanation": "Markup belongs in text, not execution.",
            }
        ],
        description="  Tiny quiz  ",
    )

    question = quiz["questions"][0]
    assert quiz["title"] == "Safety check"
    assert quiz["description"] == "Tiny quiz"
    assert quiz["question_count"] == 1
    assert question["id"] == "q1"
    assert question["question"] == "Pick the safe option"
    assert question["options"] == ["Escape HTML", "Trust strings", "Run scripts", "Skip validation"]
    assert question["correct_option_index"] == 0
    assert question["explanation"] == "Markup belongs in text, not execution."


@pytest.mark.parametrize(
    ("question", "match"),
    [
        ({}, r"questions\[0\]\.question is required"),
        ({"question": "Q", "options": ["A"]}, "exactly 4 choices"),
        ({"question": "Q", "options": ["A", "B", "C", ""]}, r"options\[3\] must be a non-empty string"),
        ({"question": "Q", "options": ["A", "B", "C", "D"], "answer": True}, "correct_option_index is required"),
        ({"question": "Q", "options": ["A", "B", "C", "D"], "answer": 9}, "between 0 and 3"),
    ],
)
def test_create_quiz_rejects_invalid_questions(question, match):
    with pytest.raises(ValueError, match=match):
        quiz_utils.create_quiz("Quiz", [question])


def test_create_quiz_enforces_question_limit():
    questions = [
        {"question": f"Q{index}", "options": ["A", "B", "C", "D"], "correct_index": 0}
        for index in range(quiz_utils.MAX_QUIZ_QUESTIONS + 1)
    ]

    with pytest.raises(ValueError, match=f"at most {quiz_utils.MAX_QUIZ_QUESTIONS}"):
        quiz_utils.create_quiz("Too many", questions)


def test_quiz_widget_payload_is_structured_frontend_data():
    quiz = {
        "quiz_id": 'quiz-1" data-bad="1',
        "title": "<strong>Unsafe</strong>",
        "description": "</script><script>alert(1)</script>",
        "question_count": 1,
        "questions": [
            {
                "id": "q1",
                "question": "</script><script>alert(1)</script>",
                "options": ["A", "B", "C", "D"],
                "correct_option_index": 2,
                "explanation": "",
            }
        ],
    }

    widget = tool_helper._build_frontend_widget_payload("quiz", quiz)

    assert widget["render_mode"] == "frontend"
    assert "allow_scripts" not in widget
    assert json.loads(widget["html"]) == quiz
