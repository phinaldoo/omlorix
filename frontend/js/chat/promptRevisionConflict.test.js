const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspaceSource = fs.readFileSync(path.join(__dirname, 'workspace.js'), 'utf8');
const formSource = fs.readFileSync(path.join(__dirname, 'workspaceCreateEditForms.js'), 'utf8');

test('prompt saves carry the editor revision and handle conflict responses', () => {
    assert.match(workspaceSource, /expected_revision:\s*this\.activePromptRevision/);
    assert.match(workspaceSource, /error\?\.status === 409/);
    assert.match(workspaceSource, /prompt_revision_conflict/);
    assert.match(workspaceSource, /showLatestPromptConflict/);
});

test('prompt editor preserves both versions and offers explicit conflict choices', () => {
    assert.match(formSource, /id="promptEditorConflict"/);
    assert.match(formSource, /id="promptConflictLocalContent"/);
    assert.match(formSource, /id="promptConflictRemoteContent"/);
    assert.match(formSource, /id="promptConflictReloadBtn"/);
    assert.match(formSource, /id="promptConflictKeepBtn"/);
});

test('prompt polling freshness is wired into the editor', () => {
    assert.match(workspaceSource, /setInterval\(\(\) => void this\.syncPromptEditor\(\), 5000\)/);
});
