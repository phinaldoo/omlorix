import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def read_split_frontend_source(entry_path: Path) -> str:
    chunk_directory = entry_path.with_suffix("")
    source_paths = [*sorted(chunk_directory.glob("*.js")), entry_path]
    return "\n".join(path.read_text(encoding="utf-8") for path in source_paths)


ADMIN_KEYS = {
    "nav_file_storage",
    "page_file_storage",
    "page_file_storage_subtitle",
    "file_storage_overview_title",
    "file_storage_total_storage",
    "file_storage_total_storage_desc",
    "file_storage_total_files",
    "file_storage_total_files_desc",
    "file_storage_users_with_files",
    "file_storage_users_with_files_desc",
    "file_storage_users_title",
    "file_storage_users_desc",
    "file_storage_search_aria",
    "file_storage_search_placeholder",
    "file_storage_col_user",
    "file_storage_col_storage",
    "file_storage_col_files",
    "file_storage_col_limits",
    "file_storage_col_latest",
    "file_storage_empty",
    "file_storage_never",
    "file_storage_unknown_user",
    "file_storage_used_of_unlimited",
    "file_storage_used_of_limit",
    "file_storage_uploads_disabled",
    "file_storage_showing",
    "file_storage_load_failed",
}

INDEX_KEYS = {
    "files_storage_usage_button",
    "files_storage_usage_title",
    "files_storage_usage_desc",
    "files_storage_usage_loading",
    "files_storage_usage_storage_label",
    "files_storage_usage_files_label",
    "files_storage_usage_uploads_disabled",
    "files_storage_usage_load_failed",
    "files_storage_usage_used_of_unlimited",
    "files_storage_usage_used_of_limit",
    "files_storage_usage_close",
}


def test_workspace_files_storage_modal_reuses_warning_card_shell():
    index_html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    modal_js = (ROOT / "frontend" / "js" / "chat" / "deleteWarningModals.js").read_text(encoding="utf-8")
    files_js = (ROOT / "frontend" / "js" / "chat" / "files.js").read_text(encoding="utf-8")

    assert 'id="filesStorageUsageButton"' in index_html
    assert "files-storage-usage-button-icon" not in index_html
    assert "filesStorageUsageOverlay" in modal_js
    assert "ariaModal: 'true'" in modal_js
    assert "ariaLabelledby: 'filesStorageUsageTitle'" in modal_js
    assert "ariaDescribedby: 'filesStorageUsageDesc'" in modal_js
    assert "role=\"progressbar\"" in modal_js
    assert "filesStorageUsageClose" in modal_js
    assert "window.Icons.info" in files_js
    assert "filesStorageUsageButton.innerHTML = window.Icons.info" in files_js
    assert "workspace-files-storage-usage-modal" in files_js
    assert "ViewManager.closeStorageUsageModal" in files_js


def test_workspace_files_storage_usage_fetches_and_refreshes_after_mutations():
    files_js = (ROOT / "frontend" / "js" / "chat" / "files.js").read_text(encoding="utf-8")
    modal_js = (ROOT / "frontend" / "js" / "chat" / "deleteWarningModals.js").read_text(encoding="utf-8")

    assert "STORAGE_USAGE: '/api/v1/files/storage/usage'" in files_js
    assert "async fetchStorageUsage()" in files_js
    assert "await API.fetchStorageUsage()" in files_js
    assert "fileStorageUsageModalState.requestId" in files_js
    assert "isLatestRequest()" in files_js
    assert files_js.count("await ViewManager.refreshStorageUsage({ silent: true });") >= 2
    assert "files_storage_usage_used_of_unlimited" in files_js
    assert "files_storage_usage_uploads_disabled" in modal_js
    assert "confirm(" not in files_js
    assert "alert(" not in files_js
    assert "prompt(" not in files_js


def test_microsoft_file_connections_and_origins_are_removed():

    files_js = (ROOT / "frontend" / "js" / "chat" / "files.js").read_text(encoding="utf-8")
    chat_box_js = read_split_frontend_source(
        ROOT / "frontend" / "js" / "chat" / "chatBox.js"
    )

    assert "microsoft-sync" not in files_js
    assert 'data-file-action="export-microsoft"' not in files_js
    assert 'data-file-action="sync-microsoft"' not in files_js
    assert "/api/v1/files/google-drive/import" in chat_box_js
    assert "/api/v1/files/google-drive/picker-session" in (
        ROOT / "frontend" / "js" / "chat" / "googleDrivePicker.js"
    ).read_text(encoding="utf-8")
    assert "/api/v1/files/one-drive" not in chat_box_js
    assert "/api/v1/files/sharepoint" not in chat_box_js
    assert "MicrosoftPicker" not in chat_box_js
    assert "getSelectedOrigins" not in files_js
    assert "filesCategoryFilterButton" not in files_js
    assert "params.set('origins'" not in files_js
    assert not (ROOT / "backend" / "app" / "files" / "microsoft_storage.py").exists()
    assert not (ROOT / "backend" / "app" / "files" / "one_drive.py").exists()

    login_social = (ROOT / "backend" / "app" / "admin" / "settings" / "schema_categories" / "login_social.py").read_text(encoding="utf-8")
    assert "enable_microsoft_login" in login_social
    assert "microsoft_client_id" in login_social


