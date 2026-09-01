import json
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BYOK_JS = (REPO_ROOT / "frontend" / "js" / "chat" / "byok.js").read_text(encoding="utf-8")
BYOK_CSS = (REPO_ROOT / "frontend" / "css" / "userSettings" / "byok.css").read_text(encoding="utf-8")
SHARED_MODAL_CSS = (
    REPO_ROOT / "frontend" / "css" / "common" / "searchModal.css"
).read_text(encoding="utf-8")
I18N_ROOT = REPO_ROOT / "frontend" / "i18n"


class _ElementAttributeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = {}
        self.dialogs = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if element_id := attributes.get("id"):
            self.elements[element_id] = (tag, attributes)
        if attributes.get("role") == "dialog":
            if labelled_by := attributes.get("aria-labelledby"):
                self.dialogs[labelled_by] = (tag, attributes)


ELEMENT_PARSER = _ElementAttributeParser()
ELEMENT_PARSER.feed(BYOK_JS)


def test_byok_editors_use_the_shared_accessible_modal_shell():
    """Keep both editors on the same header/body/footer and form pattern."""
    for overlay_id, title_id in (
        ("byokProviderOverlay", "byokProviderEditorTitle"),
        ("byokModelOverlay", "byokModelEditorTitle"),
    ):
        overlay_tag, overlay = ELEMENT_PARSER.elements[overlay_id]
        dialog_tag, dialog = ELEMENT_PARSER.dialogs[title_id]
        assert overlay_tag == "div"
        assert {"byok-modal-overlay", "shared-modal-overlay"}.issubset(
            overlay.get("class", "").split()
        )
        assert dialog_tag == "div"
        assert {"byok-modal", "shared-modal", "shared-modal--fixed"}.issubset(
            dialog.get("class", "").split()
        )
        assert dialog.get("aria-modal") == "true"
        assert dialog.get("tabindex") == "-1"

    provider_tag, provider_form = ELEMENT_PARSER.elements["byokProviderForm"]
    model_tag, model_form = ELEMENT_PARSER.elements["byokModelForm"]
    assert provider_tag == "form"
    assert model_tag == "form"
    assert "novalidate" in provider_form
    assert "novalidate" in model_form
    assert provider_form.get("autocomplete") == "off"
    assert 'id="byokProviderCancelButton"' in BYOK_JS
    assert 'id="byokModelCancelButton"' in BYOK_JS
    assert 'byokProviderResetButton' not in BYOK_JS
    assert 'byokModelResetButton' not in BYOK_JS
    assert 'class="mcp-modal-overlay" id="byok' not in BYOK_JS


def test_byok_modals_keep_required_interaction_and_responsive_guards():
    """Protect focus, background scrolling, custom selects, and mobile layout."""
    assert "document.addEventListener('keydown', trapByokModalFocus)" in BYOK_JS
    assert "document.body.classList.toggle('byok-modal-open'" in BYOK_JS
    assert "enhanceByokSelect(providerTypeSelect, 'byok_provider_type')" in BYOK_JS
    assert "reportByokControlError(" in BYOK_JS
    assert "body.byok-modal-open" in SHARED_MODAL_CSS
    assert ".shared-modal-footer" in SHARED_MODAL_CSS
    assert "max-height: min(82dvh, 720px)" in SHARED_MODAL_CSS


def test_byok_actions_and_provider_url_controls_keep_their_intended_order():
    """Keep card actions right-aligned and endpoint suggestions above the URL."""
    assert "grid-template-columns: 36px minmax(0, 1fr) auto" in BYOK_CSS
    assert "justify-self: end" in BYOK_CSS
    assert ".byok-control-wrap.byok-provider-url-stack" in BYOK_CSS
    assert "gap: 8px" in BYOK_CSS
    assert "controlWrap.insertBefore(enhancedSelect, input)" in BYOK_JS
    assert "select._singleSelect?.wrapper?.remove()" in BYOK_JS


def test_byok_provider_schema_has_a_defensive_client_boundary():
    """Prevent cached schemas from duplicating shared or administrative fields."""
    assert "BYOK_PROVIDER_SCHEMA_EXCLUDED_FIELDS" in BYOK_JS
    assert "BYOK_PROVIDER_SPECIFIC_EXCLUDED_FIELDS" in BYOK_JS
    assert "sanitizeProviderSchemaForByok(rawSchema, providerKey)" in BYOK_JS
    assert "BYOK_BASE_URL_SUGGESTIONS_KEY" in BYOK_JS
    assert "'microsoft_azure'," not in BYOK_JS[
        BYOK_JS.index("function syncProviderBaseUrlField"):
        BYOK_JS.index("function resetProviderEditor")
    ]


