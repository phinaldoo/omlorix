const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const BYOK_PATH = path.join(__dirname, 'byok.js');
const RAW_SESSION_KEY = 'omlorix_byok_session_secrets_v1';
const SEALED_SESSION_KEY = 'omlorix_byok_session_credentials_v2';
const LOCAL_DATA_KEY = 'omlorix_byok_v1';

class MemoryStorage {
    constructor(entries = {}) {
        this.entries = new Map(Object.entries(entries));
    }

    getItem(key) {
        return this.entries.has(key) ? this.entries.get(key) : null;
    }

    setItem(key, value) {
        this.entries.set(String(key), String(value));
    }

    removeItem(key) {
        this.entries.delete(String(key));
    }
}

function loadByok({ localStorage, sessionStorage }) {
    const source = fs.readFileSync(BYOK_PATH, 'utf8');
    // Keep the production closure private while exposing only the two storage
    // helpers needed to verify reload behavior in this isolated test VM.
    const instrumented = source.replace(
        /\}\)\(\);\s*$/,
        `window.__byokStorageTest = {
            getProviderCredentialToken,
            setProviderCredentialToken,
            takeProviderApiKeyForSave,
            restoreProviderApiKeyAfterFailedSave,
            renderRootSource: renderRoot.toString(),
            saveProviderSource: saveProvider.toString(),
        };
        })();`,
    );
    const listeners = new Map();
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
            addEventListener(type, callback) {
                listeners.set(type, callback);
            },
        },
        window: {
            localStorage,
            sessionStorage,
        },
    };
    vm.runInNewContext(instrumented, context, { filename: BYOK_PATH });
    return context.window;
}

test('BYOK reload storage contains only a sealed token and removes legacy plaintext', () => {
    const rawKey = 'sk-legacy-plaintext-secret';
    const provider = {
        id: 'provider-1',
        provider: 'openai',
        name: 'OpenAI',
        api_key: rawKey,
    };
    const localStorage = new MemoryStorage({
        [LOCAL_DATA_KEY]: JSON.stringify({ version: 1, providers: [provider], models: [] }),
    });
    const sessionStorage = new MemoryStorage({
        [RAW_SESSION_KEY]: JSON.stringify({ 'provider-1': rawKey }),
    });

    const firstLoad = loadByok({ localStorage, sessionStorage });

    assert.equal(sessionStorage.getItem(RAW_SESSION_KEY), null);
    assert.doesNotMatch(localStorage.getItem(LOCAL_DATA_KEY), /sk-legacy-plaintext-secret|api_key/);
    assert.doesNotMatch(
        [...localStorage.entries.values(), ...sessionStorage.entries.values()].join('\n'),
        /sk-legacy-plaintext-secret/,
    );

    const expiresAt = '2099-01-01T00:00:00Z';
    firstLoad.__byokStorageTest.setProviderCredentialToken(
        'provider-1',
        'opaque-server-sealed-token',
        expiresAt,
    );
    assert.deepEqual(JSON.parse(sessionStorage.getItem(SEALED_SESSION_KEY)), {
        'provider-1': {
            token: 'opaque-server-sealed-token',
            expires_at: expiresAt,
        },
    });

    // A fresh JS context models a same-tab reload while retaining sessionStorage.
    const reloaded = loadByok({ localStorage, sessionStorage });
    assert.equal(
        reloaded.__byokStorageTest.getProviderCredentialToken({ id: 'provider-1' }),
        'opaque-server-sealed-token',
    );

    reloaded.BYOK.clearProviderSessionCredentials();
    assert.equal(sessionStorage.getItem(SEALED_SESSION_KEY), null);
});

test('BYOK reload removes deprecated settings only from Anthropic models', () => {
    const legacySettings = {
        max_tokens: 100,
        output_format: { type: 'json_schema' },
        temperature: 0.2,
        top_k: 10,
        top_p: 0.9,
    };
    const localStorage = new MemoryStorage({
        [LOCAL_DATA_KEY]: JSON.stringify({
            version: 1,
            providers: [],
            models: [
                { model_id: 'anthropic-model', provider: 'anthropic', settings: legacySettings },
                { model_id: 'openai-model', provider: 'openai', settings: legacySettings },
            ],
        }),
    });

    loadByok({ localStorage, sessionStorage: new MemoryStorage() });

    const stored = JSON.parse(localStorage.getItem(LOCAL_DATA_KEY));
    assert.deepEqual(stored.models[0].settings, { max_tokens: 100 });
    assert.deepEqual(stored.models[1].settings, legacySettings);
});

test('BYOK API keys stay masked without entering a browser password workflow', () => {
    const window = loadByok({
        localStorage: new MemoryStorage(),
        sessionStorage: new MemoryStorage(),
    });
    const harness = window.__byokStorageTest;
    const apiKeyInput = { value: '  sk-dummy-provider-key  ', disabled: false };

    assert.match(
        harness.renderRootSource,
        /id="byokProviderForm" autocomplete="off"/,
    );
    assert.match(
        harness.renderRootSource,
        /id="byokProviderApiKey" type="password"[^>]*autocomplete="off"[^>]*autocapitalize="none"/,
    );
    assert.doesNotMatch(harness.renderRootSource, /byokProviderApiKey[^>]*autocomplete="new-password"/);
    assert.match(harness.saveProviderSource, /notifyModelChange\(\{ rerender: false \}\)/);

    const apiKey = harness.takeProviderApiKeyForSave(apiKeyInput);

    assert.equal(apiKey, 'sk-dummy-provider-key');
    assert.equal(apiKeyInput.value, '');
    assert.equal(apiKeyInput.disabled, true);

    harness.restoreProviderApiKeyAfterFailedSave(apiKeyInput, apiKey);
    assert.equal(apiKeyInput.value, 'sk-dummy-provider-key');
    assert.equal(apiKeyInput.disabled, false);
});
