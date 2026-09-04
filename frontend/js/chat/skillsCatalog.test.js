const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function harness(fetch) {
    const attrs = new Map();
    const grid = { innerHTML: '', setAttribute: (k, v) => attrs.set(k, v), removeAttribute: k => attrs.delete(k), querySelector: () => null };
    const document = { readyState: 'loading', addEventListener() {}, getElementById: id => id === 'skillsGrid' ? grid : null };
    const window = { authedFetch: fetch, WorkspaceIconUtils: { createWorkspaceIconPicker: () => ({}), getWorkspaceIconOptions: () => [{ id: 'tool' }], WORKSPACE_ICON_COLORS: [{ hex: '#000000' }] } };
    const context = vm.createContext({ window, document, URLSearchParams, console, setTimeout, clearTimeout });
    vm.runInContext(fs.readFileSync(path.join(__dirname, 'skills.js'), 'utf8'), context);
    window.SkillsManager.renderSkills = () => {};
    return { manager: window.SkillsManager, state: window.SkillsState, grid, attrs };
}
const response = data => ({ ok: true, json: async () => data });

test('skill catalog follows cursors and fetches full content before opening a summary', async () => {
    const urls = [];
    const { manager, state } = harness(async url => {
        urls.push(url);
        if (url.endsWith('/detail')) return response({ id: 'one', content: 'Complete instructions', files: { assets: [{ name: 'file' }] } });
        return response(url.includes('cursor=') ? { items: [{ id: 'two', summary_only: true }], next_cursor: null } : { items: [{ id: 'one', content: 'preview', summary_only: true }], next_cursor: 'page-two' });
    });
    await manager.loadSkills();
    await manager.loadSkills({ append: true });
    assert.equal(state.skills.length, 2);
    assert.match(urls[1], /cursor=page-two/);
    const detail = await manager.ensureSkillDetail('one');
    assert.equal(detail.content, 'Complete instructions');
    assert.equal(detail.files.assets.length, 1);
    assert.equal(detail.summary_only, false);
    assert.equal(urls[2], '/api/v1/skills/one/detail');
});

test('a late catalog or detail response cannot replace a newer selection', async () => {
    let resolveOld;
    const pending = new Promise(resolve => { resolveOld = resolve; });
    const { manager, state, attrs } = harness(url => url.includes('q=new') ? Promise.resolve(response({ items: [{ id: 'new' }] })) : pending);
    const old = manager.loadSkills();
    state.searchQuery = 'new';
    await manager.loadSkills();
    resolveOld(response({ items: [{ id: 'old' }] }));
    await old;
    assert.equal(state.skills[0].id, 'new');
    assert.equal(attrs.has('aria-busy'), false);

    let resolveDetail;
    const detailHarness = harness(() => new Promise(resolve => { resolveDetail = resolve; }));
    detailHarness.state.skills = [{ id: 'old', summary_only: true }, { id: 'current', content: 'already loaded' }];
    const late = detailHarness.manager.ensureSkillDetail('old');
    const current = await detailHarness.manager.ensureSkillDetail('current');
    resolveDetail(response({ id: 'old', content: 'late' }));
    assert.equal(await late, null);
    assert.equal(current.id, 'current');
    assert.equal(detailHarness.attrs.has('aria-busy'), false);
});
