from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend"
I18N_ROOT = FRONTEND_ROOT / "i18n"
REFERENCE_LANGUAGE = "en"
TRANSLATION_CALL_RE = re.compile(
    r"""(?<![\w$])(?:window\.)?(?:getTranslation|formatTranslation|[A-Za-z0-9_]*Translate|translate[A-Za-z0-9_]*|adminT|helperT|formatT|tf|t)\s*\(\s*(['"])(.*?)\1""",
    re.DOTALL,
)
STRING_LITERAL_RE = re.compile(
    r"""
    "((?:\\.|[^"\\\n])*)"
    |
    '((?:\\.|[^'\\\n])*)'
    """,
    re.VERBOSE,
)
# JavaScript template literals can contain nested single- and double-quoted
# translation calls. The broader string-literal expression above deliberately
# does not parse JavaScript, so it can treat an HTML attribute quote inside a
# template literal as the start of one large string and miss the nested key.
# This quote-bounded expression provides a second pass for stable keys passed
# to custom translation helpers inside those templates.
QUOTED_KEY_TOKEN_RE = re.compile(r"(?P<quote>['\"])(?P<key>[A-Za-z0-9_.:-]+)(?P=quote)")
# Translation keys embedded in data-i18n attributes inside JavaScript template
# literals are not standalone JavaScript strings. Match only the known HTML
# translation attributes so comments, route slugs, and CSS identifiers cannot
# make a retired key look live.
EMBEDDED_I18N_KEY_ATTRIBUTE_RE = re.compile(
    r"""\b(?:data-i18n|data-translate-key)\s*=\s*(?P<quote>['\"])(?P<key>[^'\"<>\s]+)(?P=quote)"""
)
EMBEDDED_I18N_ATTR_ATTRIBUTE_RE = re.compile(
    r"""\bdata-i18n-attr\s*=\s*(?P<quote>['\"])(?P<spec>[^'\"]+)(?P=quote)"""
)
DOM_I18N_SET_ATTRIBUTE_RE = re.compile(
    r"""\.setAttribute\(\s*['\"](?P<attribute>data-i18n|data-translate-key|data-i18n-attr)['\"]\s*,\s*['\"](?P<value>[^'\"]+)['\"]"""
)
PLURAL_KEY_SUFFIXES = frozenset({"zero", "one", "two", "few", "many", "other"})
DYNAMIC_KEY_PREFIX_RE = re.compile(
    r"""
    \bf(?:r|u|b|br|rb)?(['"])([^'"\n{}]*?)\{
    |
    `([^`\n$]*?)\$\{
    """,
    re.IGNORECASE | re.VERBOSE,
)
TRANSLATION_KEY_PROPERTY_RE = re.compile(
    r"""
    \b
    (?:
        labelKey|descriptionKey|titleKey|subtitleKey|messageKey|errorKey|emptyKey|ariaKey|
        placeholderKey|singularKey|pluralKey|ctaKey|buttonLabelKey|buttonAriaKey|
        label_key|description_key|title_key|subtitle_key|message_key|error_key|aria_key
    )
    \s*:\s*(['"])(.*?)\1
    """,
    re.VERBOSE | re.DOTALL,
)
SCHEMA_I18N_FIELD_RE = re.compile(
    r"""
    (?:
        (?:(['"])(?:i18n_label|i18n_description|i18n_placeholder|i18n_title)\1\s*:)
        |
        (?:\b(?:i18n_label|i18n_description|i18n_placeholder|i18n_title)\s*=)
    )
    \s*(['"])(.*?)\2
    """,
    re.VERBOSE | re.DOTALL,
)
SCHEMA_BACKEND_FALLBACK_KEY_RE = re.compile(r"""(['"])(schema_backend_[^'"]+)\1""")


class TranslationHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page_key: str | None = None
        self.script_sources: list[str] = []
        self.translation_keys: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs)

    def _handle_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value for key, value in attrs}
        if tag == "body" and not self.page_key:
            body_page_key = attr_map.get("data-page")
            if body_page_key:
                self.page_key = body_page_key

        for attr_name in ("data-i18n", "data-translate-key"):
            value = attr_map.get(attr_name)
            if value:
                self.translation_keys.add(value)

        attr_spec = attr_map.get("data-i18n-attr")
        if attr_spec:
            self.translation_keys.update(parse_i18n_attr_spec(attr_spec))

        if tag == "script":
            script_source = attr_map.get("src")
            if script_source:
                self.script_sources.append(script_source)


