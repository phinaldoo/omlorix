import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_admin_backup_page_exposes_webdav_destination_without_restore_controls():
    admin_html = (ROOT / "frontend" / "admin.html").read_text(encoding="utf-8")

    assert 'value="webdav"' in admin_html
    assert 'data-i18n="db_destination_provider_webdav"' in admin_html
    assert 'id="backupRestoreButton"' not in admin_html
    assert 'id="backupRestoreOverlay"' not in admin_html
    assert 'id="backupRestoreFileInput"' not in admin_html


def test_settings_snapshot_import_export_is_removed_end_to_end():
    admin_html = (ROOT / "frontend" / "admin.html").read_text(encoding="utf-8")
    database_js = (ROOT / "frontend" / "js" / "admin" / "database.js").read_text(encoding="utf-8")
    settings_router = (ROOT / "backend" / "app" / "settings" / "router.py").read_text(encoding="utf-8")
    settings_utils = (ROOT / "backend" / "app" / "settings" / "utils.py").read_text(encoding="utf-8")

    assert "databaseImportConfig" not in admin_html
    assert "databaseExportConfig" not in admin_html
    assert "/api/v1/settings/import" not in database_js
    assert "/api/v1/settings/export" not in database_js
    assert '@settings_router.post("/import"' not in settings_router
    assert '@settings_router.get("/export"' not in settings_router
    assert "def import_settings(" not in settings_utils
    assert "def export_settings(" not in settings_utils

    removed_translation_keys = {
        "db_config_title",
        "db_config_desc",
        "db_import_config_title",
        "db_import_config_desc",
        "db_import_config_btn",
        "db_export_config_title",
        "db_export_config_desc",
        "db_export_config_btn",
        "db_busy_importing",
        "db_busy_exporting",
        "db_import_invalid_json",
        "db_import_failed",
        "db_import_created_label",
        "db_import_updated_label",
        "db_import_skipped_label",
        "db_import_errors_suffix",
        "db_export_failed",
        "db_export_success",
    }
    for admin_file in sorted((ROOT / "frontend" / "i18n").glob("*/admin.json")):
        payload = json.loads(admin_file.read_text(encoding="utf-8"))
        assert removed_translation_keys.isdisjoint(payload), admin_file


def test_admin_backup_page_uses_single_header_refresh_button():
    admin_html = (ROOT / "frontend" / "admin.html").read_text(encoding="utf-8")
    database_js = (ROOT / "frontend" / "js" / "admin" / "database.js").read_text(encoding="utf-8")

    assert 'id="databaseRefreshButton"' in admin_html
    assert 'class="refresh-icon"' in admin_html
    assert 'class="check-icon"' in admin_html
    assert "refreshAll({ isManualRefresh: true })" in database_js
    assert "backupJobsRefreshButton" not in admin_html
    assert "backupRestoreJobsRefreshButton" not in admin_html
    assert "backupJobsRefreshButton" not in database_js
    assert "backupRestoreJobsRefreshButton" not in database_js


def test_admin_backup_modals_do_not_render_clear_form_buttons():
    admin_html = (ROOT / "frontend" / "admin.html").read_text(encoding="utf-8")
    database_js = (ROOT / "frontend" / "js" / "admin" / "database.js").read_text(encoding="utf-8")

    assert "backupDestinationResetButton" not in admin_html
    assert "backupScheduleResetButton" not in admin_html
    assert "db_destination_reset_btn" not in admin_html
    assert "db_schedule_reset_btn" not in admin_html
    assert "destinationResetButton" not in database_js
    assert "scheduleResetButton" not in database_js


def test_admin_backup_js_disables_impossible_plaintext_backup():
    database_js = (ROOT / "frontend" / "js" / "admin" / "database.js").read_text(encoding="utf-8")

    assert "plaintext_archives_allowed" in database_js
    assert "const encryptionRequired = encryptionAvailable && !plaintextAllowed;" in database_js
    assert "dom.backupNowEncryptionEnabled.disabled = encryptionRequired;" in database_js
    assert "!isBackupArchiveModeAvailable(encryptionEnabled)" in database_js
    assert "openRestoreModal" not in database_js


