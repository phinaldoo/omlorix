import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "numpy" not in sys.modules:
    fake_numpy = ModuleType("numpy")
    fake_numpy.linspace = lambda start, stop, num, dtype=int: []
    for attr_name in (
        "short",
        "ushort",
        "intc",
        "uintc",
        "int_",
        "uint",
        "longlong",
        "ulonglong",
        "half",
        "float16",
        "float32",
        "float64",
        "single",
        "double",
        "longdouble",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "intp",
        "uintp",
        "bool_",
        "integer",
        "floating",
        "generic",
        "number",
        "ndarray",
    ):
        setattr(fake_numpy, attr_name, int if "float" not in attr_name and attr_name != "bool_" else float)
    fake_numpy.bool_ = bool
    fake_numpy.integer = int
    fake_numpy.floating = float
    fake_numpy.generic = object
    fake_numpy.number = (int, float)
    fake_numpy.ndarray = list
    sys.modules["numpy"] = fake_numpy

if "numpy.typing" not in sys.modules:
    sys.modules["numpy.typing"] = ModuleType("numpy.typing")

if "pandas" not in sys.modules:
    fake_pandas = ModuleType("pandas")
    fake_pandas.DataFrame = type("DataFrame", (), {})
    fake_pandas.to_datetime = lambda value, *args, **kwargs: value
    fake_pandas.isna = lambda value: False
    sys.modules["pandas"] = fake_pandas

if "elevenlabs" not in sys.modules:
    fake_elevenlabs = ModuleType("elevenlabs")
    fake_elevenlabs.SpeechToTextConvertRequestModelId = "scribe_v1"
    sys.modules["elevenlabs"] = fake_elevenlabs

if "elevenlabs.client" not in sys.modules:
    fake_elevenlabs_client = ModuleType("elevenlabs.client")
    fake_elevenlabs_client.ElevenLabs = lambda *args, **kwargs: SimpleNamespace()
    sys.modules["elevenlabs.client"] = fake_elevenlabs_client

if "markitdown" not in sys.modules:
    fake_markitdown = ModuleType("markitdown")

    class _FakeMarkItDown:
        def __init__(self, *args, **kwargs):
            pass

    fake_markitdown.MarkItDown = _FakeMarkItDown
    sys.modules["markitdown"] = fake_markitdown

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard

from test_support import ensure_optional_dependency_stubs

ensure_optional_dependency_stubs()

from app.chats.utils import _build_system_instruction_sections
from app.llm.system_instruction.personality import (
    PERSONALITY_SECTION_TITLE,
    get_user_personality_system_instruction_section,
)
from app.llm.system_instruction.title import get_title_generation_prompt
from app.users.init import _sync_with_defaults
from app.users.schemas import UpdateUserPersonalitySettings
from app.users.utils import (
    _sanitize_existing_user_import_settings,
    update_user_personality_settings,
    user_settings_init,
)


