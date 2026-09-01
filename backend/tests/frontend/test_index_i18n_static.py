import json
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FRONTEND_ROOT = ROOT / "frontend"
INDEX_HTML = FRONTEND_ROOT / "index.html"
I18N_ROOT = ROOT / "frontend" / "i18n"

# These are the exact merge rules used by language.js. The audit intentionally
# models runtime availability rather than accepting a key merely because it
# exists in an unrelated page dictionary.
PAGE_DICTIONARIES = {
    "admin": ("schema.json", "index.json", "admin.json", "admin_chats.json", "server_setup.json"),
    "canvas_share": ("canvas-share.json",),
    "chat_share": ("index.json", "chat-share.json"),
    "error": ("error.json",),
    "index": ("password-requirements.json", "schema.json", "index.json", "server_setup.json"),
    "leaderboard": ("index.json", "leaderboard.json"),
    "legal": ("legal.json", "privacy.json", "terms.json"),
    "login": ("password-requirements.json", "index.json", "login.json"),
    "server_setup": ("index.json", "server_setup.json"),
}

# A bare `t` is occasionally used as a local variable or callback rather than the
# translation helper. These tokens are therefore not translation keys.
NON_I18N_T_CALL_ARGUMENTS = {
    "create",
    "created",
    "delete",
    "download",
    "empty",
    "error",
    "expires",
    "finished",
    "load",
    "not",
    "queued",
    "queueing",
    "refreshed",
    "refreshing",
    "size",
    "status",
}

# These representative strings came from the copied-English locale block. They
# cover each affected index feature and must remain genuinely localized.
MUST_BE_LOCALIZED = {
    "color_label_amber",
    "common_continue",
    "notes_no_notes_subtitle",
    "notes_recording_details_ready",
    "preset_save_description",
    "slide_presentation_preparing",
    "split_screen_too_narrow_desc",
    "todos_accept_desc",
    "todos_create_list_desc_placeholder",
    "us_set_password_desc",
    "chat_send_failed_retry",
    "latex_pdf_compile_failed_desc",
    "us_rate_limits_empty_desc",
}

IDENTICAL_COPY_EXEMPT_KEYS = {
    # "Item" is the same word in Portuguese; the remainder is interpolation.
    "mcp_import_error_item",
}
IDENTICAL_COPY_EXEMPT_PATTERNS = (
    re.compile(r"^(?:https?|ldaps?)://"),
    re.compile(r"^/"),
    re.compile(r"^-----BEGIN "),
    re.compile(r"^\(&"),
    re.compile(r"^[A-Z][A-Za-z-]+(?:-[A-Za-z-]+)+$"),
    re.compile(r"^(?:ou|dc|cn|uid|member|objectClass)="),
)

# These categories mirror Intl.PluralRules(...).resolvedOptions().pluralCategories
# for Omlorix's supported locales. Plural helpers receive a base key and append
# one of these suffixes at runtime, so the audit must validate the generated key
# instead of incorrectly requiring the unused bare base key.
PLURAL_CATEGORIES_BY_LOCALE = {
    "ar": {"zero", "one", "two", "few", "many", "other"},
    "de": {"one", "other"},
    "en": {"one", "other"},
    "es": {"one", "many", "other"},
    "fr": {"one", "many", "other"},
    "hi": {"one", "other"},
    "it": {"one", "many", "other"},
    "ja": {"other"},
    "pt": {"one", "many", "other"},
    "ru": {"one", "few", "many", "other"},
    "zh": {"other"},
}

PLURAL_TRANSLATION_CALL_PATTERN = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9]*PluralT|pluralTf)"
    r"\s*\(\s*['\"]([a-z][a-z0-9_.-]+)['\"]"
)


