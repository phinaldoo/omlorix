const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('memories refresh without a profile card or review tasks', async () => {
    const elements = {
        memoriesList: { innerHTML: '' },
        workspaceSectionMemories: { hidden: false, style: {}, getAttribute: () => null },
    };
    let poll;
    let timerCount = 0;
    const context = vm.createContext({
        document: { getElementById: (id) => elements[id] || null },
        setTimeout: (callback) => {
            poll = callback;
            timerCount += 1;
            return { unref() {} };
        },
    });
    context.window = context;
    const source = fs.readFileSync(path.join(__dirname, 'memories.js'), 'utf8');
    vm.runInContext(source, context);
    vm.runInContext(`
        MemoriesState.memories = [{
            id: 'aging-fact', content: 'The user uses Canva for presentations.',
            lifecycle_state: 'review', kind: 'preference', stability: 'slow',
        }];
        MemoriesState.profile = {
            last_run_status: 'processing',
        };
        MemoriesManager.renderMemories();
        MemoriesManager.scheduleProfilePoll();
    `, context);

    const cards = elements.memoriesList.innerHTML;
    assert.match(cards, /The user uses Canva for presentations\./);
    assert.match(cards, /data-memory-action="edit"/);
    assert.match(cards, /data-memory-action="delete"/);
    assert.doesNotMatch(cards, /needs-review|data-memory-action="confirm"/);

    const markup = fs.readFileSync(path.join(__dirname, '../../index.html'), 'utf8');
    assert.doesNotMatch(markup, /id="memoriesProfile|class="memories-profile/);

    context.MemoriesAPI.fetchProfile = async () => ({ last_run_status: 'updated' });
    context.MemoriesAPI.fetchMemories = async () => [
        { id: 'new-fact', content: 'The user prefers concise answers.' },
    ];
    assert.equal(timerCount, 1);
    await poll();
    assert.match(elements.memoriesList.innerHTML, /The user prefers concise answers\./);
    assert.equal(timerCount, 1, 'polling stops after the update completes');
});
