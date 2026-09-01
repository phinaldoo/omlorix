import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_byok_stats_consent_documents_retention_and_redaction():
    byok_js = (ROOT / "frontend/js/chat/byok.js").read_text()

    assert "byok_stats_consent_desc" in byok_js
    assert "redacted provider errors" in byok_js
    assert "{ days: retentionDays }" in byok_js
    assert "byok_statistics_retention_days" in byok_js


def test_byok_retention_i18n_keys_exist_in_all_languages():
    required_index_keys = {
        "byok_stats_consent_title",
        "byok_stats_consent_desc",
        "byok_stats_consent_enable",
    }
    required_admin_keys = {
        "admin_user_settings_field_chat_byok_statistics_retention_days_label",
        "admin_user_settings_field_chat_byok_statistics_retention_days_desc",
    }

    for lang_dir in (ROOT / "frontend/i18n").iterdir():
        if not lang_dir.is_dir():
            continue
        index_payload = json.loads((lang_dir / "index.json").read_text())
        admin_payload = json.loads((lang_dir / "admin.json").read_text())
        assert required_index_keys <= set(index_payload), lang_dir.name
        assert required_admin_keys <= set(admin_payload), lang_dir.name