class _StaticCopyAuditParser(HTMLParser):
    """Collect visible static copy that has no declarative translation hook."""

    TEXT_EXEMPTIONS = {
        "1.0x",
        "Artificial Analysis",
        "Omlorix",
        "Deutsch",
        "English",
        "Español",
        "Français",
        "GR",
        "Italiano",
        "PDF",
        "PPTX",
        "Português",
        "U",
        "US",
        "file.pdf",
    }
    ATTRIBUTE_EXEMPTION_PATTERNS = (
        re.compile(r"^https?://"),
        re.compile(r"^[A-Z0-9.+_-]+$"),
    )
    IGNORED_TAGS = {"script", "style", "svg", "title", "code", "pre"}
    USER_FACING_ATTRIBUTES = {"alt", "aria-label", "placeholder", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, object]] = []
        self.findings: list[tuple[int, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        translated_text = bool(attr_map.get("data-i18n") or attr_map.get("data-translate-key"))
        translated_attrs = {
            pair.partition(":")[0].strip()
            for pair in (attr_map.get("data-i18n-attr") or "").split(";")
            if pair.partition(":")[1]
        }
        self.stack.append({"tag": tag, "translated_text": translated_text})

        for name in self.USER_FACING_ATTRIBUTES:
            value = (attr_map.get(name) or "").strip()
            if not value or name in translated_attrs or not re.search(r"[A-Za-z]", value):
                continue
            if any(pattern.fullmatch(value) for pattern in self.ATTRIBUTE_EXEMPTION_PATTERNS):
                continue
            self.findings.append((self.getpos()[0], name, value))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text or not re.search(r"[A-Za-z]", text):
            return
        if any(frame["tag"] in self.IGNORED_TAGS for frame in self.stack):
            return
        if any(frame["translated_text"] for frame in self.stack):
            return
        if text in self.TEXT_EXEMPTIONS:
            return
        self.findings.append((self.getpos()[0], "text", text))


def _loaded_page_sources(page_name: str) -> dict[Path, str]:
    """Return one page and every non-vendor JavaScript file it loads."""

    html_path = FRONTEND_ROOT / f"{page_name}.html"
    html = html_path.read_text(encoding="utf-8")
    sources = {html_path: html}
    for source_url in re.findall(r'<script\s+[^>]*src="(/js/[^"?]+)', html):
        if "/vendor/" in source_url:
            continue
        source_path = ROOT / "frontend" / source_url.lstrip("/")
        sources[source_path] = source_path.read_text(encoding="utf-8")
    return sources


def _referenced_translation_keys(sources: dict[Path, str]) -> set[str]:
    """Extract direct declarative and JavaScript translation keys used by the page."""

    keys: set[str] = set()
    call_pattern = re.compile(
        r"(?:\bt|(?:getTranslation|formatTranslation|translateFn|"
        r"[A-Za-z][A-Za-z0-9]*(?:T|Tf|Translate|Translation)))"
        r"\s*\(\s*['\"]([a-z][a-z0-9_.-]+)['\"]"
    )
    text_attr_pattern = re.compile(r'data-(?:i18n|translate-key)=["\']([^"\']+)["\']')
    translated_attr_pattern = re.compile(r'data-i18n-attr=["\']([^"\']+)["\']')

    for source in sources.values():
        keys.update(
            key for key in text_attr_pattern.findall(source)
            if re.fullmatch(r"[a-z][a-z0-9_.-]+", key)
        )
        for spec in translated_attr_pattern.findall(source):
            for pair in spec.split(";"):
                _, separator, key = pair.partition(":")
                if separator and key.strip():
                    normalized_key = key.strip()
                    if re.fullmatch(r"[a-z][a-z0-9_.-]+", normalized_key):
                        keys.add(normalized_key)
        # Remove plural-helper invocations before collecting direct calls. A
        # plural base is not itself a runtime key, but the same literal may also
        # appear in a real t()/getTranslation() call elsewhere and must then
        # remain required.
        source_without_plural_calls = PLURAL_TRANSLATION_CALL_PATTERN.sub(
            "",
            source,
        )
        keys.update(call_pattern.findall(source_without_plural_calls))

    # A plural helper's literal argument is only a base used to construct keys
    # such as ``base_one`` and ``base_other``. The locale-aware audit below
    # checks those actual runtime keys separately.
    return keys - NON_I18N_T_CALL_ARGUMENTS


def _referenced_plural_translation_bases(sources: dict[Path, str]) -> set[str]:
    """Extract base keys passed to helpers that append an Intl plural suffix."""

    return {
        base_key
        for source in sources.values()
        for base_key in PLURAL_TRANSLATION_CALL_PATTERN.findall(source)
    }


def test_direct_translation_key_is_preserved_when_also_used_as_plural_base():
    """A direct key remains required even when a plural helper shares its base."""
    sources = {
        Path("fixture.js"): (
            "getTranslation('shared_count'); "
            "getPluralT('shared_count', 2);"
        )
    }

    assert _referenced_translation_keys(sources) == {"shared_count"}
    assert _referenced_plural_translation_bases(sources) == {"shared_count"}


def _page_translation_keys(page_name: str) -> set[str]:
    """Return every translation key reachable from the requested page."""

    return _referenced_translation_keys(_loaded_page_sources(page_name))


def _page_plural_translation_bases(page_name: str) -> set[str]:
    """Return plural base keys referenced by the requested page."""

    return _referenced_plural_translation_bases(_loaded_page_sources(page_name))


def _load_page_dictionary(locale_dir: Path, page_name: str) -> dict[str, str]:
    """Merge dictionaries in the same order as the page's runtime loader."""

    merged: dict[str, str] = {}
    for filename in PAGE_DICTIONARIES[page_name]:
        merged.update(json.loads((locale_dir / filename).read_text(encoding="utf-8")))
    return merged


def test_every_page_translation_reference_is_available_in_every_locale():
    """Prevent runtime fallbacks caused by keys missing from a page's dictionaries."""

    missing_by_page: dict[str, dict[str, list[str]]] = {}
    for page_name in PAGE_DICTIONARIES:
        referenced_keys = _page_translation_keys(page_name)
        plural_bases = _page_plural_translation_bases(page_name)
        assert referenced_keys or plural_bases, f"No {page_name} translation references were discovered"

        for locale_dir in sorted(path for path in I18N_ROOT.iterdir() if path.is_dir()):
            dictionary = _load_page_dictionary(locale_dir, page_name)
            plural_keys = {
                f"{base_key}_{category}"
                for base_key in plural_bases
                for category in PLURAL_CATEGORIES_BY_LOCALE[locale_dir.name]
            }
            runtime_keys = referenced_keys | plural_keys
            missing = sorted(key for key in runtime_keys if not dictionary.get(key))
            if missing:
                missing_by_page.setdefault(page_name, {})[locale_dir.name] = missing

    assert not missing_by_page, f"Page dictionaries are missing runtime keys: {missing_by_page}"


def test_non_english_index_locales_do_not_reuse_english_ui_copy():
    """Catch accidental English copies in representative index feature groups."""

    english = _load_page_dictionary(I18N_ROOT / "en", "index")
    for locale_dir in sorted(path for path in I18N_ROOT.iterdir() if path.is_dir() and path.name != "en"):
        dictionary = _load_page_dictionary(locale_dir, "index")
        untranslated = sorted(key for key in MUST_BE_LOCALIZED if dictionary.get(key) == english.get(key))
        assert not untranslated, f"{locale_dir.name} still uses English for: {untranslated}"


def test_non_english_locales_have_no_copied_english_sentences():
    """Reject multiword English sentences copied unchanged into other locales."""

    findings: dict[str, dict[str, list[str]]] = {}
    for english_path in sorted((I18N_ROOT / "en").glob("*.json")):
        english = json.loads(english_path.read_text(encoding="utf-8"))
        for locale_dir in sorted(path for path in I18N_ROOT.iterdir() if path.is_dir() and path.name != "en"):
            localized = json.loads((locale_dir / english_path.name).read_text(encoding="utf-8"))
            copied: list[str] = []
            for key, english_value in english.items():
                if key in IDENTICAL_COPY_EXEMPT_KEYS or not isinstance(english_value, str):
                    continue
                if localized.get(key) != english_value:
                    continue
                if len(re.findall(r"[A-Za-z]+", english_value)) < 4:
                    continue
                if any(pattern.search(english_value) for pattern in IDENTICAL_COPY_EXEMPT_PATTERNS):
                    continue
                copied.append(key)
            if copied:
                findings.setdefault(english_path.name, {})[locale_dir.name] = copied

    assert not findings, f"Copied English sentences remain: {findings}"


def test_every_page_static_copy_has_translation_hooks():
    """Require translation hooks for visible HTML text and accessibility copy."""

    findings: dict[str, list[tuple[int, str, str]]] = {}
    for page_name in PAGE_DICTIONARIES:
        parser = _StaticCopyAuditParser()
        parser.feed((FRONTEND_ROOT / f"{page_name}.html").read_text(encoding="utf-8"))
        if parser.findings:
            findings[page_name] = parser.findings

    assert not findings, f"Static page copy lacks translation hooks: {findings}"


def test_known_hardcoded_page_runtime_copy_is_removed():
    """Guard dynamic renderers and error paths fixed by the all-pages audit."""

    combined_source = "\n".join(
        source
        for page_name in PAGE_DICTIONARIES
        for source in _loaded_page_sources(page_name).values()
    )
    forbidden_fragments = {
        "showNotification('Failed to load todo lists'",
        "showNotification('Failed to load todos'",
        "showNotification('Failed to create todo'",
        "showNotification('List deleted successfully'",
        "notifyError(error.message || 'Failed to send message",
        'aria-label="Copy table markdown"',
        '<p class="notes-search-empty-title">No results found</p>',
        '<summary>Compile log</summary>',
        "throw new Error('Empty Mermaid render result')",
        "throw new Error('Mermaid sanitizer is unavailable.')",
        "throw new Error('Failed to fetch notifications')",
        "notifyError(`${field.label || field.key} must be a valid number.`)",
        "title.textContent = 'Sign In Unavailable'",
        "title: 'Mermaid Preview'",
        'aria-label="Table pagination"',
        'label: "Turn " +',
        'label: \'Logo\'',
        'label: \'Icon\'',
    }

    present = sorted(fragment for fragment in forbidden_fragments if fragment in combined_source)
    assert not present, f"Hardcoded index UI copy returned: {present}"
