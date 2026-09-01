const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'script.js'), 'utf8');
const localeRoot = path.join(__dirname, '../../i18n');

test('conversation review runtime copy is translated in every supported locale', () => {
    const keys = new Set(
        Array.from(source.matchAll(/\btext\(\s*['"]([a-z][a-z0-9_.-]+)['"]/g), (match) => match[1]),
    );
    const english = JSON.parse(
        fs.readFileSync(path.join(localeRoot, 'en', 'admin_chats.json'), 'utf8'),
    );

    for (const entry of fs.readdirSync(localeRoot, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        const translations = JSON.parse(
            fs.readFileSync(path.join(localeRoot, entry.name, 'admin_chats.json'), 'utf8'),
        );
        for (const key of keys) {
            assert.ok(translations[key], `${entry.name}/admin_chats.json is missing ${key}`);
            if (entry.name !== 'en' && /[A-Za-z]{2,} [A-Za-z]{2,}/.test(english[key] || '')) {
                assert.notEqual(
                    translations[key],
                    english[key],
                    `${entry.name}/admin_chats.json still uses English for ${key}`,
                );
            }
        }
    }
});

test('conversation review refreshes its initial empty state after i18n loads', () => {
    assert.match(source, /document\.addEventListener\('i18n:updated', refreshLocalizedEmptyState\)/);
    assert.match(
        source,
        /refreshLocalizedEmptyState[\s\S]*?select_user_to_view[\s\S]*?select_user_to_begin/,
    );
});
