const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const mediaGenerationFiles = [
    'audioGeneration.js',
    'imageGeneration.js',
    'musicGeneration.js',
    'videoGeneration.js',
];

function readAdminSource(fileName) {
    return fs.readFileSync(path.join(__dirname, fileName), 'utf8');
}

function readAdminTranslationBundle(locale) {
    const localeRoot = path.join(__dirname, '../../i18n', locale);
    return [
        'schema.json',
        'index.json',
        'admin.json',
        'admin_chats.json',
        'server_setup.json',
    ].reduce((bundle, fileName) => ({
        ...bundle,
        ...JSON.parse(fs.readFileSync(path.join(localeRoot, fileName), 'utf8')),
    }), {});
}

test('media and quota pages have localized runtime copy in every locale', () => {
    const localeRoot = path.join(__dirname, '../../i18n');
    const locales = fs.readdirSync(localeRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name)
        .sort();
    const referencedKeys = new Set();

    for (const fileName of mediaGenerationFiles) {
        const helper = fileName === 'imageGeneration.js' ? 't' : 'translate';
        const callPattern = new RegExp(`\\b${helper}\\s*\\(\\s*['\"]([a-z][a-z0-9_.-]+)['\"]`, 'g');
        for (const match of readAdminSource(fileName).matchAll(callPattern)) {
            referencedKeys.add(match[1]);
        }
    }

    const english = readAdminTranslationBundle('en');
    for (const locale of locales) {
        const translations = readAdminTranslationBundle(locale);
        for (const key of referencedKeys) {
            assert.ok(translations[key], `${locale} is missing media translation ${key}`);
            if (locale !== 'en' && /[A-Za-z]{2,} [A-Za-z]{2,}/.test(english[key] || '')) {
                assert.notEqual(
                    translations[key],
                    english[key],
                    `${locale} still uses English media copy for ${key}`,
                );
            }
        }
    }

    const german = readAdminTranslationBundle('de');
    assert.equal(german.rate_limits_search_placeholder, 'Ratenbegrenzungen durchsuchen');
    assert.equal(german.rate_limits_search_aria, 'Ratenbegrenzungen durchsuchen');
});

test('media generation pages do not autosave empty providers during initial restore', () => {
    for (const fileName of mediaGenerationFiles) {
        const source = readAdminSource(fileName);
        const emptyProviderBranch = source.match(/if \(!pageState\.providerId\) \{[\s\S]*?return;\n\s*\}/);

        assert.ok(emptyProviderBranch, `${fileName} should handle an empty provider selection`);
        assert.match(
            emptyProviderBranch[0],
            /if \(!preserveCurrent\) \{\s*scheduleAutoSave\(\);\s*\}/,
            `${fileName} should only autosave an empty provider after an explicit user change`
        );
    }
});

test('media generation pages reveal the model step only after provider selection', () => {
    for (const fileName of mediaGenerationFiles) {
        const source = readAdminSource(fileName);
        const providerChangeHandler = source.match(
            /providerSelect\.addEventListener\('change', async \(\) => \{([\s\S]*?)\n\s*\}\);/,
        );

        assert.match(
            source,
            /const modelRow = UI\.buildSettingsRow/,
            `${fileName} should retain the model row for wizard visibility updates`,
        );
        assert.match(
            source,
            /UI\.setStepVisible\(modelRow, Boolean\(pageState\.providerId\)\)/,
            `${fileName} should hide the model step until a provider is selected`,
        );
        assert.ok(
            providerChangeHandler,
            `${fileName} should register a provider change handler`,
        );
        assert.match(
            providerChangeHandler[1],
            /pageState\.providerId = providerSelect\.value;\s*pageState\.modelName = '';\s*updateWizardVisibility\(\);/,
            `${fileName} should update the model step on every provider change`,
        );
    }
});

test('media generation model pickers keep an empty placeholder before real models', () => {
    for (const fileName of mediaGenerationFiles) {
        const source = readAdminSource(fileName);

        assert.match(
            source,
            /modelSelect\.innerHTML = `<option value="">\$\{UI\.escapeHtml\([\s\S]*?Select a model[\s\S]*?<\/option>`/,
            `${fileName} should not let the browser auto-select its first model`,
        );
    }
});

test('image generation model settings keep multi-selects in the standard right column', () => {
    const source = readAdminSource('imageGeneration.js');

    assert.doesNotMatch(source, /column:\s*field\.type === 'select' && field\.multiple/);
    assert.match(source, /title:\s*resolveFieldLabel\(field, settingsKey\)/);
    assert.match(source, /description:\s*resolveFieldDescription\(field\)/);
});

test('image generation model settings honor schema dependencies', () => {
    const source = readAdminSource('imageGeneration.js');

    assert.match(source, /const dependencyMatches = \(field\) =>/);
    assert.match(source, /row\.hidden = !isVisible/);
    assert.match(source, /row\.style\.display = isVisible \? '' : 'none'/);
    assert.match(source, /control\.addEventListener\('change', updateDependentRows\)/);
});
