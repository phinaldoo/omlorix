const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const sourcePath = path.join(__dirname, 'customPythonTools.js');
const source = fs.readFileSync(sourcePath, 'utf8');

class FakeElement {
    constructor({ hidden = false, value = '' } = {}) {
        this.hidden = hidden;
        this.value = value;
        this.checked = false;
        this.disabled = false;
        this.textContent = '';
        this.innerHTML = '';
        this.className = '';
        this.dataset = {};
        this.listeners = new Map();
        this.classList = { add() {}, remove() {} };
    }

    addEventListener(type, listener) {
        this.listeners.set(type, listener);
    }

    dispatch(type, event = {}) {
        return this.listeners.get(type)?.({
            preventDefault() {},
            target: this,
            currentTarget: this,
            ...event,
        });
    }

    setAttribute(name, value) {
        this[name] = String(value);
    }

    querySelector() {
        return null;
    }
}

function jsonResponse(payload, { ok = true, status = 200 } = {}) {
    const body = JSON.stringify(payload);
    return {
        ok,
        status,
        headers: {
            get(name) {
                if (name.toLowerCase() === 'content-length') return String(body.length);
                if (name.toLowerCase() === 'content-type') return 'application/json';
                return null;
            },
        },
        async text() {
            return body;
        },
    };
}

function createHarness({ saveResponse, testResponse, translations = {} } = {}) {
    const elements = new Map();
    const element = (id, options) => {
        if (!elements.has(id)) elements.set(id, new FakeElement(options));
        return elements.get(id);
    };
    const pages = {
        'custom-python-tools': element('page-custom-python-tools'),
        'custom-python-tools-create': element('page-custom-python-tools-create', { hidden: true }),
        'custom-python-tools-edit': element('page-custom-python-tools-edit', { hidden: true }),
    };
    const notifications = [];
    const dirtyChecks = [];
    let prompts = 0;
    let activePage = 'custom-python-tools';
    let guard = null;

    const activate = (page) => {
        activePage = page;
        Object.entries(pages).forEach(([key, pageElement]) => {
            pageElement.hidden = key !== page;
        });
    };
    const unsavedChangesManager = {
        register(candidate) {
            guard = candidate;
        },
        confirmIfNeeded({ id }) {
            const relevantGuard = guard && (!id || guard.id === id) && guard.isActive();
            const dirty = Boolean(relevantGuard && guard.isDirty());
            dirtyChecks.push(dirty);
            if (dirty) {
                prompts += 1;
                return true;
            }
            return false;
        },
    };
    const showPage = (page) => {
        if (page === activePage) return;
        const prompted = unsavedChangesManager.confirmIfNeeded({});
        if (!prompted) activate(page);
    };
    const authedFetch = async (url, options = {}) => {
        const method = options.method || 'GET';
        if (method === 'GET' && url.endsWith('/tool-1')) {
            return jsonResponse({
                id: 'tool-1',
                enabled: true,
                timeout_seconds: 30,
                source_code: 'def run_tool(arguments, context):\n    return arguments',
                tool_schema: null,
            });
        }
        if (method === 'GET') return jsonResponse([]);
        if (url.endsWith('/test') && testResponse) return testResponse({ method, url, options });
        if (saveResponse) return saveResponse({ method, url, options });
        return jsonResponse({ id: 'tool-1' });
    };
    const document = {
        body: { appendChild() {}, removeChild() {} },
        getElementById(id) {
            return element(id);
        },
        createElement() {
            return new FakeElement();
        },
        createDocumentFragment() {
            return { appendChild() {} };
        },
    };
    const window = {
        authedFetch,
        getTranslation(key, fallback) { return translations[key] || fallback; },
        notifyError(message) { notifications.push(['error', message]); },
        notifySuccess(message) { notifications.push(['success', message]); },
        registerEscapeHandler() { return {}; },
        unsavedChangesManager,
    };
    const context = vm.createContext({
        Blob,
        Icons: { code: '', create: '', trash: '' },
        URL,
        console,
        document,
        showPage,
        window,
    });
    vm.runInContext(source, context, { filename: sourcePath });
    window.initCustomPythonToolsPage();

    const openForm = async (mode) => {
        if (mode === 'create') {
            element('customPythonToolsCreateButton').dispatch('click');
        } else {
            element('customPythonToolsList').dispatch('click', {
                target: {
                    closest(selector) {
                        return selector === '[data-action="edit"]'
                            ? { dataset: { toolId: 'tool-1' } }
                            : null;
                    },
                },
            });
            await settle();
        }
    };

    return {
        dirtyChecks,
        element,
        get activePage() { return activePage; },
        get guard() { return guard; },
        notifications,
        openForm,
        get prompts() { return prompts; },
    };
}

async function settle() {
    await new Promise((resolve) => setImmediate(resolve));
}

const formControls = {
    create: {
        cancel: 'customPythonToolsCreateCancel',
        form: 'customPythonToolsCreateForm',
        source: 'customPythonToolsCreateSource',
    },
    edit: {
        cancel: 'customPythonToolsEditCancel',
        form: 'customPythonToolsEditForm',
        source: 'customPythonToolsEditSource',
    },
};