def parse_i18n_attr_spec(spec: str) -> set[str]:
    keys: set[str] = set()
    for pair in spec.split(";"):
        if ":" not in pair:
            continue
        _, key = pair.split(":", 1)
        key = key.strip()
        if key:
            keys.add(key)
    return keys


def discover_translation_files(i18n_root: Path) -> dict[str, dict[str, Path]]:
    language_pages: dict[str, dict[str, Path]] = {}
    for language_dir in sorted(i18n_root.iterdir()):
        if not language_dir.is_dir():
            continue
        page_map = {page_file.stem: page_file for page_file in sorted(language_dir.glob("*.json"))}
        if page_map:
            language_pages[language_dir.name] = page_map

    if not language_pages:
        raise AssertionError(f"No translation files found in {i18n_root}")

    if REFERENCE_LANGUAGE not in language_pages:
        raise AssertionError(
            f"Reference language '{REFERENCE_LANGUAGE}' was not found in {i18n_root}"
        )

    return language_pages


def load_translation_keys(file_path: Path) -> set[str]:
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AssertionError(f"Translation file must contain a JSON object: {file_path}")
    return set(data)


def load_reference_key_sets(
    language_pages: dict[str, dict[str, Path]],
    reference_language: str = REFERENCE_LANGUAGE,
) -> dict[str, set[str]]:
    return {
        dictionary_name: load_translation_keys(file_path)
        for dictionary_name, file_path in sorted(language_pages[reference_language].items())
    }


def load_candidate_key_sets(
    language_pages: dict[str, dict[str, Path]],
) -> dict[str, set[str]]:
    """Return every declared key per dictionary across all supported locales.

    Locale files can legitimately contain extra plural categories such as
    ``few`` or ``many`` that English never selects. Using only the English
    dictionary as the candidate set would incorrectly classify those runtime
    variants as unused.
    """
    candidate_key_sets: dict[str, set[str]] = defaultdict(set)
    for page_map in language_pages.values():
        for dictionary_name, file_path in page_map.items():
            candidate_key_sets[dictionary_name].update(load_translation_keys(file_path))
    return dict(candidate_key_sets)


def dictionary_names_for_page(page_key: str) -> list[str]:
    if page_key == "admin":
        return ["index", "admin", "admin_chats", "server_setup"]
    if page_key == "index":
        return ["password-requirements", "index", "server_setup"]
    if page_key == "login":
        return ["password-requirements", "login"]
    if page_key in {"privacy", "terms"}:
        return ["legal", page_key]
    if page_key == "legal":
        return ["legal", "privacy", "terms"]
    return [page_key]


def resolve_local_script(frontend_root: Path, html_path: Path, script_source: str) -> Path | None:
    source = script_source.split("?", 1)[0].split("#", 1)[0].strip()
    if not source:
        return None
    if "://" in source or source.startswith("//") or source.startswith("data:"):
        return None

    if source.startswith("/"):
        candidate = frontend_root / source.lstrip("/")
    else:
        candidate = (html_path.parent / source).resolve()

    if not candidate.exists() or not candidate.is_file():
        return None

    return candidate


def parse_html_metadata(html_path: Path) -> tuple[str, set[str], list[str]]:
    parser = TranslationHtmlParser()
    content = html_path.read_text(encoding="utf-8")
    parser.feed(content)
    page_key = parser.page_key or html_path.stem
    return page_key, parser.translation_keys, parser.script_sources


def extract_translation_call_keys(text: str) -> set[str]:
    return {match.group(2) for match in TRANSLATION_CALL_RE.finditer(text) if match.group(2)}


def extract_translation_key_properties(text: str) -> set[str]:
    """Return translation keys stored in metadata fields translated by shared UI helpers."""
    return {
        match.group(2)
        for match in TRANSLATION_KEY_PROPERTY_RE.finditer(text)
        if match.group(2)
    }


