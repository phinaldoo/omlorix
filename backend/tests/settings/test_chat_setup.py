import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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

from app.settings import utils as settings_utils
from app.users import utils as users_utils
from app.utils import utils as app_utils
from app.chats import read_aloud as chat_read_aloud


class ChatSetupTests:
    def test_chat_setup_exposes_shadow_chat_deletion_policy(self):
        user = SimpleNamespace(
            id="user-1",
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.test",
        )

        def user_setting_value(_user_id, page, key, _db):
            values = {
                ("appearance", "color_theme"): "default",
                ("appearance", "theme"): "light",
                ("appearance", "font"): "Inter",
                ("general", "language"): "de",
                ("chat", "render_user_messages_markdown"): True,
                ("chat", "render_assistant_messages_markdown"): True,
                ("chat", "ctrl_enter_to_send"): False,
                ("chat", "always_use_temporary_chat"): False,
                ("chat", "show_model_settings"): True,
                ("chat", "show_assistant_message_metadata"): False,
                ("chat", "show_message_nav"): True,
                ("chat", "chat_box_show_call_input"): False,
                ("chat", "speech_playback_speed"): 1.0,
                ("states", "has_new_notifications"): False,
            }
            return values.get((page, key), False)

        def group_setting_value(_user_id, page, key, _db):
            values = {
                ("chat", "allow_chat_deletion"): True,
                ("chat", "shadow_chat_deletion"): True,
                ("chat", "allow_regenerate_response"): True,
                ("chat", "allow_rate_response"): True,
                ("chat", "allow_delete_messages"): True,
                ("chat", "allow_temporary_chat"): True,
                ("chat", "save_temp_chats"): False,
                ("chat", "save_temp_chats_retention_enabled"): False,
                ("chat", "save_temp_chats_retention_days"): 30,
                ("leaderboard", "enabled"): True,
                ("leaderboard", "artificial_analysis_api_key"): "configured-key",
            }
            return values.get((page, key), False)

        with patch.object(settings_utils, "get_user", return_value=user), patch(
            "app.users.init.get_user_setting_value",
            side_effect=user_setting_value,
        ), patch.object(
            settings_utils,
            "get_user_group_setting_value",
            side_effect=group_setting_value,
        ), patch.object(
            settings_utils,
            "get_effective_pinned_model_ids_for_user",
            return_value=[],
        ), patch.object(
            settings_utils,
            "get_settings_page",
            return_value=None,
        ), patch.object(
            app_utils,
            "get_privacy_policy_notice_policy",
            return_value={},
        ), patch.object(
            app_utils,
            "get_terms_of_service_policy",
            return_value={},
        ), patch.object(
            users_utils,
            "get_profile_picture_status",
            return_value={
                "has_custom_profile_picture": False,
                "has_profile_picture": False,
                "profile_picture_source": "initials",
                "profile_picture_provider": "",
            },
        ), patch.object(
            chat_read_aloud,
            "get_read_aloud_runtime_config",
            return_value={},
        ), patch(
            "app.groups.management.has_managed_groups_for_user",
            return_value=True,
        ), patch(
            "app.llm.models.has_applicable_rate_limits",
            return_value=True,
        ), patch(
            "app.connections.policy.group_has_enabled_workspace_connections",
            return_value=True,
        ):
            result = settings_utils.get_chat_setup("user-1", MagicMock())

        assert result["shadow_chat_deletion"] is True
        assert result["language"] == "de"
        assert result["country"] == ""
        assert result["timezone"] == ""
        assert result["show_welcome_card"] is True
        assert result["personal_info_access_enabled"] is False
        assert result["has_leaderboard_access"] is True
        assert result["allow_workspace_connections"] is True
        assert result["allow_mcp"] is False
        assert result["user_settings_navigation"] == {
            "managed_groups": True,
            "rate_limits": True,
        }
        assert "ui_scale" not in result

    def test_chat_setup_exposes_terms_of_service_policy(self):
        user = SimpleNamespace(
            id="user-1",
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.test",
        )

        def user_setting_value(_user_id, page, key, _db):
            values = {
                ("appearance", "color_theme"): "default",
                ("appearance", "theme"): "light",
                ("appearance", "font"): "Inter",
                ("chat", "render_user_messages_markdown"): True,
                ("chat", "render_assistant_messages_markdown"): True,
                ("chat", "ctrl_enter_to_send"): False,
                ("chat", "always_use_temporary_chat"): False,
                ("chat", "show_model_settings"): True,
                ("chat", "show_assistant_message_metadata"): False,
                ("chat", "show_message_nav"): True,
                ("chat", "chat_box_show_call_input"): False,
                ("chat", "speech_playback_speed"): 1.0,
                ("states", "has_new_notifications"): False,
            }
            return values.get((page, key), False)

        def group_setting_value(_user_id, page, key, _db):
            values = {
                ("chat", "allow_chat_deletion"): True,
                ("chat", "shadow_chat_deletion"): True,
                ("chat", "allow_regenerate_response"): True,
                ("chat", "allow_rate_response"): True,
                ("chat", "allow_delete_messages"): True,
                ("chat", "allow_temporary_chat"): True,
                ("chat", "save_temp_chats"): False,
                ("chat", "save_temp_chats_retention_enabled"): False,
                ("chat", "save_temp_chats_retention_days"): 30,
                ("leaderboard", "artificial_analysis_api_key"): "",
            }
            return values.get((page, key), False)

        terms_policy = {
            "revision": 6,
            "accepted_current_revision": False,
            "require_current_revision_for_access": True,
        }

        with patch.object(settings_utils, "get_user", return_value=user), patch(
            "app.users.init.get_user_setting_value",
            side_effect=user_setting_value,
        ), patch.object(
            settings_utils,
            "get_user_group_setting_value",
            side_effect=group_setting_value,
        ), patch.object(
            settings_utils,
            "get_effective_pinned_model_ids_for_user",
            return_value=[],
        ), patch.object(
            settings_utils,
            "get_settings_page",
            return_value=None,
        ), patch.object(
            app_utils,
            "get_privacy_policy_notice_policy",
            return_value={},
        ), patch.object(
            app_utils,
            "get_terms_of_service_policy",
            return_value=terms_policy,
        ), patch.object(
            users_utils,
            "get_profile_picture_status",
            return_value={
                "has_custom_profile_picture": False,
                "has_profile_picture": False,
                "profile_picture_source": "initials",
                "profile_picture_provider": "",
            },
        ), patch.object(
            chat_read_aloud,
            "get_read_aloud_runtime_config",
            return_value={},
        ), patch(
            "app.connections.policy.group_has_enabled_workspace_connections",
            return_value=False,
        ):
            result = settings_utils.get_chat_setup("user-1", MagicMock())

        assert result["terms_of_service_policy"] == terms_policy
        assert result["has_leaderboard_access"] is False
