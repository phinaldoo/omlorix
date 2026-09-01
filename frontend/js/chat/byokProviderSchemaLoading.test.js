const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const BYOK_PATH = path.join(__dirname, 'byok.js');

class MemoryStorage {
    constructor() {
        this.entries = new Map();
    }

    getItem(key) {
        return this.entries.get(String(key)) ?? null;
    }

    setItem(key, value) {
        this.entries.set(String(key), String(value));
    }

    removeItem(key) {
        this.entries.delete(String(key));
    }
}

function loadByokForProviderSchemaTest(authedFetch, translations = {}) {
    const source = fs.readFileSync(BYOK_PATH, 'utf8');
    // Expose only the internal boundaries needed to verify that resets remain
    // side-effect free and provider requests are deduplicated.
    const instrumented = source.replace(
        /\}\)\(\);\s*$/,
        `window.__byokProviderSchemaTest = {
            getCachedProviderSchema,
            loadProviderSchema,
            localizeByokSchema,
            fetchJson,
            resetProviderEditor,
            openProviderEditorSource: openProviderEditor.toString(),
            openForTest() {
                state.providerModalOpen = true;
                state.providerSettingsHost = {};
            },
            closeForTest() {
                state.providerModalOpen = false;
                state.providerSchemaRequestToken += 1;
            },
        };
        })();`,
    );
    const localStorage = new MemoryStorage();
    const sessionStorage = new MemoryStorage();
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
            getElementById() {
                return null;
            },
        },
        window: {
            authedFetch,
            getTranslation(key, fallback) {
                return Object.prototype.hasOwnProperty.call(translations, key)
                    ? translations[key]
                    : fallback;
            },
            localStorage,
            sessionStorage,
        },
    };
    vm.runInNewContext(instrumented, context, { filename: BYOK_PATH });
    return context.window.__byokProviderSchemaTest;
}

