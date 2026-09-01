const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const adminHtml = readFrontendSource(path.join(__dirname, '..', '..', 'admin.html'), 'utf8');
const adminStyles = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'admin', 'style.css'), 'utf8');
const providersCreateSource = readFrontendSource(path.join(__dirname, 'providersCreate.js'), 'utf8');

/**
 * Returns one top-level arrow function without allowing assertions to spill
 * into the next handler in this large browser script.
 */
function getHandlerSource(handlerName, nextHandlerName) {
    const startMarker = `    const ${handlerName} = async () => {`;
    const endMarker = `    const ${nextHandlerName} =`;
    const start = providersCreateSource.indexOf(startMarker);
    const end = providersCreateSource.indexOf(endMarker, start);

    assert.notEqual(start, -1, `Missing ${handlerName}`);
    assert.notEqual(end, -1, `Missing boundary after ${handlerName}`);
    return providersCreateSource.slice(start, end);
}

/**
 * Loads the browser script with private refresh handlers exposed only in the
 * in-memory test copy. The completion helper is instrumented so tests can
 * verify that each invocation passes through it exactly once.
 */
function createModelsRefreshHarness(authedFetch) {
    const completionMarker = '    const completeModelsTabRefresh = (refresh) => {\n';
    const exposedHooks = [
        '    window.__providersCreateTestHooks = {',
        '        state,',
        '        beginModelsTabRefresh,',
        '        completeModelsTabRefresh,',
        '        loadOllamaLoadedModels,',
        '        loadLMStudioModels,',
        '        getLMStudioLoadedInstanceOptions,',
        '    };',
        '})();',
    ].join('\n');
    const instrumentedSource = providersCreateSource
        .replace(completionMarker, `${completionMarker}        window.__modelsTabRefreshCompletions += 1;\n`)
        .replace('})();', exposedHooks);
    const createElement = () => ({
        hidden: false,
        tabIndex: 0,
        classList: { toggle() {} },
        setAttribute() {},
        removeAttribute() {},
        closest() { return null; },
    });
    const elements = new Map([
        ['providerEditTabSettings', createElement()],
        ['providerEditTabModels', createElement()],
        ['providerEditSettingsPage', createElement()],
        ['providerEditModelsPage', createElement()],
    ]);
    const context = {
        authedFetch,
        console: { error() {}, warn() {} },
        document: {
            readyState: 'loading',
            addEventListener() {},
            getElementById(id) { return elements.get(id) || null; },
            querySelector() { return null; },
        },
        window: { __modelsTabRefreshCompletions: 0 },
    };

    vm.runInNewContext(instrumentedSource, context, { filename: 'providersCreate.js' });
    const hooks = context.window.__providersCreateTestHooks;
    hooks.state.editingId = 'provider-1';
    hooks.state.activeTab = 'models';
    hooks.state.modelsTabVisible = true;
    hooks.state.modelsTabReady = true;
    return { hooks, window: context.window };
}

test('provider edit form exposes the translated test connection action', () => {
    assert.match(
        adminHtml,
        /id="providerEditFormTest"><span data-i18n="provider_test_connection">Test Connection<\/span>/
    );
    assert.match(
        providersCreateSource,
        /test: document\.getElementById\('providerEditFormTest'\)/
    );
});

test('provider edit connection tests identify the saved provider for secret reuse', () => {
    assert.match(
        providersCreateSource,
        /provider_id: state\.mode === 'edit' \? state\.editingId : undefined/
    );
    assert.match(
        providersCreateSource,
        /formDom\.test\?\.addEventListener\('click', handleProviderTest\)/
    );
});

