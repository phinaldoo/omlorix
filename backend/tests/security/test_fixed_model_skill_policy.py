from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: None
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: None
    sys.modules["zstandard"] = fake_zstandard

from app.chats import utils as chat_utils
from app.realtime import service as realtime_service


def test_chat_generation_rejects_user_skills_when_model_has_fixed_skill():
    with pytest.raises(HTTPException) as exc:
        chat_utils._resolve_generation_skill_ids(
            requested_skill_ids=["user-bypass"],
            model_skill_ids=["admin-guardrail"],
            agent_skill_ids=[],
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == chat_utils.FIXED_MODEL_SKILL_OVERRIDE_ERROR


def test_chat_generation_keeps_fixed_skill_as_last_non_user_skill():
    assert chat_utils._resolve_generation_skill_ids(
        requested_skill_ids=[],
        model_skill_ids=["admin-guardrail"],
        agent_skill_ids=["agent-helper"],
    ) == ["agent-helper", "admin-guardrail"]


def test_realtime_model_skill_extraction_supports_legacy_and_multi_skill_settings():
    assert realtime_service._extract_realtime_model_skill_ids({"skill_id": "admin-guardrail"}) == [
        "admin-guardrail"
    ]
    assert realtime_service._extract_realtime_model_skill_ids(
        {"skill_ids": ["admin-guardrail", "admin-guardrail", ""]}
    ) == ["admin-guardrail"]


def test_trusted_admin_skill_ids_exclude_user_controlled_agent_skills():
    assert chat_utils._resolve_trusted_admin_skill_ids(
        model_skill_ids=["admin-guardrail", "admin-guardrail", ""],
        agent_skill_ids=["shared-agent-admin-skill"],
    ) == ["admin-guardrail"]


def test_agent_skill_ids_remain_effective_without_being_trusted():
    assert chat_utils._resolve_generation_skill_ids(
        requested_skill_ids=[],
        model_skill_ids=[],
        agent_skill_ids=["shared-agent-admin-skill"],
    ) == ["shared-agent-admin-skill"]