def test_admin_backup_javascript_has_no_restore_execution_surface():
    database_js = (ROOT / "frontend" / "js" / "admin" / "database.js").read_text(encoding="utf-8")

    assert "/api/v1/admin/backups/restore" not in database_js
    assert "handleStartRestore" not in database_js
    assert "uploadRestoreArchive" not in database_js


def test_admin_backup_router_has_no_restore_web_routes():
    backup_router = (ROOT / "backend" / "app" / "backups" / "router.py").read_text(encoding="utf-8")

    assert '@backups_router.post("/restore' not in backup_router
    assert '@backups_router.get("/restore' not in backup_router


def test_nginx_has_no_special_restore_upload_route():
    for template_name in (
        "default.http.conf.template/default.conf",
    ):
        config = (ROOT / "nginx" / template_name).read_text(encoding="utf-8")
        api_location = config.split("location /api/ {", 1)[1]

        assert "location = /api/v1/admin/backups/restore/upload" not in config, template_name
        assert "client_max_body_size 16M;" in api_location, template_name


def test_admin_backup_job_download_uses_native_browser_streaming():
    database_js = (ROOT / "frontend" / "js" / "admin" / "database.js").read_text(encoding="utf-8")

    assert "renderNativeDownloadLink" in database_js
    assert "data-native-backup-download" in database_js
    assert "downloadUrl" in database_js
    assert "const blob = await response.blob()" not in database_js.split("async function handleJobAction", 1)[1]
    assert "jobDownloadsInProgress: new Set()" in database_js
    assert "method: 'HEAD'" in database_js
    assert "nativeDownloadLink.click()" in database_js


def test_admin_backup_job_verify_button_uses_persistent_loading_guard():
    database_js = (ROOT / "frontend" / "js" / "admin" / "database.js").read_text(encoding="utf-8")

    assert "jobVerificationsInProgress: new Set()" in database_js
    assert "state.jobVerificationsInProgress.has(jobId)" in database_js
    assert "setBusy(button, true, t('db_busy_verifying_backup', 'Verifying backup…'))" in database_js
    assert "state.jobVerificationsInProgress.delete(jobId)" in database_js
    assert "loading: isVerifying" in database_js


def test_admin_backup_webdav_provider_translation_exists_in_every_locale():
    i18n_root = ROOT / "frontend" / "i18n"

    for admin_file in sorted(i18n_root.glob("*/admin.json")):
        payload = json.loads(admin_file.read_text(encoding="utf-8"))
        assert payload["db_destination_provider_webdav"] == "WebDAV", admin_file


def test_admin_backup_destination_uses_provider_fields_and_safe_secret_editing():
    admin_html = (ROOT / "frontend" / "admin.html").read_text(encoding="utf-8")
    database_js = (ROOT / "frontend" / "js" / "admin" / "database.js").read_text(encoding="utf-8")

    assert 'id="backupDestinationProviderFields"' in admin_html
    assert 'id="backupDestinationAdvancedPanel"' in admin_html
    assert 'id="backupDestinationConfigError"' in admin_html
    assert 'aria-live="assertive"' in admin_html
    assert "DESTINATION_PROVIDER_DEFINITIONS" in database_js
    assert "data-clear-secret-for" in database_js
    assert "config[field.key] = REDACTED_CONFIG_VALUE" in database_js
    assert "parseAdditionalConfig({ showError: true })" in database_js


def test_admin_backup_destination_translation_keys_exist_in_every_locale():
    required_keys = {
        "db_destination_details_title",
        "db_destination_provider_webdav_desc",
        "db_destination_field_bucket",
        "db_destination_field_password",
        "db_destination_secret_saved",
        "db_destination_secret_clear",
        "db_destination_advanced_title",
        "db_destination_config_invalid_position",
        "db_destination_config_duplicate_keys",
        "db_destination_review_errors",
    }

    for admin_file in sorted((ROOT / "frontend" / "i18n").glob("*/admin.json")):
        payload = json.loads(admin_file.read_text(encoding="utf-8"))
        missing = required_keys.difference(payload)
        assert not missing, f"{admin_file}: missing {sorted(missing)}"


def test_admin_backup_verification_loading_translation_exists_in_every_locale():
    i18n_root = ROOT / "frontend" / "i18n"

    for admin_file in sorted(i18n_root.glob("*/admin.json")):
        payload = json.loads(admin_file.read_text(encoding="utf-8"))
        assert payload["db_busy_verifying_backup"], admin_file
