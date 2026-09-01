from pathlib import Path
import json


REPO_ROOT = Path(__file__).resolve().parents[3]
BYOK_JS = REPO_ROOT / "frontend" / "js" / "chat" / "byok.js"
I18N_ROOT = REPO_ROOT / "frontend" / "i18n"

DISCLOSURE_KEYS = {
    "byok_transfer_disclosure",
    "byok_model_transfer_disclosure",
    "byok_provider_editor_disclosure",
    "byok_api_key_storage_disclosure",
    "byok_api_key_transfer_disclosure",
}
CREDENTIAL_DURATION_KEYS = {
    "byok_api_key_storage_disclosure",
    "byok_provider_api_key_desc_session",
}


def test_byok_disclosure_copy_is_translated_for_all_locales():
    locales = [path for path in I18N_ROOT.iterdir() if path.is_dir()]
    assert locales

    for locale in locales:
        payload = json.loads((locale / "index.json").read_text(encoding="utf-8"))
        missing = DISCLOSURE_KEYS.difference(payload)
        assert not missing, f"{locale.name} missing {sorted(missing)}"
        for key in DISCLOSURE_KEYS:
            assert str(payload[key]).strip(), f"{locale.name} has empty {key}"
        for key in CREDENTIAL_DURATION_KEYS:
            assert "30" in str(payload.get(key) or ""), f"{locale.name} must disclose the 30-day credential lifetime"


def test_byok_page_discloses_session_storage_and_external_transfer():
    """The editor must describe sealed-token storage and external transfer."""

    source = BYOK_JS.read_text(encoding="utf-8")

    for key in DISCLOSURE_KEYS:
        assert key in source

    assert "JSON.stringify(sanitizeDataForStorage(state.data))" in source
    assert "byok_provider_api_key_desc_session" in source
    assert "byok_provider_api_key_desc_local" not in source
    assert "sealed credential token in this tab for up to 30 days" in source
    assert "Only the encrypted token is stored in this tab for up to 30 days" in source
