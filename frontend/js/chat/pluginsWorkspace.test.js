const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(path.join(root, 'js/chat/plugins.js'), 'utf8');
const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'css/chat/plugins.css'), 'utf8');

test('plugin management uses the authenticated lifecycle API and no native dialogs', () => {
    assert.match(script, /\/api\/v1\/plugins\/preview/);
    assert.match(script, /\/api\/v1\/plugins\/install/);
    assert.match(script, /showDeleteConfirm/);
    assert.doesNotMatch(script, /\b(?:alert|confirm|prompt)\s*\(/);
});

test('plugin review is an accessible modal and the status is announced', () => {
    assert.match(index, /id="pluginReviewOverlay"[^>]*aria-hidden="true"/);
    assert.match(index, /class="plugins-review-dialog" role="dialog" aria-modal="true"/);
    assert.match(index, /id="pluginsStatus" role="status" aria-live="polite"/);
});

test('plugin hover styling is limited to devices that support hover', () => {
    const unconditional = css.replace(/@media \(hover: hover\) and \(pointer: fine\) \{[\s\S]*?\n\}/g, '');
    assert.doesNotMatch(unconditional, /:hover/);
});

test('plugin workspace copy exists in every supported locale', () => {
    const localeRoot = path.join(root, 'i18n');
    const required = [
        'workspace_tab_plugins', 'workspace_plugins_title', 'workspace_plugins_subtitle',
        'workspace_plugins_install', 'workspace_plugins_security_note',
        'workspace_plugins_empty_title', 'workspace_plugins_review_eyebrow',
        'workspace_plugins_installs_disabled', 'workspace_plugins_uninstall_desc',
        'workspace_plugins_request_error',
        'workspace_plugins_warning_local_process', 'workspace_plugins_warning_hooks',
        'workspace_plugins_warning_apps', 'workspace_plugins_warning_empty',
    ];
    for (const locale of fs.readdirSync(localeRoot)) {
        const file = path.join(localeRoot, locale, 'index.json');
        if (!fs.existsSync(file)) continue;
        const dictionary = JSON.parse(fs.readFileSync(file, 'utf8'));
        for (const key of required) {
            assert.equal(typeof dictionary[key], 'string', `${locale} is missing ${key}`);
            assert.ok(dictionary[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});