def extract_string_literal_keys(text: str, candidate_keys: set[str]) -> set[str]:
    """Return string literals that exactly match known translation keys."""
    keys: set[str] = set()
    for match in STRING_LITERAL_RE.finditer(text):
        literal = match.group(1) if match.group(1) is not None else match.group(2)
        if literal in candidate_keys:
            keys.add(literal)
    return keys


def extract_quoted_key_tokens(text: str, candidate_keys: set[str]) -> set[str]:
    """Find exact quoted key tokens, including calls nested in template literals."""
    return {
        match.group("key")
        for match in QUOTED_KEY_TOKEN_RE.finditer(text)
        if match.group("key") in candidate_keys
    }


def extract_embedded_html_translation_keys(text: str, candidate_keys: set[str]) -> set[str]:
    """Find translation attributes in generated HTML and explicit DOM calls."""
    keys = {
        match.group("key")
        for match in EMBEDDED_I18N_KEY_ATTRIBUTE_RE.finditer(text)
    }
    for match in EMBEDDED_I18N_ATTR_ATTRIBUTE_RE.finditer(text):
        keys.update(parse_i18n_attr_spec(match.group("spec")))
    for match in DOM_I18N_SET_ATTRIBUTE_RE.finditer(text):
        value = match.group("value")
        if match.group("attribute") == "data-i18n-attr":
            keys.update(parse_i18n_attr_spec(value))
        else:
            keys.add(value)
    return keys & candidate_keys


def build_plural_key_families(candidate_keys: set[str]) -> dict[str, set[str]]:
    """Map runtime plural base keys to every explicit locale-specific variant."""
    families: dict[str, set[str]] = defaultdict(set)
    for key in candidate_keys:
        base, separator, suffix = key.rpartition("_")
        if separator and base and suffix in PLURAL_KEY_SUFFIXES:
            families[base].add(key)
    return families


def extract_dynamic_key_prefixes(text: str, candidate_keys: set[str]) -> set[str]:
    """Return dynamic string prefixes that match known translation key families."""
    prefixes: set[str] = set()
    for match in DYNAMIC_KEY_PREFIX_RE.finditer(text):
        prefix = match.group(2) if match.group(2) is not None else match.group(3)
        if not prefix or "_" not in prefix:
            continue
        if any(key.startswith(prefix) for key in candidate_keys):
            prefixes.add(prefix)
    return prefixes


def extract_schema_i18n_keys(text: str) -> set[str]:
    keys = {match.group(3) for match in SCHEMA_I18N_FIELD_RE.finditer(text) if match.group(3)}
    keys.update(
        match.group(2)
        for match in SCHEMA_BACKEND_FALLBACK_KEY_RE.finditer(text)
        if match.group(2)
    )
    return keys


def extract_used_keys_from_file(file_path: Path) -> set[str]:
    content = file_path.read_text(encoding="utf-8")
    used_keys = extract_translation_call_keys(content)
    used_keys.update(extract_translation_key_properties(content))
    if file_path.suffix == ".html":
        parser = TranslationHtmlParser()
        parser.feed(content)
        used_keys.update(parser.translation_keys)
    return used_keys


def build_page_assets(frontend_root: Path) -> dict[str, set[Path]]:
    page_assets: dict[str, set[Path]] = defaultdict(set)

    for html_path in sorted(frontend_root.glob("*.html")):
        page_key, _, script_sources = parse_html_metadata(html_path)
        page_assets[page_key].add(html_path)

        for script_source in script_sources:
            local_script = resolve_local_script(frontend_root, html_path, script_source)
            if local_script is not None:
                page_assets[page_key].add(local_script)

    return page_assets


def build_used_keys_by_page(page_assets: dict[str, set[Path]]) -> dict[str, set[str]]:
    used_keys_by_page: dict[str, set[str]] = {}
    extraction_cache: dict[Path, set[str]] = {}

    for page_key, asset_paths in page_assets.items():
        used_keys: set[str] = set()
        for asset_path in sorted(asset_paths):
            if asset_path not in extraction_cache:
                extraction_cache[asset_path] = extract_used_keys_from_file(asset_path)
            used_keys.update(extraction_cache[asset_path])
        used_keys_by_page[page_key] = used_keys

    return used_keys_by_page