test('resetting the hidden BYOK provider editor never fetches a provider schema', () => {
    let requestCount = 0;
    const harness = loadByokForProviderSchemaTest(async () => {
        requestCount += 1;
        return { ok: true, json: async () => ({ sections: [] }) };
    });

    harness.resetProviderEditor();

    assert.equal(requestCount, 0);
    assert.match(harness.openProviderEditorSource, /await loadProviderSchema\(/);
});

test('provider schemas are cached per provider and concurrent loads are deduplicated', async () => {
    const requestedUrls = [];
    const harness = loadByokForProviderSchemaTest(async (url) => {
        requestedUrls.push(url);
        return {
            ok: true,
            json: async () => ({ sections: [], request: requestedUrls.length }),
        };
    });

    const [first, concurrent] = await Promise.all([
        harness.getCachedProviderSchema('openai'),
        harness.getCachedProviderSchema('openai'),
    ]);
    const reopened = await harness.getCachedProviderSchema('openai');
    await harness.getCachedProviderSchema('anthropic');

    assert.equal(first.request, 1);
    assert.equal(concurrent.request, 1);
    assert.equal(reopened.request, 1);
    assert.deepEqual(requestedUrls, [
        '/api/v1/llm/byok/provider-schema?provider=openai',
        '/api/v1/llm/byok/provider-schema?provider=anthropic',
    ]);
});

test('failed provider schema requests are evicted so a later open can retry', async () => {
    let requestCount = 0;
    const harness = loadByokForProviderSchemaTest(async () => {
        requestCount += 1;
        if (requestCount === 1) {
            return {
                ok: false,
                status: 503,
                json: async () => ({ detail: 'temporarily unavailable' }),
            };
        }
        return { ok: true, json: async () => ({ sections: [], recovered: true }) };
    });

    await assert.rejects(harness.getCachedProviderSchema('xai'), /temporarily unavailable/);
    const recovered = await harness.getCachedProviderSchema('xai');

    assert.equal(requestCount, 2);
    assert.equal(recovered.recovered, true);
});

test('a rejected stale provider schema request is ignored after the editor closes', async () => {
    let rejectRequest;
    const request = new Promise((_resolve, reject) => {
        rejectRequest = reject;
    });
    const harness = loadByokForProviderSchemaTest(() => request);
    harness.openForTest();

    const loading = harness.loadProviderSchema('openai');
    harness.closeForTest();
    rejectRequest(new Error('stale provider failure'));

    await assert.doesNotReject(loading);
});

test('BYOK localizes schema sections, fields, placeholders, and options without mutating the response', () => {
    const harness = loadByokForProviderSchemaTest(async () => ({ ok: true }), {
        section_title: 'API-Anmeldeinformationen und Endpunkte',
        section_description: 'Konfiguriere Anmeldedaten und Routing.',
        field_label: 'Organisations-ID',
        field_description: 'Optionaler Organisationsbezeichner.',
        field_placeholder: 'Z. B. org-abc123',
        option_label: 'Automatisch',
    });
    const schema = {
        sections: [{
            title: 'API credentials & endpoints',
            i18n_title: 'section_title',
            description: 'Configure credentials and routing.',
            i18n_description: 'section_description',
            fields: [{
                key: 'settings.organization',
                label: 'Organization ID',
                i18n_label: 'field_label',
                description: 'Optional organization identifier.',
                i18n_description: 'field_description',
                placeholder: 'E.g. org-abc123',
                i18n_placeholder: 'field_placeholder',
                options: [{ value: 'auto', label: 'Automatic', i18n_label: 'option_label' }],
            }],
        }],
    };

    const localized = harness.localizeByokSchema(schema);

    assert.equal(localized.sections[0].title, 'API-Anmeldeinformationen und Endpunkte');
    assert.equal(localized.sections[0].description, 'Konfiguriere Anmeldedaten und Routing.');
    assert.equal(localized.sections[0].fields[0].label, 'Organisations-ID');
    assert.equal(localized.sections[0].fields[0].description, 'Optionaler Organisationsbezeichner.');
    assert.equal(localized.sections[0].fields[0].placeholder, 'Z. B. org-abc123');
    assert.equal(localized.sections[0].fields[0].options[0].label, 'Automatisch');
    assert.equal(schema.sections[0].fields[0].label, 'Organization ID');
});

test('reported OpenAI provider and model schema copy resolves through the real German catalog', () => {
    const germanSchema = JSON.parse(fs.readFileSync(
        path.join(__dirname, '..', '..', 'i18n', 'de', 'schema.json'),
        'utf8',
    ));
    const harness = loadByokForProviderSchemaTest(async () => ({ ok: true }), germanSchema);
    const localized = harness.localizeByokSchema({
        sections: [
            {
                title: 'API credentials & endpoints',
                i18n_title: 'schema_backend_api_credentials_and_endpoints',
                fields: [
                    { key: 'settings.organization', label: 'Organization ID', i18n_label: 'schema_backend_organization_id' },
                    { key: 'settings.project', label: 'Project ID', i18n_label: 'schema_backend_project_id' },
                    { key: 'settings.custom_headers', label: 'Custom HTTP headers', i18n_label: 'schema_backend_custom_http_headers' },
                ],
            },
            {
                title: 'Model Information',
                i18n_title: 'schema_backend_model_information',
                fields: [{
                    key: 'model_name',
                    label: 'Model ID',
                    description: 'Identifier used when calling the provider API.',
                    i18n_description: 'schema_backend_identifier_used_when_calling_the_provider_api',
                }],
            },
        ],
    });

    assert.equal(localized.sections[0].title, 'API-Anmeldeinformationen und Endpunkte');
    assert.deepEqual(
        Array.from(localized.sections[0].fields, (field) => field.label),
        ['Organisations-ID', 'Projekt-ID', 'Benutzerdefinierte HTTP-Header'],
    );
    assert.equal(localized.sections[1].title, 'Modellinformationen');
    assert.match(localized.sections[1].fields[0].description, /Provider-API/);
});

test('BYOK discovery errors use localized stable codes instead of raw provider copy', async () => {
    const harness = loadByokForProviderSchemaTest(async () => ({
        ok: false,
        status: 401,
        json: async () => ({
            detail: {
                code: 'byok_provider_authentication_failed',
                message: 'Incorrect API key provided',
            },
        }),
    }), {
        byok_provider_authentication_failed: 'Der Provider hat die API-Anmeldedaten abgelehnt.',
    });

    await assert.rejects(
        harness.fetchJson('/api/v1/llm/models/byok', { method: 'POST' }),
        /Der Provider hat die API-Anmeldedaten abgelehnt/,
    );
});

test('legacy raw BYOK discovery errors fall back to localized generic copy', async () => {
    const harness = loadByokForProviderSchemaTest(async () => ({
        ok: false,
        status: 400,
        json: async () => ({ detail: 'Failed to list OpenAI models: Incorrect API key provided' }),
    }), {
        byok_model_discovery_failed: 'Modelle konnten nicht vom Provider geladen werden.',
    });

    await assert.rejects(
        harness.fetchJson('/api/v1/llm/models/byok', { method: 'POST' }),
        /Modelle konnten nicht vom Provider geladen werden/,
    );
});

test('every locale contains localized BYOK discovery error copy', () => {
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const keys = [
        'byok_provider_authentication_failed',
        'byok_provider_configuration_invalid',
        'byok_model_discovery_failed',
    ];

    for (const locale of fs.readdirSync(i18nRoot)) {
        const dictionaryPath = path.join(i18nRoot, locale, 'index.json');
        if (!fs.existsSync(dictionaryPath)) continue;
        const dictionary = JSON.parse(fs.readFileSync(dictionaryPath, 'utf8'));
        keys.forEach((key) => {
            assert.ok(dictionary[key]?.trim(), `${locale} is missing ${key}`);
        });
    }
});

test('every locale contains localized dynamic model option labels', () => {
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const keys = [
        'llm.shared.option.concise',
        'llm.shared.option.detailed',
        'llm.shared.option.flex',
        'llm.shared.option.image',
        'llm.shared.option.none',
        'llm.shared.option.pdf',
        'llm.shared.option.priority',
        'llm.shared.option.text',
        'llm.shared.option.text_document',
    ];

    for (const locale of fs.readdirSync(i18nRoot)) {
        const dictionaryPath = path.join(i18nRoot, locale, 'schema.json');
        if (!fs.existsSync(dictionaryPath)) continue;
        const dictionary = JSON.parse(fs.readFileSync(dictionaryPath, 'utf8'));
        keys.forEach((key) => {
            assert.ok(dictionary[key]?.trim(), `${locale} is missing ${key}`);
        });
    }
});
