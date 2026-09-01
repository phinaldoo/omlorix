import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
I18N_ROOT = REPO_ROOT / "frontend" / "i18n"
REFERENCE_LANGUAGE = "en"

# ``Intl.PluralRules`` can select categories that English never uses.  These
# categories are therefore legitimate additions to a locale file rather than
# stray keys.  Keep this dependency-free table aligned with the locales shipped
# in ``frontend/i18n`` and the cardinal categories exposed by the browser API.
PLURAL_CATEGORIES_BY_LANGUAGE: dict[str, frozenset[str]] = {
    "ar": frozenset({"zero", "one", "two", "few", "many", "other"}),
    "de": frozenset({"one", "other"}),
    "en": frozenset({"one", "other"}),
    "es": frozenset({"one", "many", "other"}),
    "fr": frozenset({"one", "many", "other"}),
    "hi": frozenset({"one", "other"}),
    "it": frozenset({"one", "many", "other"}),
    "ja": frozenset({"other"}),
    "pt": frozenset({"one", "many", "other"}),
    "ru": frozenset({"one", "few", "many", "other"}),
    "zh": frozenset({"other"}),
}


def discover_language_pages() -> dict[str, dict[str, Path]]:
    language_pages: dict[str, dict[str, Path]] = {}

    for language_dir in sorted(I18N_ROOT.iterdir()):
        if not language_dir.is_dir():
            continue

        page_map = {
            page_file.name: page_file
            for page_file in sorted(language_dir.glob("*.json"))
        }
        if page_map:
            language_pages[language_dir.name] = page_map

    if not language_pages:
        raise AssertionError(f"No translation files found in {I18N_ROOT}")

    if REFERENCE_LANGUAGE not in language_pages:
        raise AssertionError(
            f"Reference language '{REFERENCE_LANGUAGE}' was not found in {I18N_ROOT}"
        )

    return language_pages


def iter_reference_pages(
    language_pages: dict[str, dict[str, Path]],
    reference_language: str = REFERENCE_LANGUAGE,
) -> list[str]:
    return sorted(language_pages[reference_language])


def load_json(file_path: Path) -> Any:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def collect_key_paths(data: Any, prefix: str = "") -> set[str]:
    key_paths: set[str] = set()

    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            key_paths.add(path)
            key_paths.update(collect_key_paths(value, path))
        return key_paths

    if isinstance(data, list):
        list_prefix = f"{prefix}[]" if prefix else "[]"
        for item in data:
            key_paths.update(collect_key_paths(item, list_prefix))
        return key_paths

    return key_paths


def split_plural_key(key: str) -> tuple[str, str] | None:
    """Return a translation key's base and CLDR category, when present."""

    for category in ("zero", "one", "two", "few", "many", "other"):
        suffix = f"_{category}"
        if key.endswith(suffix):
            return key[: -len(suffix)], category
    return None


def is_locale_plural_variant(
    key: str,
    language: str,
    reference_keys: set[str],
) -> bool:
    """Check whether an extra key is a valid locale-specific plural form.

    A suffix alone is not enough: keys such as ``error_too_many`` are ordinary
    messages.  Requiring the English ``_one``/``_other`` pair establishes that
    the base is an actual plural family before a locale may add its categories.
    """

    plural_key = split_plural_key(key)
    if plural_key is None:
        return False

    base_key, category = plural_key
    base_language = language.lower().replace("_", "-").split("-", 1)[0]
    locale_categories = PLURAL_CATEGORIES_BY_LANGUAGE.get(base_language, frozenset())
    if category not in locale_categories:
        return False

    return (
        f"{base_key}_one" in reference_keys
        and f"{base_key}_other" in reference_keys
    )


def build_page_presence_report(
    language_pages: dict[str, dict[str, Path]],
    reference_language: str = REFERENCE_LANGUAGE,
) -> str | None:
    reference_pages = set(language_pages[reference_language])
    missing_pages: dict[str, list[str]] = defaultdict(list)
    unexpected_pages: dict[str, list[str]] = defaultdict(list)

    for language, page_map in sorted(language_pages.items()):
        page_names = set(page_map)

        for page_name in sorted(reference_pages - page_names):
            missing_pages[page_name].append(language)

        for page_name in sorted(page_names - reference_pages):
            unexpected_pages[page_name].append(language)

    if not missing_pages and not unexpected_pages:
        return None

    lines: list[str] = []
    if missing_pages:
        lines.append("Missing translation page files:")
        for page_name in sorted(missing_pages):
            lines.append(f"- {page_name}: {', '.join(sorted(missing_pages[page_name]))}")

    if unexpected_pages:
        if lines:
            lines.append("")
        lines.append("Unexpected translation page files:")
        for page_name in sorted(unexpected_pages):
            lines.append(f"- {page_name}: {', '.join(sorted(unexpected_pages[page_name]))}")

    return "\n".join(lines)


