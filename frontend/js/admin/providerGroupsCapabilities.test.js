const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'providerGroups.js'), 'utf8');

test('provider groups request only model-capable providers', () => {
    assert.match(
        source,
        /\/api\/v1\/llm\/providers\?model_capable_only=true/
    );
});

test('speech-only provider rejection is translated in every admin locale', () => {
    const i18nRoot = path.resolve(__dirname, '../../i18n');
    const locales = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    assert.ok(locales.length > 0);
    for (const locale of locales) {
        const translations = JSON.parse(
            fs.readFileSync(path.join(i18nRoot, locale, 'admin.json'), 'utf8')
        );
        assert.ok(
            translations.provider_group_provider_not_model_capable,
            `missing provider-group capability translation for ${locale}`
        );
    }
});
