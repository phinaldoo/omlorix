const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadSharedDataCache(fetchImpl) {
    const source = fs.readFileSync(path.join(__dirname, 'sharedDataCache.js'), 'utf8');
    const context = {
        fetch: fetchImpl,
        window: null,
        globalThis: null,
    };
    context.globalThis = context;
    vm.runInNewContext(source, context, { filename: 'sharedDataCache.js' });
    return context;
}

test('shared data cache deduplicates concurrent user model requests', async () => {
    let calls = 0;
    const context = loadSharedDataCache(async (url) => {
        calls += 1;
        return {
            ok: true,
            json: async () => [{ model_id: 'model-a', url }],
        };
    });

    const [first, second] = await Promise.all([
        context.getCachedUserModels(),
        context.getCachedUserModels(),
    ]);

    assert.equal(calls, 1);
    assert.equal(first, second);
    assert.deepEqual(first, [{ model_id: 'model-a', url: '/api/v1/llm/models/user' }]);
});

test('shared data cache can force refresh settings init data', async () => {
    let calls = 0;
    const context = loadSharedDataCache(async () => {
        calls += 1;
        return {
            ok: true,
            json: async () => ({ chat: { sequence: calls } }),
        };
    });

    const first = await context.getCachedUserSettingsInit();
    const cached = await context.getCachedUserSettingsInit();
    const refreshed = await context.getCachedUserSettingsInit({ forceRefresh: true });

    assert.equal(calls, 2);
    assert.equal(first, cached);
    assert.deepEqual(refreshed, { chat: { sequence: 2 } });
});
