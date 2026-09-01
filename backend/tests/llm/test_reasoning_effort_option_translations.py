import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.reasoning_effort_options import (  # noqa: E402
    REASONING_EFFORT_OPTION_I18N,
    build_reasoning_effort_options,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
ADMIN_RENDERING_DICTIONARIES = ("schema", "index", "admin", "admin_chats", "server_setup")


def load_translation_file(file_path: Path) -> dict:
    """Load one frontend translation JSON file."""
    return json.loads(file_path.read_text(encoding="utf-8"))


def load_merged_translation_files(locale_dir: Path, dictionary_names: tuple[str, ...]) -> dict:
    """Mirror the frontend dictionary merge used by language.js for rendered pages."""
    translations = {}
    for dictionary_name in dictionary_names:
        translations.update(load_translation_file(locale_dir / f"{dictionary_name}.json"))
    return translations


def test_reasoning_effort_options_have_stable_i18n_keys():
    """Ensure every supported reasoning effort value gets a stable translation key."""
    options = build_reasoning_effort_options(list(REASONING_EFFORT_OPTION_I18N))

    assert [option.value for option in options] == list(REASONING_EFFORT_OPTION_I18N)
    assert all(option.i18n_label for option in options)


def test_reasoning_effort_translation_keys_exist_in_every_rendering_locale():
    """Ensure admin and chat locale files can render every reasoning effort label."""
    expected_keys = {key for _, key in REASONING_EFFORT_OPTION_I18N.values()}
    i18n_root = REPO_ROOT / "frontend" / "i18n"

    for locale_dir in sorted(path for path in i18n_root.iterdir() if path.is_dir()):
        index_translations = load_translation_file(locale_dir / "index.json")
        missing_index_keys = sorted(expected_keys - set(index_translations))
        assert not missing_index_keys, f"{locale_dir / 'index.json'} is missing {missing_index_keys}"

        admin_translations = load_merged_translation_files(locale_dir, ADMIN_RENDERING_DICTIONARIES)
        missing_admin_keys = sorted(expected_keys - set(admin_translations))
        assert not missing_admin_keys, f"{locale_dir / 'admin.json'} render bundle is missing {missing_admin_keys}"
