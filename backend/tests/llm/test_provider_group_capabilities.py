from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.llm.models import _validate_provider_group_members


def _db_returning(*providers):
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.first.side_effect = providers
    return db


def test_provider_groups_reject_speech_only_providers():
    db = _db_returning(
        SimpleNamespace(id="speech-1", provider="elevenlabs"),
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_provider_group_members(
            db,
            [
                {"provider_id": "speech-1", "weight": 1},
                {"provider_id": "speech-2", "weight": 1},
            ],
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "provider_group_provider_not_model_capable"


def test_provider_groups_accept_same_type_chat_model_providers():
    db = _db_returning(
        SimpleNamespace(id="openai-1", provider="openai"),
        SimpleNamespace(id="openai-2", provider="openai"),
    )

    result = _validate_provider_group_members(
        db,
        [
            {"provider_id": "openai-1", "weight": 2},
            {"provider_id": "openai-2", "weight": 1},
        ],
    )

    assert result == [
        {"provider_id": "openai-1", "weight": 2},
        {"provider_id": "openai-2", "weight": 1},
    ]