def test_workspace_file_edit_action_uses_edit_icon():
    """The existing-file action must use the edit glyph, not the create glyph."""

    files_js = (ROOT / "frontend" / "js" / "chat" / "files.js").read_text(encoding="utf-8")

    assert "${Icons.edit}" in files_js
    assert "${Icons.create}" not in files_js


def test_google_picker_admin_configuration_is_registered_and_translated():
    """Self-hosted admins can configure Picker without editing source files."""

    admin_schema = (
        ROOT
        / "backend"
        / "app"
        / "admin"
        / "settings"
        / "schema_categories"
        / "login_social.py"
    ).read_text(encoding="utf-8")
    defaults = (ROOT / "backend" / "app" / "settings" / "defaults.py").read_text(encoding="utf-8")
    required_keys = {
        "schema_login_social_google_picker_api_key",
        "schema_login_social_google_picker_api_key_desc",
        "schema_login_social_google_picker_app_id",
        "schema_login_social_google_picker_app_id_desc",
    }

    assert 'key="google_picker_api_key"' in admin_schema
    assert 'key="google_picker_app_id"' in admin_schema
    assert '"google_picker_api_key": ""' in defaults
    assert '"google_picker_app_id": ""' in defaults
    for locale_dir in sorted(path for path in (ROOT / "frontend" / "i18n").iterdir() if path.is_dir()):
        payload = json.loads((locale_dir / "admin.json").read_text(encoding="utf-8"))
        missing = sorted(key for key in required_keys if not payload.get(key))
        assert not missing, f"{locale_dir.name}/admin.json missing {missing}"
















def test_direct_workspace_files_route_retries_loading_after_app_setup():
    """A cold files route must load after authentication reveals the app shell."""

    workspace_js = (ROOT / "frontend" / "js" / "chat" / "workspace.js").read_text(encoding="utf-8")

    assert "this.initializeActiveFilesAfterSetup();" in workspace_js
    assert "initializeActiveFilesAfterSetup()" in workspace_js
    assert "WorkspaceState.activeTab !== 'files'" in workspace_js
    assert "requestAnimationFrame" in workspace_js
    assert "FilesManager.initialize()" in workspace_js


def test_admin_file_storage_page_is_registered_and_translated():
    admin_html = (ROOT / "frontend" / "admin.html").read_text(encoding="utf-8")
    helper_js = read_split_frontend_source(
        ROOT / "frontend" / "js" / "admin" / "helper.js"
    )
    pages_js = (ROOT / "frontend" / "js" / "admin" / "pages.js").read_text(encoding="utf-8")
    storage_js = (ROOT / "frontend" / "js" / "admin" / "fileStorage.js").read_text(encoding="utf-8")

    assert 'id="page-file-storage"' in admin_html
    assert 'data-i18n="page_file_storage"' in admin_html
    assert "/js/admin/fileStorage.js" in admin_html
    assert "/css/admin/fileStorage.css" in admin_html
    assert "nav_file_storage" in helper_js
    assert "initFileStoragePage" in pages_js
    assert "/api/v1/admin/file-storage/statistics" in storage_js
    assert "file_storage_uploads_disabled" in storage_js
    assert "getCurrentLocale()" in storage_js
    assert "state.pendingLoad = true" in storage_js
    assert 'id="fileStorageRefreshButton"' not in admin_html


def test_file_storage_translation_keys_exist_in_every_locale():
    i18n_root = ROOT / "frontend" / "i18n"

    for locale_dir in sorted(path for path in i18n_root.iterdir() if path.is_dir()):
        admin_payload = json.loads((locale_dir / "admin.json").read_text(encoding="utf-8"))
        index_payload = json.loads((locale_dir / "index.json").read_text(encoding="utf-8"))

        for key in ADMIN_KEYS:
            assert admin_payload.get(key), f"{locale_dir.name}/admin.json missing {key}"
        for key in INDEX_KEYS:
            assert index_payload.get(key), f"{locale_dir.name}/index.json missing {key}"

    german_admin_payload = json.loads(
        (i18n_root / "de" / "admin.json").read_text(encoding="utf-8")
    )
    assert german_admin_payload["file_storage_col_limits"] == "Grenzwerte"
