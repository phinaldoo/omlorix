const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup(fetch, options = {}) {
    const source = fs.readFileSync(path.join(__dirname, 'rendering/visualization-bridge.js'), 'utf8');
    const states = [];
    const sandbox = { window: { fetch }, document: { body: { dataset: {} } }, visualizationStateStores: new Set(), TextEncoder, setTimeout, clearTimeout };
    vm.createContext(sandbox);
    vm.runInContext(source + '\nthis.createStore = createVisualizationStateStore;', sandbox);
    const store = sandbox.createStore({ messageId: 'message', toolCallId: 'call', ...options }, state => states.push(state));
    return { store, states };
}
const response = value => ({ ok: true, status: 200, json: async () => value });

test('state saves coalesce, retain private/model partitions, and preserve writes during a request', async () => {
    let revision = 2;
    const writes = [];
    let finish;
    const { store } = setup(async (_, init) => {
        if (init.method === 'GET') return response({ revision, widgetState: { privateContent: { zoom: 4 } }, design: {} });
        writes.push(JSON.parse(init.body));
        if (writes.length === 1) await new Promise(resolve => { finish = resolve; });
        return response({ revision: ++revision });
    });
    await store.load();
    assert.equal(store.snapshot.widgetState.privateContent.zoom, 4);
    const first = store.update({ widgetState: { modelContent: { country: 'Germany' }, privateContent: { zoom: 5 } } });
    const second = store.update({ design: { variants: { mockup: 'Focus' } } });
    const saving = store.flush();
    await new Promise(resolve => setImmediate(resolve));
    const third = store.update({ design: { variants: { mockup: 'Discover' } } });
    finish();
    await Promise.all([first, second, third, saving]);
    assert.equal(writes.length, 2);
    assert.equal(writes[0].revision, 2);
    assert.equal(writes[1].revision, 3);
    assert.equal(writes[1].design.variants.mockup, 'Discover');
    assert.equal(writes[1].widgetState.privateContent.zoom, 5);
    store.dispose();
});

test('revision conflicts are visible and never silently overwrite another tab', async () => {
    const { store, states } = setup(async (_, init) => init.method === 'GET' ? response({ revision: 0 }) : { ok: false, status: 409 });
    await store.load();
    const save = store.update({ widgetState: { modelContent: 'new' } });
    const assertion = assert.rejects(save, /conflict/);
    await assert.rejects(store.flush(), /conflict/);
    await assertion;
    assert.equal(states.at(-1), 'conflict');
    store.dispose();
});

test('temporary state never accesses the server and rejects oversized UTF-8 snapshots', async () => {
    let temporary;
    const { store } = setup(() => { throw new Error('Unexpected network'); }, { temporary: true, onStateChange: value => { temporary = value; } });
    await store.load();
    const save = store.update({ widgetState: { privateContent: { selected: 1 } } });
    await store.flush(); await save;
    assert.equal(temporary.widgetState.privateContent.selected, 1);
    await assert.rejects(store.update({ widgetState: { privateContent: 'é'.repeat(9000) } }));
    store.dispose();
});

test('live widgets queue saves until the assistant message receives its persisted ID', async () => {
    let id = '';
    const urls = [];
    const { store } = setup(async (url, init) => {
        urls.push(url);
        return response({ revision: init.method === 'GET' ? 0 : 1 });
    }, { messageId: '', getMessageId: () => id });
    await store.load();
    const saving = store.update({ widgetState: { modelContent: 'selection' } });
    await store.flush(); await saving;
    assert.equal(urls.length, 0);
    id = 'persisted-assistant';
    await store.flush();
    assert.equal(urls.length, 2);
    assert.ok(urls.every(url => url.includes('/persisted-assistant/')));
    store.dispose();
});

test('a failed initial load can be retried without inventing an empty saved state', async () => {
    let online = false;
    const { store, states } = setup(async () => {
        if (!online) throw new Error('offline');
        return response({ revision: 4, widgetState: { modelContent: 'restored' }, design: {} });
    });
    await store.load();
    assert.equal(states.at(-1), 'error');
    online = true;
    assert.equal(await store.retry(), true);
    assert.equal(store.snapshot.widgetState.modelContent, 'restored');
    store.dispose();
});