test('provider form single selects use the shared accessible custom-select widget', () => {
    assert.match(
        providersCreateSource,
        /const enhanceProviderSelect = \(select, field, row\) => \{[\s\S]*?window\.upgradeAdminSingleSelect\(select, \{/
    );
    assert.match(
        providersCreateSource,
        /label\.id = label\.id \|\| labelId;[\s\S]*?select\.setAttribute\('aria-labelledby', label\.id\);/
    );
    assert.match(
        providersCreateSource,
        /if \(control\.tagName === 'SELECT'\) \{\s*enhanceProviderSelect\(control, fieldDef, row\);\s*\}/
    );
});

test('provider connection tests send the complete visible draft settings', () => {
    const handlerSource = getHandlerSource('handleProviderTest', 'bindFormControls');

    assert.match(handlerSource, /const payload = buildProviderPayload\(\);/);
    assert.match(handlerSource, /api_key: payload\.api_key/);
    assert.match(handlerSource, /base_url: payload\.settings\?\.base_url/);
    assert.match(handlerSource, /settings: payload\.settings \|\| \{\}/);
    assert.match(handlerSource, /testProviderConnection\(testPayload\)/);
});

test('provider creation and connection tests validate required API keys locally without a notification', () => {
    const handlerStart = providersCreateSource.indexOf('    const handleProviderFormSubmit = async (event) => {');
    const handlerEnd = providersCreateSource.indexOf('    const handleFormBack =', handlerStart);
    assert.notEqual(handlerStart, -1, 'Missing handleProviderFormSubmit');
    assert.notEqual(handlerEnd, -1, 'Missing handleFormBack boundary');
    const handlerSource = providersCreateSource.slice(handlerStart, handlerEnd);
    const testHandlerSource = getHandlerSource('handleProviderTest', 'bindFormControls');
    const validationStart = providersCreateSource.indexOf('    const validateProviderDraft = () => {');
    const validationEnd = providersCreateSource.indexOf('    const handleProviderFormSubmit =', validationStart);
    assert.notEqual(validationStart, -1, 'Missing validateProviderDraft');
    assert.notEqual(validationEnd, -1, 'Missing validation helper boundary');
    const validationSource = providersCreateSource.slice(validationStart, validationEnd);

    assert.match(providersCreateSource, /FALLBACK_REQUIRED_API_KEY_PROVIDER_KEYS[\s\S]*?'anthropic'/);
    assert.match(providersCreateSource, /fieldDef\.required = true/);
    assert.match(validationSource, /FieldValidation\?\.validate\(controlsArray, \{ notify: false \}\)/);
    assert.match(handlerSource, /if \(!validateProviderDraft\(\)\)/);
    assert.match(testHandlerSource, /if \(!validateProviderDraft\(\)\)/);
});

test('provider test backend error codes map to translated messages in every locale', () => {
    const expectedKeys = [
        'provider_test_saved_provider_type_mismatch',
        'provider_test_api_key_required',
    ];

    expectedKeys.forEach((key) => {
        assert.match(
            providersCreateSource,
            new RegExp(`case '${key}'`)
        );
    });

    const localesDirectory = path.join(__dirname, '..', '..', 'i18n');
    fs.readdirSync(localesDirectory, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .forEach((entry) => {
            const translationsPath = path.join(localesDirectory, entry.name, 'admin.json');
            const translations = JSON.parse(readFrontendSource(translationsPath, 'utf8'));
            expectedKeys.forEach((key) => {
                assert.equal(
                    typeof translations[key],
                    'string',
                    `${entry.name} is missing ${key}`
                );
                assert.notEqual(
                    translations[key].trim(),
                    '',
                    `${entry.name} has an empty ${key}`
                );
            });
        });
});

test('unknown model download progress preserves the last announced percentage', () => {
    const start = providersCreateSource.indexOf('    const applyModelDownloadProgress =');
    const end = providersCreateSource.indexOf('    const resetModelDownloadProgress =', start);
    const applyModelDownloadProgress = vm.runInNewContext(
        `${providersCreateSource.slice(start, end)}\napplyModelDownloadProgress`
    );
    const values = [];
    const refs = {
        wrapper: { hidden: true },
        bar: { setAttribute(_name, value) { values.push(value); } },
    };

    applyModelDownloadProgress(refs, { percent: 42 }, 'Downloading');
    applyModelDownloadProgress(refs, { percent: null }, 'Downloading');

    assert.deepEqual(values, ['42']);
});

test('Ollama and LM Studio model refreshes restore the Models tab on success and failure', async (t) => {
    const handlers = [
        ['loadOllamaLoadedModels', 'createLMStudioInput'],
        ['loadLMStudioModels', 'refreshProviderExtras'],
    ];

    for (const [handlerName, nextHandlerName] of handlers) {
        const handlerSource = getHandlerSource(handlerName, nextHandlerName);
        assert.match(handlerSource, /const modelsRefresh = beginModelsTabRefresh\(providerId\);/);
        assert.match(
            handlerSource,
            /finally \{[\s\S]*?completeModelsTabRefresh\(modelsRefresh\);[\s\S]*?\}/
        );

        for (const outcome of ['success', 'failure']) {
            await t.test(`${handlerName} restores after ${outcome}`, async () => {
                const response = { ok: true, status: 200, async json() { return []; } };
                const harness = createModelsRefreshHarness(() => (
                    outcome === 'success' ? Promise.resolve(response) : Promise.reject(new Error('refresh failed'))
                ));
                harness.hooks.state.mode = 'edit';
                harness.hooks.state.providerKey = handlerName === 'loadOllamaLoadedModels'
                    ? 'ollama'
                    : 'lmstudio';

                await harness.hooks[handlerName]();

                assert.equal(harness.hooks.state.modelsTabReady, true);
                assert.equal(harness.hooks.state.activeTab, 'models');
                assert.equal(harness.window.__modelsTabRefreshCompletions, 1);
            });
        }
    }
});

test('model refresh completion does not reselect Models after the administrator leaves it', () => {
    const completionStart = providersCreateSource.indexOf('    const completeModelsTabRefresh =');
    const completionEnd = providersCreateSource.indexOf('    const bindProviderEditTabs =', completionStart);
    const completionSource = providersCreateSource.slice(completionStart, completionEnd);

    assert.match(
        completionSource,
        /refresh\.tabSelectionGeneration === state\.providerTabSelectionGeneration/
    );

    const harness = createModelsRefreshHarness(() => Promise.reject(new Error('unused')));
    harness.hooks.state.mode = 'edit';
    harness.hooks.state.providerKey = 'ollama';
    const refresh = harness.hooks.beginModelsTabRefresh('provider-1');
    harness.hooks.state.providerTabSelectionGeneration += 1;
    harness.hooks.state.activeTab = 'settings';

    harness.hooks.completeModelsTabRefresh(refresh);

    assert.equal(harness.hooks.state.activeTab, 'settings');
});

test('stale model refresh completion cannot publish readiness for a newer request', () => {
    const harness = createModelsRefreshHarness(() => Promise.reject(new Error('unused')));
    harness.hooks.state.mode = 'edit';
    harness.hooks.state.providerKey = 'ollama';
    const staleRefresh = harness.hooks.beginModelsTabRefresh('provider-1');
    harness.hooks.beginModelsTabRefresh('provider-1');

    harness.hooks.completeModelsTabRefresh(staleRefresh);

    assert.equal(harness.hooks.state.modelsTabReady, false);
});

test('a failed optional Ollama inventory request preserves loaded models', async () => {
    const loadedModels = [{ name: 'llama3:latest' }];
    const harness = createModelsRefreshHarness((url) => {
        if (url.includes('/models/all')) {
            return Promise.reject(new Error('inventory unavailable'));
        }
        return Promise.resolve({
            ok: true,
            status: 200,
            async json() { return loadedModels; },
        });
    });
    harness.hooks.state.mode = 'edit';
    harness.hooks.state.providerKey = 'ollama';

    await harness.hooks.loadOllamaLoadedModels();

    assert.deepEqual(harness.hooks.state.ollamaModels, loadedModels);
    assert.equal(harness.hooks.state.ollamaAllModels.length, 0);
    assert.equal(harness.hooks.state.modelsTabReady, true);
});

test('LM Studio unload options include individual instances and model-wide actions', () => {
    const harness = createModelsRefreshHarness(() => Promise.reject(new Error('unused')));
    harness.hooks.state.lmstudioLoadedModels = [
        { instance_id: 'gemma-a', model: 'google/gemma', name: 'Gemma' },
        { instance_id: 'gemma-b', model: 'google/gemma', name: 'Gemma' },
    ];

    const options = harness.hooks.getLMStudioLoadedInstanceOptions();
    assert.deepEqual(
        Array.from(options, ({ value }) => value).sort(),
        ['gemma-a', 'gemma-b', 'google/gemma']
    );
    assert.match(
        options.find(({ value }) => value === 'google/gemma').label,
        /All instances of Gemma/
    );
});

test('LM Studio model-wide unload option is translated in every locale', () => {
    const localesDirectory = path.join(__dirname, '..', '..', 'i18n');
    fs.readdirSync(localesDirectory, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .forEach((entry) => {
            const translationsPath = path.join(localesDirectory, entry.name, 'admin.json');
            const translations = JSON.parse(readFrontendSource(translationsPath, 'utf8'));
            assert.match(
                translations.provider_lmstudio_unload_all_instances_option || '',
                /\{model\}/,
                `${entry.name} is missing the model-wide unload option`
            );
        });
});

test('Ollama and LM Studio model dropdowns can escape their management sections', () => {
    assert.match(
        adminStyles,
        /\.ollama-models-download-section \.ollama-models-body,\s*\.lmstudio-models-management-section \.ollama-models-body\s*\{\s*overflow:\s*visible;/
    );
});
