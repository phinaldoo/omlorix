const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const INDEX_PATH = path.join(__dirname, '../../index.html');
const I18N_ROOT = path.join(__dirname, '../../i18n');
const MODEL_SELECT_PATH = path.join(__dirname, 'modelSelect.js');

/**
 * Load the small access-visibility helper from the production script without
 * bootstrapping the model selector's much larger browser-only DOM surface.
 */
function loadLeaderboardVisibilityHelper(footer) {
    const source = fs.readFileSync(MODEL_SELECT_PATH, 'utf8');
    const helperSource = source.match(
        /function setModelSelectLeaderboardAccess\(hasAccess\)\s*{[\s\S]*?\n}/
    )?.[0];

    assert.ok(helperSource, 'leaderboard access helper must remain available');

    const context = {
        document: {
            getElementById(id) {
                return id === 'modelSelectLeaderboardFooter' ? footer : null;
            },
        },
    };
    vm.runInNewContext(helperSource, context);
    return context.setModelSelectLeaderboardAccess;
}

test('model selector exposes an accessible native leaderboard link', () => {
    const html = fs.readFileSync(INDEX_PATH, 'utf8');
    const footerMarkup = html.match(
        /<div class="model-select-footer" id="modelSelectLeaderboardFooter"[\s\S]*?<\/div>/
    )?.[0] || '';

    // The footer starts hidden so setup data cannot briefly expose an action
    // that the effective group configuration does not permit.
    assert.match(footerMarkup, /id="modelSelectLeaderboardFooter" hidden/);
    assert.match(footerMarkup, /id="modelSelectHelpButton"/);
    assert.match(footerMarkup, /href="\/leaderboard"/);
    assert.match(footerMarkup, /target="_blank"/);
    assert.match(footerMarkup, /rel="noopener"/);
    assert.match(footerMarkup, /data-i18n="model_select_help_tooltip"/);
});

test('model selector follows the setup response leaderboard access flag', () => {
    const footer = { hidden: true };
    const setAccess = loadLeaderboardVisibilityHelper(footer);

    setAccess(true);
    assert.equal(footer.hidden, false);

    setAccess(false);
    assert.equal(footer.hidden, true);

    const source = fs.readFileSync(MODEL_SELECT_PATH, 'utf8');
    assert.match(
        source,
        /function initializeModelSelectFromChatSetup\(chatSetup\)[\s\S]*?initModelSelect\(Boolean\(chatSetup\.has_leaderboard_access\)\)/
    );
});

test('model selector initializes for both early and later chat setup responses', () => {
    const source = fs.readFileSync(MODEL_SELECT_PATH, 'utf8');

    assert.match(
        source,
        /if \(window\.chatSetup\)\s*{\s*initializeModelSelectFromChatSetup\(window\.chatSetup\);/
    );
    assert.match(
        source,
        /document\.addEventListener\('chatSetupReady',[\s\S]*?initializeModelSelectFromChatSetup\(event\?\.detail\);[\s\S]*?{ once: true }\);/
    );
});

test('every index locale translates the leaderboard link', () => {
    const localeDirectories = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    for (const locale of localeDirectories) {
        const dictionaryPath = path.join(I18N_ROOT, locale, 'index.json');
        const dictionary = JSON.parse(fs.readFileSync(dictionaryPath, 'utf8'));

        assert.equal(
            typeof dictionary.model_select_help_tooltip,
            'string',
            `${locale} must translate model_select_help_tooltip`
        );
        assert.ok(
            dictionary.model_select_help_tooltip.trim(),
            `${locale} model_select_help_tooltip must not be empty`
        );
    }
});

test('every index locale translates model picker agent metadata', () => {
    const keys = [
        'model_select_icon_alt',
        'model_select_shared_agent',
        'model_select_by',
        'model_select_custom_agent',
    ];
    const english = JSON.parse(fs.readFileSync(path.join(I18N_ROOT, 'en', 'index.json'), 'utf8'));
    const localeDirectories = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    for (const locale of localeDirectories) {
        const dictionary = JSON.parse(
            fs.readFileSync(path.join(I18N_ROOT, locale, 'index.json'), 'utf8')
        );

        for (const key of keys) {
            assert.ok(dictionary[key]?.trim(), `${locale} must translate ${key}`);
            if (locale !== 'en') {
                assert.notEqual(dictionary[key], english[key], `${locale} ${key} must not use English`);
            }
        }
    }
});
