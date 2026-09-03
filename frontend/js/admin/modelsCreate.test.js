const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeElement {
    constructor() {
        this.children = [];
        this.hidden = false;
        this._innerHTML = '';
    }

    set innerHTML(value) {
        this._innerHTML = String(value ?? '');
        this.children = [];
    }

    get innerHTML() {
        return this._innerHTML;
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }
}

const createHarness = () => {
    const elements = new Map([
        ['page-models', new FakeElement()],
        ['page-models-create-step-1', new FakeElement()],
        ['page-models-create-step-2', new FakeElement()],
        ['page-models-create-step-3', new FakeElement()],
        ['modelCreateProviderGrid', new FakeElement()],
    ]);
    const placeholderCalls = [];
    const document = {
        getElementById(id) {
            return elements.get(id) || null;
        },
        querySelector() {
            return null;
        },
        querySelectorAll() {
            return [];
        },
        addEventListener() {},
    };
    const window = {
        Icons: { omlorix: '<svg></svg>' },
        modelsApi: {
            async fetchProviderList() {
                return [];
            },
        },
        async authedFetch() {
            return {
                ok: true,
                async json() {
                    return [];
                },
            };
        },
        createAdminLoadingPlaceholder(options) {
            const placeholder = { kind: 'loading', options };
            placeholderCalls.push(placeholder);
            return placeholder;
        },
        createAdminEmptyPlaceholder(options) {
            const placeholder = { kind: 'empty', options };
            placeholderCalls.push(placeholder);
            return placeholder;
        },
    };
    const context = {
        Icons: window.Icons,
        console,
        document,
        window,
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'modelsCreate.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'modelsCreate.js' });

    return {
        providerGrid: elements.get('modelCreateProviderGrid'),
        placeholderCalls,
        window,
    };
};

test('model creation replaces the provider spinner with an empty state when no providers exist', async () => {
    const { providerGrid, placeholderCalls, window } = createHarness();

    window.startModelsCreateFlow();
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(placeholderCalls[0].kind, 'loading');
    assert.equal(providerGrid.children.length, 1);
    assert.equal(providerGrid.children[0].kind, 'empty');
    assert.equal(
        providerGrid.children[0].options.title,
        'No providers available. Create a provider first.'
    );
});
