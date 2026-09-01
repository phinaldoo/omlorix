const assert = require('node:assert/strict');
const { execFile } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { promisify } = require('node:util');
const vm = require('node:vm');

const BYOK_PATH = path.join(__dirname, 'byok.js');
const LANGUAGE_PATH = path.join(__dirname, '..', 'common', 'language.js');
const execFileAsync = promisify(execFile);

function readSource(filePath) {
    return fs.readFileSync(filePath, 'utf8');
}

function loadTranslationRefHarness() {
    const source = readSource(BYOK_PATH);
    const instrumented = source.replace(
        /\}\)\(\);\s*$/,
        `window.__byokLanguageRefreshTest = { translationRef, resolveTranslationRef };
        })();`,
    );
    let translations = {};
    const storage = {
        getItem() { return null; },
        setItem() {},
        removeItem() {},
    };
    const context = {
        console,
        Date,
        JSON,
        Map,
        Object,
        Set,
        String,
        document: {
            readyState: 'loading',
            addEventListener() {},
        },
        window: {
            localStorage: storage,
            sessionStorage: storage,
            getTranslation(key, fallback) {
                return Object.prototype.hasOwnProperty.call(translations, key)
                    ? translations[key]
                    : fallback;
            },
            formatTranslation(key, fallback, vars) {
                const template = Object.prototype.hasOwnProperty.call(translations, key)
                    ? translations[key]
                    : fallback;
                return String(template).replace(/\{(\w+)\}/g, (_, token) => String(vars?.[token] ?? ''));
            },
        },
    };
    vm.runInNewContext(instrumented, context, { filename: BYOK_PATH });
    return {
        ...context.window.__byokLanguageRefreshTest,
        setTranslations(next) {
            translations = next;
        },
    };
}

test('BYOK refreshes from the canonical i18n event after dictionaries are active', () => {
    const source = readSource(BYOK_PATH);
    assert.match(
        source,
        /document\.addEventListener\('i18n:updated', handleByokI18nUpdated\)/,
    );
    assert.match(
        source,
        /function handleByokI18nUpdated\(\)[\s\S]*renderProviderList\(\)[\s\S]*renderModelList\(\)[\s\S]*renderByokStatsContent\(\)[\s\S]*refreshProviderEditorTranslations\(\)[\s\S]*refreshModelEditorTranslations\(\)/,
    );
    assert.match(
        source,
        /populateProviderSelect\(document\.getElementById\('byokModelProviderInstance'\)\)/,
    );
    for (const key of [
        'byok_provider_instances_title',
        'byok_models_title',
        'byok_stats_usage_title',
        'byok_provider_type_label',
        'byok_model_provider_instance_label',
    ]) {
        assert.match(source, new RegExp(`data-i18n="${key}"`));
    }
});

test('BYOK locale refresh preserves open-editor drafts and rebuilds schema copy', () => {
    const source = readSource(BYOK_PATH);
    assert.match(source, /function refreshProviderEditorTranslations\(\)[\s\S]*values = collectContextValues\(state\.providerFormContext\)/);
    assert.match(source, /function refreshModelEditorTranslations\(\)[\s\S]*values = collectContextValues\(state\.modelFormContext\)/);
    assert.match(source, /state\.providerFormContext = renderSchemaFields\([\s\S]*state\.providerSchema[\s\S]*values/);
    assert.match(source, /state\.modelFormContext = renderSchemaFields\([\s\S]*state\.modelSchema[\s\S]*values/);
    assert.doesNotMatch(
        source.match(/function handleByokI18nUpdated\(\)[\s\S]*?\n    \}/)?.[0] || '',
        /renderRoot\(/,
    );
    assert.match(source, /if \(state\.dialogOpen\) renderDialog\(\)/);
});

test('open BYOK dialog copy resolves again when the active locale changes', () => {
    const harness = loadTranslationRefHarness();
    const config = harness.translationRef(
        'byok_provider_delete_desc',
        'Remove {name}.',
        { name: 'Work account' },
    );

    harness.setTranslations({
        byok_provider_delete_desc: 'Remove {name}.',
    });
    assert.equal(harness.resolveTranslationRef(config), 'Remove Work account.');

    harness.setTranslations({
        byok_provider_delete_desc: '{name} entfernen.',
    });
    assert.equal(harness.resolveTranslationRef(config), 'Work account entfernen.');
});

test('every supported locale contains every stable translation key used by BYOK', () => {
    const byokSource = readSource(BYOK_PATH);
    const languageSource = readSource(LANGUAGE_PATH);
    const supportedMatch = languageSource.match(/const SUPPORTED_LANGS = \[([^\]]+)\]/);
    assert.ok(supportedMatch, 'language.js must expose the supported locale catalog');
    const supportedLocales = [...supportedMatch[1].matchAll(/["']([a-z]{2})["']/g)]
        .map((match) => match[1]);
    const usedKeys = new Set(
        [...byokSource.matchAll(/(?:byokT|translationRef|formatTranslation|\bt)\(\s*['"]([^'"]+)['"]/g)]
            .map((match) => match[1]),
    );

    assert.ok(supportedLocales.length > 1);
    assert.ok(usedKeys.size > 100);
    for (const locale of supportedLocales) {
        const catalogPath = path.join(__dirname, '..', '..', 'i18n', locale, 'index.json');
        const catalog = JSON.parse(readSource(catalogPath));
        const missing = [...usedKeys].filter((key) => !Object.prototype.hasOwnProperty.call(catalog, key));
        assert.deepEqual(missing, [], `${locale} is missing BYOK translations: ${missing.join(', ')}`);
    }
});

test('BYOK pane and open dialogs switch through every locale without a reload', {
    timeout: 60_000,
}, async () => {
    const electronPath = require('electron');
    const runnerPath = path.resolve(
        __dirname,
        '..',
        '..',
        '..',
        'electron',
        'tests',
        'fixtures',
        'byok-language-refresh-runner.js',
    );
    const { stdout } = await execFileAsync(electronPath, [
        '--headless',
        '--disable-gpu',
        runnerPath,
    ], {
        cwd: path.resolve(__dirname, '..', '..', '..'),
        env: {
            ...process.env,
            ELECTRON_DISABLE_SECURITY_WARNINGS: 'true',
        },
        timeout: 50_000,
    });

    assert.deepEqual(JSON.parse(stdout.trim()), {
        locales: 11,
        status: 'passed',
    });
});
