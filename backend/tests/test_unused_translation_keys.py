from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "dev_scripts" / "find_unused_translation_keys.py"
MODULE_SPEC = importlib.util.spec_from_file_location("find_unused_translation_keys", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
unused_translation_keys = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(unused_translation_keys)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class UnusedTranslationKeyTests:
    def test_find_unused_translation_keys_is_page_aware(self, tmp_path):
        repo_root = tmp_path / "repo"
        frontend_root = repo_root / "frontend"
        i18n_root = frontend_root / "i18n"
        js_root = frontend_root / "js"

        write_json(
            i18n_root / "en" / "index.json",
            {
                "idx_used": "Index used",
                "idx_shadowed": "Index shadowed",
                "idx_unused": "Index unused",
                "js_only_used": "JS only",
            },
        )
        write_json(
            i18n_root / "en" / "server_setup.json",
            {
                "idx_shadowed": "Server override",
                "ss_used": "Server used",
                "ss_unused": "Server unused",
            },
        )
        write_json(
            i18n_root / "en" / "admin.json",
            {
                "admin_used": "Admin used",
                "admin_schema_used": "Admin schema used",
                "admin_unused": "Admin unused",
            },
        )
        write_json(
            i18n_root / "en" / "admin_chats.json",
            {
                "admin_chat_used": "Admin chats used",
            },
        )
        write_json(
            i18n_root / "en" / "legal.json",
            {
                "legal_used": "Legal used",
                "legal_shadow": "Legal shadowed",
                "legal_unused": "Legal unused",
            },
        )
        write_json(
            i18n_root / "en" / "privacy.json",
            {
                "legal_shadow": "Privacy override",
                "privacy_used": "Privacy used",
            },
        )
        write_json(
            i18n_root / "en" / "login.json",
            {
                "login_used": "Login used",
                "login_reset_used": "Reset used",
                "login_literal_used": "Literal used",
                "login_helper_used": "Helper used",
                "login_config_used": "Config used",
                "login_dynamic_used_alpha": "Dynamic alpha",
                "login_dynamic_used_beta": "Dynamic beta",
                "login_nested_template_used": "Nested template used",
                "login_template_attribute_used": "Template attribute used",
                "login_template_placeholder_used": "Template placeholder used",
                "login_dom_aria_used": "DOM aria label used",
                "login_dom_title_used": "DOM title used",
                "login_result_count_one": "One result",
                "login_result_count_other": "Results",
                "login_upload_one": "One upload",
                "login_upload_other": "Uploads",
                "login_test_only": "Mentioned only by a test",
                "login_hyphen_segment_only": "Embedded in a longer identifier",
                "login_comment_only": "Mentioned only by a comment",
                "title": "Title",
                "login_unused": "Login unused",
            },
        )
        write_json(
            i18n_root / "de" / "login.json",
            {
                "login_used": "Anmeldung genutzt",
                "login_reset_used": "Reset genutzt",
                "login_literal_used": "Literal genutzt",
                "login_helper_used": "Helper genutzt",
                "login_config_used": "Konfiguration genutzt",
                "login_dynamic_used_alpha": "Dynamisch alpha",
                "login_dynamic_used_beta": "Dynamisch beta",
                "login_nested_template_used": "Verschachtelte Vorlage genutzt",
                "login_template_attribute_used": "Vorlagenattribut genutzt",
                "login_template_placeholder_used": "Vorlagenplatzhalter genutzt",
                "login_dom_aria_used": "DOM-ARIA-Beschriftung genutzt",
                "login_dom_title_used": "DOM-Titel genutzt",
                "login_result_count_one": "Ein Ergebnis",
                "login_result_count_few": "Einige Ergebnisse",
                "login_result_count_other": "Ergebnisse",
                "login_upload_one": "Ein Upload",
                "login_upload_few": "Einige Uploads",
                "login_upload_other": "Uploads",
                "login_test_only": "Nur in einem Test erwähnt",
                "login_hyphen_segment_only": "In einer längeren Kennung enthalten",
                "login_comment_only": "Nur in einem Kommentar erwähnt",
                "title": "Titel",
                "login_unused": "Anmeldung ungenutzt",
            },
        )

        (frontend_root / "index.html").write_text(
            """<!DOCTYPE html>
<html>
  <body data-page="index">
    <div data-i18n="idx_used"></div>
    <div data-i18n="idx_shadowed"></div>
    <div data-translate-key="ss_used"></div>
    <script src="/js/index.js"></script>
  </body>
</html>
""",
            encoding="utf-8",
        )
        (frontend_root / "admin.html").write_text(
            """<!DOCTYPE html>
<html>
  <body data-page="admin">
    <div data-i18n="admin_used"></div>
    <div data-i18n="admin_chat_used"></div>
  </body>
</html>
""",
            encoding="utf-8",
        )
        (frontend_root / "privacy.html").write_text(
            """<!DOCTYPE html>
<html>
  <body data-page="privacy">
    <div data-i18n="legal_used"></div>
    <div data-i18n="legal_shadow"></div>
    <div data-i18n="privacy_used"></div>
  </body>
</html>
""",
            encoding="utf-8",
        )
        (frontend_root / "login.html").write_text(
            """<!DOCTYPE html>
<html>
  <body data-page="login">
    <input data-i18n-attr="placeholder:login_used" />
    <script src="/js/login.js"></script>
  </body>
</html>
""",
            encoding="utf-8",
        )
        (frontend_root / "reset_password.html").write_text(
            """<!DOCTYPE html>
<html>
  <body data-page="login">
    <div data-i18n="login_reset_used"></div>
    <script src="/js/login.js"></script>
  </body>
</html>
""",
            encoding="utf-8",
        )

        js_root.mkdir(parents=True, exist_ok=True)
        (js_root / "index.js").write_text(
            'window.getTranslation("js_only_used", "fallback");\n',
            encoding="utf-8",
        )
        (js_root / "login.js").write_text(
            """const title = "title";
function t(key, fallback) { return fallback || key; }
t("login_reset_used", "Reset used");
loginTranslate("login_helper_used", "Helper used");
const menu = { labelKey: "login_config_used" };
const backendDetailKey = "login_literal_used";
const dynamicKey = `login_dynamic_used_${suffix}`;
const modal = `<button title="${customT('login_nested_template_used', 'Used')}" data-i18n="login_template_attribute_used" data-i18n-attr="placeholder:login_template_placeholder_used">`;
const pluralKey = (category) => `${baseKey}_${category}`;
pluralKey('login_result_count');
customPlural('login_upload_one', 'login_upload_other');
const route = "/something-login_hyphen_segment_only-extra";
// TODO: remove login_comment_only after the migration.
element.setAttribute('data-i18n-attr', 'aria-label:login_dom_aria_used;title:login_dom_title_used');
""",
            encoding="utf-8",
        )
        (js_root / "login.test.js").write_text(
            "assert.doesNotMatch(source, /login_test_only/);\n",
            encoding="utf-8",
        )
        (repo_root / "backend" / "app" / "admin" / "schema.py").parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        (repo_root / "backend" / "app" / "admin" / "schema.py").write_text(
            'FIELD = {"i18n_label": "admin_schema_used"}\n',
            encoding="utf-8",
        )

        unused_by_file = unused_translation_keys.find_unused_translation_keys(repo_root)
        formatted = {
            str(path.relative_to(repo_root)): keys for path, keys in sorted(unused_by_file.items())
        }

        assert formatted == {
            "frontend/i18n/de/login.json": [
                "login_comment_only",
                "login_hyphen_segment_only",
                "login_test_only",
                "login_unused",
                "title",
            ],
            "frontend/i18n/en/admin.json": ["admin_unused"],
            "frontend/i18n/en/index.json": ["idx_unused"],
            "frontend/i18n/en/legal.json": ["legal_unused"],
            "frontend/i18n/en/login.json": [
                "login_comment_only",
                "login_hyphen_segment_only",
                "login_test_only",
                "login_unused",
                "title",
            ],
            "frontend/i18n/en/server_setup.json": ["ss_unused"],
        }

        report = unused_translation_keys.format_unused_report(repo_root, unused_by_file)
        output_path = repo_root / "temp" / "a.txt"
        unused_translation_keys.write_report(output_path, report)

        assert output_path.read_text(encoding="utf-8") == report
        assert "frontend/i18n/en/index.json" in report
        assert "- idx_unused" in report
        assert "- title" in report