def build_page_key_details(
    language_pages: dict[str, dict[str, Path]],
    page_name: str,
    reference_language: str = REFERENCE_LANGUAGE,
) -> dict[str, dict[str, list[str]]]:
    reference_pages = language_pages[reference_language]
    reference_path = reference_pages[page_name]
    reference_keys = collect_key_paths(load_json(reference_path))

    missing: dict[str, list[str]] = {}
    extra: dict[str, list[str]] = {}

    for language, page_map in sorted(language_pages.items()):
        page_path = page_map.get(page_name)
        if page_path is None:
            continue

        language_keys = collect_key_paths(load_json(page_path))
        missing_keys = sorted(reference_keys - language_keys)
        # Locale files may add plural categories that the English reference can
        # never select.  Exclude only verified members of an English plural
        # family, while continuing to report every unrelated extra key.
        extra_keys = sorted(
            key
            for key in language_keys - reference_keys
            if not is_locale_plural_variant(key, language, reference_keys)
        )

        if missing_keys:
            missing[language] = missing_keys
        if extra_keys:
            extra[language] = extra_keys

    return {"missing": missing, "extra": extra}


def build_page_key_summary(
    language_pages: dict[str, dict[str, Path]],
    page_name: str,
    reference_language: str = REFERENCE_LANGUAGE,
) -> str | None:
    details = build_page_key_details(language_pages, page_name, reference_language)
    missing = details["missing"]
    extra = details["extra"]

    if not missing and not extra:
        return None

    lines = [f"Page: {page_name}"]
    for language in sorted(missing):
        lines.append(f"- {language} missing {len(missing[language])} keys")
    for language in sorted(extra):
        lines.append(f"- {language} has {len(extra[language])} extra keys")
    return "\n".join(lines)


def build_full_report(
    language_pages: dict[str, dict[str, Path]],
    reference_language: str = REFERENCE_LANGUAGE,
) -> str:
    sections: list[str] = []

    page_presence_report = build_page_presence_report(language_pages, reference_language)
    if page_presence_report:
        sections.append(page_presence_report)

    for page_name in iter_reference_pages(language_pages, reference_language):
        details = build_page_key_details(language_pages, page_name, reference_language)
        missing = details["missing"]
        extra = details["extra"]
        if not missing and not extra:
            continue

        lines = [f"Page: {page_name}"]

        for language in sorted(missing):
            lines.append(f"- {language} missing {len(missing[language])} keys:")
            lines.extend(f"  - {key}" for key in missing[language])

        for language in sorted(extra):
            lines.append(f"- {language} has {len(extra[language])} extra keys:")
            lines.extend(f"  - {key}" for key in extra[language])

        sections.append("\n".join(lines))

    if not sections:
        return (
            f"All translation files match `{reference_language}`. "
            f"Checked {len(language_pages)} languages across {len(iter_reference_pages(language_pages, reference_language))} pages."
        )

    return "\n\n".join(sections)


def write_report(output_path: Path, report: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that every translation JSON file has the same keys as the reference language."
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only per-page summary counts instead of every missing key.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "temp" / "check_translation_keys.txt",
        help="Write the report to this path (default: tests/check_translation_keys.txt).",
    )
    args = parser.parse_args()

    language_pages = discover_language_pages()
    page_presence_report = build_page_presence_report(language_pages)
    page_summaries = [
        build_page_key_summary(language_pages, page_name)
        for page_name in iter_reference_pages(language_pages)
    ]
    page_summaries = [summary for summary in page_summaries if summary]

    if page_presence_report or page_summaries:
        if args.summary_only:
            sections = []
            if page_presence_report:
                sections.append(page_presence_report)
            sections.extend(page_summaries)
            report = "\n\n".join(sections)
            print(report)
        else:
            report = build_full_report(language_pages)
            print(report)
        write_report(args.output, report)
        return 1

    report = build_full_report(language_pages)
    print(report)
    write_report(args.output, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