for (const mode of ['create', 'edit']) {
    test(`successful ${mode} save returns to the tool list without a discard prompt`, async () => {
        const harness = createHarness();
        const controls = formControls[mode];
        await harness.openForm(mode);
        harness.element(controls.source).value += '\n# changed';

        assert.equal(harness.guard.isDirty(), true);
        harness.element(controls.form).dispatch('submit');
        await settle();

        assert.equal(harness.activePage, 'custom-python-tools');
        assert.equal(harness.prompts, 0);
        assert.equal(harness.dirtyChecks.at(-1), false);
        assert.deepEqual(harness.notifications, [['success', 'Custom Python tool saved.']]);
    });

    test(`failed ${mode} save keeps the unsaved-change protection active`, async () => {
        const harness = createHarness({
            saveResponse: async () => jsonResponse(
                { detail: 'Save failed' },
                { ok: false, status: 500 },
            ),
        });
        const controls = formControls[mode];
        await harness.openForm(mode);
        harness.element(controls.source).value += '\n# changed';

        harness.element(controls.form).dispatch('submit');
        await settle();
        harness.element(controls.cancel).dispatch('click');

        assert.equal(harness.activePage, `custom-python-tools-${mode}`);
        assert.equal(harness.prompts, 1);
        assert.equal(harness.guard.isDirty(), true);
        assert.deepEqual(harness.notifications, [['error', 'Save failed']]);
    });
}

test('edits made during a successful save are not mistaken for saved changes', async () => {
    let resolveSave;
    const pendingSave = new Promise((resolve) => {
        resolveSave = resolve;
    });
    const harness = createHarness({ saveResponse: () => pendingSave });
    const controls = formControls.edit;
    await harness.openForm('edit');
    harness.element(controls.source).value += '\n# submitted';
    harness.element(controls.form).dispatch('submit');
    harness.element(controls.source).value += '\n# changed while saving';

    resolveSave(jsonResponse({ id: 'tool-1' }));
    await settle();

    assert.equal(harness.activePage, 'custom-python-tools-edit');
    assert.equal(harness.prompts, 1);
    assert.equal(harness.guard.isDirty(), true);
});

test('required test arguments use the localized structured backend error', async () => {
    const localizedMessage = 'Das erforderliche Testargument „arguments.message“ fehlt.';
    const harness = createHarness({
        testResponse: async () => jsonResponse(
            {
                detail: {
                    code: 'custom_tool_argument_required',
                    path: 'arguments.message',
                },
            },
            { ok: false, status: 400 },
        ),
        translations: {
            custom_tools_argument_required: 'Das erforderliche Testargument „{path}“ fehlt.',
        },
    });
    await harness.openForm('create');

    harness.element('customPythonToolsCreateTest').dispatch('click');
    await settle();

    assert.equal(harness.element('customPythonToolsCreateStatus').textContent, localizedMessage);
    assert.deepEqual(harness.notifications, [['error', localizedMessage]]);
});

test('other argument-schema errors use localized structured backend errors', async () => {
    const localizedMessage = 'Das Testargument „arguments.message“ entspricht nicht der Tool-Definition.';
    const harness = createHarness({
        testResponse: async () => jsonResponse(
            {
                detail: {
                    code: 'custom_tool_argument_invalid',
                    path: 'arguments.message',
                },
            },
            { ok: false, status: 400 },
        ),
        translations: {
            custom_tools_argument_invalid: 'Das Testargument „{path}“ entspricht nicht der Tool-Definition.',
        },
    });
    await harness.openForm('create');

    harness.element('customPythonToolsCreateTest').dispatch('click');
    await settle();

    assert.equal(harness.element('customPythonToolsCreateStatus').textContent, localizedMessage);
    assert.deepEqual(harness.notifications, [['error', localizedMessage]]);
});

test('malformed test argument JSON uses localized client validation', async () => {
    const localizedMessage = 'Die Testargumente müssen ein gültiges JSON-Objekt sein.';
    const harness = createHarness({
        translations: {
            custom_tools_test_args_label: 'Die Testargumente',
            custom_tools_test_args_invalid_json: '{label} müssen ein gültiges JSON-Objekt sein.',
        },
    });
    await harness.openForm('create');
    harness.element('customPythonToolsCreateArguments').value = '{';

    harness.element('customPythonToolsCreateTest').dispatch('click');
    await settle();

    assert.equal(harness.element('customPythonToolsCreateStatus').textContent, localizedMessage);
    assert.deepEqual(harness.notifications, [['error', localizedMessage]]);
});

test('custom-tool argument validation copy exists in every admin locale', () => {
    const localeRoot = path.resolve(__dirname, '../../i18n');
    for (const locale of fs.readdirSync(localeRoot)) {
        const adminPath = path.join(localeRoot, locale, 'admin.json');
        if (!fs.existsSync(adminPath)) continue;
        const translations = JSON.parse(fs.readFileSync(adminPath, 'utf8'));
        assert.match(
            translations.custom_tools_argument_required,
            /\{path\}/,
            `${locale} required-argument message must retain {path}`
        );
        assert.match(
            translations.custom_tools_argument_invalid,
            /\{path\}/,
            `${locale} invalid-argument message must retain {path}`
        );
        assert.match(
            translations.custom_tools_test_args_invalid_json,
            /\{label\}/,
            `${locale} malformed-JSON message must retain {label}`
        );
    }
});