def iter_schema_source_files(repo_root: Path) -> list[Path]:
    roots = [
        repo_root / "backend" / "app",
        repo_root / "frontend" / "js",
    ]
    suffixes = {".js", ".py"}
    source_files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for file_path in root.rglob("*"):
            if not file_path.is_file() or file_path.suffix not in suffixes:
                continue
            if file_path.name.endswith(".test.js"):
                continue
            source_files.append(file_path)
    return sorted(source_files)


def build_schema_i18n_used_keys(repo_root: Path) -> set[str]:
    used_keys: set[str] = set()
    for file_path in iter_schema_source_files(repo_root):
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        used_keys.update(extract_schema_i18n_keys(content))
    return used_keys


def iter_translation_usage_source_files(repo_root: Path) -> list[Path]:
    """Return app source files that can contain frontend or backend translation key literals."""
    roots = [
        repo_root / "backend" / "app",
        repo_root / "frontend",
    ]
    suffixes = {".html", ".js", ".py"}
    excluded_parts = {
        "i18n",
        "frontend_dist",
        "node_modules",
        "__pycache__",
        "prism",
        "markdown",
        "katex",
    }
    source_files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for file_path in root.rglob("*"):
            if not file_path.is_file() or file_path.suffix not in suffixes:
                continue
            if file_path.name.endswith(".test.js"):
                # Tests may mention retired keys in negative assertions. Only
                # application source should keep a translation alive.
                continue
            relative_parts = set(file_path.relative_to(repo_root).parts)
            if relative_parts & excluded_parts:
                continue
            source_files.append(file_path)
    return sorted(source_files)


