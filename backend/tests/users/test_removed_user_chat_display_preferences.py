from pathlib import Path

from app.users.defaults import DEFAULT_USER_SETTINGS
from app.users.init import _sync_with_defaults


REPO_ROOT = Path(__file__).resolve().parents[3]
RETIRED_CHAT_DISPLAY_SETTINGS = {
    "show_model_select_search_bar",
    "show_model_select_model_description",
    "chat_show_assistant_thinking",
    "chat_box_show_microphone_input",
    "chat_box_show_files_add",
    "chat_box_show_thinking_setting",
    "user_message_button_list_copy",
}


def test_legacy_chat_display_preferences_are_removed_during_settings_sync():
    """Retired display preferences must be scrubbed without harming live settings."""

    legacy_chat_settings = {
        key: False for key in RETIRED_CHAT_DISPLAY_SETTINGS
    }
    legacy_chat_settings["show_message_nav"] = False

    changed, synchronized = _sync_with_defaults({"chat": legacy_chat_settings})

    assert changed is True
    assert RETIRED_CHAT_DISPLAY_SETTINGS.isdisjoint(DEFAULT_USER_SETTINGS["chat"])
    assert RETIRED_CHAT_DISPLAY_SETTINGS.isdisjoint(synchronized["chat"])
    assert synchronized["chat"]["show_message_nav"] is False


def test_retired_chat_display_preferences_are_absent_from_api_and_ui_sources():
    """Prevent a partial rollback from exposing or consuming retired settings."""

    source_paths = (
        "backend/app/settings/utils.py",
        "backend/app/users/schemas.py",
        "backend/app/users/utils.py",
        "frontend/index.html",
        "frontend/js/chat/chatBox.js",
        "frontend/js/chat/init.js",
        "frontend/js/chat/modelSelect.js",
        "frontend/js/chat/userSettings/toggle.js",
    )
    source_paths += tuple(
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "frontend/js/chat/messages").glob("*.js"))
    )
    source_paths += tuple(
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "frontend/js/chat/chatBox").glob("*.js"))
    )
    combined_source = "\n".join(
        (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for relative_path in source_paths
    )

    for setting_key in RETIRED_CHAT_DISPLAY_SETTINGS:
        assert setting_key not in combined_source
