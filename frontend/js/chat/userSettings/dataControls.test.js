const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

/** Minimal DOM element used by the account-archive settings tests. */
class FakeElement {
    constructor() {
        this.attributes = {};
        this.children = [];
        this.classNames = new Set();
        this.disabled = false;
        this.files = [];
        this.hidden = false;
        this.listeners = {};
        this.style = {};
        this.textContent = '';
        this.value = '';
        this.clickCount = 0;
        this.focusCount = 0;
        this.classList = {
            add: (name) => this.classNames.add(name),
            remove: (name) => this.classNames.delete(name),
            contains: (name) => this.classNames.has(name),
            toggle: (name, force) => {
                const enabled = force === undefined ? !this.classNames.has(name) : Boolean(force);
                if (enabled) this.classNames.add(name);
                else this.classNames.delete(name);
                return enabled;
            },
        };
    }

    addEventListener(name, handler) {
        this.listeners[name] = handler;
    }

    appendChild(child) {
        this.children.push(child);
        child.parentNode = this;
        return child;
    }

    click() {
        this.clickCount += 1;
    }

    focus() {
        this.focusCount += 1;
    }

    remove() {
        if (!this.parentNode) return;
        this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    }

    removeAttribute(name) {
        delete this.attributes[name];
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }
}

function response({ ok = true, json = {}, blob = {} } = {}) {
    return {
        ok,
        json: async () => json,
        blob: async () => blob,
    };
}

/** Load dataControls.js with the supported account and ChatGPT transfer calls. */
function createHarness({ fetchImpl } = {}) {
    const ids = [
        'dataControlDownloadAllButton',
        'dataControlUploadAllButton',
        'dataControlUploadInput',
        'dataControlBundleSection',
        'dataControlCta',
        'dataControlImportPreview',
        'dataControlImportPreviewSummary',
        'dataControlImportPreviewDetails',
        'dataControlImportPreviewWarning',
        'dataControlImportPreviewStart',
        'dataControlImportPreviewCancel',
        'dataControlChatGPTSection',
        'chatgptImportButton',
        'chatgptImportInput',
        'chatgptImportPreview',
        'chatgptImportPreviewSummary',
        'chatgptImportPreviewWarning',
        'chatgptImportStart',
        'chatgptImportCancel',
        'dataControlStatusBanner',
        'dataControlStatusBannerMessage',
        'dataControlStatusBannerBar',
    ];
    const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement()]));
    elements.dataControlImportPreview.hidden = true;
    elements.chatgptImportPreview.hidden = true;
    elements.dataControlStatusBanner.hidden = true;

    const fetches = [];
    const notifications = { errors: [], successes: [], warnings: [] };
    const dispatched = [];
    const downloads = [];
    const document = {
        body: new FakeElement(),
        createElement: () => new FakeElement(),
        getElementById: (id) => elements[id] || null,
    };
    const window = {
        authedFetch: async (url, options = {}) => {
            fetches.push({ url, options });
            if (fetchImpl) return fetchImpl(url, options);
            return response();
        },
        dispatchEvent: (event) => dispatched.push(event),
        formatTranslation: (_key, fallback, variables = {}) => String(fallback).replace(
            /\{(\w+)\}/g,
            (_match, token) => String(variables[token] ?? ''),
        ),
        getTranslation: (_key, fallback) => fallback,
        notifyError: (message) => notifications.errors.push(message),
        notifySuccess: (message) => notifications.successes.push(message),
        notifyWarning: (message) => notifications.warnings.push(message),
    };
    const context = {
        console,
        CustomEvent: class CustomEvent {
            constructor(type, init) {
                this.type = type;
                this.detail = init?.detail;
            }
        },
        document,
        FormData: class FormData {
            constructor() {
                this.values = new Map();
            }

            append(name, value) {
                this.values.set(name, value);
            }

            get(name) {
                return this.values.get(name);
            }
        },
        setTimeout: (handler) => handler(),
        URL: {
            createObjectURL: (blob) => {
                downloads.push(blob);
                return 'blob:account-archive';
            },
            revokeObjectURL() {},
        },
        window,
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'dataControls.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'dataControls.js' });

    return { dispatched, downloads, elements, fetches, notifications, source, window };
}

test('account archive picker leaves type validation to the in-app preflight', () => {
    const html = fs.readFileSync(path.join(__dirname, '../../../index.html'), 'utf8');
    const [uploadInput = ''] = html.match(/<input\b[^>]*id="dataControlUploadInput"[^>]*>/) || [];

    assert.ok(uploadInput, 'account archive file input must exist');
    assert.doesNotMatch(uploadInput, /\saccept=/);
});

test('complete export calls only the unified user-data endpoint', async () => {
    const archiveBlob = { kind: 'complete-account-json' };
    const harness = createHarness({
        fetchImpl: async () => response({ blob: archiveBlob }),
    });

    await harness.elements.dataControlDownloadAllButton.listeners.click();

    assert.deepEqual(harness.fetches.map(({ url }) => url), ['/api/v1/users/export']);
    assert.equal(harness.fetches[0].options.method, 'GET');
    assert.equal(harness.downloads[0], archiveBlob);
    assert.equal(harness.notifications.successes.length, 1);
});

