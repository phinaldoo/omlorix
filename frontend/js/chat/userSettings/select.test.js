const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createClassList() {
    const classes = new Set();
    return {
        toggle(name, force) {
            if (force) {
                classes.add(name);
            } else {
                classes.delete(name);
            }
        },
        contains(name) {
            return classes.has(name);
        },
    };
}

function loadSelectSettingsScript(fetchImpl) {
    const source = fs.readFileSync(path.join(__dirname, 'select.js'), 'utf8');
    const selectListeners = {};
    const clearedCacheKeys = [];
    const appliedFonts = [];
    const authenticatedLanguages = [];
    const selectValues = [];
    const errors = [];

    const trigger = {
        dataset: { field: 'font' },
        classList: createClassList(),
        setAttribute() {},
        querySelector() {
            return { textContent: '' };
        },
        nextElementSibling: null,
    };
    const selectElement = {
        __customSelectState: { field: 'font', trigger },
        classList: createClassList(),
        addEventListener(type, listener) {
            selectListeners[type] = listener;
        },
        querySelector() {
            return trigger;
        },
    };

    const context = {
        console,
        window: null,
        document: {
            readyState: 'complete',
            querySelectorAll(selector) {
                return selector === '.custom-select' ? [selectElement] : [];
            },
            addEventListener() {},
        },
        SharedDataCache: {
            clear(key) {
                clearedCacheKeys.push(key);
            },
        },
        chatSetup: {
            font: 'inter',
            font_family: 'inter',
        },
        authedFetch: fetchImpl,
        setCustomSelectValue(field, value) {
            selectValues.push({ field, value });
        },
        setFontFamilyPreference(value) {
            appliedFonts.push(value);
            return value;
        },
        applyAuthenticatedLanguage(value) {
            authenticatedLanguages.push(value);
            return Promise.resolve(true);
        },
        notifyError(message) {
            errors.push(message);
        },
        getTranslation(_key, fallback) {
            return fallback;
        },
    };
    context.window = context;

    vm.runInNewContext(source, context, { filename: 'select.js' });

    return {
        context,
        selectListeners,
        clearedCacheKeys,
        appliedFonts,
        authenticatedLanguages,
        selectValues,
        errors,
    };
}

test('font select save invalidates settings cache and syncs live setup', async () => {
    let requestBody = null;
    const harness = loadSelectSettingsScript(async (_url, options) => {
        requestBody = JSON.parse(options.body);
        return {
            ok: true,
            json: async () => ({ status: 'success', updated: { font: 'verdana' } }),
        };
    });

    harness.context.initUserSettingsSelect({ font: 'inter' });
    await harness.selectListeners.customSelectChange({
        detail: { field: 'font', value: 'verdana' },
    });

    assert.deepEqual(requestBody, { font: 'verdana' });
    assert.deepEqual(harness.clearedCacheKeys, ['userSettingsInit']);
    assert.equal(harness.context.chatSetup.font, 'verdana');
    assert.equal(harness.context.chatSetup.font_family, 'verdana');
    assert.deepEqual(harness.appliedFonts, ['verdana']);
    assert.deepEqual(harness.errors, []);
});

test('font select save failure restores previous value without clearing cache', async () => {
    const harness = loadSelectSettingsScript(async () => ({
        ok: false,
        status: 400,
        json: async () => ({ detail: 'Unsupported font selection.' }),
    }));

    harness.context.initUserSettingsSelect({ font: 'inter' });
    await harness.selectListeners.customSelectChange({
        detail: { field: 'font', value: 'not-a-font' },
    });

    assert.deepEqual(harness.clearedCacheKeys, []);
    assert.equal(harness.context.chatSetup.font, 'inter');
    assert.equal(harness.context.chatSetup.font_family, 'inter');
    assert.deepEqual(harness.appliedFonts, []);
    assert.deepEqual(harness.selectValues.at(-1), { field: 'font', value: 'inter' });
    assert.equal(harness.errors.length, 1);
});

test('initial language select value synchronizes the active translator', () => {
    const harness = loadSelectSettingsScript(async () => {
        throw new Error('The initial language sync must not save a setting.');
    });

    harness.context.initUserSettingsSelect({ language: 'de' });

    assert.deepEqual(harness.authenticatedLanguages, ['de']);
});

test('timezone initialization preserves a valid persisted IANA alias', () => {
    const source = fs.readFileSync(path.join(__dirname, 'select.js'), 'utf8');
    const refreshes = [];
    let optionValues = ['UTC'];
    const timeZoneRoot = {
        dataset: { timeZoneOptionsReady: 'true' },
        querySelector(selector) {
            if (selector === '.select-option.selected' || selector === '.select-option') {
                return { dataset: { value: 'UTC' } };
            }
            return null;
        },
        querySelectorAll(selector) {
            if (selector !== '.select-option') return [];
            return optionValues.map((value) => ({ dataset: { value } }));
        },
    };
    const timeZoneTrigger = {
        closest(selector) {
            return selector === '.custom-select' ? timeZoneRoot : null;
        },
    };
    const context = {
        console,
        document: {
            readyState: 'complete',
            querySelector(selector) {
                return selector.includes('data-field="timezone"') ? timeZoneTrigger : null;
            },
            querySelectorAll() {
                return [];
            },
            addEventListener() {},
        },
        window: null,
        getCustomSelectValue() {
            return 'UTC';
        },
        refreshCustomSelect(_root, config) {
            refreshes.push(config);
            optionValues = config.options.map((option) => option.value);
            return true;
        },
        OmlorixTimeZones: {
            getBrowserTimeZone() {
                return 'UTC';
            },
            getSupportedTimeZoneOptions(extraValues) {
                return Array.from(new Set(['UTC', ...extraValues.filter(Boolean)]))
                    .map((value) => ({ value, label: value }));
            },
        },
    };
    context.window = context;

    vm.runInNewContext(source, context, { filename: 'select.js' });
    context.initUserSettingsSelect({ timezone: 'US/Pacific' });

    assert.equal(refreshes.length, 1);
    assert.equal(refreshes[0].value, 'US/Pacific');
    assert.equal(refreshes[0].options.some((option) => option.value === 'US/Pacific'), true);
});
