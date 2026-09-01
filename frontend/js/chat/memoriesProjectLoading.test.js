const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const MEMORIES_PATH = path.join(__dirname, 'memories.js');

function response({ ok, payload }) {
    return {
        ok,
        headers: { get: () => 'application/json' },
        json: async () => payload,
    };
}

test('project loading retries after a transient failure and caches success', async () => {
    let attempts = 0;
    const context = {
        document: { getElementById: () => null },
        window: null,
    };
    context.window = context;
    context.getTranslation = (_key, fallback) => fallback;
    context.authedFetch = async () => {
        attempts += 1;
        if (attempts === 1) {
            return response({ ok: false, payload: { detail: 'Temporary failure' } });
        }
        return response({ ok: true, payload: [{ id: 'project-1', title: 'Project' }] });
    };

    vm.runInNewContext(fs.readFileSync(MEMORIES_PATH, 'utf8'), context, {
        filename: MEMORIES_PATH,
    });

    await context.MemoriesManager.loadProjects();
    await context.MemoriesManager.loadProjects();
    assert.equal(attempts, 2);

    await context.MemoriesManager.loadProjects();
    assert.equal(attempts, 2);
});