test('complete import previews then posts the original archive once', async () => {
    const payload = {
        export_type: 'user_data',
        export_version: 1.0,
        user: { name: 'Example' },
        notes: [{ title: 'Dormant note' }],
        memories: [{ content: 'Dormant memory' }],
    };
    const harness = createHarness({
        fetchImpl: async () => response({
            json: { imported: ['user', 'notes', 'memories'], errors: [] },
        }),
    });
    harness.elements.dataControlUploadInput.files = [{
        name: 'account.json',
        text: async () => JSON.stringify(payload),
    }];

    await harness.elements.dataControlUploadInput.listeners.change();

    assert.equal(harness.fetches.length, 0, 'preview must not mutate data');
    assert.equal(harness.elements.dataControlImportPreview.hidden, false);
    assert.match(harness.elements.dataControlImportPreviewSummary.textContent, /account\.json/);

    await harness.elements.dataControlImportPreviewStart.listeners.click();

    assert.equal(harness.fetches.length, 1);
    assert.equal(harness.fetches[0].url, '/api/v1/users/import/self');
    assert.equal(harness.fetches[0].options.method, 'POST');
    assert.deepEqual(JSON.parse(harness.fetches[0].options.body), payload);
    assert.equal(harness.dispatched[0].type, 'dataControls:importedDataChanged');
    assert.deepEqual(
        Array.from(harness.dispatched[0].detail.sections),
        ['user', 'notes', 'memories'],
    );
});

test('complete import surfaces unresolved automation context as a warning', async () => {
    const payload = {
        export_type: 'user_data',
        export_version: 1.0,
        user: { name: 'Example' },
        automations: [{ id: 'automation-1' }],
    };
    const harness = createHarness({
        fetchImpl: async () => response({
            json: {
                imported: ['automations'],
                warnings: [{
                    code: 'automation_mcp_servers_unavailable',
                    inaccessible_mcp_server_ids: ['missing-server'],
                }],
                errors: [],
            },
        }),
    });
    harness.elements.dataControlUploadInput.files = [{
        name: 'account.json',
        text: async () => JSON.stringify(payload),
    }];

    await harness.elements.dataControlUploadInput.listeners.change();
    await harness.elements.dataControlImportPreviewStart.listeners.click();

    assert.equal(harness.notifications.errors.length, 0);
    assert.equal(harness.notifications.successes.length, 0);
    assert.equal(harness.notifications.warnings.length, 1);
    assert.match(harness.notifications.warnings[0], /needs review: 1/);
});

test('invalid JSON account archives never reach the import endpoint', async () => {
    const harness = createHarness();
    harness.elements.dataControlUploadInput.files = [{
        name: 'category-export.json',
        text: async () => JSON.stringify({ export_type: 'notes', export_version: 1.0 }),
    }];

    await harness.elements.dataControlUploadInput.listeners.change();

    assert.equal(harness.fetches.length, 0);
    assert.equal(harness.elements.dataControlImportPreview.hidden, true);
    assert.equal(harness.notifications.errors.length, 1);
});

test('ChatGPT import previews then uploads the original ZIP as multipart data', async () => {
    const archive = { name: 'chatgpt-export.zip', size: 1024 };
    const harness = createHarness({
        fetchImpl: async () => response({
            json: {
                imported_chats: 2,
                imported_messages: 8,
                imported_files: 1,
                skipped_chats: 1,
                skipped_duplicates: 1,
                shared_index_entries: 0,
            },
        }),
    });
    harness.elements.chatgptImportInput.files = [archive];

    harness.elements.chatgptImportInput.listeners.change();

    assert.equal(harness.fetches.length, 0, 'preview must not mutate data');
    assert.equal(harness.elements.chatgptImportPreview.hidden, false);
    assert.match(harness.elements.chatgptImportPreviewSummary.textContent, /chatgpt-export\.zip/);

    await harness.elements.chatgptImportStart.listeners.click();

    assert.equal(harness.fetches.length, 1);
    assert.equal(harness.fetches[0].url, '/api/v1/chats/import/chatgpt');
    assert.equal(harness.fetches[0].options.method, 'POST');
    assert.equal(harness.fetches[0].options.headers, undefined);
    assert.equal(harness.fetches[0].options.body.get('archive'), archive);
    assert.deepEqual(Array.from(harness.dispatched[0].detail.sections), ['chats', 'files']);
    assert.equal(harness.dispatched[0].detail.refreshChats, true);
    assert.equal(harness.dispatched[0].detail.refreshFiles, true);
    assert.equal(harness.notifications.successes.length, 1);
});

test('non-ZIP ChatGPT selections never reach the import endpoint', () => {
    const harness = createHarness();
    harness.elements.chatgptImportInput.files = [{ name: 'conversations.json', size: 10 }];

    harness.elements.chatgptImportInput.listeners.change();

    assert.equal(harness.fetches.length, 0);
    assert.equal(harness.elements.chatgptImportPreview.hidden, true);
    assert.equal(harness.notifications.errors.length, 1);
});

test('only allow_user_data controls account archive visibility', () => {
    const harness = createHarness();

    const hidden = harness.window.updateDataControlAvailability({
        allow_user_data: false,
        allow_notes: true,
        allow_memories: true,
    });
    assert.equal(hidden.anyEnabled, false);
    assert.equal(harness.elements.dataControlBundleSection.style.display, 'none');
    assert.equal(harness.elements.dataControlChatGPTSection.style.display, 'none');

    const visible = harness.window.updateDataControlAvailability({ allow_user_data: true });
    assert.equal(visible.allEnabled, true);
    assert.equal(harness.elements.dataControlBundleSection.style.display, '');
    assert.equal(harness.elements.dataControlChatGPTSection.style.display, '');
});

test('self-service frontend exposes ChatGPT migration without category or OpenWebUI endpoints', () => {
    const harness = createHarness();

    assert.doesNotMatch(
        harness.source,
        /\/api\/v1\/(?:skills|automations|notes|memories|todos|files)\/(?:export|import)/,
    );
    assert.match(harness.source, /\/api\/v1\/chats\/import\/chatgpt/);
    assert.doesNotMatch(harness.source, /openwebui|JSZip/i);
});
