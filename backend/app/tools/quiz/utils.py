from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


MAX_QUIZ_QUESTIONS = 20
QUIZ_OPTIONS_PER_QUESTION = 4


def _normalize_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _normalize_option_text(option: Any, question_number: int, option_number: int) -> str:
    value = option
    if isinstance(option, dict):
        value = option.get("text") or option.get("label") or option.get("value")
    option_text = str(value or "").strip()
    if not option_text:
        raise ValueError(
            f"questions[{question_number}].options[{option_number}] must be a non-empty string"
        )
    return option_text


def _resolve_correct_option_index(question: dict, options: list[str], question_number: int) -> int:
    candidate = question.get("correct_option_index")
    if candidate is None:
        candidate = question.get("correct_index")
    if candidate is None:
        candidate = question.get("answer")
    if candidate is None:
        candidate = question.get("correct_option")

    index: int | None = None
    if isinstance(candidate, bool):
        index = None
    elif isinstance(candidate, int):
        index = candidate
    elif isinstance(candidate, float) and candidate.is_integer():
        index = int(candidate)
    elif isinstance(candidate, str):
        stripped = candidate.strip()
        if stripped.isdigit():
            index = int(stripped)
        else:
            lowered = stripped.lower()
            for option_index, option in enumerate(options):
                if option.lower() == lowered:
                    index = option_index
                    break

    if index is None:
        raise ValueError(
            f"questions[{question_number}].correct_option_index is required and must reference one of the 4 options"
        )
    if index < 0 or index >= QUIZ_OPTIONS_PER_QUESTION:
        raise ValueError(
            f"questions[{question_number}].correct_option_index must be between 0 and {QUIZ_OPTIONS_PER_QUESTION - 1}"
        )
    return index


def create_quiz(title: Any, questions: Any, description: Any = None) -> dict[str, Any]:
    quiz_title = _normalize_text(title, "title")
    quiz_description = str(description or "").strip()

    if not isinstance(questions, list) or not questions:
        raise ValueError("questions must be a non-empty list")
    if len(questions) > MAX_QUIZ_QUESTIONS:
        raise ValueError(f"questions supports at most {MAX_QUIZ_QUESTIONS} items")

    normalized_questions: list[dict[str, Any]] = []
    for question_index, raw_question in enumerate(questions):
        if not isinstance(raw_question, dict):
            raise ValueError(f"questions[{question_index}] must be an object")

        question_text = _normalize_text(
            raw_question.get("question") or raw_question.get("prompt"),
            f"questions[{question_index}].question",
        )
        raw_options = raw_question.get("options") or raw_question.get("choices")
        if not isinstance(raw_options, list):
            raise ValueError(f"questions[{question_index}].options must be an array")
        if len(raw_options) != QUIZ_OPTIONS_PER_QUESTION:
            raise ValueError(
                f"questions[{question_index}].options must include exactly {QUIZ_OPTIONS_PER_QUESTION} choices"
            )

        options: list[str] = []
        for option_index, raw_option in enumerate(raw_options):
            options.append(_normalize_option_text(raw_option, question_index, option_index))

        correct_option_index = _resolve_correct_option_index(raw_question, options, question_index)
        explanation = str(raw_question.get("explanation") or "").strip()

        normalized_questions.append(
            {
                "id": f"q{question_index + 1}",
                "question": question_text,
                "options": options,
                "correct_option_index": correct_option_index,
                "explanation": explanation,
            }
        )

    return {
        "quiz_id": str(uuid4()),
        "title": quiz_title,
        "description": quiz_description,
        "question_count": len(normalized_questions),
        "questions": normalized_questions,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