def test_byok_schema_toggles_are_centered_and_trailing_aligned():
    """Keep compact switches centered beside long copy and at the row edge."""
    assert "row.classList.add('byok-toggle-setting')" in BYOK_JS
    assert "controlWrap.classList.add('byok-toggle-control')" in BYOK_JS
    assert ".byok-control-wrap.byok-toggle-control" in BYOK_CSS
    assert "align-items: center" in BYOK_CSS
    assert "justify-content: flex-end" in BYOK_CSS
    assert ".byok-control-wrap.byok-toggle-control > .toggle-switch" in BYOK_CSS
    assert "align-self: center" in BYOK_CSS
    assert ".byok-setting-item.byok-toggle-setting .byok-toggle-control" in BYOK_CSS


def test_byok_provider_icons_are_seeded_from_the_selected_brand():
    """Keep every selectable provider mapped to a real preset logo."""
    expected_options = {
        "openai": "openai",
        "openrouter": "openrouter",
        "openai_responses": "openai",
        "openai_chat_completions": "openai",
        "microsoft_azure": "microsoft",
        "ollama": "ollama",
        "lmstudio": "lmstudio",
        "anthropic": "anthropic",
        "anthropic_base": "anthropic",
        "google_aistudio": "google_aistudio",
    }
    for provider, icon in expected_options.items():
        option = f"{{ value: '{provider}', label:"
        option_start = BYOK_JS.index(option)
        option_end = BYOK_JS.index("},", option_start)
        assert f"icon: '{icon}'" in BYOK_JS[option_start:option_end]

    assert "icon: resolveProviderIcon(providerKey, providerValues?.icon)" in BYOK_JS
    assert "const providerIcon = resolveProviderIcon(providerType" in BYOK_JS
    assert "syncValue(picker.getValue())" in BYOK_JS


def test_byok_exposes_lmstudio_with_its_required_endpoint_field():
    """Keep the dedicated LM Studio flow selectable and preserve its native root URL."""
    assert "{ value: 'lmstudio', label: 'LM Studio', icon: 'lmstudio' }" in BYOK_JS
    assert "const OPTIONAL_API_KEY_PROVIDERS = new Set(['ollama', 'lmstudio']);" in BYOK_JS

    base_url_visibility = BYOK_JS[
        BYOK_JS.index("function syncProviderBaseUrlField"):
        BYOK_JS.index("function resetProviderEditor")
    ]
    assert "'lmstudio'," in base_url_visibility


def test_byok_remote_model_select_uses_translated_search():
    """Keep long remote-model lists searchable in every supported language."""
    assert "enhanceByokSelect(select, key, options = {})" in BYOK_JS
    assert "enhanceByokSelect(document.getElementById('byokRemoteModelSelect')" in BYOK_JS
    assert "searchable: true" in BYOK_JS
    assert "byok_remote_search_placeholder" in BYOK_JS
    assert "byok_remote_search_empty" in BYOK_JS

    locale_files = sorted(I18N_ROOT.glob("*/index.json"))
    assert locale_files
    for locale_file in locale_files:
        translations = json.loads(locale_file.read_text(encoding="utf-8"))
        assert translations.get("byok_remote_search_placeholder"), locale_file
        assert translations.get("byok_remote_search_empty"), locale_file


def test_byok_lists_omit_redundant_counts_and_positive_status_metadata():
    """Keep list headers and configured-provider cards visually concise."""
    removed_keys = {
        "byok_provider_key_active",
        "byok_provider_default_endpoint",
        "byok_provider_saved_count_one",
        "byok_provider_saved_count_other",
        "byok_model_saved_count_one",
        "byok_model_saved_count_other",
    }

    assert 'id="byokProviderMeta"' not in BYOK_JS
    assert 'id="byokModelMeta"' not in BYOK_JS
    assert "const providerMeta = endpoint ? `${providerLabel} • ${endpoint}` : providerLabel" in BYOK_JS
    assert "hasKey || !requiresKey ? ''" in BYOK_JS
    assert "OPTIONAL_API_KEY_PROVIDERS.has(normalizeProviderType(provider.provider))" in BYOK_JS
    assert "byok_provider_key_required" in BYOK_JS
    assert all(key not in BYOK_JS for key in removed_keys)
    assert ".byok-section-count" not in BYOK_CSS

    locale_files = sorted(I18N_ROOT.glob("*/index.json"))
    assert locale_files
    for locale_file in locale_files:
        translations = json.loads(locale_file.read_text(encoding="utf-8"))
        assert removed_keys.isdisjoint(translations), locale_file
