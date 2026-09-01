import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BYOK_JS = REPO_ROOT / "frontend" / "js" / "chat" / "byok.js"
AUTH_JS = REPO_ROOT / "frontend" / "js" / "common" / "auth.js"


def _source() -> str:
    return BYOK_JS.read_text(encoding="utf-8")


def test_byok_storage_persists_only_server_sealed_credential_tokens():
    """Reload support must never put raw provider API keys in Web Storage."""

    source = _source()

    assert "SESSION_CREDENTIAL_TOKENS_KEY" in source
    assert "LEGACY_SESSION_SECRETS_KEY" in source
    assert "window.sessionStorage" in source
    assert "storage.setItem(SESSION_CREDENTIAL_TOKENS_KEY" in source
    assert "storage.setItem(LEGACY_SESSION_SECRETS_KEY" not in source
    assert "storage.removeItem(LEGACY_SESSION_SECRETS_KEY)" in source
    assert "storage.setItem(STORAGE_KEY, JSON.stringify(sanitizeDataForStorage(state.data)))" in source
    assert re.search(
        r"function sanitizeProviderForStorage\(provider\).*?delete sanitized\.api_key;",
        source,
        flags=re.DOTALL,
    )
    assert "issueProviderCredentialToken(payload.id, payload.provider, apiKey)" in source
    assert "credential_token: getProviderCredentialToken(provider)" in source
    assert "api_key: getProviderApiKey(provider)" not in source


def test_byok_editor_keeps_or_replaces_sealed_token_without_revealing_key():
    """Editing preserves its token without putting the raw key back in the DOM."""

    source = _source()

    assert "apiKey.value = provider.api_key" not in source
    assert "api_key: provider.api_key" not in source
    assert "credential_token: credentialToken || undefined" in source
    assert "byok_provider_api_key_placeholder_keep_session" in source
    assert "byok_api_key_storage_disclosure" in source
    assert "clearProviderSessionCredentials" in source


def test_logout_clears_the_reload_safe_byok_credential():
    """Account transitions must not inherit a previous user's sealed token."""

    auth_source = AUTH_JS.read_text(encoding="utf-8")

    assert "window.BYOK?.clearProviderSessionCredentials?.()" in auth_source


def test_byok_frontend_requires_anthropic_credentials_and_localizes_discovery_errors():
    """Discovery must render stable local copy instead of provider-authored English."""

    source = _source()

    assert "const OPTIONAL_API_KEY_PROVIDERS = new Set(['ollama', 'lmstudio']);" in source
    assert "byok_credential_unavailable: byokT(" in source
    assert "byok_provider_authentication_failed: byokT(" in source
    assert "byok_provider_configuration_invalid: byokT(" in source
    assert "byok_model_discovery_failed: byokT(" in source
    assert "String(url).includes('/api/v1/llm/models/byok')" in source
    assert "typeof detail.message === 'string' ? detail.message.trim() : ''" in source


def test_byok_provider_edit_subtitle_is_distinct_and_translated():
    """Editing an existing connection must not reuse creation guidance."""

    source = _source()
    assert "byok_provider_editor_subtitle_edit_session" in source

    i18n_root = BYOK_JS.parents[2] / "i18n"
    locale_files = sorted(i18n_root.glob("*/index.json"))
    assert locale_files
    for locale_file in locale_files:
        translations = json.loads(locale_file.read_text(encoding="utf-8"))
        assert translations.get("byok_provider_editor_subtitle_edit_session"), locale_file