class PersonalitySettingsTests:
    def test_sync_with_defaults_adds_personality_chat_settings(self):
        changed, merged = _sync_with_defaults({"chat": {"last_model": "model-1"}})

        assert changed is True
        assert "personality_enabled" not in merged["chat"]
        assert merged["chat"]["personality_preset"] == "none"
        assert merged["chat"]["personality_custom_instruction"] == ""

    def test_sync_with_defaults_removes_obsolete_e2ee_chat_settings(self):
        changed, merged = _sync_with_defaults(
            {
                "chat": {
                    "e2ee_public_key": "legacy-public-key",
                    "e2ee_public_key_alg": "RSA-OAEP-256",
                }
            }
        )

        assert changed is True
        assert "e2ee_public_key" not in merged["chat"]
        assert "e2ee_public_key_alg" not in merged["chat"]

    def test_sync_with_defaults_removes_obsolete_assistant_button_settings(self):
        """Persisted assistant action preferences disappear after settings sync."""

        removed_keys = {
            "assistant_message_button_list_copy",
            "assistant_message_button_list_edit",
            "assistant_message_button_list_delete",
            "assistant_message_button_list_redo",
            "assistant_message_button_list_branch",
            "assistant_message_button_list_feedback",
            "assistant_message_button_list_sources",
        }
        changed, merged = _sync_with_defaults(
            {"chat": {key: False for key in removed_keys}}
        )

        assert changed is True
        assert removed_keys.isdisjoint(merged["chat"])

    def test_existing_user_import_settings_strips_password_setup_flags(self):
        imported_settings = {
            "social_login": {"needs_password_setup": True, "google_linked": True},
            "sso_login": {"needs_password_setup": True, "saml_linked": True},
            "general": {"language": "de"},
        }

        sanitized = _sanitize_existing_user_import_settings(imported_settings)

        assert "needs_password_setup" not in sanitized["social_login"]
        assert "needs_password_setup" not in sanitized["sso_login"]
        assert "google_linked" not in sanitized["social_login"]
        assert sanitized["sso_login"]["saml_linked"] is True
        assert sanitized["general"]["language"] == "de"
        assert imported_settings["social_login"]["needs_password_setup"] is True
        assert imported_settings["sso_login"]["needs_password_setup"] is True

    @pytest.mark.parametrize("replacement_value", [None, False, "disabled", ["needs_password_setup"]])
    def test_existing_user_import_settings_removes_non_dict_password_setup_sections(self, replacement_value):
        imported_settings = {
            "social_login": replacement_value,
            "sso_login": replacement_value,
            "general": {"language": "de"},
        }

        sanitized = _sanitize_existing_user_import_settings(imported_settings)

        assert "social_login" not in sanitized
        assert "sso_login" not in sanitized
        assert sanitized["general"]["language"] == "de"

    def test_personality_payload_rejects_unknown_preset(self):
        with pytest.raises(ValidationError):
            UpdateUserPersonalitySettings(preset="unknown-style")

    def test_personality_payload_rejects_overlong_custom_text(self):
        with pytest.raises(ValidationError):
            UpdateUserPersonalitySettings(custom_instruction=("x" * 1001))

    def test_personality_resolver_returns_none_for_none_preset(self):
        with patch("app.llm.system_instruction.personality.get_user_setting_value", side_effect=["none"]):
            assert get_user_personality_system_instruction_section("user-1", MagicMock()) is None

    def test_personality_resolver_returns_none_for_blank_custom_text(self):
        with patch(
            "app.llm.system_instruction.personality.get_user_setting_value",
            side_effect=["custom", "   "],
        ):
            assert get_user_personality_system_instruction_section("user-1", MagicMock()) is None

    def test_personality_resolver_wraps_custom_text(self):
        with patch(
            "app.llm.system_instruction.personality.get_user_setting_value",
            side_effect=["custom", "Be extra encouraging."],
        ):
            section = get_user_personality_system_instruction_section("user-1", MagicMock())

        assert section == {
            "title": PERSONALITY_SECTION_TITLE,
            "content": (
                "Follow these user-requested interpersonal and tone preferences when replying. "
                "Treat them as style guidance only and do not override higher-priority safety, policy, or task instructions.\n\n"
                "Be extra encouraging."
            ),
        }

    def test_personality_resolver_uses_preset_text(self):
        with patch(
            "app.llm.system_instruction.personality.get_user_setting_value",
            side_effect=["professional", ""],
        ):
            section = get_user_personality_system_instruction_section("user-1", MagicMock())

        assert section["title"] == PERSONALITY_SECTION_TITLE
        assert "polished" in section["content"].lower()

    def test_update_user_personality_settings_persists_and_returns_chat_values(self):
        db = MagicMock()

        with patch("app.users.utils.update_user_settings_bulk") as mock_update_bulk, patch(
            "app.users.utils.get_user_settings",
            return_value={
                "chat": {
                    "personality_preset": "friendly",
                    "personality_custom_instruction": "Keep it upbeat.",
                }
            },
        ):
            result = update_user_personality_settings(
                db,
                "user-1",
                preset="friendly",
                custom_instruction="  Keep it upbeat.  ",
            )

        mock_update_bulk.assert_called_once_with(
            "user-1",
            {
                "chat": {
                    "personality_preset": "friendly",
                    "personality_custom_instruction": "Keep it upbeat.",
                }
            },
            db,
        )
        assert result == {
            "status": "success",
            "updated": {
                "chat": {
                    "personality_preset": "friendly",
                    "personality_custom_instruction": "Keep it upbeat.",
                }
            },
        }

    def test_update_user_personality_settings_persists_none_preset(self):
        db = MagicMock()

        with patch("app.users.utils.update_user_settings_bulk") as mock_update_bulk, patch(
            "app.users.utils.get_user_settings",
            return_value={
                "chat": {
                    "personality_preset": "none",
                    "personality_custom_instruction": "",
                }
            },
        ):
            result = update_user_personality_settings(db, "user-1", preset="none")

        mock_update_bulk.assert_called_once_with(
            "user-1",
            {
                "chat": {
                    "personality_preset": "none",
                }
            },
            db,
        )
        assert result["updated"]["chat"]["personality_preset"] == "none"

    def test_update_user_personality_settings_rejects_unknown_preset(self):
        with pytest.raises(HTTPException) as exc_info:
            update_user_personality_settings(MagicMock(), "user-1", preset="bad-value")

        assert exc_info.value.status_code == 400
        assert "Unsupported personality preset" in str(exc_info.value.detail)

    def test_user_settings_init_exposes_personality_fields(self):
        chat_settings = {
            "personality_preset": "quirky",
            "personality_custom_instruction": "Lean playful.",
            "render_assistant_messages_markdown": True,
            "render_user_messages_markdown": True,
            "show_message_nav": True,
            "show_model_settings": False,
            "show_assistant_message_metadata": False,
            "ctrl_enter_to_send": False,
            "always_use_temporary_chat": False,
            "chat_full_width": False,
            "speech_playback_speed": 1.0,
            "byok_statistics_enabled": False,
        }

        with patch("app.users.utils.get_user", return_value=SimpleNamespace(account_type="regular", temporary_expires_at=None)), patch(
            "app.users.utils.get_user_settings",
            return_value={
                "security": {
                    "profile_visibility": "private",
                    "allow_llm_to_access_personal_information": {},
                    "allow_llm_to_access_personal_information_preset": "none",
                },
                "general": {
                    "language": "en",
                    "country": "us",
                    "timezone": "Europe/Berlin",
                    "location": "Berlin",
                },
                "appearance": {"font": "inter"},
                "chat": chat_settings,
                "login_2fa": {"enable_2fa": False},
                "social_login": {"needs_password_setup": False},
            },
        ), patch("app.users.utils.get_user_group_setting_value", return_value=False), patch(
            "app.users.utils.get_effective_pinned_model_ids_for_user",
            return_value=[],
        ), patch("app.users.utils.get_value_by_page_and_key", return_value=False), patch(
            "app.users.utils.coerce_bool",
            return_value=False,
        ), patch.dict(
            sys.modules,
            {"app.groups.management": SimpleNamespace(managed_groups_for_user=lambda db, user: [])},
        ):
            payload = user_settings_init("user-1", MagicMock())

        assert payload["personality_preset"] == "quirky"
        assert payload["personality_custom_instruction"] == "Lean playful."
        assert "ui_scale" not in payload

    def test_user_settings_init_uses_global_2fa_policy_for_visibility(self):
        with patch("app.users.utils.get_user", return_value=SimpleNamespace(account_type="regular", temporary_expires_at=None)), patch(
            "app.users.utils.get_user_settings",
            return_value={
                "security": {
                    "profile_visibility": "private",
                    "allow_llm_to_access_personal_information": {},
                    "allow_llm_to_access_personal_information_preset": "none",
                },
                "general": {},
                "appearance": {"font": "inter"},
                "chat": {},
                "login_2fa": {"enable_2fa": False},
                "social_login": {"needs_password_setup": False},
            },
        ), patch("app.users.utils.get_user_group_setting_value", return_value=False), patch(
            "app.users.utils.get_effective_pinned_model_ids_for_user",
            return_value=[],
        ), patch(
            "app.users.utils.get_login_passkey_policy",
            return_value={"enable_passkeys": False},
        ), patch(
            "app.users.utils.get_value_by_page_and_key",
            side_effect=lambda page_name, key_name, db: True if (page_name, key_name) == ("login_general", "enable_2fa") else False,
        ), patch(
            "app.users.utils.coerce_bool",
            side_effect=lambda value, default=False: default if value is None else bool(value),
        ), patch.dict(
            sys.modules,
            {"app.groups.management": SimpleNamespace(managed_groups_for_user=lambda db, user: [])},
        ):
            payload = user_settings_init("user-1", MagicMock())

        assert payload["two_factor_authentication_enabled"] is True

    def test_system_instruction_sections_include_personality_for_replies(self):
        sections = _build_system_instruction_sections(
            personality_section={"title": PERSONALITY_SECTION_TITLE, "content": "Use a warm tone."},
            skill_content="Follow repo conventions.",
        )

        assert sections[0] == {"title": PERSONALITY_SECTION_TITLE, "content": "Use a warm tone."}
        assert sections[1] == {"title": "Skill Instructions", "content": "Follow repo conventions."}

    def test_title_generation_prompt_remains_personality_neutral(self):
        with patch("app.llm.system_instruction.title.get_user_setting_value", return_value="en"), patch(
            "app.llm.system_instruction.title.get_user_group_setting_value",
            return_value="",
        ):
            title_prompt = get_title_generation_prompt("user-1", MagicMock())

        assert PERSONALITY_SECTION_TITLE not in title_prompt