def build_literal_used_keys(
    repo_root: Path,
    reference_key_sets: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Find translation-key literals in app source and credit each matching dictionary."""
    key_to_dictionaries: dict[str, set[str]] = defaultdict(set)
    for dictionary_name, keys in reference_key_sets.items():
        for key in keys:
            key_to_dictionaries[key].add(dictionary_name)

    candidate_keys = {
        key
        for key, dictionary_names in key_to_dictionaries.items()
        if dictionary_names and ("_" in key or "." in key)
    }
    used_keys_by_dictionary: dict[str, set[str]] = defaultdict(set)
    if not candidate_keys:
        return used_keys_by_dictionary

    plural_key_families = build_plural_key_families(candidate_keys)
    plural_base_keys = set(plural_key_families)

    for file_path in iter_translation_usage_source_files(repo_root):
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        literal_keys = extract_string_literal_keys(content, candidate_keys)
        literal_keys.update(extract_quoted_key_tokens(content, candidate_keys))
        literal_keys.update(extract_embedded_html_translation_keys(content, candidate_keys))
        for key in literal_keys:
            for dictionary_name in key_to_dictionaries[key]:
                used_keys_by_dictionary[dictionary_name].add(key)

            # Helpers that receive explicit ``_one`` and ``_other`` keys can
            # derive ``_few``/``_many`` at runtime. Once any member is used,
            # retain all categories declared for the same locale family.
            base_key, separator, suffix = key.rpartition("_")
            if separator and suffix in PLURAL_KEY_SUFFIXES:
                for family_key in plural_key_families.get(base_key, set()):
                    for dictionary_name in key_to_dictionaries[family_key]:
                        used_keys_by_dictionary[dictionary_name].add(family_key)

        # Plural helpers receive a stable base key and append the category
        # chosen by Intl.PluralRules at runtime. Preserve every declared form
        # for that base because languages can select zero/two/few/many even
        # when English exercises only one/other.
        for base_key in extract_quoted_key_tokens(content, plural_base_keys):
            for key in plural_key_families[base_key]:
                for dictionary_name in key_to_dictionaries[key]:
                    used_keys_by_dictionary[dictionary_name].add(key)
        for prefix in extract_dynamic_key_prefixes(content, candidate_keys):
            for key in candidate_keys:
                if not key.startswith(prefix):
                    continue
                for dictionary_name in key_to_dictionaries[key]:
                    used_keys_by_dictionary[dictionary_name].add(key)

    return used_keys_by_dictionary


def build_effective_used_keys_by_dictionary(
    used_keys_by_page: dict[str, set[str]],
    reference_key_sets: dict[str, set[str]],
    schema_i18n_used_keys: set[str] | None = None,
    literal_used_keys_by_dictionary: dict[str, set[str]] | None = None,
) -> dict[str, set[str]]:
    used_keys_by_dictionary: dict[str, set[str]] = defaultdict(set)

    for page_key, page_used_keys in used_keys_by_page.items():
        for dictionary_name in dictionary_names_for_page(page_key):
            if dictionary_name not in reference_key_sets:
                continue

            dictionary_keys = reference_key_sets[dictionary_name]
            used_keys_by_dictionary[dictionary_name].update(page_used_keys & dictionary_keys)

    if schema_i18n_used_keys:
        for dictionary_name, reference_keys in reference_key_sets.items():
            used_keys_by_dictionary[dictionary_name].update(schema_i18n_used_keys & reference_keys)

    if literal_used_keys_by_dictionary:
        for dictionary_name, keys in literal_used_keys_by_dictionary.items():
            used_keys_by_dictionary[dictionary_name].update(keys)

    return used_keys_by_dictionary


def find_unused_translation_keys(
    repo_root: Path,
    reference_language: str = REFERENCE_LANGUAGE,
) -> dict[Path, list[str]]:
    frontend_root = repo_root / "frontend"
    i18n_root = frontend_root / "i18n"
    language_pages = discover_translation_files(i18n_root)
    # Validate that the requested reference language exists even though usage
    # discovery considers the union of keys from every locale. The union is
    # necessary for locale-specific plural categories.
    load_reference_key_sets(language_pages, reference_language)
    candidate_key_sets = load_candidate_key_sets(language_pages)
    page_assets = build_page_assets(frontend_root)
    used_keys_by_page = build_used_keys_by_page(page_assets)
    schema_i18n_used_keys = build_schema_i18n_used_keys(repo_root)
    literal_used_keys_by_dictionary = build_literal_used_keys(
        repo_root,
        candidate_key_sets,
    )
    used_keys_by_dictionary = build_effective_used_keys_by_dictionary(
        used_keys_by_page,
        candidate_key_sets,
        schema_i18n_used_keys,
        literal_used_keys_by_dictionary,
    )

    unused_by_file: dict[Path, list[str]] = {}
    for _, page_map in sorted(language_pages.items()):
        for dictionary_name, translation_path in sorted(page_map.items()):
            translation_keys = load_translation_keys(translation_path)
            unused_keys = sorted(translation_keys - used_keys_by_dictionary.get(dictionary_name, set()))
            if unused_keys:
                unused_by_file[translation_path] = unused_keys

    return unused_by_file


def format_unused_report(repo_root: Path, unused_by_file: dict[Path, list[str]]) -> str:
    if not unused_by_file:
        return "No unused translation keys found.\n"

    key_count = sum(len(keys) for keys in unused_by_file.values())
    lines = [
        f"Found {key_count} unused translation keys across {len(unused_by_file)} translation files.",
    ]
    for translation_path, unused_keys in sorted(unused_by_file.items(), key=lambda item: str(item[0])):
        lines.append("")
        lines.append(str(translation_path.relative_to(repo_root)))
        lines.extend(f"- {key}" for key in unused_keys)
    return "\n".join(lines).rstrip() + "\n"


def write_report(output_path: Path, report: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find unused frontend translation keys and write them to a report file."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "temp" / "unused_translation_keys.txt",
        help="Write the report to this path (default: temp/unused_translation_keys.txt in the repo root).",
    )
    args = parser.parse_args()

    unused_by_file = find_unused_translation_keys(REPO_ROOT)
    report = format_unused_report(REPO_ROOT, unused_by_file)
    print(report, end="")
    write_report(args.output, report)
    return 1 if unused_by_file else 0


if __name__ == "__main__":
    sys.exit(main())
