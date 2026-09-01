const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeElement {
    constructor(tagName = 'div') {
        this.tagName = String(tagName).toUpperCase();
        this.listeners = new Map();
    }

    addEventListener(type, listener) {
        this.listeners.set(type, listener);
    }

    removeEventListener(type, listener) {
        if (this.listeners.get(type) === listener) {
            this.listeners.delete(type);
        }
    }

    appendChild() {}
}

function createHarness() {
    const elements = new Map([
        ['deepResearchSettingsBack', new FakeElement('button')],
        ['deepResearchSettingsFields', new FakeElement('div')],
        ['deepResearchSettingsStatus', new FakeElement('div')],
    ]);
    const calls = { init: 0, teardown: 0 };
    const context = {
        console,
        document: {
            getElementById(id) {
                return elements.get(id) || null;
            },
            createElement(tagName) {
                return new FakeElement(tagName);
            },
        },
        window: {
            createSettingsPageController(config) {
                context.controllerConfig = config;
                return {
                    init() {
                        calls.init += 1;
                    },
                    teardown() {
                        calls.teardown += 1;
                    },
                };
            },
            getTranslation(_key, fallback) {
                return fallback;
            },
            notifyError() {},
        },
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'deepResearch.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'deepResearch.js' });

    return { calls, config: context.controllerConfig, window: context.window };
}

test('deep research reloads fields after mode and native provider saves', () => {
    const harness = createHarness();

    harness.window.initDeepResearchSettingsPage();
    assert.equal(harness.calls.init, 1);

    harness.config.onFieldSaved({ fieldKey: 'execution_mode' });
    assert.deepEqual(harness.calls, { init: 2, teardown: 1 });

    harness.config.onFieldSaved({ fieldKey: 'native_provider_id' });
    assert.deepEqual(harness.calls, { init: 3, teardown: 2 });

    harness.config.onFieldSaved({ fieldKey: 'native_model_name' });
    harness.config.onFieldSaved({ fieldKey: 'provider_id' });
    assert.deepEqual(harness.calls, { init: 3, teardown: 2 });
});
